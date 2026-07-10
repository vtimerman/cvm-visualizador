#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agentes_tese.py — AGENTES ESPECIALISTAS POR TESE/TEMA.

Classifica cada caso (analises de julgados/TCs em conduta.db) num TEMA da
taxonomia fixa e mantem a tabela agentes_tese: um dossie/system-prompt por
tema, consultavel quando chega um processo novo daquele assunto.

v1: conhecimento = 1120 analises IA (2022+) + conduta dos diretores.
v2 (apos a leitura da jurisprudencia 1999-2025): evolucao historica das teses.

Comandos:
  python agentes_tese.py classificar        # (re)classifica casos por tema
  python agentes_tese.py stats
  python agentes_tese.py material <tema> <saida.json>   # material p/ o agente
  python agentes_tese.py aplicar_ia <json>  # {tema: {dossie...}} -> agentes_tese
  python agentes_tese.py roteia "<texto>"   # classifica um caso novo
"""
import os
import re
import sys
import json
import sqlite3
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "conduta.db")

# taxonomia fixa: tema -> padroes (regex, case-insensitive, sem acento tratado
# de forma tolerante)
TAXONOMIA = {
    "insider_trading": r"informa[cç][aã]o privilegiada|insider|art\.? ?155.{0,20}4|vedacao a negocia|RCVM ?44|Res(olucao)? ?CVM ?(n.? ?)?44",
    "manipulacao_fraude": r"manipula[cç]|condi[cç][oõ]es artificiais|opera[cç][aã]o fraudulenta|pr[aá]ticas n[aã]o equitativas|Instru[cç][aã]o (CVM )?(n.? ?)?0?8[ ,/]|RCVM ?62",
    "fato_relevante_dri": r"fato relevante|art\.? ?157|divulga[cç][aã]o (imediata|tempestiva)|ICVM ?358|dever de informar",
    "dever_diligencia_adm": r"dever de dilig[eê]ncia|dever de lealdade|art\.? ?15[34]|administradores? da companhia|conselheir",
    "abuso_controle": r"abuso de poder de controle|art\.? ?11[67]",
    "carteira_irregular": r"administra[cç][aã]o .{0,20}carteira.{0,40}(sem|irregular)|art\.? ?23 da Lei|RCVM ?21|sem pr[eé]via autoriza[cç][aã]o|exerc[ií]cio irregular",
    "fundos_investimento": r"fundos? de investimento|ICVM ?555|RCVM ?175|FIDC|FII\b|desenquadramento|administrador fiduci|gestor de fundo",
    "ofertas_publicas": r"oferta p[uú]blica|ICVM ?400|RCVM ?160|esfor[cç]os restritos|ICVM ?476|crowdfunding|oferta irregular",
    "auditoria": r"auditor(ia)? independente|normas de auditoria",
    "intermediarios": r"corretora|churning|suitability|intermedia[cç][aã]o|distribuidora de t[ií]tulos",
    "informacoes_periodicas": r"formul[aá]rio de refer[eê]ncia|demonstra[cç][oõ]es financeiras|informa[cç][oõ]es peri[oó]dicas|atraso na entrega|registro de companhia|ITR\b|DFP\b|assembleia geral ordin[aá]ria no prazo",
    "assembleia_conflito": r"assembleia|conflito de interesses|direito de voto|acionista controlador",
    "agentes_autonomos": r"agente aut[oô]nomo|assessor de investimento",
    "rito_processual": r"prescri[cç][aã]o|nulidade|cerceamento de defesa",
}
ORDEM = list(TAXONOMIA)


def classifica_texto(texto):
    """Temas de um texto (pode ter mais de um; 1o = dominante pela ordem)."""
    t = str(texto or "")
    hits = [tema for tema, pat in TAXONOMIA.items() if re.search(pat, t, re.I)]
    return hits or ["outros"]


def conectar():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS caso_tema(
        proc_norm TEXT, tipo TEXT, tema TEXT, dominante INTEGER,
        PRIMARY KEY (proc_norm, tipo, tema))""")
    con.execute("""CREATE TABLE IF NOT EXISTS agentes_tese(
        tema TEXT PRIMARY KEY, dossie TEXT, n_casos INTEGER,
        ai_feito INTEGER DEFAULT 0, atualizado_em TEXT)""")
    con.commit()
    return con


