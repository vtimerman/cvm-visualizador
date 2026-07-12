#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""juris_areas.py -- Fase D da jurisprudencia: monta os AGENTES DAS AREAS
TECNICAS (SEP/SMI/SNC/SIN/SFI/SRE/SPS) que acusam perante o Colegiado da CVM.

Para cada area:
  - agrega os julgados em que ela foi a area acusadora (juris.db doc_analises,
    campo area_tecnica), com estatistica REAL de acolhimento (condenatorio /
    misto / absolutorio / TAC), temas defendidos e evolucao por periodo;
  - cruza com os superintendentes do Boletim de Pessoal (pessoal.db movimentos);
  - grava um "pacote" deterministico (packets) que os subagentes usam para
    redigir o dossie -- os NUMEROS sao verdade, a IA so interpreta;
  - aplica os dossies (aplicar) na conduta.db::agentes_area (versionada).

juris.db e LOCAL/gitignored -> rodar LOCAL e commitar a conduta.db.

Uso:
  python juris_areas.py packets [DIR]      # gera pacotes de entrada
  python juris_areas.py aplicar out_*.json # aplica dossies dos agentes
  python juris_areas.py status
"""
import os
import re
import sys
import json
import glob
import sqlite3
import unicodedata
import collections

DIR = os.path.dirname(os.path.abspath(__file__))
JURIS = os.path.join(DIR, "juris.db")
CONDUTA = os.path.join(DIR, "conduta.db")
PESSOAL = os.path.join(DIR, "pessoal.db")

# areas acusadoras (a PFE e procuradoria/parecer juridico, nao acusa; SSE tem
# massa insuficiente) -> sigla -> nome canonico
AREAS = {
    "SEP": "Superintendencia de Relacoes com Empresas",
    "SMI": "Superintendencia de Relacoes com o Mercado e Intermediarios",
    "SNC": "Superintendencia de Normas Contabeis e de Auditoria",
    "SIN": "Superintendencia de Supervisao de Investidores Institucionais",
    "SFI": "Superintendencia de Fiscalizacao Externa",
    "SRE": "Superintendencia de Registro de Valores Mobiliarios",
    "SPS": "Superintendencia de Processos Sancionadores",
}
PERIODOS = [(1999, 2005), (2006, 2011), (2012, 2017), (2018, 2025)]


def _na(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _classif(resultado):
    """(condenatorio, absolutorio, tac) booleans a partir do texto livre."""
    t = _na(resultado)
    absv = bool(re.search(r"absolv|improceden|arquiv|nao conhec|extin", t))
    tac = bool(re.search(r"termo de compromisso|celebra(?:d|r)", t))
    pen = bool(re.search(r"multa|inabilit|proib|penalidade|conden|"
                         r"adverten|suspens", t))
    return pen, absv, tac


def _ano(data_julg, arquivo):
    m = re.search(r"(19|20)\d{2}", str(data_julg or ""))
    if m:
        return int(m.group(0))
    m = re.search(r"(19|20)\d{2}", str(arquivo or ""))
    return int(m.group(0)) if m else 0


def _temas_do_doc(teses_json):
    out = []
    try:
        arr = json.loads(teses_json or "[]")
        for t in arr:
            if isinstance(t, dict) and t.get("tema"):
                out.append(_na(t["tema"]).strip())
    except (ValueError, TypeError):
        pass
    return out


def _superintendentes(sigla):
    if not os.path.exists(PESSOAL):
        return []
    c = sqlite3.connect(PESSOAL)
    try:
        rows = c.execute(
            "SELECT servidor_nome, tipo, data_efeito, data_ato_iso FROM "
            "movimentos WHERE funcao LIKE '%uperinten%' AND UPPER(sigla)=? "
            "ORDER BY COALESCE(data_efeito,data_ato_iso) DESC", (sigla,)
        ).fetchall()
    except Exception:
        rows = []
    c.close()
    vis, out = set(), []
    for nome, tipo, de, da in rows:
        k = _na(nome)
        if k in vis:
            continue
        vis.add(k)
        out.append({"nome": (nome or "").strip(), "tipo": tipo,
                    "data": de or da or ""})
    return out[:8]


def _acolh(cond, misto, absv):
    merito = cond + misto + absv
    return round(100.0 * (cond + misto) / merito, 1) if merito else None


def packets(outdir):
    if not os.path.exists(JURIS):
        print("juris.db AUSENTE (fonte local)."); return
    os.makedirs(outdir, exist_ok=True)
    j = sqlite3.connect(JURIS)
    rows = j.execute(
        "SELECT arquivo, proc_norm, data_julg, relator, resultado, resumo, "
        "teses, area_tecnica FROM doc_analises WHERE ai_feito=1").fetchall()
    j.close()

    gerados = 0
    for sig, nome in AREAS.items():
        pat = re.compile(r"\b" + sig.lower() + r"\b")
        casos = []
        temas = collections.Counter()
        # por periodo: [cond, misto, abs, tac, total]
        per = {f"{a}-{b}": [0, 0, 0, 0, 0] for a, b in PERIODOS}
        tot = [0, 0, 0, 0]  # cond, misto, abs, tac
        for arq, pn, dj, rel, resu, resumo, teses, area in rows:
            if not pat.search(_na(area)):
                continue
            pen, absv, tac = _classif(resu)
            if pen and absv:
                bucket = 1  # misto
            elif pen:
                bucket = 0
            elif absv:
                bucket = 2
            elif tac:
                bucket = 3
            else:
                continue  # indefinido: fora da estatistica de merito
            tot[bucket] += 1
            ano = _ano(dj, arq)
            for a, b in PERIODOS:
                if a <= ano <= b:
                    key = f"{a}-{b}"
                    per[key][bucket] += 1
                    per[key][4] += 1
                    break
            dtemas = _temas_do_doc(teses)
            for t in dtemas:
                temas[t] += 1
            casos.append({
                "proc": pn or "", "ano": ano, "relator": (rel or "")[:60],
                "res": _na(resu)[:170], "temas": dtemas[:3],
                "_b": bucket,
            })
        n = len(casos)
        if n < 8:
            print(f"  {sig}: {n} casos (poucos) -- pulado")
            continue
        # amostra: espalhar pelos anos, priorizar recentes e absolutorios
        casos.sort(key=lambda x: (x["ano"], x["_b"]))
        amostra = casos if n <= 60 else (
            casos[::max(1, n // 45)] + [c for c in casos if c["_b"] == 2][:15])
        # dedup amostra por proc
        vis, amo = set(), []
        for c in amostra:
            if c["proc"] in vis:
                continue
            vis.add(c["proc"])
            amo.append({k: v for k, v in c.items() if k != "_b"})
        pkt = {
            "sigla": sig, "nome": nome, "n_casos": n,
            "resultados": {"condenatorio": tot[0], "misto": tot[1],
                           "absolutorio": tot[2], "tac": tot[3]},
            "acolhimento_pct": _acolh(tot[0], tot[1], tot[2]),
            "temas_top": temas.most_common(12),
            "por_periodo": {
                k: {"n": v[4], "condenatorio": v[0], "misto": v[1],
                    "absolutorio": v[2], "tac": v[3],
                    "acolhimento_pct": _acolh(v[0], v[1], v[2])}
                for k, v in per.items() if v[4]},
            "superintendentes": _superintendentes(sig),
            "casos": amo[:60],
        }
        path = os.path.join(outdir, f"in_{sig}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pkt, f, ensure_ascii=False, indent=1)
        gerados += 1
        ac = pkt["acolhimento_pct"]
        print(f"  {sig}: {n} casos | acolhimento {ac}% | "
              f"{len(pkt['superintendentes'])} superint. -> {os.path.basename(path)}")
    print(f"[packets] {gerados} pacotes em {outdir}")


def _ensure_table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS agentes_area(
        sigla TEXT PRIMARY KEY, nome TEXT, dossie TEXT,
        n_casos INTEGER, acolhimento REAL, ai_feito INTEGER DEFAULT 0,
        atualizado_em TEXT)""")


