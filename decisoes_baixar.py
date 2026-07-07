#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decisoes_baixar.py — coleta as Decisões do Colegiado e as Atas do Colegiado da CVM.

Fonte: https://conteudo.cvm.gov.br/decisoes/index.html (busca paginada por searchPage).
  - categoria=decisao -> Decisões do Colegiado (cada uma com ementa, data, tipo, nº do
    processo no título e link para a página da decisão).
  - categoria=ata     -> Atas das reuniões do Colegiado (data, tipo, link da reunião).

Guarda em decisoes.db (tabelas `decisoes` e `atas_colegiado`).

Uso:
  python decisoes_baixar.py            # incremental (poucas páginas; para a rotina)
  python decisoes_baixar.py backfill   # varre TUDO (popular a base)
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
DB_PATH = os.path.join(DIR, "decisoes.db")
BASE = "https://conteudo.cvm.gov.br/decisoes/index.html"
SITE = "https://conteudo.cvm.gov.br"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

RE_ART = re.compile(r"<article>(.*?)</article>", re.S)
RE_LINK = re.compile(r'<h3><a href="([^"]+)"[^>]*title="([^"]*)"', re.S)
RE_DESC = re.compile(r'class="contentDesc">(.*?)</div>', re.S)
RE_DATA = re.compile(r"Data:\s*</b>\s*([0-3]?\d/[01]?\d/\d{4})")
RE_TIPO = re.compile(r"Tipo:\s*</b>\s*([^<]+)")
RE_PROC = re.compile(r"(1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}|"
                     r"SP\s?\d{4}/\d{3,6})")


def _limpo(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _iso(d):
    m = re.match(r"([0-3]?\d)/([01]?\d)/(\d{4})", d or "")
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""


def norm_proc(p):
    m = RE_PROC.search(str(p or ""))
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS decisoes(
        link TEXT PRIMARY KEY, ementa TEXT, descricao TEXT, data TEXT, data_iso TEXT,
        tipo TEXT, processo TEXT, proc_norm TEXT, coletado_em TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS atas_colegiado(
        link TEXT PRIMARY KEY, titulo TEXT, data TEXT, data_iso TEXT, tipo TEXT,
        coletado_em TEXT)""")
    con.commit()
    return con


def _get(params, tentativas=4):
    for i in range(tentativas):
        try:
            r = requests.get(BASE, headers=H, params=params, timeout=60)
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except requests.exceptions.RequestException:
            if i == tentativas - 1:
                raise
            time.sleep(2 * (i + 1))


def _data_do_link(link):
    """Extrai DD/MM/AAAA do link da reunião (.../YYYYMMDD_R1.html)."""
    m = re.search(r"/(\d{4})(\d{2})(\d{2})_R\d", link)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else ""


def parse_artigos(t):
    t = re.sub(r">\s+<", "><", t)
    out = []
    for art in RE_ART.findall(t):
        ml = RE_LINK.search(art)
        if not ml:
            continue
        link, titulo = ml.group(1), _limpo(ml.group(2))
        if link.startswith("/"):
            link = SITE + link
        md, mt = RE_DATA.search(art), RE_TIPO.search(art)
        mdesc = RE_DESC.search(art)
        out.append({
            "link": link, "titulo": titulo,
            "descricao": _limpo(mdesc.group(1)) if mdesc else "",
            "data": md.group(1) if md else "",
            "tipo": _limpo(mt.group(1)) if mt else "",
            "processo": (norm_proc(titulo) and RE_PROC.search(titulo).group(0)) or ""})
    return out


def coletar(categoria, max_pag, parar_sem_novos):
    con = conectar()
    hoje = dt.date.today().isoformat()
    tab = "decisoes" if categoria == "decisao" else "atas_colegiado"
    existentes = {u for (u,) in con.execute(f"SELECT link FROM {tab}")}
    novos = paginas = 0
    for p in range(1, max_pag + 1):
        params = {"lastNameShow": "", "lastName": "", "filtro": "todos",
                  "dataInicio": "01/01/2000", "dataFim": "", "categoria": categoria,
                  "searchPage": str(p), "itensPagina": "10"}
        if categoria == "ata":
            params["buscadoAta"] = "false"
        else:
            params["buscadoDecisao"] = "false"
        arts = parse_artigos(_get(params))
        if not arts:
            break
        paginas += 1
        achou = 0
        for a in arts:
            if a["link"] in existentes:
                continue
            if categoria == "decisao":
                con.execute("""INSERT OR IGNORE INTO decisoes(link,ementa,descricao,
                    data,data_iso,tipo,processo,proc_norm,coletado_em)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (a["link"], a["titulo"], a["descricao"], a["data"],
                     _iso(a["data"]), a["tipo"], a["processo"],
                     norm_proc(a["processo"]), hoje))
            else:
                data = a["data"] if re.match(r"\d{2}/\d{2}/\d{4}", a["data"]) \
                    else _data_do_link(a["link"])
                con.execute("""INSERT OR IGNORE INTO atas_colegiado(link,titulo,data,
                    data_iso,tipo,coletado_em) VALUES(?,?,?,?,?,?)""",
                    (a["link"], a["titulo"], data, _iso(data), a["tipo"], hoje))
            existentes.add(a["link"])
            novos += 1
            achou += 1
        con.commit()
        if parar_sem_novos and achou == 0 and p >= 2:
            break
        if p % 50 == 0:
            print(f"  [{categoria}] pag {p} | novos {novos}")
        time.sleep(0.2)
    tot = con.execute(f"SELECT COUNT(*) FROM {tab}").fetchone()[0]
    con.close()
    print(f"[decisoes] {categoria}: {paginas} paginas | {novos} novos | total {tot}")


def construir(backfill=False):
    mx = 1400 if backfill else 6
    parar = not backfill
    coletar("decisao", mx, parar)
    coletar("ata", 300 if backfill else 6, parar)


if __name__ == "__main__":
    construir(backfill=len(sys.argv) > 1 and sys.argv[1] == "backfill")
