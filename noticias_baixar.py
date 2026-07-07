#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
noticias_baixar.py — coleta as Notícias publicadas pela CVM.

Fonte: https://www.gov.br/cvm/pt-br/assuntos/noticias (paginação Plone b_start).
Cada notícia tem: categoria (ATIVIDADE SANCIONADORA, ALERTA AO MERCADO, ...),
título, link, data, resumo (lead) e tags. Guarda tudo em noticias.db.

O corpo completo de cada notícia (onde aparecem os números de processo e os nomes
dos envolvidos) é baixado à parte, da própria página da notícia (div
`parent-fieldname-text` do Plone), e guardado na coluna `corpo`.

Uso:
  python noticias_baixar.py              # incremental (poucas páginas + corpo dos novos)
  python noticias_baixar.py backfill     # varre TODAS as páginas (popular a base)
  python noticias_baixar.py corpos       # baixa o corpo das notícias que ainda não têm
"""
import os
import re
import sys
import html
import time
import sqlite3
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "noticias.db")
BASE = "https://www.gov.br/cvm/pt-br/assuntos/noticias"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

RE_LI = re.compile(r'<li><div class="conteudo">(.*?)</li>', re.S)
RE_CAT = re.compile(r'subtitulo-noticia">([^<]+)<')
RE_TIT = re.compile(r'<h2 class="titulo"><a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
RE_DATA = re.compile(r'class="data">\s*(\d{2}/\d{2}/\d{4})')
RE_DESC = re.compile(r'class="descricao">(.*?)</span></span>', re.S)
RE_TAG = re.compile(r'rel="tag"[^>]*>([^<]+)</a>')


def _limpo(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _iso(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS noticias(
        url TEXT PRIMARY KEY, categoria TEXT, titulo TEXT, data TEXT, data_iso TEXT,
        resumo TEXT, tags TEXT, coletado_em TEXT)""")
    cols = {r[1] for r in con.execute("PRAGMA table_info(noticias)")}
    if "corpo" not in cols:
        con.execute("ALTER TABLE noticias ADD COLUMN corpo TEXT")
    con.commit()
    return con


# marcadores que indicam o fim do texto da notícia (rodapé/boilerplate do Plone)
_FIM_CORPO = ("viewlet-below-content", "documentActions", "<footer",
              "Saiba mais</", "id=\"disqus", "portletNavigationTree",
              "class=\"related")


def extrair_corpo(t):
    """Extrai o texto do corpo da notícia (div parent-fieldname-text do Plone)."""
    i = t.find("parent-fieldname-text")
    if i < 0:
        return ""
    j = t.find(">", i)                      # pula o resto da tag de abertura
    seg = t[j + 1:] if j > 0 else t[i:]
    cortes = [seg.find(s) for s in _FIM_CORPO if 0 < seg.find(s)]
    seg = seg[:min(cortes)] if cortes else seg[:20000]
    corpo = re.sub(r"<[^>]+>", " ", seg)
    return re.sub(r"\s+", " ", html.unescape(corpo)).strip()[:20000]


def baixar_corpo(url, tentativas=3):
    for i in range(tentativas):
        try:
            r = requests.get(url, headers=H, timeout=60)
            r.encoding = r.apparent_encoding or "utf-8"
            return extrair_corpo(r.text)
        except requests.exceptions.RequestException:
            if i == tentativas - 1:
                return ""
            time.sleep(2 * (i + 1))


# categorias cujo corpo interessa cruzar com processos (sanção/julgamento/TC).
# As demais (agenda, evento, normatização...) não citam processos e ficam sem corpo,
# para não inchar o banco.
CAT_RELEVANTE = ("(categoria LIKE '%SANCION%' OR categoria LIKE '%JULG%' OR "
                 "categoria LIKE '%TERMO DE COMPROMISSO%' OR categoria LIKE '%ALERTA%')")


