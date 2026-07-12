#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atas_lotes.py -- divide as atas do Colegiado PENDENTES de ficha (com texto,
sem ai_feito) em LOTES por tamanho de texto, para os agentes redigirem as fichas.

Cada lote e' um in_NNN.json = [{link,titulo,data,data_iso,texto}]. O agente le,
produz {link: {resumo,participantes,itens:[{processo,assunto,area_tecnica,
decisao,votos}]}} e grava out_NNN.json. Aplicar com:
  python atas_colegiado_ficha.py aplicar_ia out_NNN.json

Uso:
  python atas_lotes.py [cutoff_iso] [alvo_chars] [max_atas]
  (padrao: cutoff=2002-01-01, alvo=48000 chars/lote, max=8 atas/lote)
Env: SCRATCH=dir destino (default ./atas_batches)
"""
import os
import sys
import json
import sqlite3

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "decisoes.db")
CAP_ATA = 42000   # trunca texto de uma ata gigantesca (preserva o essencial)


def main():
    cutoff = sys.argv[1] if len(sys.argv) > 1 else "2002-01-01"
    alvo = int(sys.argv[2]) if len(sys.argv) > 2 else 48000
    maxa = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    outdir = os.environ.get("SCRATCH", os.path.join(DIR, "atas_batches"))
    os.makedirs(outdir, exist_ok=True)
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT link, titulo, data, data_iso, texto FROM atas_colegiado "
        "WHERE data_iso>=? AND texto IS NOT NULL AND LENGTH(texto)>=120 "
        "AND (ai_feito IS NULL OR ai_feito=0) ORDER BY data_iso DESC",
        (cutoff,)).fetchall()
    con.close()
    lote, size, n = [], 0, 0
    def flush():
        nonlocal lote, size, n
        if not lote:
            return
        n += 1
        with open(os.path.join(outdir, f"in_{n:03d}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(lote, f, ensure_ascii=False)
        lote, size = [], 0
    for link, tit, data, di, texto in rows:
        t = (texto or "")[:CAP_ATA]
        item = {"link": link, "titulo": tit, "data": data, "data_iso": di,
                "texto": t}
        if lote and (size + len(t) > alvo or len(lote) >= maxa):
            flush()
        lote.append(item)
        size += len(t)
        if len(lote) >= maxa or size > alvo:
            flush()
    flush()
    print(f"[atas_lotes] {len(rows)} atas pendentes -> {n} lotes em {outdir} "
          f"(cutoff {cutoff}, alvo {alvo} chars, max {maxa}/lote)")


if __name__ == "__main__":
    main()
