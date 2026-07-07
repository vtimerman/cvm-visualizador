#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
informativos_baixar.py — baixa todos os PDFs dos Informativos do Colegiado (CVM)
e extrai o texto com pdftotext.

Le informativos_urls.txt (linhas 'URL<TAB>titulo'), baixa em informativos_pdf/
(pulando os que ja existem) e gera informativos_txt/<base>.txt.

Uso:
  python informativos_baixar.py                 # baixa + extrai tudo
  python informativos_baixar.py listar_urls     # recoleta a lista de URLs do site
"""
import os
import re
import sys
import glob
import time
import shutil
import subprocess
import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
URLS = os.path.join(DIR, "informativos_urls.txt")
PDF_DIR = os.path.join(DIR, "informativos_pdf")
TXT_DIR = os.path.join(DIR, "informativos_txt")
BASE = "https://conteudo.cvm.gov.br"
PAGINA = BASE + "/publicacao/informativos_colegiado.html"
H = {"User-Agent": "Mozilla/5.0"}


def achar_pdftotext():
    """Localiza o pdftotext: env var -> PATH -> poppler do winget (Windows)."""
    p = os.environ.get("PDFTOTEXT")
    if p and os.path.exists(p):
        return p
    p = shutil.which("pdftotext")
    if p:
        return p
    padrao = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages",
        "oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe")
    hits = glob.glob(os.path.join(padrao, "**", "pdftotext.exe"), recursive=True)
    return hits[0] if hits else "pdftotext"


PDFTOTEXT = achar_pdftotext()


def _get_retry(url, params, tentativas=4):
    """GET resiliente ao site lento da CVM (retry com backoff)."""
    for i in range(tentativas):
        try:
            return requests.get(url, headers=H, params=params, timeout=90)
        except requests.exceptions.RequestException:
            if i == tentativas - 1:
                raise
            time.sleep(3 * (i + 1))


def listar_urls():
    """(re)coleta URL<TAB>titulo de todas as paginas da busca AJAX."""
    itens, seen = [], set()
    for page in range(1, 12):
        data = {"searchPage": str(page), "itensPagina": "50", "ordenar": "recentes",
                "lastName": "", "filtro": "", "dataInicio": "", "dataFim": "",
                "tipos": "", "buscado": "false"}
        r = _get_retry(PAGINA, data)
        r.encoding = "windows-1252"
        achou = 0
        for m in re.finditer(
                r'<h3>\s*<a href=["\']([^"\']+\.pdf)["\'][^>]*title=["\']([^"\']*)["\']',
                r.text, re.I):
            href, tit = m.group(1), m.group(2).strip()
            if href not in seen:
                seen.add(href)
                itens.append((href, tit))
                achou += 1
        if achou == 0:
            break
        time.sleep(0.3)
    with open(URLS, "w", encoding="utf-8") as f:
        for href, tit in itens:
            full = BASE + href if href.startswith("/") else href
            f.write(full + "\t" + tit + "\n")
    print(f"[informativos] {len(itens)} URLs salvas em informativos_urls.txt")
    return itens


def carregar_lista():
    itens = []
    for lin in open(URLS, encoding="utf-8"):
        lin = lin.rstrip("\n")
        if not lin:
            continue
        url, _, tit = lin.partition("\t")
        itens.append((url, tit))
    return itens


def baixar_um(url):
    base = os.path.splitext(os.path.basename(url))[0]
    dest = os.path.join(PDF_DIR, base + ".pdf")
    if os.path.exists(dest) and os.path.getsize(dest) > 1000:
        return base, "existe"
    try:
        r = requests.get(url, headers=H, timeout=60)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            with open(dest, "wb") as f:
                f.write(r.content)
            return base, "ok"
        return base, f"http {r.status_code}"
    except Exception as e:
        return base, f"erro {e}"


def extrair_txt(base):
    pdf = os.path.join(PDF_DIR, base + ".pdf")
    txt = os.path.join(TXT_DIR, base + ".txt")
    if os.path.exists(txt) and os.path.getsize(txt) > 50:
        return base, "txt existe"
    if not os.path.exists(pdf):
        return base, "sem pdf"
    try:
        subprocess.run([PDFTOTEXT, "-enc", "UTF-8", "-nopgbrk", pdf, txt],
                       check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return base, "txt ok"
    except Exception as e:
        return base, f"txt erro {e}"


def main():
    os.makedirs(PDF_DIR, exist_ok=True)
    os.makedirs(TXT_DIR, exist_ok=True)
    listar_urls()   # SEMPRE re-lista para descobrir informativos novos
    itens = carregar_lista()
    urls = [u for u, _ in itens]
    print(f"[informativos] {len(urls)} informativos na lista")

    # download paralelo
    novos = existentes = falhas = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for base, st in ex.map(baixar_um, urls):
            if st == "ok":
                novos += 1
            elif st == "existe":
                existentes += 1
            else:
                falhas += 1
                print(f"  ! {base}: {st}")
    print(f"[informativos] download: {novos} novos, {existentes} ja tinha, {falhas} falhas")

    # extracao de texto (serial; pdftotext e' rapido)
    bases = [os.path.splitext(os.path.basename(u))[0] for u in urls]
    ok = 0
    for b in bases:
        _, st = extrair_txt(b)
        if "ok" in st or "existe" in st:
            ok += 1
    npdf = len(glob.glob(os.path.join(PDF_DIR, "*.pdf")))
    ntxt = len(glob.glob(os.path.join(TXT_DIR, "*.txt")))
    print(f"[informativos] pdf: {npdf} | txt: {ntxt} | {dt.datetime.now():%H:%M:%S}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "listar_urls":
        listar_urls()
    else:
        main()
