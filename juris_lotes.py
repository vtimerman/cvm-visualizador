#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
juris_lotes.py — exporta os julgados legiveis ainda NAO analisados em lotes
condensados para o workflow da Fase B (analise IA por julgado).

Condensacao: mantem cabecalho+extrato (inicio) e voto/dispositivo (fim), que
concentram area tecnica acusadora e as teses; corta o miolo do relatorio nos
documentos muito grandes, para caber no contexto do agente sem perder o essencial.
"""
import os
import sys
import json
import sqlite3

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "juris.db")
OUT = os.path.join(
    os.environ.get("SCRATCH", os.path.join(DIR, "_juris_batches")))
CAP_HEAD = 12000
CAP_TAIL = 20000


def condensa(t):
    t = t or ""
    if len(t) <= CAP_HEAD + CAP_TAIL:
        return t
    return (t[:CAP_HEAD] + "\n\n[...trecho intermediario do relatorio omitido...]"
            "\n\n" + t[-CAP_TAIL:])


def main(por_lote=4, saida=None):
    saida = saida or OUT
    os.makedirs(saida, exist_ok=True)
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT d.arquivo, d.ano_pasta, d.proc_norm, d.texto FROM docs d "
        "LEFT JOIN doc_analises a ON a.arquivo=d.arquivo AND a.ai_feito=1 "
        "WHERE d.legivel=1 AND a.arquivo IS NULL "
        "ORDER BY d.ano_pasta DESC, d.arquivo").fetchall()
    con.close()
    n = 0
    for i in range(0, len(rows), int(por_lote)):
        n += 1
        lote = [{"arquivo": r[0], "ano": r[1], "proc_norm": r[2],
                 "texto": condensa(r[3])} for r in rows[i:i + int(por_lote)]]
        with open(os.path.join(saida, f"in_{n:03d}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(lote, f, ensure_ascii=False)
    print(f"{len(rows)} docs -> {n} lotes em {saida}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else 4,
         sys.argv[2] if len(sys.argv) > 2 else None)