def aplicar(paths):
    if not os.path.exists(CONDUTA):
        print("conduta.db ausente."); return
    c = sqlite3.connect(CONDUTA)
    _ensure_table(c)
    ap = 0
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ! {p}: {e}"); continue
        items = data if isinstance(data, list) else [data]
        for d in items:
            sig = (d.get("sigla") or "").upper()
            if sig not in AREAS:
                print(f"  ? sigla desconhecida: {sig}"); continue
            dossie = d.get("dossie", d)
            c.execute(
                "INSERT INTO agentes_area(sigla,nome,dossie,n_casos,"
                "acolhimento,ai_feito,atualizado_em) VALUES(?,?,?,?,?,1,"
                "datetime('now')) ON CONFLICT(sigla) DO UPDATE SET "
                "nome=excluded.nome, dossie=excluded.dossie, "
                "n_casos=excluded.n_casos, acolhimento=excluded.acolhimento, "
                "ai_feito=1, atualizado_em=excluded.atualizado_em",
                (sig, AREAS[sig], json.dumps(dossie, ensure_ascii=False),
                 d.get("n_casos"), d.get("acolhimento_pct")))
            ap += 1
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM agentes_area WHERE ai_feito=1"
                  ).fetchone()[0]
    c.close()
    print(f"[aplicar] {ap} dossies aplicados. agentes_area prontos: {n}")


def status():
    if not os.path.exists(CONDUTA):
        print("conduta.db ausente."); return
    c = sqlite3.connect(CONDUTA)
    _ensure_table(c)
    for sig, nome in AREAS.items():
        r = c.execute("SELECT n_casos, acolhimento, ai_feito FROM "
                      "agentes_area WHERE sigla=?", (sig,)).fetchone()
        if r:
            print(f"  {sig}: {r[0]} casos | acolh {r[1]}% | ok={r[2]}")
        else:
            print(f"  {sig}: (pendente)")
    c.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "packets":
        packets(sys.argv[2] if len(sys.argv) > 2 else os.path.join(DIR, "area_packets"))
    elif cmd == "aplicar":
        ps = []
        for a in sys.argv[2:]:
            ps.extend(glob.glob(a))
        aplicar(ps)
    else:
        status()
