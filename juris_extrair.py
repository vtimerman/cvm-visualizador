#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
juris_extrair.py — extrai o texto dos julgados completos da CVM (votos,
relatorios, extratos) da pasta local "Jurisprudencia CVM" para juris.db.

ATENCAO: juris.db e LOCAL (gitignored) por decisao do usuario — a pasta de
jurisprudencia nao vai para a nuvem por enquanto.

Estrutura da fonte: <ano>/<19957-XXXXXX-AAAA-DD | outros>.pdf (1999-2025).

Uso:
  python juris_extrair.py extrair [limite]
  python juris_extrair.py stats
"""
import os
import re
import sys
import glob
import shutil
import sqlite3
import tempfile
import subprocess
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "juris.db")
FONTE = os.environ.get("JURIS_DIR",
                       r"C:\Users\vtime\OneDrive\Desktop\Jurisprudencia CVM")


def _pdftotext():
    hit = shutil.which("pdftotext")
    if hit:
        return hit
    g = glob.glob(os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
        "oschwartz10612.Poppler*", "poppler-*", "Library", "bin",
        "pdftotext.exe"))
    return g[0] if g else "pdftotext"


PDFTOTEXT = _pdftotext()


def conectar():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS docs(
        arquivo TEXT PRIMARY KEY, ano_pasta TEXT, proc_norm TEXT,
        chars INTEGER, legivel INTEGER, texto TEXT, coletado_em TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS doc_analises(
        arquivo TEXT PRIMARY KEY, proc_norm TEXT, data_julg TEXT, relator TEXT,
        resultado TEXT, resumo TEXT, teses TEXT, area_tecnica TEXT,
        votos TEXT, ai_feito INTEGER DEFAULT 0, atualizado_em TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS teses(
        id INTEGER PRIMARY KEY AUTOINCREMENT, tema TEXT, tese TEXT,
        processo TEXT, data TEXT, relator TEXT, status TEXT,
        evolucao TEXT, fonte_arquivo TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_docs_proc ON docs(proc_norm)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_teses_tema ON teses(tema)")
    con.commit()
    return con


def _proc_do_nome(nome):
    """'19957-000596-2019-95.pdf' -> '19957.000596/2019-95' (ou variantes)."""
    base = os.path.splitext(os.path.basename(nome))[0]
    m = re.match(r"(1\d{4})-(\d{6})-(\d{4})-(\d{2})", base)
    if m:
        return f"{m.group(1)}.{m.group(2)}/{m.group(3)}-{m.group(4)}"
    m = re.match(r"(?:RJ|SP)?(\d{4})[-_](\d{1,6})", base)
    if m:
        return base.replace("-", "/", 1).upper()
    return base


def _legivel(txt):
    if len(txt) < 400:
        return 0
    bons = sum(c.isalnum() or c.isspace() or c in ".,;:()/-%" for c in txt[:4000])
    return 1 if bons / max(1, len(txt[:4000])) > 0.75 else 0


def extrair(limite=None):
    con = conectar()
    feitos = {r[0] for r in con.execute("SELECT arquivo FROM docs")}
    pdfs = sorted(glob.glob(os.path.join(FONTE, "[12][0-9][0-9][0-9]", "*.pdf")),
                  reverse=True)   # anos recentes primeiro
    pdfs = [p for p in pdfs if os.path.relpath(p, FONTE) not in feitos]
    if limite:
        pdfs = pdfs[:int(limite)]
    hoje = dt.date.today().isoformat()
    n = ileg = 0
    for pdf in pdfs:
        rel = os.path.relpath(pdf, FONTE)
        ano = rel.split(os.sep)[0]
        txt_path = os.path.join(tempfile.gettempdir(), "_juris_tmp.txt")
        try:
            subprocess.run([PDFTOTEXT, "-layout", "-enc", "UTF-8", pdf, txt_path],
                           check=True, capture_output=True, timeout=180)
            texto = open(txt_path, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            print(f"  ! falha {rel}: {e}", file=sys.stderr)
            texto = ""
        leg = _legivel(texto)
        ileg += (1 - leg)
        con.execute(
            "INSERT OR REPLACE INTO docs VALUES(?,?,?,?,?,?,?)",
            (rel, ano, _proc_do_nome(pdf), len(texto), leg,
             texto if leg else texto[:2000], hoje))
        n += 1
        if n % 100 == 0:
            con.commit()
            print(f"[juris] {n}/{len(pdfs)} extraidos ({ileg} ilegiveis)")
    con.commit()
    print(f"[juris] concluido: {n} extraidos | {ileg} ilegiveis (OCR depois)")
    stats(con)
    con.close()


def stats(con=None):
    own = con is None
    con = con or conectar()
    tot = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    leg = con.execute("SELECT COUNT(*) FROM docs WHERE legivel=1").fetchone()[0]
    ana = con.execute("SELECT COUNT(*) FROM doc_analises WHERE ai_feito=1"
                      ).fetchone()[0]
    ts = con.execute("SELECT COUNT(*) FROM teses").fetchone()[0]
    anos = con.execute("SELECT MIN(ano_pasta), MAX(ano_pasta) FROM docs"
                       ).fetchone()
    print(f"docs: {tot} ({leg} legiveis) | analisados: {ana} | teses: {ts} | "
          f"anos {anos[0]}-{anos[1]}")
    if own:
        con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "extrair":
        extrair(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        stats()