def classificar():
    con = conectar()
    con.execute("DELETE FROM caso_tema")
    n = 0
    for pn, tipo, a in con.execute(
            "SELECT proc_norm, tipo, analise FROM analises WHERE ai_feito=1"):
        try:
            j = json.loads(a)
        except (ValueError, TypeError):
            continue
        blob = " ".join(str(j.get(k) or "") for k in
                        ("conduta_imputada", "resumo", "desfecho", "racional"))
        temas = classifica_texto(blob)
        for i, tema in enumerate(temas):
            con.execute("INSERT OR REPLACE INTO caso_tema VALUES(?,?,?,?)",
                        (pn, tipo, tema, 1 if i == 0 else 0))
            n += 1
    con.commit()
    print(f"[tese] {n} classificacoes gravadas.")
    stats(con)
    con.close()


def material(tema, saida):
    """Exporta o material completo de um tema para o agente especialista."""
    con = conectar()
    casos = []
    for pn, tipo in con.execute(
            "SELECT proc_norm, tipo FROM caso_tema WHERE tema=?", (tema,)):
        row = con.execute("SELECT analise FROM analises WHERE proc_norm=? AND "
                          "tipo=?", (pn, tipo)).fetchone()
        if not row:
            continue
        a = json.loads(row[0])
        rel = con.execute("SELECT diretor, data_iso, valor FROM eventos WHERE "
                          "evento='julgou' AND proc_norm=?", (pn,)).fetchone()
        casos.append({"processo": pn, "tipo": tipo,
                      "relator": rel[0] if rel else "",
                      "data": rel[1] if rel else "",
                      "multas": rel[2] if rel else 0,
                      **{k: a.get(k) for k in ("resumo", "conduta_imputada",
                                               "desfecho", "severidade",
                                               "racional", "valor")}})
    con.close()
    json.dump({"tema": tema, "casos": casos},
              open(saida, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[tese] {tema}: {len(casos)} casos -> {saida}")


def aplicar_ia(caminho):
    con = conectar()
    dados = json.load(open(caminho, encoding="utf-8"))
    hoje = dt.date.today().isoformat()
    for tema, d in dados.items():
        n = con.execute("SELECT COUNT(*) FROM caso_tema WHERE tema=?",
                        (tema,)).fetchone()[0]
        con.execute(
            "INSERT INTO agentes_tese(tema,dossie,n_casos,ai_feito,"
            "atualizado_em) VALUES(?,?,?,1,?) ON CONFLICT(tema) DO UPDATE SET "
            "dossie=excluded.dossie, n_casos=excluded.n_casos, ai_feito=1, "
            "atualizado_em=excluded.atualizado_em",
            (tema, json.dumps(d, ensure_ascii=False), n, hoje))
        print(f"[tese] agente '{tema}' aplicado ({n} casos).")
    con.commit()
    con.close()


def stats(con=None):
    own = con is None
    con = con or conectar()
    print("== casos por tema (dominante) ==")
    for r in con.execute("SELECT tema, COUNT(*) FROM caso_tema WHERE "
                         "dominante=1 GROUP BY tema ORDER BY 2 DESC"):
        ag = con.execute("SELECT ai_feito FROM agentes_tese WHERE tema=?",
                         (r[0],)).fetchone()
        print(f"  {r[1]:4d}  {r[0]}" + ("  [agente OK]" if ag and ag[0] else ""))
    if own:
        con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if cmd == "classificar":
        classificar()
    elif cmd == "material":
        material(sys.argv[2], sys.argv[3])
    elif cmd == "aplicar_ia":
        aplicar_ia(sys.argv[2])
    elif cmd == "roteia":
        print(classifica_texto(" ".join(sys.argv[2:])))
    else:
        stats()
