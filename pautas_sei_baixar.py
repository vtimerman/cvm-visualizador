#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pautas_sei_baixar.py — ingere os documentos "Pauta de Sessao de Julgamento"
publicados no Diario Eletronico (SEI) para complementar a base de pautas
(pautas.db / tabela pauta_processos, fonte='SEI').

Cada publicacao "Pauta de Sessao de Julgamento" (em publicacoes.db) aponta para
um documento HTML no SEI com UM processo incluido em sessao, contendo: nº do
processo, Reg. Colegiado, relator, data/hora/local da sessao, modo (presencial/
videoconferencia), objeto e a lista de acusados + advogados. O campo 'resumo'
da publicacao vem vazio; por isso baixamos o documento.

Diferente do coletor por snapshot (so ve a pauta atual), o SEI da o HISTORICO,
o que permite calcular "tirado de pauta" (pautado x extrato/ata da sessao).

Uso:
  python pautas_sei_baixar.py backfill [limite]
  python pautas_sei_baixar.py stats
"""
import os
import re
import sys
import time
import html
import sqlite3
import datetime as dt

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
PUB_DB = os.path.join(DIR, "publicacoes.db")
PAU_DB = os.path.join(DIR, "pautas.db")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PAUSA = float(os.environ.get("PAUSA", "0.3"))
FONTE = "SEI (Diario Eletronico)"

RE_PROC = re.compile(r"(1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6})")
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
         "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
         "novembro": 11, "dezembro": 12}


def _norm_proc(p):
    m = RE_PROC.search(str(p or ""))
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def _texto(hbody):
    h = re.sub(r"<style.*?</style>", " ", hbody, flags=re.S | re.I)
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", "\n", h)
    h = html.unescape(h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n[ \t]*\n+", "\n", h)
    return h.strip()


def _campo(txt, rotulo):
    """Valor de um rotulo do formato 'Rotulo : valor' (exige os dois-pontos, para
    nao casar a palavra em prosa, ex.: 'objeto do processo')."""
    m = re.search(rotulo + r"\s*:\s*([^\n]+)", txt, re.I)
    return m.group(1).strip(" .:-") if m else ""


def _reg(txt):
    m = re.search(r"Reg\.?\s*Col\.?\s*n?[ºo\.]?\s*([\d./-]+)", txt, re.I)
    return m.group(1).strip(" ./-") if m else ""


def _mk(d, mo, a):
    try:
        return f"{d:02d}/{mo:02d}/{a}", dt.date(a, mo, d).isoformat()
    except ValueError:
        return "", ""


def _data_sessao(txt, retirado=False):
    """Data da sessao: 'Data : DD.MM.AAAA' (inclusao) ou 'retirado da pauta da
    sessao de julgamento de DD/MM/AAAA' (retirada). Aceita data por extenso."""
    D = r"(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})"  # tolera '23 .12.2025'
    pats = ([r"retirad[oa]\s+da\s+pauta\s+da\s+sess[ãa]o\s+de\s+julgamento\s+de\s+"
             + D] if retirado else []) + [
        r"Data\s*:\s*" + D,
        r"sess[ãa]o\s+de\s+julgamento\s+de\s+" + D]
    for p in pats:
        m = re.search(p, txt, re.I)
        if m:
            return _mk(*map(int, m.groups()))
    # por extenso
    m = re.search(r"Data\s*:[^\n]*?(\d{1,2})\s+de\s+([a-zA-Zçã]+)\s+de\s+(\d{4})",
                  txt, re.I)
    if m and MESES.get(m.group(2).lower()):
        return _mk(int(m.group(1)), MESES[m.group(2).lower()], int(m.group(3)))
    return "", ""


def _acusados_advogados(txt):
    """Bloco 'Acusados ... Advogados ...' -> (acusados, advogados) como texto."""
    m = re.search(r"Acusados(.*?)(?:Ficam os acusados|Sem preju|$)", txt, re.S | re.I)
    if not m:
        return "", ""
    bloco = re.sub(r"\s*\n\s*", " | ", m.group(1)).strip(" |")
    # separa advogados (linhas com OAB) do resto
    advs = re.findall(r"([^|]*OAB[^|]*)", bloco)
    return bloco[:1500], " | ".join(a.strip() for a in advs)[:800]


