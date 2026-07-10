#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publicacoes_baixar.py — coleta as Publicacoes Eletronicas da CVM (Diario
Eletronico) do SEI para publicacoes.db.

Fonte (busca POST): https://sei.cvm.gov.br/sei/publicacoes/controlador_publicacoes.php
  acao=publicacao_pesquisar ; filtro por intervalo de datas com
  rdoDataPublicacao=E (Periodo explicito; 'I'=Indeterminada NAO filtra) ;
  pagina via hdnInicio (offset). Cada resultado:
  Protocolo, Descricao, Veiculo, Data, Unidade, Orgao, Resumo e link do documento
  (acao=publicacao_visualizar&id_documento=NNN).

Uso:
  python publicacoes_baixar.py backfill [dd/mm/aaaa dd/mm/aaaa]  # padrao: 2022->hoje
  python publicacoes_baixar.py atualizar [dias]                  # padrao: 7 dias
  python publicacoes_baixar.py stats
"""
import os
import re
import sys
import time
import sqlite3
import datetime as dt
from calendar import monthrange

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "publicacoes.db")
BASE = "https://sei.cvm.gov.br/sei/publicacoes/controlador_publicacoes.php"
QS = "?acao=publicacao_pesquisar&acao_origem=publicacao_pesquisar&id_orgao_publicacao=0"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PAUSA = float(os.environ.get("PAUSA", "0.4"))
RE_TR = re.compile(r'<tr[^>]*class="infraTr(?:Clara|Escura)"[^>]*>(.*?)</tr>', re.S)
RE_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
RE_ID = re.compile(r"id_documento=(\d+)")


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS publicacoes(
        id_documento INTEGER PRIMARY KEY, protocolo TEXT, descricao TEXT,
        veiculo TEXT, data TEXT, data_iso TEXT, unidade TEXT, orgao TEXT,
        resumo TEXT, link TEXT, coletado_em TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_pub_data ON publicacoes(data_iso)")
    con.commit()
    return con


def _txt(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&nbsp;", " ").strip()


def _iso(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(d or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def _hidden(t, name):
    m = re.search(r'name="' + name + r'"[^>]*value="([^"]*)"', t)
    return m.group(1) if m else ""


def _linhas(html):
    out = []
    for tr in RE_TR.findall(html):
        mid = RE_ID.search(tr)
        if not mid:
            continue
        cel = [_txt(c) for c in RE_TD.findall(tr)]
        # cel[0] = checkbox; depois: Protocolo, Descricao, Veiculo, Data,
        # Unidade, Orgao, Resumo, Imprensa Nacional, Acoes
        def g(i):
            return cel[i] if i < len(cel) else ""
        out.append({
            "id_documento": int(mid.group(1)), "protocolo": g(1),
            "descricao": g(2), "veiculo": g(3), "data": g(4),
            "unidade": g(5), "orgao": g(6), "resumo": g(7),
            "link": ("https://sei.cvm.gov.br/sei/publicacoes/"
                     "controlador_publicacoes.php?acao=publicacao_visualizar"
                     f"&id_documento={mid.group(1)}&id_orgao_publicacao=0")})
    return out


def buscar(con, dataIni, dataFim):
    """Coleta todas as publicacoes disponibilizadas no intervalo (paginado)."""
    s = requests.Session()
    s.headers["User-Agent"] = UA
    try:
        t = s.get(BASE + QS, timeout=60).text
    except requests.RequestException as e:
        print(f"  ! erro de rede (GET): {e}", file=sys.stderr)
        return 0
    comum = {"hdnInfraTipoPagina": _hidden(t, "hdnInfraTipoPagina"),
             "hdnInfraPrefixoCookie": _hidden(t, "hdnInfraPrefixoCookie"),
             "selOrgao[]": "0", "rdoDataPublicacao": "E",
             "txtDataInicio": dataIni, "txtDataFim": dataFim,
             "sbmPesquisar": "Pesquisar"}
    hoje = dt.date.today().isoformat()
    novos = inicio = 0
    vistos = set()
    while True:
        try:
            p = s.post(BASE + QS, data=dict(comum, hdnInicio=str(inicio)), timeout=90)
        except requests.RequestException as e:
            print(f"  ! erro de rede (POST {inicio}): {e}", file=sys.stderr)
            break
        p.encoding = "iso-8859-1"
        linhas = _linhas(p.text)
        ids = {x["id_documento"] for x in linhas}
        if not linhas or ids <= vistos:      # pagina vazia ou repetida = fim
            break
        vistos |= ids
        for x in linhas:
            con.execute(
                "INSERT OR REPLACE INTO publicacoes(id_documento,protocolo,"
                "descricao,veiculo,data,data_iso,unidade,orgao,resumo,link,"
                "coletado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (x["id_documento"], x["protocolo"], x["descricao"], x["veiculo"],
                 x["data"], _iso(x["data"]), x["unidade"], x["orgao"],
                 x["resumo"], x["link"], hoje))
            novos += 1
        con.commit()
        inicio += 40
        time.sleep(PAUSA)
    return novos


def backfill(ini="01/01/2022", fim=None):
    con = conectar()
    fim = fim or dt.date.today().strftime("%d/%m/%Y")
    d0 = dt.datetime.strptime(ini, "%d/%m/%Y").date()
    d1 = dt.datetime.strptime(fim, "%d/%m/%Y").date()
    # mes a mes (queries menores)
    cur = dt.date(d0.year, d0.month, 1)
    total = 0
    while cur <= d1:
        ult = monthrange(cur.year, cur.month)[1]
        di = max(cur, d0).strftime("%d/%m/%Y")
        df = min(dt.date(cur.year, cur.month, ult), d1).strftime("%d/%m/%Y")
        n = buscar(con, di, df)
        total += n
        print(f"[pub] {di}..{df}: +{n} (acumulado {total})")
        cur = dt.date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    tot = con.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
    con.close()
    print(f"[pub] backfill concluido: {total} coletadas nesta rodada | base {tot}")


def atualizar(dias=7):
    con = conectar()
    fim = dt.date.today()
    ini = fim - dt.timedelta(days=int(dias))
    n = buscar(con, ini.strftime("%d/%m/%Y"), fim.strftime("%d/%m/%Y"))
    tot = con.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
    con.close()
    print(f"[pub] atualizar ({dias}d): {n} publicacao(oes) na janela | base {tot}")


def stats():
    con = conectar()
    tot = con.execute("SELECT COUNT(*) FROM publicacoes").fetchone()[0]
    faixa = con.execute("SELECT MIN(data_iso), MAX(data_iso) FROM publicacoes "
                        "WHERE data_iso<>''").fetchone()
    print(f"publicacoes: {tot} | periodo: {faixa[0]} a {faixa[1]}")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "atualizar"
    if cmd == "backfill":
        backfill(*(sys.argv[2:4] if len(sys.argv) > 3 else []))
    elif cmd == "atualizar":
        atualizar(sys.argv[2] if len(sys.argv) > 2 else 7)
    else:
        stats()
