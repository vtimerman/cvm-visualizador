#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
julgar_baixar.py — coleta a lista oficial de "Processos a Julgar (Por Relator)"
da CVM, que e' a fonte AUTORITATIVA do relator atual de cada PAS a julgar.

Fonte: https://www.gov.br/cvm/pt-br/assuntos/processos/processos-a-julgar-por-relator
A pagina publica um arquivo .xlsx (nome muda a cada atualizacao). Baixamos o link
.xlsx, lemos a planilha (Relator | Dt. inicio | Processo | Tipo | Rito | Data),
fazendo forward-fill do relator (so vem na 1a linha de cada grupo) e gravamos em
julgar.db (tabela `a_julgar`, snapshot completo a cada coleta).

Uso:
  python julgar_baixar.py
"""
import os
import io
import re
import sqlite3
import datetime as dt

import requests
import openpyxl

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "julgar.db")
PAGINA = ("https://www.gov.br/cvm/pt-br/assuntos/processos/"
          "processos-a-julgar-por-relator")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def norm_proc(p):
    if not p:
        return ""
    m = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}", str(p))
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS a_julgar(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        relator_nome TEXT, processo TEXT, proc_norm TEXT,
        tipo TEXT, rito TEXT, dt_inicio TEXT, data_ref TEXT,
        fonte TEXT, coletado_em TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS julgar_meta(
        chave TEXT PRIMARY KEY, valor TEXT)""")
    con.commit()
    return con


def achar_xlsx():
    r = requests.get(PAGINA, headers=H, timeout=60)
    r.encoding = "utf-8"
    # link .xlsx do conteudo (o rotulo costuma citar "Sancionadores com Relator")
    cands = re.findall(r'href=["\']([^"\']+\.xlsx)["\']', r.text, re.I)
    cands = [c if c.startswith("http") else
             ("https://www.gov.br" + c if c.startswith("/") else PAGINA + "/" + c)
             for c in cands]
    return cands[0] if cands else None


def _fmt_data(v):
    if isinstance(v, dt.datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, dt.date):
        return v.strftime("%d/%m/%Y")
    return str(v).strip() if v is not None else ""


def parse_xlsx(conteudo):
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True)
    ws = wb.worksheets[0]
    titulo = ""
    linhas = list(ws.iter_rows(values_only=True))
    # localizar cabecalho (linha com "Relator" e "Processo")
    hdr = None
    for i, row in enumerate(linhas):
        vals = [str(c).strip().lower() if c else "" for c in row]
        if not titulo and any(v and "processos no colegiado" in v for v in vals):
            titulo = " ".join(str(c) for c in row if c).strip()
        if "relator" in vals and any("processo" in v for v in vals):
            hdr = i
            cols = vals
            break
    mt = re.search(r"(Processos no Colegiado.*?\d{2}/\d{2}/\d{4})", titulo)
    titulo = re.sub(r"\s+", " ", mt.group(1)).strip() if mt else \
        re.sub(r"\s+", " ", titulo).strip()
    if hdr is None:
        return titulo, []
    ci = {name: cols.index(name) for name in
          ["relator", "processo", "tipo", "rito"] if name in cols}
    # colunas de data pelo rotulo aproximado
    idx_ini = next((j for j, v in enumerate(cols) if "in" in v and "cio" in v), None)
    idx_data = next((j for j, v in enumerate(cols)
                     if v.strip() == "data"), None)
    out = []
    relator = ""
    for row in linhas[hdr + 1:]:
        proc = row[ci["processo"]] if "processo" in ci else None
        if proc is None or not str(proc).strip():
            continue
        rel_cell = row[ci["relator"]] if "relator" in ci else None
        if rel_cell and str(rel_cell).strip():
            relator = re.sub(r"\s+", " ", str(rel_cell)).strip()
        out.append({
            "relator": relator,
            "processo": str(proc).strip(),
            "tipo": str(row[ci["tipo"]]).strip() if "tipo" in ci and row[ci["tipo"]] else "",
            "rito": str(row[ci["rito"]]).strip() if "rito" in ci and row[ci["rito"]] else "",
            "dt_inicio": _fmt_data(row[idx_ini]) if idx_ini is not None else "",
            "data_ref": _fmt_data(row[idx_data]) if idx_data is not None else "",
        })
    return titulo, out


def construir():
    xlsx = achar_xlsx()
    if not xlsx:
        print("[julgar] nao encontrei o link .xlsx na pagina.")
        return
    r = requests.get(xlsx, headers=H, timeout=60)
    titulo, linhas = parse_xlsx(r.content)
    con = conectar()
    hoje = dt.date.today().isoformat()
    con.execute("DELETE FROM a_julgar")
    for x in linhas:
        con.execute("""INSERT INTO a_julgar(relator_nome,processo,proc_norm,tipo,
            rito,dt_inicio,data_ref,fonte,coletado_em)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (x["relator"], x["processo"], norm_proc(x["processo"]), x["tipo"],
             x["rito"], x["dt_inicio"], x["data_ref"],
             os.path.basename(xlsx), hoje))
    con.execute("INSERT OR REPLACE INTO julgar_meta VALUES('titulo',?)", (titulo,))
    con.execute("INSERT OR REPLACE INTO julgar_meta VALUES('fonte',?)",
                (os.path.basename(xlsx),))
    con.execute("INSERT OR REPLACE INTO julgar_meta VALUES('atualizado_em',?)", (hoje,))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM a_julgar").fetchone()[0]
    nrel = con.execute("SELECT COUNT(DISTINCT relator_nome) FROM a_julgar").fetchone()[0]
    con.close()
    print(f"[julgar] {n} processos a julgar | {nrel} relatores | fonte {os.path.basename(xlsx)}")
    print(f"[julgar] titulo: {titulo}")


if __name__ == "__main__":
    construir()
