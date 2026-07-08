#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atas_colegiado_ficha.py — apoio a ficha (parametrizacao por IA) das Atas do
Colegiado da CVM em decisoes.db/atas_colegiado.

A "IA" e' o proprio Claude (agente): le o campo `texto` da ata e produz, por
ITEM da reuniao, como a AREA TECNICA se posicionou e como cada DIRETOR votou
(ou nao votou: impedido/ausente/divergente/vencido).

Colunas usadas em atas_colegiado: resumo TEXT, ficha TEXT (JSON), ai_feito INT.
Estrutura de `ficha` (JSON):
  {"resumo": "...", "itens": [
     {"processo":"...", "assunto":"...", "area_tecnica":"...",
      "decisao":"...", "votos":"..."}, ...]}

Uso:
  python atas_colegiado_ficha.py stats
  python atas_colegiado_ficha.py pendentes N [cutoff_iso]   # JSON p/ stdout
  python atas_colegiado_ficha.py aplicar_ia <arquivo.json>
"""
import os
import sys
import json
import sqlite3

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "decisoes.db")
CUTOFF = "2022-01-01"


def conectar():
    con = sqlite3.connect(DB_PATH)
    cols = [r[1] for r in con.execute("PRAGMA table_info(atas_colegiado)").fetchall()]
    if "resumo" not in cols:
        con.execute("ALTER TABLE atas_colegiado ADD COLUMN resumo TEXT")
    if "ficha" not in cols:
        con.execute("ALTER TABLE atas_colegiado ADD COLUMN ficha TEXT")
    if "ai_feito" not in cols:
        con.execute("ALTER TABLE atas_colegiado ADD COLUMN ai_feito INTEGER DEFAULT 0")
    con.commit()
    return con


def stats():
    con = conectar()
    base = con.execute("SELECT COUNT(*) FROM atas_colegiado WHERE data_iso>=? "
                       "AND texto IS NOT NULL AND texto<>''", (CUTOFF,)).fetchone()[0]
    feito = con.execute("SELECT COUNT(*) FROM atas_colegiado WHERE data_iso>=? "
                        "AND ai_feito=1", (CUTOFF,)).fetchone()[0]
    print(f"atas 2022+ com texto: {base} | com ficha: {feito} | pendentes: {base - feito}")
    con.close()


def pendentes(n, cutoff=CUTOFF):
    con = conectar()
    rows = con.execute(
        "SELECT link, titulo, data, data_iso, texto FROM atas_colegiado "
        "WHERE data_iso>=? AND texto IS NOT NULL AND texto<>'' "
        "AND (ai_feito IS NULL OR ai_feito=0) "
        "ORDER BY data_iso DESC LIMIT ?", (cutoff, int(n))).fetchall()
    out = [{"link": r[0], "titulo": r[1], "data": r[2], "data_iso": r[3],
            "texto": r[4]} for r in rows]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    con.close()


def aplicar_ia(caminho):
    con = conectar()
    dados = json.load(open(caminho, encoding="utf-8"))
    n = 0
    for link, ficha in dados.items():
        resumo = ficha.get("resumo", "") if isinstance(ficha, dict) else ""
        con.execute(
            "UPDATE atas_colegiado SET resumo=?, ficha=?, ai_feito=1 WHERE link=?",
            (resumo, json.dumps(ficha, ensure_ascii=False), link))
        n += con.total_changes and 1 or 0
    con.commit()
    print(f"[atas-ficha] ficha aplicada a {len(dados)} ata(s).")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "stats":
        stats()
    elif cmd == "pendentes":
        pendentes(sys.argv[2] if len(sys.argv) > 2 else 5,
                  sys.argv[3] if len(sys.argv) > 3 else CUTOFF)
    elif cmd == "aplicar_ia":
        aplicar_ia(sys.argv[2])
    else:
        print(__doc__)
