#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analise_seed.py — pipeline das ANALISES (IA) de processos julgados e termos de
compromisso, gravadas em conduta.db/analises e exibidas nas fichas do app.

Para cada processo JULGADO, consolida o material (objeto/ementa, acusados e
desfechos, relator/data do julgamento, extrato da sessao com multas, itens de
ata que citam o processo, TC anterior se houver) e a IA devolve uma analise
estruturada. Para cada TERMO DE COMPROMISSO, consolida partes/situacao/valores.

Comandos:
  python analise_seed.py stats
  python analise_seed.py pendentes N [julgado|tc]   # JSON com material p/ IA
  python analise_seed.py aplicar_ia <arquivo.json>  # {"<proc>|<tipo>": {analise}}
"""
import os
import re
import sys
import json
import sqlite3
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "conduta.db")
RE_PROC = re.compile(r"1\d{4}\.\d{6}/\d{4}-\d{2}")


def _norm(p):
    m = RE_PROC.search(str(p or ""))
    return m.group(0) if m else ""


def conectar():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS analises(
        proc_norm TEXT, tipo TEXT, analise TEXT, resumo TEXT,
        ai_feito INTEGER DEFAULT 0, atualizado_em TEXT,
        PRIMARY KEY (proc_norm, tipo))""")
    con.commit()
    return con


def _material_julgados():
    """proc_norm -> material consolidado de cada processo julgado."""
    out = {}
    j = sqlite3.connect(os.path.join(DIR, "julgar.db"))
    for rel, proc, pn, tipo, rito, data in j.execute(
            "SELECT relator_nome, processo, proc_norm, tipo, rito, data_julg "
            "FROM julgados"):
        if pn:
            out[pn] = {"processo": proc, "relator": rel, "data_julgamento": data,
                       "peca": tipo, "rito": rito}
    try:
        for pn, txt, mt, ab, inb in j.execute(
                "SELECT proc_norm, texto, multas_total, absolvicoes, "
                "inabilitacoes FROM extratos_julgamento"):
            if pn in out:
                out[pn]["extrato_resultado"] = re.sub(r"\s+", " ", txt or "")[:2500]
                out[pn]["multas_total"] = mt
    except sqlite3.OperationalError:
        pass
    j.close()
    p = sqlite3.connect(os.path.join(DIR, "processos.db"))
    info = {}
    for idp, num, obj, eme in p.execute(
            "SELECT idproc, numero, objeto, ementa FROM processos"):
        pn = _norm(num)
        if pn:
            info[pn] = (idp, obj, eme)
    ac = {}
    for idp, nome, sit, hist in p.execute(
            "SELECT idproc, nome, situacao, historico FROM acusados"):
        ac.setdefault(idp, []).append(f"{nome} [{sit}]")
    p.close()
    for pn, o in out.items():
        if pn in info:
            idp, obj, eme = info[pn]
            o["objeto"] = (obj or "")[:800]
            o["ementa"] = (eme or "")[:800]
            o["acusados"] = ac.get(idp, [])[:15]
    # itens de ata citando o processo
    dz = sqlite3.connect(os.path.join(DIR, "decisoes.db"))
    for data_iso, ficha in dz.execute(
            "SELECT data_iso, ficha FROM atas_colegiado WHERE ficha<>''"):
        try:
            f = json.loads(ficha)
        except (ValueError, TypeError):
            continue
        for it in (f.get("itens") or []):
            pn = _norm(it.get("processo"))
            if pn in out:
                out[pn].setdefault("itens_de_ata", []).append(
                    {"data": data_iso, "decisao": str(it.get("decisao"))[:300],
                     "votos": str(it.get("votos"))[:300]})
    dz.close()
    # TC anterior no mesmo processo
    t = sqlite3.connect(os.path.join(DIR, "termos.db"))
    for proc, sit, data in t.execute(
            "SELECT processo, situacao, data_decisao FROM termos"):
        pn = _norm(proc)
        if pn in out:
            out[pn]["tc_anterior"] = f"{sit} em {data}"
    t.close()
    return out


