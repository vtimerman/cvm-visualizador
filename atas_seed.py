#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atas_seed.py — monta a base das Atas do CGE (camada 1: metadados objetivos).

Le os textos ja extraidos em atas_txt/*.txt, extrai numero, tipo, data e a lista
de membros por regex, guarda o texto completo + o link, e grava em atas.db.
A "parametrizacao por IA" (resumo, deliberacoes, palavras-chave) e' feita depois,
pelo Claude, preenchendo as colunas correspondentes (ai_feito passa a 1).

Uso:
  python atas_seed.py                 # (re)constroi a base a partir de atas_txt/
  python atas_seed.py pendentes       # lista as atas ainda sem parametrizacao de IA
"""
import os
import re
import sys
import glob
import sqlite3
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "atas.db")
TXT_DIR = os.path.join(DIR, "atas_txt")
URLS = os.path.join(DIR, "atas_urls.txt")

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
         "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
         "outubro": 10, "novembro": 11, "dezembro": 12}


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS atas(
        arquivo TEXT PRIMARY KEY, numero TEXT, tipo TEXT, data TEXT, data_iso TEXT,
        membros TEXT, deliberacoes TEXT, resumo TEXT, palavras_chave TEXT,
        texto TEXT, link TEXT, ai_feito INTEGER DEFAULT 0, coletado_em TEXT)""")
    con.commit()
    return con


def mapa_links():
    m = {}
    if os.path.exists(URLS):
        for u in open(URLS, encoding="utf-8"):
            u = u.strip()
            if u:
                base = os.path.splitext(os.path.basename(u))[0]
                m[base] = u
    return m


def _fmt(d, mo, a):
    if 1 <= d <= 31 and 1 <= mo <= 12 and 2000 <= a <= 2100:
        return f"{d:02d}/{mo:02d}/{a}", f"{a}-{mo:02d}-{d:02d}"
    return "", ""


def _data_de(texto, base):
    """Data da reunião: prioriza 'DATA ... REALIZAÇÃO: DD/MM/AAAA', depois a data
    no nome do arquivo, depois 'DD de MÊS de AAAA' (evita datas de boilerplate)."""
    m = re.search(r"REALIZA[ÇC][ÃA]O:?\s*(\d{1,2})/(\d{1,2})/(\d{4})", texto, re.I)
    if m:
        r = _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if r[0]:
            return r
    m = re.search(r"(\d{1,2})[-_.](\d{1,2})[-_.](\d{4})", base)  # nome do arquivo
    if m:
        r = _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if r[0]:
            return r
    m = re.search(r"(\d{1,2})\s+DE\s+([A-Za-zçÇ]+)\s+DE\s+(\d{4})", texto[:400], re.I)
    if m:
        mm = MESES.get(m.group(2).lower().replace("ç", "c"), 0)
        if mm:
            r = _fmt(int(m.group(1)), mm, int(m.group(3)))
            if r[0]:
                return r
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", texto)  # 1a data dd/mm/aaaa
    if m:
        return _fmt(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return "", ""


def parse_meta(texto, base=""):
    cab = re.sub(r"\s+", " ", texto[:800])
    up = cab.upper()
    numero = ""
    m = re.search(r"(\d+)\s*[ªa]\s*REUNI", up) or re.search(r"\((\d+)\s*[ªa]\)", cab)
    if m:
        numero = m.group(1)
    elif base:  # nº da ata pelo nome do arquivo (padrões seguros)
        mb = (re.search(r"__(\d{2,3})__", base) or re.search(r"_(\d{2,3})_", base)
              or re.search(r"\bata[-_](\d{2,3})\b", base, re.I))
        if mb:
            numero = mb.group(1)
    tipo = ""
    if "EXTRAORDIN" in up:
        tipo = "Extraordinária"
    elif "ORDIN" in up:
        tipo = "Ordinária"
    data, data_iso = _data_de(texto, base)
    # membros: bloco apos "Membros do CGE" ate assinaturas/rodape
    membros = []
    mm = re.search(r"Membros do CGE\s*:?(.*?)(Documento assinado|assinado eletronicamente|$)",
                   texto, re.I | re.S)
    if mm:
        for lin in mm.group(1).splitlines():
            lin = lin.strip()
            g = re.match(r"^\d+\.\s*(.+)$", lin)
            if g:
                nome = re.split(r"\s+[–-]\s+", g.group(1))[0].strip()
                if nome:
                    membros.append(nome)
    return numero, tipo, data, data_iso, " | ".join(membros)


def construir():
    con = conectar()
    links = mapa_links()
    hoje = dt.date.today().isoformat()
    n = 0
    for txt in sorted(glob.glob(os.path.join(TXT_DIR, "*.txt"))):
        base = os.path.splitext(os.path.basename(txt))[0]
        texto = open(txt, encoding="utf-8").read()
        numero, tipo, data, data_iso, membros = parse_meta(texto, base)
        link = links.get(base, "")
        # preserva a parametrizacao de IA se ja existir
        row = con.execute("SELECT deliberacoes, resumo, palavras_chave, ai_feito "
                          "FROM atas WHERE arquivo=?", (base,)).fetchone()
        delib, resumo, pchave, ai = (row if row else ("", "", "", 0))
        con.execute("""INSERT INTO atas(arquivo,numero,tipo,data,data_iso,membros,
            deliberacoes,resumo,palavras_chave,texto,link,ai_feito,coletado_em)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(arquivo) DO UPDATE SET
            numero=excluded.numero, tipo=excluded.tipo, data=excluded.data,
            data_iso=excluded.data_iso, membros=excluded.membros,
            texto=excluded.texto, link=excluded.link""",
            (base, numero, tipo, data, data_iso, membros, delib, resumo, pchave,
             texto, link, ai, hoje))
        n += 1
    con.commit()
    tot = con.execute("SELECT COUNT(*) FROM atas").fetchone()[0]
    comai = con.execute("SELECT COUNT(*) FROM atas WHERE ai_feito=1").fetchone()[0]
    con.close()
    print(f"[atas_seed] {n} atas processadas | total {tot} | com IA {comai}")


def pendentes():
    con = conectar()
    for r in con.execute("SELECT arquivo, numero, tipo, data FROM atas "
                         "WHERE ai_feito=0 ORDER BY data_iso"):
        print(r)
    con.close()


def aplicar_ia(json_path):
    """Aplica a parametrizacao de IA a partir de um JSON:
    { "arquivo": {"resumo": "...", "deliberacoes": "...", "palavras_chave": "..."} }
    e marca ai_feito=1 para cada ata atualizada."""
    import json
    dados = json.load(open(json_path, encoding="utf-8"))
    con = conectar()
    n = 0
    for arq, campos in dados.items():
        con.execute("""UPDATE atas SET resumo=?, deliberacoes=?, palavras_chave=?,
                       ai_feito=1 WHERE arquivo=?""",
                    (campos.get("resumo", ""), campos.get("deliberacoes", ""),
                     campos.get("palavras_chave", ""), arq))
        n += con.total_changes and 1 or 0
    con.commit()
    con.close()
    print(f"[atas_seed] IA aplicada a {len(dados)} ata(s).")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pendentes":
        pendentes()
    elif len(sys.argv) > 2 and sys.argv[1] == "aplicar_ia":
        aplicar_ia(sys.argv[2])
    else:
        construir()
