#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""juris_divergencias.py — minera as DIVERGENCIAS (votos vencidos) de TODA a
jurisprudencia (juris.db doc_analises: campos resultado+votos) para a
conduta.db, tabela alinhamentos, com fonte='juris'. Ancora os nomes na lista
de diretores conhecidos (evita capturar lixo do texto livre).

juris.db e LOCAL/gitignored -> rodar LOCAL e commitar a conduta.db. O
conduta_build.py preserva as linhas fonte='juris' ao reconstruir (as dele
recebem fonte='atas').

Uso: python juris_divergencias.py
"""
import os
import re
import json
import sqlite3
import unicodedata

DIR = os.path.dirname(os.path.abspath(__file__))
JURIS = os.path.join(DIR, "juris.db")
CONDUTA = os.path.join(DIR, "conduta.db")

# sobrenome-chave (sem acento) -> rotulo canonico do diretor
SOBRE = {
    "lobo": "Otto Lobo", "accioly": "Joao Accioly", "copola": "Marina Copola",
    "bernardo": "Daniel Maeda", "maeda": "Daniel Maeda",
    "perlingeiro": "Flavia Perlingeiro", "nascimento": "Joao Pedro Nascimento",
    "muniz": "Igor Muniz", "rangel": "Alexandre Rangel",
    "gonzalez": "Gustavo Gonzalez", "renteria": "Pablo Renteria",
    "yazbek": "Otavio Yazbek", "borba": "Gustavo Borba",
    "machado": "Henrique Machado", "novaes": "Ana Novaes",
    "trindade": "Marcelo Trindade", "santana": "Maria Helena Santana",
    "pereira": "Leonardo Pereira", "dias": "Luciana Dias",
    "tavares": "Roberto Tadeu", "kanczuk": "Henrique Kanczuk",
}
# 'barbosa' e ambiguo (Marcelo Barbosa) -> so quando 'marcelo' perto; tratado a parte


def _na(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _dec(v):
    v = str(v or "")
    try:
        x = json.loads(v)
        if isinstance(x, str):
            return x
        if isinstance(x, dict):
            return " ".join(str(y) for y in x.values())
        return str(x)
    except (ValueError, TypeError):
        return v


def _vencidos(blob):
    out = set()
    for sob, lab in SOBRE.items():
        if re.search(r"vencid[oa]s?\b[^.]{0,45}?\b" + re.escape(sob) + r"\b", blob):
            out.add(lab)
        elif re.search(r"\b" + re.escape(sob) + r"\b[^.]{0,25}?(?:ficou|restou|"
                       r"restaram|foi|foram)\s+vencid", blob):
            out.add(lab)
    if re.search(r"marcelo[^.]{0,20}barbosa|barbosa[^.]{0,20}marcelo", blob) and \
            re.search(r"vencid[oa]s?\b[^.]{0,45}?barbosa", blob):
        out.add("Marcelo Barbosa")
    return out


def _relator_lab(rel):
    r = _na(rel)
    for sob, lab in SOBRE.items():
        if re.search(r"\b" + re.escape(sob) + r"\b", r):
            return lab
    return ""


def _carregar_diretores():
    """Amplia SOBRE com TODOS os diretores/presidentes estatutarios do Boletim
    (movimentos), preservando os apelidos ja definidos."""
    pe = os.path.join(DIR, "pessoal.db")
    if not os.path.exists(pe):
        return
    con = sqlite3.connect(pe)
    try:
        for (nome,) in con.execute(
                "SELECT DISTINCT servidor_nome FROM movimentos WHERE "
                "funcao LIKE '%Diretor%' OR funcao LIKE '%Presidente%'"):
            toks = [t for t in _na(nome).split() if len(t) > 2]
            partes = nome.split()
            if len(toks) >= 2 and toks[-1] not in SOBRE and len(toks[-1]) > 3:
                SOBRE[toks[-1]] = f"{partes[0].capitalize()} {partes[-1].capitalize()}"
    except Exception:
        pass
    con.close()


def minerar():
    if not os.path.exists(JURIS) or not os.path.exists(CONDUTA):
        print("[div] juris.db ou conduta.db ausente.")
        return
    _carregar_diretores()
    j = sqlite3.connect(JURIS)
    rows = j.execute("SELECT proc_norm, data_julg, relator, resultado, votos "
                     "FROM doc_analises WHERE ai_feito=1").fetchall()
    j.close()
    c = sqlite3.connect(CONDUTA)
    cols = [x[1] for x in c.execute("PRAGMA table_info(alinhamentos)")]
    if "fonte" not in cols:
        c.execute("ALTER TABLE alinhamentos ADD COLUMN fonte TEXT")
    c.execute("UPDATE alinhamentos SET fonte='atas' WHERE fonte IS NULL")
    c.execute("DELETE FROM alinhamentos WHERE fonte='juris'")
    n = 0
    for pn, dj, rel, resu, votos in rows:
        blob = _na(_dec(resu) + " " + _dec(votos))
        if "vencid" not in blob:
            continue
        venc = _vencidos(blob)
        if not venc:
            continue
        rlab = _relator_lab(rel)
        data_iso = ""
        m = re.search(r"(\d{2})/(\d{2})/(\d{4})", str(dj or ""))
        if m:
            data_iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        trecho = (f"RELATOR: {_dec(rel)}\nDECISAO: {_dec(resu)[:500]}\n"
                  f"VOTOS: {_dec(votos)[:900]}")[:1600]
        for v in venc:
            db = rlab if (rlab and rlab != v) else "(maioria do Colegiado)"
            c.execute("INSERT INTO alinhamentos(diretor_a,tipo,diretor_b,processo,"
                      "data_iso,trecho,fonte) VALUES(?,?,?,?,?,?, 'juris')",
                      (v, "divergiu_de", db, pn, data_iso, trecho))
            n += 1
    c.commit()
    tot = c.execute("SELECT COUNT(*) FROM alinhamentos").fetchone()[0]
    jn = c.execute("SELECT COUNT(*) FROM alinhamentos WHERE fonte='juris'"
                   ).fetchone()[0]
    c.close()
    print(f"[div] {n} divergencias mineradas da jurisprudencia (fonte=juris). "
          f"alinhamentos total: {tot} (juris={jn}).")


if __name__ == "__main__":
    minerar()