def _material_tcs():
    out = {}
    t = sqlite3.connect(os.path.join(DIR, "termos.db"))
    for proc, pn, sit, data, partes in t.execute(
            "SELECT processo, proc_norm, situacao, data_decisao, partes "
            "FROM termos"):
        if pn:
            out[pn] = {"processo": proc, "situacao": sit, "data_decisao": data,
                       "partes": (partes or "")[:600]}
    t.close()
    dz = sqlite3.connect(os.path.join(DIR, "decisoes.db"))
    for data_iso, ficha in dz.execute(
            "SELECT data_iso, ficha FROM atas_colegiado WHERE ficha<>''"):
        try:
            f = json.loads(ficha)
        except (ValueError, TypeError):
            continue
        for it in (f.get("itens") or []):
            pn = _norm(it.get("processo"))
            blob = str(it.get("assunto") or "").lower()
            if pn in out and "termo de compromisso" in blob:
                out[pn]["deliberacao_ata"] = {
                    "data": data_iso,
                    "area_tecnica": str(it.get("area_tecnica"))[:400],
                    "decisao": str(it.get("decisao"))[:400]}
    dz.close()
    p = sqlite3.connect(os.path.join(DIR, "processos.db"))
    for num, obj in p.execute("SELECT numero, objeto FROM processos"):
        pn = _norm(num)
        if pn in out:
            out[pn]["objeto"] = (obj or "")[:600]
    p.close()
    return out


def pendentes(n, so_tipo=None):
    con = conectar()
    feitos = {(r[0], r[1]) for r in con.execute(
        "SELECT proc_norm, tipo FROM analises WHERE ai_feito=1")}
    con.close()
    saida = []
    if so_tipo in (None, "julgado"):
        mj = _material_julgados()
        for pn, m in mj.items():
            if (pn, "julgado") not in feitos:
                saida.append({"proc_norm": pn, "tipo": "julgado", **m})
    if so_tipo in (None, "tc"):
        mt = _material_tcs()
        for pn, m in mt.items():
            if (pn, "tc") not in feitos:
                saida.append({"proc_norm": pn, "tipo": "tc", **m})
    print(json.dumps(saida[:int(n)], ensure_ascii=False, indent=1))


def aplicar_ia(caminho):
    con = conectar()
    dados = json.load(open(caminho, encoding="utf-8"))
    hoje = dt.date.today().isoformat()
    n = 0
    for chave, a in dados.items():
        pn, _, tipo = chave.partition("|")
        # a chave vem do nosso proprio export (proc_norm da base de origem);
        # TCs antigos usam formatos nao-SEI ("01/2009", "RJ2013/...") — aceitar.
        pn = pn.strip()
        if not pn or tipo not in ("julgado", "tc"):
            print(f"  ! chave invalida: {chave}", file=sys.stderr)
            continue
        con.execute(
            "INSERT INTO analises(proc_norm,tipo,analise,resumo,ai_feito,"
            "atualizado_em) VALUES(?,?,?,?,1,?) ON CONFLICT(proc_norm,tipo) DO "
            "UPDATE SET analise=excluded.analise, resumo=excluded.resumo, "
            "ai_feito=1, atualizado_em=excluded.atualizado_em",
            (pn, tipo, json.dumps(a, ensure_ascii=False),
             str(a.get("resumo") or "")[:400], hoje))
        n += 1
    con.commit()
    con.close()
    print(f"[analise] aplicadas {n} analise(s).")


def stats():
    con = conectar()
    nj = len(_material_julgados())
    nt = len(_material_tcs())
    fj = con.execute("SELECT COUNT(*) FROM analises WHERE tipo='julgado' AND "
                     "ai_feito=1").fetchone()[0]
    ft = con.execute("SELECT COUNT(*) FROM analises WHERE tipo='tc' AND "
                     "ai_feito=1").fetchone()[0]
    print(f"julgados: {nj} (analisados {fj}, pendentes {nj - fj}) | "
          f"tcs: {nt} (analisados {ft}, pendentes {nt - ft})")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "pendentes":
        pendentes(sys.argv[2] if len(sys.argv) > 2 else 10,
                  sys.argv[3] if len(sys.argv) > 3 else None)
    elif cmd == "aplicar_ia":
        aplicar_ia(sys.argv[2])
    else:
        stats()
