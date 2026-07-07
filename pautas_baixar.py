#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pautas_baixar.py — coleta as Pautas de Julgamento (sessões agendadas) da CVM.

Fonte: https://www.gov.br/cvm/pt-br/assuntos/processos/pautas-de-julgamento
A página publica a pauta ATUAL como um PDF datado (ex.: ...2026.05.29.pdf), com
blocos "PAS CVM nº ..." (data da sessão, relator, superintendência, objeto).

Como só a pauta atual fica publicada, guardamos um SNAPSHOT de cada versão (por
nome de arquivo/data). Ao surgir uma pauta nova, comparamos com a anterior: um
processo que estava pautado e sumiu (sem ter sido julgado) foi RETIRADO DE PAUTA
— e isso é um alerta.

Uso:
  python pautas_baixar.py
"""
import os
import re
import glob
import shutil
import sqlite3
import subprocess
import datetime as dt

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "pautas.db")
PDF_DIR = os.path.join(DIR, "pautas_pdf")
PAGINA = "https://www.gov.br/cvm/pt-br/assuntos/processos/pautas-de-julgamento"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def achar_pdftotext():
    p = os.environ.get("PDFTOTEXT") or shutil.which("pdftotext")
    if p and os.path.exists(p):
        return p
    hits = glob.glob(os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
        "oschwartz10612.Poppler*", "**", "pdftotext.exe"), recursive=True)
    return hits[0] if hits else "pdftotext"


def norm_proc(p):
    m = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}", str(p or ""))
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS snapshots(
        fonte TEXT PRIMARY KEY, atualizada_em TEXT, n_proc INTEGER, coletado_em TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS pauta_processos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fonte TEXT, processo TEXT, proc_norm TEXT, data_sessao TEXT,
        data_sessao_iso TEXT, horario TEXT, relator TEXT, superintendencia TEXT,
        procurador TEXT, objeto TEXT, publicada TEXT, atualizada_em TEXT,
        UNIQUE(fonte, proc_norm))""")
    con.execute("""CREATE TABLE IF NOT EXISTS retirados(
        proc_norm TEXT, processo TEXT, data_sessao TEXT, relator TEXT, objeto TEXT,
        visto_em TEXT, detectado_em TEXT, UNIQUE(proc_norm, visto_em))""")
    con.commit()
    return con


def _iso(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def achar_pdf_pauta():
    r = requests.get(PAGINA, headers=H, timeout=60)
    r.encoding = "utf-8"
    cands = re.findall(r'href="([^"]*[Pp]auta[^"]*\.pdf)"', r.text)
    if not cands:
        return None
    url = cands[0]
    return url if url.startswith("http") else "https://www.gov.br" + url


def parse_pauta(texto):
    m = re.search(r"ATUALIZAD[AO]\s+EM\s+(\d{2}/\d{2}/\d{4})", texto, re.I)
    atualizada = m.group(1) if m else ""
    blocos = re.split(r"(?=PAS CVM n[ºo°]\s*\d)", texto)
    itens = []
    for b in blocos:
        mp = re.search(r"PAS CVM n[ºo°]\s*(1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d+)", b)
        if not mp:
            continue
        proc = mp.group(1).strip()

        def campo(rot):
            mm = re.search(rot + r"\s*:?\s*([^\n]+)", b, re.I)
            return re.sub(r"\s+", " ", mm.group(1)).strip() if mm else ""
        data = campo(r"Data")
        data = re.search(r"\d{2}/\d{2}/\d{4}", data)
        data = data.group(0) if data else ""
        obj = re.search(r"Objeto(?:\s+do\s+processo)?\s*:?\s*(.+?)(?:Pauta publicada|"
                        r"De ordem|PAS CVM|$)", b, re.S | re.I)
        objeto = re.sub(r"\s+", " ", obj.group(1)).strip() if obj else ""
        itens.append({
            "processo": proc, "proc_norm": norm_proc(proc),
            "data_sessao": data, "data_sessao_iso": _iso(data),
            "horario": campo(r"Hor[áa]rio"),
            "relator": campo(r"Relator[a]?"),
            "superintendencia": campo(r"Superintend[êe]ncia"),
            "procurador": campo(r"Procurador[a]?"),
            "objeto": objeto,
            "publicada": (re.search(r"Pauta publicada.*?(\d{2}/\d{2}/\d{4})", b) or
                          [None, ""])[1] if re.search(r"Pauta publicada", b) else "",
        })
    return atualizada, itens


def construir():
    os.makedirs(PDF_DIR, exist_ok=True)
    url = achar_pdf_pauta()
    if not url:
        print("[pautas] nao encontrei o PDF da pauta na pagina.")
        return
    fonte = url.split("/")[-1]
    con = conectar()
    if con.execute("SELECT 1 FROM snapshots WHERE fonte=?", (fonte,)).fetchone():
        print(f"[pautas] pauta atual ({fonte}) ja coletada; nada novo.")
        con.close()
        return
    # baixar + extrair
    r = requests.get(url, headers=H, timeout=60)
    pdf = os.path.join(PDF_DIR, fonte)
    open(pdf, "wb").write(r.content)
    txt = pdf.rsplit(".", 1)[0] + ".txt"
    subprocess.run([achar_pdftotext(), "-enc", "UTF-8", "-layout", pdf, txt],
                   check=True, timeout=120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atualizada, itens = parse_pauta(open(txt, encoding="utf-8").read())
    hoje = dt.date.today().isoformat()
    # snapshot anterior (para detectar retirados)
    ant = con.execute("SELECT fonte FROM snapshots ORDER BY coletado_em DESC "
                      "LIMIT 1").fetchone()
    ant_fonte = ant[0] if ant else None
    novos_pn = {i["proc_norm"] for i in itens if i["proc_norm"]}
    for i in itens:
        con.execute("""INSERT OR IGNORE INTO pauta_processos(fonte,processo,proc_norm,
            data_sessao,data_sessao_iso,horario,relator,superintendencia,procurador,
            objeto,publicada,atualizada_em) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fonte, i["processo"], i["proc_norm"], i["data_sessao"],
             i["data_sessao_iso"], i["horario"], i["relator"], i["superintendencia"],
             i["procurador"], i["objeto"], i["publicada"], atualizada))
    con.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?)",
                (fonte, atualizada, len(itens), hoje))
    n_ret = 0
    if ant_fonte:
        antes = con.execute("SELECT processo,proc_norm,data_sessao,relator,objeto "
                            "FROM pauta_processos WHERE fonte=?", (ant_fonte,)).fetchall()
        for proc, pn, ds, rel, obj in antes:
            if pn and pn not in novos_pn:
                con.execute("""INSERT OR IGNORE INTO retirados(proc_norm,processo,
                    data_sessao,relator,objeto,visto_em,detectado_em)
                    VALUES(?,?,?,?,?,?,?)""",
                    (pn, proc, ds, rel, obj, ant_fonte, hoje))
                n_ret += 1
    con.commit()
    con.close()
    print(f"[pautas] snapshot {fonte} (atualizada {atualizada}): {len(itens)} "
          f"processos pautados | {n_ret} retirado(s) detectado(s) vs pauta anterior")


if __name__ == "__main__":
    construir()
