#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quem_baixar.py — coleta o "Quem é Quem" da CVM (organograma com pessoas):
nome, cargo, sigla da unidade, e-mail, telefone e link do perfil.

Fonte: https://www.gov.br/cvm/pt-br/acesso-a-informacao-cvm/institucional/quem-e-quem
Cada pessoa vem em um card:
  <p class="nome"><a href="perfil">NOME</a></p>
  <p class="cargo">CARGO - SIGLA</p>
  <p class="telefone">...</p>  <p class="email">...sigla@cvm.gov.br</p>
O prefixo do e-mail e' a sigla da unidade (pte, dja, dmc, ...).

Uso:
  python quem_baixar.py
"""
import os
import re
import sqlite3
import datetime as dt

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "quem.db")
URL = ("https://www.gov.br/cvm/pt-br/acesso-a-informacao-cvm/institucional/"
       "quem-e-quem")
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

CARD = re.compile(
    r'<p class="nome"><a[^>]*href="([^"]*)"[^>]*>\s*([^<]+?)\s*</a></p>'
    r'<p class="cargo">([^<]+)</p>(.*?)(?=<p class="nome">|</body|$)', re.S)


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS quem(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT, cargo TEXT, sigla TEXT, email TEXT, telefone TEXT,
        perfil TEXT, e_relator INTEGER DEFAULT 0, coletado_em TEXT)""")
    con.commit()
    return con


def _limpo(s):
    return re.sub(r"\s+", " ", (s or "").replace("\xa0", " ")).strip()


def parse(html):
    t = re.sub(r">\s+<", "><", html)
    out = []
    for perfil, nome, cargo, resto in CARD.findall(t):
        nome = _limpo(nome)
        cargo = _limpo(cargo)
        me = re.search(r"E-mail</span><span>[^<]*</span><span>\s*([^<\s]+@[^<\s]+)",
                       resto)
        email = _limpo(me.group(1)) if me else ""
        mt = re.search(r"Telefone[^<]*</span><span>[^<]*</span><span>([^<]+)</span>",
                       resto)
        telefone = _limpo(mt.group(1)) if mt else ""
        # sigla: prefixo do e-mail (pte@cvm...), senao '- SIGLA' no cargo
        sigla = ""
        if email:
            sigla = email.split("@")[0].upper()
        if not sigla or len(sigla) > 8:
            ms = re.search(r"[–-]\s*([A-Z]{2,6})\s*$", cargo)
            sigla = ms.group(1) if ms else sigla
        if perfil.startswith("/"):
            perfil = "https://www.gov.br" + perfil
        e_relator = 1 if re.match(r"(Presidente|Diretor)", cargo) else 0
        if nome:
            out.append((nome, cargo, sigla, email, telefone, perfil, e_relator))
    return out


def construir():
    r = requests.get(URL, headers=H, timeout=60)
    r.encoding = "utf-8"
    pessoas = parse(r.text)
    con = conectar()
    hoje = dt.date.today().isoformat()
    con.execute("DELETE FROM quem")  # snapshot completo
    for nome, cargo, sigla, email, tel, perfil, erel in pessoas:
        con.execute("""INSERT INTO quem(nome,cargo,sigla,email,telefone,perfil,
            e_relator,coletado_em) VALUES(?,?,?,?,?,?,?,?)""",
            (nome, cargo, sigla, email, tel, perfil, erel, hoje))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM quem").fetchone()[0]
    nrel = con.execute("SELECT COUNT(*) FROM quem WHERE e_relator=1").fetchone()[0]
    con.close()
    print(f"[quem] {n} pessoas | {nrel} no Colegiado (relatores em potencial)")
    print("[quem] Colegiado:")
    con = sqlite3.connect(DB_PATH)
    for nm, cg, sg in con.execute("SELECT nome,cargo,sigla FROM quem WHERE e_relator=1"):
        print(f"   {sg:5} {nm} — {cg}")
    con.close()


if __name__ == "__main__":
    construir()
