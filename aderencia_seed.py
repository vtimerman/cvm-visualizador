#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aderencia_seed.py — RELATORIO DE ADERENCIA A JURISPRUDENCIA.

Para cada processo JULGADO em que um DIRETOR ATUAL foi relator, cruza a analise
do caso (conduta imputada, desfecho, conduta_relator) com o dossie do agente da
TESE correspondente (agentes_tese) e a IA devolve um relatorio de como o
voto/decisao do relator CONFIRMA ou CONTRARIA a jurisprudencia consolidada.

Gravado em conduta.db/aderencia_juris e exibido na ficha do processo.

Comandos:
  python aderencia_seed.py stats
  python aderencia_seed.py pendentes N       # JSON com material p/ a IA
  python aderencia_seed.py aplicar_ia <arquivo.json>   # {"<proc>": {relatorio}}
"""
import os
import sys
import json
import sqlite3
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "conduta.db")

# Ex-diretores recentes que TEM decisoes (relatorias) e seguem no acervo mesmo
# fora do Colegiado atual — mantidos como historico.
EX_COM_DECISAO = ["Joao Pedro Nascimento", "Daniel Maeda"]


def _diretores_atuais_quem():
    """Nomes de diretor a analisar. Fonte UNICA do Colegiado atual = quem.db
    (e_relator=1), mapeados para o nome curto usado em eventos via o registro
    canonico (pessoal.db). Uniao com os ex-diretores recentes que tem decisoes.
    Fallback para lista fixa se as bases nao existirem.
    """
    fixo = ["Otto Lobo", "Joao Accioly", "Marina Copola"] + EX_COM_DECISAO
    pes = os.path.join(DIR, "pessoal.db")
    if not os.path.exists(pes):
        return fixo
    try:
        con = sqlite3.connect(pes)
        # nome curto (eventos.diretor) das pessoas marcadas diretor atual
        cur = sqlite3.connect(DB)
        curtos = [r[0] for r in cur.execute(
            "SELECT DISTINCT diretor FROM eventos WHERE diretor NOT LIKE "
            "'%Colegiado%' AND diretor<>''")]
        cur.close()
        import unicodedata
        import re as _re

        def ck(n):
            s = unicodedata.normalize("NFKD", str(n or ""))
            s = "".join(c for c in s if not unicodedata.combining(c))
            s = s.encode("ascii", "ignore").decode()
            return _re.sub(r"\s+", " ", _re.sub(r"[^A-Za-z ]", " ", s).upper()).strip()
        atuais_pids = {r[0] for r in con.execute(
            "SELECT person_id FROM pessoas WHERE e_diretor_atual=1")}
        alias = {k: p for k, p in con.execute(
            "SELECT alias_key, person_id FROM pessoa_alias")}
        con.close()
        curr = [n for n in curtos if alias.get(ck(n)) in atuais_pids]
        return sorted(set(curr) | set(EX_COM_DECISAO)) or fixo
    except Exception:
        return fixo


DIRETORES_ATUAIS = _diretores_atuais_quem()

# campos do dossie relevantes p/ o cotejo (system_prompt fica de fora do material)
DOSSIE_CAMPOS = ("tese_vigente", "marcos", "dosimetria",
                 "posicao_por_diretor", "controversias")


def conectar():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS aderencia_juris(
        proc_norm TEXT, relator TEXT, tema TEXT, relatorio TEXT,
        veredito TEXT, ai_feito INTEGER DEFAULT 0, atualizado_em TEXT,
        PRIMARY KEY (proc_norm))""")
    # Posição de voto de cada diretor atual por processo (não só o relator).
    con.execute("""CREATE TABLE IF NOT EXISTS aderencia_voto(
        proc_norm TEXT, diretor TEXT, tema TEXT, papel TEXT, veredito TEXT,
        posicao TEXT, atualizado_em TEXT,
        PRIMARY KEY (proc_norm, diretor))""")
    con.commit()
    return con


def aplicar_voto(caminho):
    """Aplica posições de voto. Entrada: {"<proc>||<diretor>": {papel,veredito,posicao}}."""
    con = conectar()
    dados = json.load(open(caminho, encoding="utf-8"))
    hoje = dt.date.today().isoformat()
    tema_de = {r[0]: r[1] for r in con.execute(
        "SELECT proc_norm, tema FROM caso_tema WHERE dominante=1")}
    n = 0
    for chave, v in dados.items():
        pn, _, diretor = chave.partition("||")
        pn, diretor = pn.strip(), diretor.strip()
        if not pn or not diretor:
            continue
        con.execute(
            "INSERT INTO aderencia_voto(proc_norm,diretor,tema,papel,veredito,"
            "posicao,atualizado_em) VALUES(?,?,?,?,?,?,?) ON CONFLICT"
            "(proc_norm,diretor) DO UPDATE SET papel=excluded.papel, "
            "veredito=excluded.veredito, posicao=excluded.posicao, "
            "atualizado_em=excluded.atualizado_em",
            (pn, diretor, tema_de.get(pn, ""), str(v.get("papel") or ""),
             str(v.get("veredito") or ""), str(v.get("posicao") or ""), hoje))
        n += 1
    con.commit()
    con.close()
    print(f"[aderencia] aplicadas {n} posicao(oes) de voto.")


