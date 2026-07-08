#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sancionadores_julgados_baixar.py — coleta o link da PAGINA DE JULGAMENTO
(relatorio/voto/decisao) de cada Processo Sancionador Julgado da CVM.

Fonte: listagem AJAX em
  https://conteudo.cvm.gov.br/sancionadores/index.html?filtro=todos&buscado=true
  &itensPagina=100&searchPage=N&ordenar=recentes
Cada resultado aponta para uma pagina HTML da decisao, cuja URL embute o numero
do processo (17 digitos apos "_PAS_"):
  /sancionadores/sancionador/<ANO>/<AAAAMMDD>_PAS_<17digitos>.html

Grava em julgar.db (tabela `julgamento_paginas`): proc_norm -> (data, link).
O app usa isso para linkar direto o julgamento na ficha do processo.

Uso:
  python sancionadores_julgados_baixar.py            # coleta/atualiza
  python sancionadores_julgados_baixar.py stats
"""
import os
import re
import sys
import time
import sqlite3
import datetime as dt

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("JULGAR_DB_PATH", os.path.join(DIR, "julgar.db"))
BASE = "https://conteudo.cvm.gov.br/sancionadores/index.html"
HOST = "https://conteudo.cvm.gov.br"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PAUSA = float(os.environ.get("PAUSA", "0.3"))
MAX_PAGINAS = int(os.environ.get("MAX_PAGINAS", "60"))

LINK_RE = re.compile(
    r'href=["\']([^"\']*sancionador/(\d{4})/(\d{8})_PAS_(\d{15,18})\.html)["\']',
    re.I)


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS julgamento_paginas(
        proc_norm TEXT PRIMARY KEY, data TEXT, link TEXT, coletado_em TEXT)""")
    con.commit()
    return con


def _norm(dig):
    """17 digitos -> 19957.001254/2023-79 (formato SEI)."""
    if len(dig) == 17:
        return f"{dig[0:5]}.{dig[5:11]}/{dig[11:15]}-{dig[15:17]}"
    return ""


def _iso(aaaammdd):
    try:
        return f"{aaaammdd[0:4]}-{aaaammdd[4:6]}-{aaaammdd[6:8]}"
    except Exception:
        return ""


def coletar():
    con = conectar()
    hoje = dt.date.today().isoformat()
    vistos = {}  # proc_norm -> (data_iso, link)  — mantem o 1o (mais recente)
    for page in range(1, MAX_PAGINAS + 1):
        params = {"filtro": "todos", "buscado": "true", "itensPagina": "100",
                  "searchPage": str(page), "ordenar": "recentes"}
        try:
            r = requests.get(BASE, params=params, headers={"User-Agent": UA},
                             timeout=60)
        except requests.RequestException as e:
            print(f"  ! erro de rede na pagina {page}: {e}", file=sys.stderr)
            break
        achados = LINK_RE.findall(r.text)
        if not achados:
            break
        novos_pg = 0
        for full, ano, data, dig in achados:
            pn = _norm(dig)
            if not pn or pn in vistos:
                continue
            link = full if full.startswith("http") else HOST + full
            vistos[pn] = (_iso(data), link)
            novos_pg += 1
        print(f"[sanc-julg] pagina {page}: {len(achados)} links "
              f"({novos_pg} novos; acumulado {len(vistos)})")
        time.sleep(PAUSA)
    # grava (upsert)
    for pn, (data, link) in vistos.items():
        con.execute(
            "INSERT INTO julgamento_paginas(proc_norm,data,link,coletado_em) "
            "VALUES(?,?,?,?) ON CONFLICT(proc_norm) DO UPDATE SET "
            "data=excluded.data, link=excluded.link, coletado_em=excluded.coletado_em",
            (pn, data, link, hoje))
    con.commit()
    tot = con.execute("SELECT COUNT(*) FROM julgamento_paginas").fetchone()[0]
    # cobertura vs. julgados (se a tabela existir)
    try:
        jg = [r[0] for r in con.execute("SELECT proc_norm FROM julgados").fetchall()]
        cob = sum(1 for x in jg if x in vistos)
        print(f"[sanc-julg] paginas de julgamento: {len(vistos)} coletadas | "
              f"base total {tot} | cobre {cob}/{len(jg)} julgados")
    except sqlite3.OperationalError:
        print(f"[sanc-julg] paginas de julgamento: {len(vistos)} | base total {tot}")
    con.close()


def stats():
    con = conectar()
    tot = con.execute("SELECT COUNT(*) FROM julgamento_paginas").fetchone()[0]
    print(f"paginas de julgamento na base: {tot}")
    con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        stats()
    else:
        coletar()
