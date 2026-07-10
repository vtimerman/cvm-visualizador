#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extratos_sei_baixar.py — baixa os documentos "Extrato de Sessao de Julgamento"
do Diario Eletronico (SEI) e extrai o RESULTADO com valores (multas, absolvicoes,
inabilitacoes) por processo, para julgar.db / tabela extratos_julgamento.

Cada publicacao "Extrato de Sessao de Julgamento" (em publicacoes.db) aponta para
um documento HTML com o resultado oficial da sessao: processo, acusados e o
dispositivo (ex.: "multa pecuniaria de R$ 500.000,00", "absolver", "inabilitacao
temporaria"). Os valores em R$ sao somados em multas_total (centavos ignorados).

Uso:
  python extratos_sei_baixar.py backfill [limite]
  python extratos_sei_baixar.py stats
"""
import os
import re
import sys
import html
import time
import sqlite3
import datetime as dt

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
PUB_DB = os.path.join(DIR, "publicacoes.db")
JUL_DB = os.path.join(DIR, "julgar.db")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PAUSA = float(os.environ.get("PAUSA", "0.3"))
RE_PROC = re.compile(r"1\d{4}\.\d{6}/\d{4}-\d{2}")
RE_RS = re.compile(r"R\$\s*([\d.]+(?:,\d{2})?)")


def conectar():
    con = sqlite3.connect(JUL_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS extratos_julgamento(
        link_sei TEXT PRIMARY KEY, proc_norm TEXT, data_publicacao TEXT,
        texto TEXT, multas_total REAL, n_multas INTEGER, absolvicoes INTEGER,
        inabilitacoes INTEGER, coletado_em TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ej_proc "
                "ON extratos_julgamento(proc_norm)")
    con.commit()
    return con


def _texto(hbody):
    h = re.sub(r"<style.*?</style>", " ", hbody, flags=re.S | re.I)
    h = re.sub(r"<script.*?</script>", " ", h, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", "\n", h)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n[ \t]*\n+", "\n", t).strip()


def _valor(s):
    """'1.234.567,89' -> 1234567.89"""
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def parse_extrato(hbody):
    txt = _texto(hbody)
    flat = re.sub(r"\s+", "", txt)
    m = RE_PROC.search(flat)
    proc = m.group(0) if m else ""
    valores = [_valor(v) for v in RE_RS.findall(txt)]
    absolv = len(re.findall(r"absolv", txt, re.I))
    inabil = len(re.findall(r"inabilita", txt, re.I))
    return {"proc_norm": proc, "texto": txt[:6000],
            "multas_total": round(sum(valores), 2), "n_multas": len(valores),
            "absolvicoes": absolv, "inabilitacoes": inabil}


def backfill(limite=None):
    cp = sqlite3.connect(PUB_DB)
    linhas = cp.execute(
        "SELECT link, data FROM publicacoes WHERE descricao LIKE "
        "'Extrato de Sess%' ORDER BY data_iso DESC").fetchall()
    cp.close()
    if limite:
        linhas = linhas[:int(limite)]
    con = conectar()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    hoje = dt.date.today().isoformat()
    ins = 0
    for link, data_pub in linhas:
        if con.execute("SELECT 1 FROM extratos_julgamento WHERE link_sei=?",
                       (link,)).fetchone():
            continue
        try:
            r = s.get(link, timeout=90)
            r.encoding = "iso-8859-1"
        except requests.RequestException as e:
            print(f"  ! erro rede: {e}", file=sys.stderr)
            continue
        p = parse_extrato(r.text)
        if not p["proc_norm"]:
            time.sleep(PAUSA)
            continue
        con.execute(
            "INSERT OR REPLACE INTO extratos_julgamento(link_sei,proc_norm,"
            "data_publicacao,texto,multas_total,n_multas,absolvicoes,"
            "inabilitacoes,coletado_em) VALUES(?,?,?,?,?,?,?,?,?)",
            (link, p["proc_norm"], data_pub, p["texto"], p["multas_total"],
             p["n_multas"], p["absolvicoes"], p["inabilitacoes"], hoje))
        ins += 1
        con.commit()
        if ins % 30 == 0:
            print(f"[extratos] {ins} processados")
        time.sleep(PAUSA)
    print(f"[extratos] concluido: {ins} novos")
    stats(con)
    con.close()


def stats(con=None):
    own = con is None
    con = con or conectar()
    n = con.execute("SELECT COUNT(*) FROM extratos_julgamento").fetchone()[0]
    tot = con.execute("SELECT SUM(multas_total) FROM extratos_julgamento"
                      ).fetchone()[0] or 0
    print(f"extratos: {n} | multas somadas: R$ {tot:,.2f}")
    if own:
        con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "backfill":
        backfill(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        stats()