def _juris():
    """proc_norm -> voto real do acervo (resultado, teses, votos) do juris.db."""
    out = {}
    j = sqlite3.connect(os.path.join(DIR, "juris.db"))
    for pn, res, teses, votos in j.execute(
            "SELECT proc_norm, resultado, teses, votos FROM doc_analises "
            "WHERE ai_feito=1"):
        if pn:
            out[pn] = {"resultado": res, "teses": teses, "votos": votos}
    j.close()
    return out


def _universo(con):
    """(proc_norm, relator, tema) das relatorias julgadas com analise e tese."""
    out = []
    ph = ",".join("?" * len(DIRETORES_ATUAIS))
    for pn, rel, data in con.execute(
            f"SELECT proc_norm, diretor, data_iso FROM eventos WHERE "
            f"evento='julgou' AND diretor IN ({ph})", DIRETORES_ATUAIS):
        a = con.execute("SELECT analise FROM analises WHERE proc_norm=? AND "
                        "tipo='julgado' AND ai_feito=1", (pn,)).fetchone()
        if not a:
            continue
        t = con.execute("SELECT tema FROM caso_tema WHERE proc_norm=? AND "
                        "dominante=1 AND tema!='nao_classificado'",
                        (pn,)).fetchone()
        if not t:
            continue
        out.append((pn, rel, t[0], data, a[0]))
    return out


def _proc_em_marcos(marcos):
    """Conjunto de proc_norm citados nos marcos da tese (p/ flag eh_marco)."""
    import re
    nums = set()
    for m in (marcos or []):
        p = str(m.get("processo") or "") if isinstance(m, dict) else str(m)
        mm = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}", p)
        if mm:
            nums.add(mm.group(0))
    return nums


def pendentes(n):
    con = conectar()
    juris = _juris()
    feitos = {r[0] for r in con.execute(
        "SELECT proc_norm FROM aderencia_juris WHERE ai_feito=1")}
    saida = []
    for pn, rel, tema, data, analise in _universo(con):
        if pn in feitos:
            continue
        dossie = con.execute("SELECT dossie FROM agentes_tese WHERE tema=?",
                             (tema,)).fetchone()
        if not dossie:
            continue
        d = json.loads(dossie[0])
        caso = json.loads(analise)
        v = juris.get(pn, {})
        saida.append({
            "proc_norm": pn, "relator": rel, "tema": tema, "data": data,
            "eh_marco": pn in _proc_em_marcos(d.get("marcos")),
            "caso": {k: caso.get(k) for k in
                     ("resumo", "conduta_imputada", "desfecho", "racional")},
            "voto": {"resultado": v.get("resultado"),
                     "teses_do_caso": v.get("teses"),
                     "votos": v.get("votos")},
            "tese": {k: d.get(k) for k in DOSSIE_CAMPOS},
        })
    con.close()
    print(json.dumps(saida[:int(n)], ensure_ascii=False, indent=1))


def aplicar_ia(caminho):
    con = conectar()
    dados = json.load(open(caminho, encoding="utf-8"))
    hoje = dt.date.today().isoformat()
    uni = {pn: (rel, tema) for pn, rel, tema, _, _ in _universo(con)}
    n = 0
    for pn, r in dados.items():
        pn = pn.strip()
        rel, tema = uni.get(pn, (r.get("relator", ""), r.get("tema", "")))
        con.execute(
            "INSERT INTO aderencia_juris(proc_norm,relator,tema,relatorio,"
            "veredito,ai_feito,atualizado_em) VALUES(?,?,?,?,?,1,?) ON CONFLICT"
            "(proc_norm) DO UPDATE SET relatorio=excluded.relatorio, "
            "veredito=excluded.veredito, ai_feito=1, "
            "atualizado_em=excluded.atualizado_em",
            (pn, rel, tema, json.dumps(r, ensure_ascii=False),
             str(r.get("veredito") or ""), hoje))
        n += 1
    con.commit()
    con.close()
    print(f"[aderencia] aplicados {n} relatorio(s).")


def stats():
    con = conectar()
    uni = _universo(con)
    feitos = con.execute(
        "SELECT COUNT(*) FROM aderencia_juris WHERE ai_feito=1").fetchone()[0]
    print(f"universo (julgadas c/ analise e tese): {len(uni)} | "
          f"relatorios feitos: {feitos} | pendentes: {len(uni) - feitos}")
    por = {}
    for _, rel, _, _, _ in uni:
        por[rel] = por.get(rel, 0) + 1
    for rel, c in sorted(por.items(), key=lambda x: -x[1]):
        print(f"  {rel:24} {c}")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "pendentes":
        pendentes(sys.argv[2] if len(sys.argv) > 2 else 10)
    elif cmd == "aplicar_ia":
        aplicar_ia(sys.argv[2])
    elif cmd == "aplicar_voto":
        aplicar_voto(sys.argv[2])
    else:
        stats()
