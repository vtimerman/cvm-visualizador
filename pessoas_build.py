#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pessoas_build.py — REGISTRO CANONICO DE PESSOAS (servidores + diretores).

Resolve a mesma pessoa que aparece com varias grafias entre as bases e cria um
nome canonico unico, usado em todas as buscas/direcionamentos do app.

Fontes: pessoal.db (movimentos/viagens: servidor_nome/servidor_key),
quem.db (Colegiado/dirigentes ATUAIS, autoritativo — campo e_relator),
transparencia.db (viagens_gov: cpf + beneficiario).

Ancoras: NAO usa matricula (poluida — 20% cobertura, valores repetidos entre
pessoas). Clusteriza por nome normalizado + FUSAO FUZZY das variantes quase
identicas (typo/acento/ordem) dentro do mesmo grupo (1o,ultimo nome). CPF
ancora a Transparencia.

Grava em pessoal.db: pessoas(person_id, nome_canonico, e_diretor_atual, cargo,
sigla, cpf, aliases_json) + pessoa_alias(alias_key PK, person_id).

Uso:  python pessoas_build.py            # (re)constroi o registro
      python pessoas_build.py stats
"""
import os
import re
import sys
import json
import sqlite3
import unicodedata
import datetime as dt
from difflib import SequenceMatcher

DIR = os.path.dirname(os.path.abspath(__file__))
PESSOAL = os.path.join(DIR, "pessoal.db")
QUEM = os.path.join(DIR, "quem.db")
TRANSP = os.path.join(DIR, "transparencia.db")
CONDUTA = os.path.join(DIR, "conduta.db")
SIM = 0.90  # limiar de fusao fuzzy


def ekey(n):
    """Normaliza nome -> chave (UPPER, sem acento, espacos colapsados)."""
    s = unicodedata.normalize("NFKD", str(n or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z ]", " ", s).upper()
    # nota: colapso de espacos abaixo


def _norm(n):
    return re.sub(r"\s+", " ", ekey(n)).strip()


def firstlast(k):
    t = [x for x in _norm(k).split() if len(x) > 2]
    if not t:
        return ("", "")
    return (t[0], t[-1])


def sim(a, b):
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _subseq(menor, maior):
    """tokens de `menor` sao subsequencia (mesma ordem) de `maior`?"""
    it = iter(maior)
    return all(tok in it for tok in menor)


def mesma_pessoa(a, b):
    """Funde por typo/acento (fuzzy alto) OU nome curto = subconjunto ordenado
    do longo com sobrenome acrescido (ambos com >=3 tokens)."""
    if sim(a, b) >= SIM:
        return True
    ta = [t for t in _norm(a).split() if len(t) > 1]
    tb = [t for t in _norm(b).split() if len(t) > 1]
    curto, longo = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(curto) >= 3 and _subseq(curto, longo):
        return True
    return False


class UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def _melhor_nome(nomes):
    """Escolhe a grafia mais completa (mais tokens, depois mais chars, com acento)."""
    def score(n):
        s = str(n)
        toks = _norm(n).split()
        # nome com conjuncao "E"/"&" isolada = provavel concatenacao de 2 pessoas
        conj = any(t in ("E", "&") for t in toks)
        return (0 if conj else 1,
                len([t for t in toks if len(t) > 1]),
                len(s), sum(1 for c in s if ord(c) > 127))
    return max(nomes, key=score)


def construir():
    con = sqlite3.connect(PESSOAL)
    # 1) unidades = servidor_key distintos + suas grafias e chaves-DB originais
    grafias = {}   # key_norm -> set(servidor_nome originais, p/ escolher canonico)
    origkeys = {}  # key_norm -> set(servidor_key ORIGINAIS do banco, p/ filtrar)
    for tbl in ("movimentos", "viagens"):
        for nome, key in con.execute(
                f"SELECT servidor_nome, servidor_key FROM {tbl}"):
            kn = _norm(key or nome)
            if not kn:
                continue
            grafias.setdefault(kn, set()).add(str(nome or key))
            if key:
                origkeys.setdefault(kn, set()).add(str(key))
    unidades = list(grafias)

    # 2) cluster: blocos por (1o,ultimo) E (1o,2o token) — cobre typo no
    #    sobrenome E sobrenome acrescido (que muda o ultimo token) — + fusao.
    def blocos(kn):
        t = [x for x in _norm(kn).split() if len(x) > 2]
        b = set()
        if t:
            b.add((t[0], t[-1]))
            if len(t) > 1:
                b.add((t[0], t[1]))
        return b

    porgrupo = {}
    for kn in unidades:
        for bk in blocos(kn):
            porgrupo.setdefault(bk, []).append(kn)
    uf = UF()
    for kn in unidades:
        uf.find(kn)
    for bk, membros in porgrupo.items():
        for i in range(len(membros)):
            for j in range(i + 1, len(membros)):
                if mesma_pessoa(membros[i], membros[j]):
                    uf.union(membros[i], membros[j])

    # 3) pessoas a partir dos clusters
    clusters = {}
    for kn in unidades:
        clusters.setdefault(uf.find(kn), []).append(kn)
    pessoas = []          # (person_id, nome_canonico, aliases[list of key_norm])
    alias2pid = {}
    for pid, (root, keys) in enumerate(sorted(clusters.items()), start=1):
        todas = set()
        oks = set()
        for k in keys:
            todas |= grafias[k]
            oks |= origkeys.get(k, set())
        nome_can = _melhor_nome(todas)
        # aliases = chaves-DB originais (p/ WHERE servidor_key IN) + norms
        pessoas.append({"person_id": pid, "nome_canonico": nome_can,
                        "aliases": sorted(oks | set(keys)), "e_diretor_atual": 0,
                        "cargo": "", "sigla": "", "cpf": ""})
        for k in keys:            # chave normalizada
            alias2pid[k] = pid
        for ok in oks:            # chave-DB original (servidor_key)
            alias2pid[ok] = pid

    # 4) semear quem.db (autoritativo p/ dirigentes atuais)
    qadd = 0
    if os.path.exists(QUEM):
        q = sqlite3.connect(QUEM)
        try:
            rows = q.execute("SELECT nome, cargo, sigla, e_relator FROM quem").fetchall()
        except Exception:
            rows = []
        q.close()
        for nome, cargo, sigla, erel in rows:
            kn = _norm(nome)
            if not kn or "em breve" in str(nome).lower():
                continue
            # acha pessoa por chave exata, senao por (1o,ult)+fuzzy
            pid = alias2pid.get(kn)
            if pid is None:
                fl = firstlast(kn)
                cand = [p for p in pessoas if firstlast(p["nome_canonico"]) == fl
                        and sim(p["nome_canonico"], nome) >= 0.80]
                if cand:
                    pid = cand[0]["person_id"]
            if pid is None:                       # cria nova pessoa
                pid = len(pessoas) + 1
                pessoas.append({"person_id": pid, "nome_canonico": nome,
                                "aliases": [kn], "e_diretor_atual": 0,
                                "cargo": "", "sigla": "", "cpf": ""})
                qadd += 1
            p = next(x for x in pessoas if x["person_id"] == pid)
            # quem.db e autoritativo: nome/cargo/sigla e flag de diretor atual
            p["nome_canonico"] = str(nome)
            p["cargo"] = str(cargo or "")
            p["sigla"] = str(sigla or "")
            p["e_diretor_atual"] = int(erel or 0)
            if kn not in p["aliases"]:
                p["aliases"].append(kn)
            alias2pid[kn] = pid

    # 4b) semear nomes CURTOS de diretor do conduta.db (eventos.diretor) como
    #     alias da pessoa canonica — linka a ficha/aderencia ao registro.
    if os.path.exists(CONDUTA):
        cc = sqlite3.connect(CONDUTA)
        try:
            curtos = [r[0] for r in cc.execute(
                "SELECT DISTINCT diretor FROM eventos WHERE diretor NOT LIKE "
                "'%Colegiado%' AND diretor<>''")]
        except Exception:
            curtos = []
        cc.close()
        for nome in curtos:
            kn = _norm(nome)
            if not kn or kn in alias2pid:
                continue
            toks = [t for t in kn.split() if len(t) > 2]
            if not toks:
                continue
            # casa: mesmo 1o nome + nome curto e' subsequencia ordenada do canonico
            def ptoks(p):
                return [t for t in _norm(p["nome_canonico"]).split() if len(t) > 2]
            cand = [p for p in pessoas
                    if ptoks(p) and ptoks(p)[0] == toks[0] and _subseq(toks, ptoks(p))]
            cand.sort(key=lambda p: (p["e_diretor_atual"],
                                     len(_norm(p["nome_canonico"]))), reverse=True)
            if cand:
                pid = cand[0]["person_id"]
                if kn not in cand[0]["aliases"]:
                    cand[0]["aliases"].append(kn)
                alias2pid[kn] = pid
            else:  # ex-diretor sem correspondencia no Boletim: cria pessoa
                pid = len(pessoas) + 1
                pessoas.append({"person_id": pid, "nome_canonico": str(nome),
                                "aliases": [kn], "e_diretor_atual": 0,
                                "cargo": "", "sigla": "", "cpf": ""})
                alias2pid[kn] = pid

    # 5) CPF da Transparencia -> pessoa (por nome), grava cpf
    cpflink = 0
    if os.path.exists(TRANSP):
        t = sqlite3.connect(TRANSP)
        try:
            cpfnome = {}
            for benef, cpf in t.execute(
                    "SELECT beneficiario, cpf FROM viagens_gov WHERE cpf<>''"):
                cpfnome.setdefault(str(cpf), set()).add(str(benef))
        except Exception:
            cpfnome = {}
        t.close()
        for cpf, nomes in cpfnome.items():
            nm = _melhor_nome(nomes)
            kn = _norm(nm)
            pid = alias2pid.get(kn)
            if pid is None:
                fl = firstlast(kn)
                cand = [p for p in pessoas if firstlast(p["nome_canonico"]) == fl
                        and sim(p["nome_canonico"], nm) >= 0.85]
                pid = cand[0]["person_id"] if cand else None
            if pid is not None:
                p = next(x for x in pessoas if x["person_id"] == pid)
                if not p["cpf"]:
                    p["cpf"] = cpf
                    cpflink += 1
                for k in (kn,):
                    alias2pid.setdefault(k, pid)

    # 6) grava
    con.execute("DROP TABLE IF EXISTS pessoas")
    con.execute("DROP TABLE IF EXISTS pessoa_alias")
    con.execute("""CREATE TABLE pessoas(person_id INTEGER PRIMARY KEY,
        nome_canonico TEXT, e_diretor_atual INTEGER DEFAULT 0, cargo TEXT,
        sigla TEXT, cpf TEXT, aliases_json TEXT, atualizado_em TEXT)""")
    con.execute("""CREATE TABLE pessoa_alias(alias_key TEXT PRIMARY KEY,
        person_id INTEGER)""")
    hoje = dt.date.today().isoformat()
    for p in pessoas:
        con.execute("INSERT INTO pessoas VALUES(?,?,?,?,?,?,?,?)",
                    (p["person_id"], p["nome_canonico"], p["e_diretor_atual"],
                     p["cargo"], p["sigla"], p["cpf"],
                     json.dumps(p["aliases"], ensure_ascii=False), hoje))
    for k, pid in alias2pid.items():
        con.execute("INSERT OR REPLACE INTO pessoa_alias VALUES(?,?)", (k, pid))
    con.commit()
    con.close()
    fundidos = len(unidades) - len(clusters)
    print(f"[pessoas] {len(pessoas)} pessoas | {len(unidades)} grafias "
          f"-> {fundidos} fundidas | quem.db novas {qadd} | cpf ligados {cpflink}")
    print(f"[pessoas] diretores atuais (quem.db e_relator=1): " +
          ", ".join(p["nome_canonico"] for p in pessoas if p["e_diretor_atual"]))


def stats():
    con = sqlite3.connect(PESSOAL)
    try:
        n = con.execute("SELECT COUNT(*) FROM pessoas").fetchone()[0]
        na = con.execute("SELECT COUNT(*) FROM pessoa_alias").fetchone()[0]
        nd = con.execute("SELECT COUNT(*) FROM pessoas WHERE "
                         "e_diretor_atual=1").fetchone()[0]
        ncpf = con.execute("SELECT COUNT(*) FROM pessoas WHERE cpf<>''").fetchone()[0]
        print(f"pessoas={n} aliases={na} diretores_atuais={nd} com_cpf={ncpf}")
        print("multi-grafia (top):")
        for pid, nome, al in con.execute(
                "SELECT person_id,nome_canonico,aliases_json FROM pessoas"):
            a = json.loads(al)
            if len(a) > 1:
                print(f"  {nome}: {a}")
    except sqlite3.OperationalError:
        print("registro ainda nao construido — rode: python pessoas_build.py")
    con.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        stats()
    else:
        construir()
