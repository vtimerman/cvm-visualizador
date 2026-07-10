#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
conduta_build.py — constroi a base de CONDUTA DECISORIA por diretor (conduta.db)
cruzando tudo que ja coletamos:

  julgar.db/julgados            -> quem relatou cada julgamento
  julgar.db/extratos_julgamento -> multas/absolvicoes/inabilitacoes do resultado
  decisoes.db/atas_colegiado    -> fichas IA: relatoria de itens, votos vencidos,
                                   pedidos de vista, divergencias, TC aceito/rejeitado
  pautas.db/pauta_sei           -> retiradas de pauta por relator

Saida (conduta.db):
  eventos(diretor, papel, evento, processo, data_iso, valor, detalhe, fonte)
  resumo(diretor, ...)  -> agregado por diretor (a materia-prima do "agente")

Determinístico e re-executavel (DELETE + rebuild). Rode apos as coletas.
"""
import os
import re
import sys
import json
import sqlite3
import unicodedata
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "conduta.db")

DIRETORES = ["Otto Lobo", "Joao Accioly", "Marina Copola", "Joao Pedro Nascimento",
             "Daniel Maeda", "Flavia Perlingeiro", "Alexandre Rangel",
             "Marcelo Barbosa", "Otavio Yazbek", "Thiago Paiva Chaves",
             "Luis Felipe Marques Lobianco", "Andre Passaro", "Igor Muniz"]
SIGLAS = {"DJA": "Joao Accioly", "DMC": "Marina Copola", "DOL": "Otto Lobo",
          "DFP": "Flavia Perlingeiro", "DAR": "Alexandre Rangel",
          "DDM": "Daniel Maeda", "DIM": "Igor Muniz", "TPC": "Thiago Paiva Chaves"}
# presidencia por periodo (para resolver itens com relator 'PTE')
PRES = [("Marcelo Barbosa", "", "2021-07-31"),
        ("Joao Pedro Nascimento", "2021-08-01", "2025-07-31"),
        ("Otto Lobo", "2025-08-01", "2025-12-31"),
        ("Joao Accioly", "2026-01-01", "9999-12-31")]

RE_RS = re.compile(r"R\$\s*([\d.]+(?:,\d{2})?)")


def _key(nome):
    s = unicodedata.normalize("NFKD", str(nome or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def canon(nome):
    k = _key(nome)
    for d in DIRETORES:
        if _key(d) in k or (len(k) > 8 and k in _key(d)):
            return d
    # nomes completos dos julgados (ex.: OTTO EDUARDO FONSECA DE ALBUQUERQUE LOBO)
    ALIAS = {"OTTO EDUARDO": "Otto Lobo", "UZEDA ACCIOLY": "Joao Accioly",
             "BARROSO DO NASCIMENTO": "Joao Pedro Nascimento",
             "PALMA COPOLA": "Marina Copola", "SANT ANNA PERLINGEIRO":
             "Flavia Perlingeiro", "MAEDA BERNARDO": "Daniel Maeda",
             "COSTA RANGEL": "Alexandre Rangel", "SANTOS BARBOSA":
             "Marcelo Barbosa"}
    for parte, d in ALIAS.items():
        if parte in k:
            return d
    return ""


def pte_em(data_iso):
    for nome, a, b in PRES:
        if (not a or data_iso >= a) and data_iso <= b:
            return nome
    return ""


def _valor(s):
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def conectar():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS eventos(
        id INTEGER PRIMARY KEY AUTOINCREMENT, diretor TEXT, papel TEXT,
        evento TEXT, processo TEXT, proc_norm TEXT, data_iso TEXT,
        valor REAL, detalhe TEXT, fonte TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS perfis(
        diretor TEXT PRIMARY KEY, dossie TEXT, ai_feito INTEGER DEFAULT 0,
        atualizado_em TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ev_dir ON eventos(diretor)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ev_proc ON eventos(proc_norm)")
    con.commit()
    return con


def build():
    con = conectar()
    con.execute("DELETE FROM eventos")
    ins = ("INSERT INTO eventos(diretor,papel,evento,processo,proc_norm,data_iso,"
           "valor,detalhe,fonte) VALUES(?,?,?,?,?,?,?,?,?)")

    # ---- A) julgados + resultado do extrato -------------------------------
    j = sqlite3.connect(os.path.join(DIR, "julgar.db"))
    extr = {}
    try:
        for pn, mt, ab, inb in j.execute(
                "SELECT proc_norm, SUM(multas_total), SUM(absolvicoes), "
                "SUM(inabilitacoes) FROM extratos_julgamento GROUP BY proc_norm"):
            extr[pn] = (mt or 0, ab or 0, inb or 0)
    except sqlite3.OperationalError:
        pass
    n = 0
    for rel, proc, pn, tipo, rito, data in j.execute(
            "SELECT relator_nome, processo, proc_norm, tipo, rito, data_julg "
            "FROM julgados"):
        d = canon(rel)
        if not d:
            continue
        iso = ""
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(data or ""))
        if m:
            iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        mt, ab, inb = extr.get(pn, (0, 0, 0))
        det = f"{tipo} · {rito}"
        if mt:
            det += f" · multas R$ {mt:,.2f}"
        if ab:
            det += f" · {ab} mencao(oes) a absolvicao"
        if inb:
            det += f" · inabilitacao"
        con.execute(ins, (d, "relator", "julgou", proc, pn, iso, mt, det,
                          "julgados+extratos"))
        n += 1
    j.close()
    print(f"[conduta] julgamentos: {n}")

    # ---- B) fichas das atas: relatoria, vencidos, vistas, TC --------------
    dz = sqlite3.connect(os.path.join(DIR, "decisoes.db"))
    nb = 0
    for link, data_iso, ficha in dz.execute(
            "SELECT link, data_iso, ficha FROM atas_colegiado WHERE "
            "ficha IS NOT NULL AND ficha<>''"):
        try:
            f = json.loads(ficha)
        except (ValueError, TypeError):
            continue
        for it in (f.get("itens") or []):
            proc = str(it.get("processo") or "")
            pn_m = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}", proc)
            pn = pn_m.group(0) if pn_m else ""
            votos = str(it.get("votos") or "")
            dec = str(it.get("decisao") or "")
            ass = str(it.get("assunto") or "")[:120]
            rl = str(it.get("relator") or "").strip().upper()
            # relatoria de item por diretor (sigla ou PTE resolvido pela data)
            drel = SIGLAS.get(rl) or (pte_em(data_iso or "") if rl == "PTE" else "")
            if drel:
                con.execute(ins, (drel, "relator", "relatou_item", proc, pn,
                                  data_iso, 0, ass, "atas_ficha"))
                nb += 1
            # vencidos / vistas / divergencias (por nome citado)
            for m in re.finditer(r"vencid[oa]s?,? (?:o |a )?(?:Diretor[a]? |"
                                 r"Presidente(?: Interino)? |Diretor Substituto )?"
                                 r"([A-Za-zÀ-ú]+(?: [A-Za-zÀ-ú]+){0,4})", votos):
                d = canon(m.group(1))
                if d:
                    con.execute(ins, (d, "votante", "voto_vencido", proc, pn,
                                      data_iso, 0, votos[:200], "atas_ficha"))
            for m in re.finditer(r"(?:pedido de vista d[oa]|pediu vista|solicitou "
                                 r"vista)[^.]*?([A-Za-zÀ-ú]+(?: [A-Za-zÀ-ú]+){0,4})",
                                 votos + " " + dec):
                d = canon(m.group(1))
                if d:
                    con.execute(ins, (d, "votante", "pediu_vista", proc, pn,
                                      data_iso, 0, ass, "atas_ficha"))
            # TC aceito/rejeitado (item colegiado; valor quando citado)
            blob = (ass + " " + dec).lower()
            if "termo de compromisso" in blob:
                vals = [_valor(v) for v in RE_RS.findall(dec + " "
                        + str(it.get("area_tecnica") or ""))]
                ev = ("tc_aceito" if re.search(r"aceit", blob) else
                      "tc_rejeitado" if re.search(r"rejeit", blob) else "")
                if ev:
                    con.execute(ins, ("(Colegiado)", "colegiado", ev, proc, pn,
                                      data_iso, max(vals) if vals else 0,
                                      ass, "atas_ficha"))
            # divergencia da area tecnica (colegiado)
            if re.search(r"divergindo da .rea|contrariando a .rea", dec.lower()):
                con.execute(ins, ("(Colegiado)", "colegiado",
                                  "divergiu_area_tecnica", proc, pn, data_iso,
                                  0, ass, "atas_ficha"))
    dz.close()
    print(f"[conduta] itens de ata com relator-diretor: {nb}")

    # ---- C) retiradas de pauta por relator --------------------------------
    pa = sqlite3.connect(os.path.join(DIR, "pautas.db"))
    try:
        for proc, rel, iso, sit in pa.execute(
                "SELECT processo, relator, data_sessao_iso, situacao FROM "
                "pauta_sei WHERE situacao LIKE 'retirado%'"):
            d = canon(rel)
            if d:
                con.execute(ins, (d, "relator", "retirou_de_pauta", proc, proc,
                                  iso or "", 0, sit, "pauta_sei"))
    except sqlite3.OperationalError:
        pass
    pa.close()

    con.commit()
    tot = con.execute("SELECT COUNT(*) FROM eventos").fetchone()[0]
    print(f"[conduta] eventos totais: {tot}")
    stats(con)
    con.close()


def stats(con=None):
    own = con is None
    con = con or conectar()
    print("== conduta por diretor ==")
    for r in con.execute("""
        SELECT diretor,
          SUM(evento='julgou'), ROUND(SUM(CASE WHEN evento='julgou' THEN valor END)),
          SUM(evento='relatou_item'), SUM(evento='voto_vencido'),
          SUM(evento='pediu_vista'), SUM(evento='retirou_de_pauta')
        FROM eventos WHERE diretor<>'(Colegiado)'
        GROUP BY diretor ORDER BY 2 DESC"""):
        print(f"  {r[0]:24s} julgou:{r[1] or 0:3d}  multasR${(r[2] or 0):>13,.0f}  "
              f"itens:{r[3] or 0:3d}  vencido:{r[4] or 0:2d}  vista:{r[5] or 0:2d}  "
              f"retirou:{r[6] or 0:2d}")
    tc = con.execute("SELECT SUM(evento='tc_aceito'), SUM(evento='tc_rejeitado') "
                     "FROM eventos").fetchone()
    print(f"  (Colegiado) TC aceitos:{tc[0] or 0} rejeitados:{tc[1] or 0}")
    if own:
        con.close()


def aplicar_dossie(caminho):
    """Grava dossiês {diretor: {...}} na tabela perfis."""
    con = conectar()
    dados = json.load(open(caminho, encoding="utf-8"))
    hoje = dt.date.today().isoformat()
    for diretor, d in dados.items():
        con.execute(
            "INSERT INTO perfis(diretor,dossie,ai_feito,atualizado_em) "
            "VALUES(?,?,1,?) ON CONFLICT(diretor) DO UPDATE SET "
            "dossie=excluded.dossie, ai_feito=1, atualizado_em=excluded.atualizado_em",
            (diretor, json.dumps(d, ensure_ascii=False), hoje))
    con.commit()
    con.close()
    print(f"[conduta] dossie(s) aplicado(s): {list(dados)}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    elif cmd == "aplicar_dossie":
        aplicar_dossie(sys.argv[2])
    else:
        stats()
