#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atas_colegiado_texto.py — baixa o CONTEUDO (texto integral) das Atas do Colegiado
da CVM para a coluna `texto` de decisoes.db/atas_colegiado.

A base ja tem os metadados (link, titulo, data, tipo) coletados por
decisoes_baixar.py; aqui seguimos o `link` de cada ata e extraimos o corpo
(<article>) — participantes + todas as deliberacoes daquela reuniao.

Uso:
  python atas_colegiado_texto.py [cutoff_iso]   # padrao: 2022-01-01
  python atas_colegiado_texto.py stats
"""
import os
import re
import sys
import time
import html
import sqlite3
import datetime as dt

import requests

import json

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "decisoes.db")
SITE = "https://conteudo.cvm.gov.br"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
PAUSA = float(os.environ.get("PAUSA", "0.3"))
RE_ART = re.compile(r"<article.*?</article>", re.S)
RE_A = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.S | re.I)


def conectar():
    con = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in con.execute("PRAGMA table_info(atas_colegiado)").fetchall()]
    if "texto" not in cols:
        con.execute("ALTER TABLE atas_colegiado ADD COLUMN texto TEXT")
    if "anexos" not in cols:
        con.execute("ALTER TABLE atas_colegiado ADD COLUMN anexos TEXT")
    con.commit()
    return con


def extrair_texto(html_txt):
    m = RE_ART.search(html_txt)
    corpo = m.group(0) if m else html_txt
    corpo = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", corpo, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", corpo)
    txt = html.unescape(txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def extrair_anexos(html_txt):
    """Links dos anexos da ata (manifestacao da area tecnica e VOTOS dos
    diretores) — lista [{titulo, link}], deduplicada por URL."""
    m = RE_ART.search(html_txt)
    corpo = m.group(0) if m else html_txt
    out, vistos = [], set()
    for href, texto in RE_A.findall(corpo):
        titulo = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", texto))).strip()
        if not titulo or href.lower().startswith("javascript"):
            continue
        # so anexos de decisao (pdf/doc em .../anexos/...)
        if "/anexos/" not in href.lower() and not re.search(r"\.(pdf|docx?|rtf)$", href, re.I):
            continue
        link = href if href.startswith("http") else SITE + href
        if link in vistos:
            continue
        vistos.add(link)
        out.append({"titulo": titulo, "link": link})
    return out


def baixar(cutoff="2022-01-01"):
    con = conectar()
    pend = con.execute(
        "SELECT link, data_iso FROM atas_colegiado "
        "WHERE data_iso >= ? AND (texto IS NULL OR texto='' OR anexos IS NULL) "
        "ORDER BY data_iso DESC", (cutoff,)).fetchall()
    print(f"[atas-texto] {len(pend)} ata(s) a partir de {cutoff} a coletar "
          "(texto e/ou anexos).")
    ok = falhas = 0
    for i, (link, di) in enumerate(pend, 1):
        if not link:
            continue
        try:
            r = requests.get(link, headers={"User-Agent": UA}, timeout=60)
            r.encoding = "utf-8"
            txt = extrair_texto(r.text)
            anexos = json.dumps(extrair_anexos(r.text), ensure_ascii=False)
            if len(txt) >= 120:            # corpo minimamente valido
                con.execute("UPDATE atas_colegiado SET texto=?, anexos=? WHERE link=?",
                            (txt, anexos, link))
                con.commit()
                ok += 1
            else:
                con.execute("UPDATE atas_colegiado SET anexos=? WHERE link=?",
                            (anexos, link))
                con.commit()
                falhas += 1
                print(f"  ! corpo curto ({len(txt)}) em {link}", file=sys.stderr)
        except requests.RequestException as e:
            falhas += 1
            print(f"  ! erro de rede em {link}: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  ... {i}/{len(pend)} ({ok} ok, {falhas} falhas)")
        time.sleep(PAUSA)
    con.close()
    print(f"[atas-texto] concluido: {ok} com texto+anexos, {falhas} falha(s).")


def stats():
    con = conectar()
    tot = con.execute("SELECT COUNT(*) FROM atas_colegiado").fetchone()[0]
    comtxt = con.execute("SELECT COUNT(*) FROM atas_colegiado "
                         "WHERE texto IS NOT NULL AND texto<>''").fetchone()[0]
    d22 = con.execute("SELECT COUNT(*) FROM atas_colegiado WHERE data_iso>='2022-01-01' "
                      "AND texto IS NOT NULL AND texto<>''").fetchone()[0]
    t22 = con.execute("SELECT COUNT(*) FROM atas_colegiado "
                      "WHERE data_iso>='2022-01-01'").fetchone()[0]
    print(f"atas total: {tot} | com texto: {comtxt} | 2022+: {d22}/{t22}")
    con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        stats()
    else:
        cut = sys.argv[1] if len(sys.argv) > 1 else "2022-01-01"
        baixar(cut)
