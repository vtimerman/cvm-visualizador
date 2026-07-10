#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boletim_pessoal_baixar.py — coleta e indexa o Boletim de Pessoal da CVM
(movimentacoes da forca de trabalho: nomeacoes, exoneracoes, designacoes,
remocoes, aposentadorias, cessoes, substituicoes, portarias de estrutura).

Fonte (indice paginado, GET):
  https://conteudo.cvm.gov.br/publicacao/boletim_pessoal.html
    ?searchPage=N&itensPagina=100&ordenar=recentes
Cada resultado aponta para um PDF em
  /export/sites/cvm/publicacao/boletim_pessoal/anexos/AAAA/NUM_de_DD_de_MM_de_AAAA[_assinado].pdf
O texto de cada boletim e extraido com o pdftotext do poppler.

Uso:
  python boletim_pessoal_baixar.py backfill [limite_pdfs]  # indice + baixa todos
  python boletim_pessoal_baixar.py indice                  # so (re)indexa metadados
  python boletim_pessoal_baixar.py pdfs [limite]           # baixa textos faltantes
  python boletim_pessoal_baixar.py atualizar               # pagina 1 (recentes) + novos
  python boletim_pessoal_baixar.py stats
"""
import os
import re
import sys
import glob
import time
import shutil
import sqlite3
import tempfile
import datetime as dt
import subprocess

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "pessoal.db")
HOST = "https://conteudo.cvm.gov.br"
BASE = HOST + "/publicacao/boletim_pessoal.html"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PAUSA = float(os.environ.get("PAUSA", "0.3"))

RE_PAR = re.compile(r'href="([^"]*anexos/\d+/[^"]+\.pdf)"[^>]*title="([^"]*)"')
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
         "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
         "outubro": 10, "novembro": 11, "dezembro": 12}


def _poppler_bin(exe):
    # Linux/CI: binario no PATH ('pdftotext'); Windows: PATH ou poppler do winget.
    base = exe[:-4] if exe.lower().endswith(".exe") else exe
    hit = shutil.which(base) or shutil.which(exe)
    if hit:
        return hit
    glb = glob.glob(os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
        "oschwartz10612.Poppler*", "poppler-*", "Library", "bin", exe))
    return glb[0] if glb else base


PDFTOTEXT = _poppler_bin("pdftotext.exe")


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS boletins(
        pdf_url TEXT PRIMARY KEY, numero TEXT, num_int INTEGER, sufixo TEXT,
        titulo TEXT, data TEXT, data_iso TEXT, ano TEXT,
        texto TEXT, coletado_em TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_bol_data ON boletins(data_iso)")
    con.commit()
    return con


def _norm_num(bruto):
    """'1.078-C' / '1078C' -> (num_int, sufixo, 'numero-legivel')."""
    s = re.sub(r"\.", "", str(bruto or "")).strip()
    m = re.match(r"(\d+)\s*-?\s*([A-Za-z])?", s)
    if not m:
        return None, "", s
    ni = int(m.group(1))
    suf = (m.group(2) or "").upper()
    return ni, suf, f"{ni}" + (f"-{suf}" if suf else "")


def _data_de(titulo, pdf_url):
    """Data (dd/mm/aaaa, iso) a partir do titulo; fallback no nome do arquivo."""
    fontes = [titulo or "", os.path.basename(pdf_url or "")]
    for src in fontes:
        s = src.replace("_", " ")
        # '06 de julho de 2026' ou '1º de junho de 2026'
        m = re.search(r"(\d{1,2})\s*(?:º|o)?\s*de\s+([a-zA-Zçã]+)\s+de\s+(\d{4})",
                      s, re.I)
        if m:
            mes = MESES.get(m.group(2).lower())
            if mes:
                d, a = int(m.group(1)), int(m.group(3))
                try:
                    iso = dt.date(a, mes, d).isoformat()
                    return f"{d:02d}/{mes:02d}/{a}", iso
                except ValueError:
                    pass
        # numerico: '09 de 07 de 2026' / '16de 04de 2018'
        m = re.search(r"(\d{1,2})\s*de\s*(\d{1,2})\s*de\s*(\d{4})", s, re.I)
        if m:
            d, mes, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                iso = dt.date(a, mes, d).isoformat()
                return f"{d:02d}/{mes:02d}/{a}", iso
            except ValueError:
                pass
    return "", ""


def _num_do_arquivo(url):
    """Numero/sufixo a partir do nome do PDF (fonte mais confiavel).

    'boletim822.pdf'->822 ; '1049A_de_...'->1049-A ; 'boletim819-A.pdf'->819-A ;
    '1050_de_06...'->1050 (o 'de' nao vira sufixo por exigir [A-Z] colado)."""
    base = os.path.basename(url or "")
    m = re.match(r"(?:boletim)?_?(\d+)(?:-?([A-Z]))?", base)
    if not m:
        return None, "", ""
    ni = int(m.group(1))
    suf = m.group(2) or ""
    return ni, suf, f"{ni}" + (f"-{suf}" if suf else "")


def _extrai(html):
    out = []
    for href, titulo in RE_PAR.findall(html):
        url = href if href.startswith("http") else HOST + href
        num_int, sufixo, numero = _num_do_arquivo(url)
        if num_int is None:   # fallback no titulo
            mnum = re.search(r"n[ºo\.]?\s*([\d\.]+\s*-?\s*[A-Za-z]?)", titulo)
            num_int, sufixo, numero = _norm_num(mnum.group(1) if mnum else "")
        data, iso = _data_de(titulo, url)
        out.append({"pdf_url": url, "numero": numero, "num_int": num_int,
                    "sufixo": sufixo, "titulo": re.sub(r"\s+", " ", titulo).strip(),
                    "data": data, "data_iso": iso, "ano": iso[:4] if iso else ""})
    return out


def baixar_indice(con, so_pagina1=False):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    hoje = dt.date.today().isoformat()
    novos = pg = 0
    vistos = set()
    while True:
        pg += 1
        u = f"{BASE}?searchPage={pg}&itensPagina=100&ordenar=recentes"
        try:
            html = s.get(u, timeout=60).text
        except requests.RequestException as e:
            print(f"  ! erro rede (pg {pg}): {e}", file=sys.stderr)
            break
        itens = _extrai(html)
        urls = {x["pdf_url"] for x in itens}
        if not itens or urls <= vistos:
            break
        vistos |= urls
        for x in itens:
            cur = con.execute("SELECT 1 FROM boletins WHERE pdf_url=?",
                              (x["pdf_url"],)).fetchone()
            if cur is None:
                novos += 1
            con.execute(
                "INSERT INTO boletins(pdf_url,numero,num_int,sufixo,titulo,data,"
                "data_iso,ano,coletado_em) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(pdf_url) DO UPDATE SET numero=excluded.numero,"
                "num_int=excluded.num_int,sufixo=excluded.sufixo,"
                "titulo=excluded.titulo,data=excluded.data,"
                "data_iso=excluded.data_iso,ano=excluded.ano",
                (x["pdf_url"], x["numero"], x["num_int"], x["sufixo"], x["titulo"],
                 x["data"], x["data_iso"], x["ano"], hoje))
        con.commit()
        print(f"[bol] indice pg {pg}: {len(itens)} itens ({novos} novos ate agora)")
        if so_pagina1:
            break
        time.sleep(PAUSA)
    return novos


def _pdf_para_texto(pdf_bytes):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp = f.name
    txt = tmp[:-4] + ".txt"
    try:
        subprocess.run([PDFTOTEXT, "-layout", "-enc", "UTF-8", tmp, txt],
                       check=True, capture_output=True, timeout=120)
        with open(txt, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except Exception as e:
        print(f"  ! pdftotext falhou: {e}", file=sys.stderr)
        return ""
    finally:
        for p in (tmp, txt):
            try:
                os.remove(p)
            except OSError:
                pass


def baixar_pdfs(con, limite=None):
    s = requests.Session()
    s.headers["User-Agent"] = UA
    hoje = dt.date.today().isoformat()
    q = ("SELECT pdf_url,numero FROM boletins WHERE texto IS NULL OR texto='' "
         "ORDER BY data_iso DESC")
    linhas = con.execute(q).fetchall()
    if limite:
        linhas = linhas[:int(limite)]
    feito = 0
    for url, numero in linhas:
        try:
            resp = s.get(url, timeout=120)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  ! download falhou ({numero}): {e}", file=sys.stderr)
            continue
        texto = _pdf_para_texto(resp.content)
        con.execute("UPDATE boletins SET texto=?, coletado_em=? WHERE pdf_url=?",
                    (texto, hoje, url))
        con.commit()
        feito += 1
        if feito % 20 == 0:
            print(f"[bol] pdfs: {feito}/{len(linhas)} (ultimo nº {numero})")
        time.sleep(PAUSA)
    print(f"[bol] pdfs concluido: {feito} baixados")
    return feito


def backfill(limite=None):
    con = conectar()
    n = baixar_indice(con)
    print(f"[bol] indice: {n} novos boletins")
    baixar_pdfs(con, limite)
    stats(con)
    con.close()


def atualizar():
    con = conectar()
    baixar_indice(con, so_pagina1=True)
    baixar_pdfs(con)
    stats(con)
    con.close()


def stats(con=None):
    own = con is None
    con = con or conectar()
    tot = con.execute("SELECT COUNT(*) FROM boletins").fetchone()[0]
    ctx = con.execute("SELECT COUNT(*) FROM boletins WHERE texto IS NOT NULL "
                      "AND texto<>''").fetchone()[0]
    faixa = con.execute("SELECT MIN(data_iso),MAX(data_iso) FROM boletins "
                        "WHERE data_iso<>''").fetchone()
    print(f"boletins: {tot} | com texto: {ctx} | periodo: {faixa[0]} a {faixa[1]}")
    if own:
        con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "backfill":
        backfill(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "indice":
        c = conectar(); print(baixar_indice(c), "novos"); c.close()
    elif cmd == "pdfs":
        c = conectar(); baixar_pdfs(c, sys.argv[2] if len(sys.argv) > 2 else None); c.close()
    elif cmd == "atualizar":
        atualizar()
    else:
        stats()