def backfill_corpos(workers=8):
    """Preenche `corpo` das notícias relevantes que ainda não têm (varredura única)."""
    con = conectar()
    pend = [u for (u,) in con.execute(
        "SELECT url FROM noticias WHERE (corpo IS NULL OR corpo='') AND "
        + CAT_RELEVANTE)]
    con.close()
    print(f"[noticias] corpos a baixar: {len(pend)}")
    feitos = vazios = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        resultados = ex.map(lambda u: (u, baixar_corpo(u)), pend)
        con = conectar()
        for k, (url, corpo) in enumerate(resultados, 1):
            con.execute("UPDATE noticias SET corpo=? WHERE url=?", (corpo, url))
            feitos += 1
            if not corpo:
                vazios += 1
            if k % 100 == 0:
                con.commit()
                print(f"  corpos {k}/{len(pend)} | vazios {vazios}")
        con.commit()
        com = con.execute("SELECT COUNT(*) FROM noticias "
                          "WHERE corpo IS NOT NULL AND corpo!=''").fetchone()[0]
        con.close()
    print(f"[noticias] corpos preenchidos: {feitos} ({vazios} vazios) | "
          f"total com corpo: {com}")


def parse_pagina(t):
    t = re.sub(r">\s+<", "><", t)
    out = []
    for li in RE_LI.findall(t):
        mt = RE_TIT.search(li)
        if not mt:
            continue
        url, titulo = mt.group(1), _limpo(mt.group(2))
        cat = RE_CAT.search(li)
        data = RE_DATA.search(li)
        desc = RE_DESC.search(li)
        resumo = _limpo(re.sub(r'<span class="data">.*?</span>', "", desc.group(1))) \
            if desc else ""
        resumo = resumo.lstrip("- ").strip()
        tags = "; ".join(_limpo(x) for x in RE_TAG.findall(li))
        out.append({
            "url": url, "categoria": _limpo(cat.group(1)) if cat else "",
            "titulo": titulo, "data": data.group(1) if data else "",
            "data_iso": _iso(data.group(1)) if data else "", "resumo": resumo,
            "tags": tags})
    return out


def coletar(max_paginas, parar_sem_novos=True):
    con = conectar()
    hoje = dt.date.today().isoformat()
    existentes = {u for (u,) in con.execute("SELECT url FROM noticias")}
    novos = paginas = 0
    for p in range(max_paginas):
        bs = p * 30
        r = requests.get(BASE, headers=H, params={"b_start:int": bs}, timeout=60)
        r.encoding = r.apparent_encoding or "utf-8"
        itens = parse_pagina(r.text)
        if not itens:
            break
        paginas += 1
        achou_novo = 0
        for it in itens:
            if it["url"] in existentes:
                continue
            corpo = baixar_corpo(it["url"])   # novos ja entram com o corpo
            con.execute("""INSERT OR IGNORE INTO noticias(url,categoria,titulo,data,
                data_iso,resumo,tags,coletado_em,corpo) VALUES(?,?,?,?,?,?,?,?,?)""",
                (it["url"], it["categoria"], it["titulo"], it["data"],
                 it["data_iso"], it["resumo"], it["tags"], hoje, corpo))
            existentes.add(it["url"])
            novos += 1
            achou_novo += 1
        con.commit()
        if parar_sem_novos and achou_novo == 0 and p >= 1:
            break               # incremental: nada novo nesta página -> encerra
        time.sleep(0.25)
    tot = con.execute("SELECT COUNT(*) FROM noticias").fetchone()[0]
    sanc = con.execute("SELECT COUNT(*) FROM noticias WHERE categoria LIKE '%SANCION%'"
                       ).fetchone()[0]
    con.close()
    print(f"[noticias] {paginas} paginas | {novos} novas | total {tot} | "
          f"atividade sancionadora {sanc}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "backfill":
        coletar(max_paginas=400, parar_sem_novos=False)
    elif cmd == "corpos":
        backfill_corpos()
    else:
        coletar(max_paginas=5, parar_sem_novos=True)