def parse_pauta(hbody):
    txt = _texto(hbody)
    flat = re.sub(r"\s+", "", txt)   # repara nº quebrado por <br> (ex.: '1'+quebra)
    mproc = RE_PROC.search(flat)
    proc = re.sub(r"\s+", "", mproc.group(0)).upper() if mproc else ""
    reg = _reg(flat) or _reg(txt)
    relator = _campo(txt, r"Relator[ao]?")
    horario = _campo(txt, r"Hor[aá]rio")
    local = _campo(txt, r"Local")
    obj = _campo(txt, r"Objeto(?:\s+do\s+processo)?")
    modo = ("presencial" if re.search(r"presencial", txt, re.I) else "") + \
           (" / videoconferencia" if re.search(r"videoconfer", txt, re.I) else "")
    retirado = bool(re.search(r"retirad[oa]\s+da\s+pauta", txt, re.I))
    situacao = "retirado" if retirado else "incluido"
    if retirado and re.search(r"sine\s+die", txt, re.I):
        situacao = "retirado (sine die)"
    data, iso = _data_sessao(txt, retirado)
    acus, advs = _acusados_advogados(txt)
    return {"processo": proc, "proc_norm": proc, "reg_col": reg.strip(),
            "relator": relator, "data_sessao": data, "data_sessao_iso": iso,
            "horario": horario, "local": local, "modo": modo.strip(" /"),
            "objeto": obj, "acusados": acus, "advogados": advs,
            "situacao": situacao}


def _prep_schema(con):
    # Tabela dedicada ao HISTORICO das pautas publicadas no SEI. Nao usa
    # pauta_processos (que tem UNIQUE(fonte,proc_norm), pensada p/ snapshot: 1
    # linha por processo). Aqui um processo pode aparecer em varias sessoes.
    con.execute("""CREATE TABLE IF NOT EXISTS pauta_sei(
        link_sei TEXT PRIMARY KEY, processo TEXT, proc_norm TEXT, reg_col TEXT,
        relator TEXT, data_sessao TEXT, data_sessao_iso TEXT, horario TEXT,
        local TEXT, modo TEXT, objeto TEXT, acusados TEXT, advogados TEXT,
        situacao TEXT, data_publicacao TEXT, coletado_em TEXT)""")
    if "situacao" not in [r[1] for r in con.execute("PRAGMA table_info(pauta_sei)")]:
        con.execute("ALTER TABLE pauta_sei ADD COLUMN situacao TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ps_proc ON pauta_sei(proc_norm)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ps_sessao "
                "ON pauta_sei(data_sessao_iso)")
    con.commit()


def backfill(limite=None):
    if not os.path.exists(PUB_DB):
        print("publicacoes.db nao encontrado", file=sys.stderr)
        return
    cp = sqlite3.connect(PUB_DB)
    linhas = cp.execute(
        "SELECT link, data FROM publicacoes WHERE descricao LIKE 'Pauta de Sess%' "
        "ORDER BY data_iso DESC").fetchall()
    cp.close()
    if limite:
        linhas = linhas[:int(limite)]
    con = sqlite3.connect(PAU_DB)
    _prep_schema(con)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    hoje = dt.date.today().isoformat()
    feito = ins = 0
    for link, data_pub in linhas:
        # ja temos esse documento?
        if con.execute("SELECT 1 FROM pauta_sei WHERE link_sei=?",
                       (link,)).fetchone():
            continue
        try:
            r = s.get(link, timeout=90)
            r.encoding = "iso-8859-1"
        except requests.RequestException as e:
            print(f"  ! erro rede: {e}", file=sys.stderr)
            continue
        p = parse_pauta(r.text)
        feito += 1
        if not p["proc_norm"]:
            time.sleep(PAUSA)
            continue
        con.execute(
            "INSERT OR REPLACE INTO pauta_sei(link_sei,processo,proc_norm,reg_col,"
            "relator,data_sessao,data_sessao_iso,horario,local,modo,objeto,"
            "acusados,advogados,situacao,data_publicacao,coletado_em) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (link, p["processo"], p["proc_norm"], p["reg_col"], p["relator"],
             p["data_sessao"], p["data_sessao_iso"], p["horario"], p["local"],
             p["modo"], p["objeto"], p["acusados"], p["advogados"], p["situacao"],
             data_pub, hoje))
        ins += 1
        con.commit()
        if ins % 25 == 0:
            print(f"[pauta-sei] {ins} inseridas / {feito} lidas")
        time.sleep(PAUSA)
    print(f"[pauta-sei] concluido: {ins} inseridas, {feito} documentos lidos")
    stats(con)
    con.close()


def stats(con=None):
    own = con is None
    con = con or sqlite3.connect(PAU_DB)
    _prep_schema(con)
    n = con.execute("SELECT COUNT(*) FROM pauta_sei").fetchone()[0]
    npr = con.execute("SELECT COUNT(DISTINCT proc_norm) FROM pauta_sei").fetchone()[0]
    faixa = con.execute("SELECT MIN(data_sessao_iso),MAX(data_sessao_iso) FROM "
                        "pauta_sei WHERE data_sessao_iso<>''").fetchone()
    print(f"pauta_sei: {n} inclusoes | {npr} processos | sessoes {faixa[0]} a {faixa[1]}")
    if own:
        con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "backfill":
        backfill(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        stats()
