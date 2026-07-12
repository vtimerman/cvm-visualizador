#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""juris_unidades.py -- Fase D (parte 2): agentes das UNIDADES da CVM.

Camadas:
  1. SUPERINTENDENCIAS -- dossie institucional por SI: competencia, titulares
     (Boletim de Pessoal), gerencias subordinadas, atuacao sancionadora (link ao
     agente de area) e FOOTPRINT RECURSAL (decisoes.db: quantos recursos o
     Colegiado julgou contra decisao daquela SI + amostra). As SI com
     jurisprudencia recebem dossie de IA; as administrativas, card deterministico.
  2. GERENCIAS -- arvore deterministica (pessoal.db movimentos): cada gerencia
     com SI-pai, chefe atual e historico. Sem IA (dado puro).
  3. CTC (Comite de Termo de Compromisso) -- dossie de IA do termos.db (situacao
     Aceito/Rejeitado, evolucao) + amostra de decisoes de proposta de TC.

Fontes: decisoes.db, pessoal.db, termos.db (versionadas) -> conduta.db.

Uso:
  python juris_unidades.py gerencias         # arvore -> agentes_gerencia
  python juris_unidades.py packets [DIR]      # pacotes SI + CTC p/ os agentes
  python juris_unidades.py cards              # cards deterministicos das SI adm
  python juris_unidades.py aplicar out_*.json # dossies de IA -> agentes_unidade
  python juris_unidades.py status
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
DECISOES = os.path.join(DIR, "decisoes.db")
PESSOAL = os.path.join(DIR, "pessoal.db")
TERMOS = os.path.join(DIR, "termos.db")
CONDUTA = os.path.join(DIR, "conduta.db")

# universo das superintendencias: sigla -> (nome, tipo, competencia oficial)
# tipo: 'sancionadora' | 'registro' | 'normativa' | 'orientacao' | 'apoio'
SIS = {
    "SEP": ("Superintendência de Relações com Empresas", "sancionadora",
            "Registro e supervisão de companhias abertas: informações "
            "periódicas e eventuais, fatos relevantes, governança, deveres de "
            "administradores e controladores."),
    "SIN": ("Superintendência de Supervisão de Investidores Institucionais",
            "sancionadora",
            "Supervisão de fundos de investimento, administradores de carteira, "
            "gestores e consultores; registro e fiscalização da indústria de "
            "gestão de recursos."),
    "SMI": ("Superintendência de Relações com o Mercado e Intermediários",
            "sancionadora",
            "Supervisão do mercado secundário, das entidades administradoras "
            "de mercado (bolsas) e dos intermediários; repressão a manipulação "
            "e insider."),
    "SNC": ("Superintendência de Normas Contábeis e de Auditoria",
            "normativa",
            "Normatização contábil e de auditoria; registro e supervisão dos "
            "auditores independentes."),
    "SRE": ("Superintendência de Registro de Valores Mobiliários", "registro",
            "Registro de ofertas públicas de distribuição e de emissores; "
            "supervisão da regularidade das ofertas."),
    "SFI": ("Superintendência de Fiscalização Externa", "sancionadora",
            "Inspeções e fiscalização de campo dos participantes do mercado; "
            "apuração de irregularidades."),
    "SPS": ("Superintendência de Processos Sancionadores", "sancionadora",
            "Condução e instrução dos processos administrativos sancionadores; "
            "formulação de acusação e uniformização do rito."),
    "SOI": ("Superintendência de Orientação aos Investidores", "orientacao",
            "Atendimento e orientação ao investidor, educação financeira e "
            "análise de reclamações; recursos em pedidos de acesso/atendimento."),
    "SDM": ("Superintendência de Desenvolvimento de Mercado", "normativa",
            "Regulação e desenvolvimento do mercado: elaboração de normas, "
            "consultas públicas e aperfeiçoamento regulatório."),
    "SSE": ("Superintendência de Supervisão de Securitização", "sancionadora",
            "Supervisão das companhias securitizadoras, ofertas de CRI/CRA e "
            "agentes fiduciários."),
    "SSR": ("Superintendência de Supervisão de Riscos Estratégicos", "apoio",
            "Supervisão baseada em risco e monitoramento de riscos "
            "estratégicos do mercado."),
    # administrativas / apoio (card deterministico):
    "SGE": ("Superintendência Geral", "apoio",
            "Coordenação executiva das áreas técnicas e administrativas."),
    "SAD": ("Superintendência Administrativo-Financeira", "apoio",
            "Gestão orçamentária, financeira, de compras, contratos e "
            "patrimônio."),
    "SGP": ("Superintendência de Gestão de Pessoas", "apoio",
            "Gestão de pessoas, folha e desenvolvimento de servidores."),
    "STI": ("Superintendência de Tecnologia da Informação", "apoio",
            "Infraestrutura e sistemas de tecnologia da informação."),
    "SSI": ("Superintendência de Informática", "apoio",
            "Informática e sistemas (denominação histórica)."),
    "SPL": ("Superintendência de Planejamento", "apoio",
            "Planejamento institucional, projetos e gestão estratégica."),
    "SDI": ("Superintendência de Desenvolvimento de Inteligência", "apoio",
            "Engenharia e análise de dados, inteligência e desenvolvimento "
            "de soluções analíticas."),
    "SDE": ("Superintendência Seccional de Desenvolvimento", "apoio",
            "Desenvolvimento seccional/regional."),
    "SRL": ("Superintendência de Relações Institucionais", "apoio",
            "Relações institucionais e articulação externa."),
    "SRB": ("Superintendência Regional de Brasília", "apoio",
            "Atuação regional em Brasília."),
    "SRS": ("Superintendência Regional de São Paulo", "apoio",
            "Atuação regional em São Paulo."),
    "SMD": ("Superintendência de Supervisão de Mercados e Desenvolvimento",
            "apoio", "Supervisão de mercados (denominação recente)."),
}
# SI que recebem dossie de IA (tem jurisprudencia/footprint):
SIS_IA = {"SEP", "SIN", "SMI", "SNC", "SRE", "SFI", "SPS", "SOI", "SDM", "SSE"}

# gerencia (por palavra-chave do nome/unidade) -> SI-pai
GER_KW = [
    ("empresa", "SEP"), ("sancionador", "SPS"), ("orientacao ao invest", "SOI"),
    ("investidor institu", "SIN"), ("fundo", "SIN"), ("carteira", "SIN"),
    ("intermediari", "SMI"), ("estrutura de mercado", "SMI"),
    ("acompanhamento de mercado", "SMI"), ("mercado e sist", "SMI"),
    ("fiscalizacao externa", "SFI"), ("fiscalizacao", "SFI"),
    ("norma", "SNC"), ("contabe", "SNC"), ("auditoria", "SNC"),
    ("registro", "SRE"), ("oferta", "SRE"), ("securitiz", "SSE"),
    ("regulac", "SDM"), ("desenvolvimento de merc", "SDM"),
    ("aperfeicoamento de norma", "SDM"), ("risco", "SSR"),
    ("tecnologia", "STI"), ("sistema", "STI"), ("informatica", "STI"),
    ("recursos humanos", "SGP"), ("gestao de pessoa", "SGP"),
    ("bem-estar", "SGP"), ("atendimento e bem", "SGP"),
    ("orcament", "SAD"), ("financ", "SAD"), ("contabilidade e fin", "SAD"),
    ("arrecad", "SAD"), ("compras", "SAD"), ("servicos gerais", "SAD"),
    ("licitac", "SAD"), ("patrimonio", "SAD"), ("contrato", "SAD"),
    ("dados", "SDI"), ("inteligencia", "SDI"), ("estudos e pesquisa", "SDI"),
    ("analise de negocio", "SDI"), ("planejamento", "SPL"),
    ("projeto", "SPL"), ("informac", "SGE"), ("colegiado", "SGE"),
]


def _na(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _ensure_tables(c):
    c.execute("""CREATE TABLE IF NOT EXISTS agentes_unidade(
        sigla TEXT PRIMARY KEY, nome TEXT, tipo TEXT, dossie TEXT,
        n_recursos INTEGER, ai_feito INTEGER DEFAULT 0, atualizado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS agentes_gerencia(
        sigla TEXT PRIMARY KEY, nome TEXT, si_pai TEXT, chefe_atual TEXT,
        chefe_desde TEXT, historico TEXT, atualizado_em TEXT)""")


# ------------------------------------------------------------------ pessoal
def _titulares(sigla):
    """Historico de superintendentes da SI (nome, tipo, data), mais recente 1o."""
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
    return out


def _si_de_gerencia(sigla, unidade):
    txt = _na((unidade or "") + " " + (sigla or ""))
    for kw, si in GER_KW:
        if kw in txt:
            return si
    return "?"


def gerencias():
    """Arvore deterministica das gerencias -> conduta.db::agentes_gerencia."""
    if not (os.path.exists(PESSOAL) and os.path.exists(CONDUTA)):
        print("pessoal.db/conduta.db ausente."); return
    p = sqlite3.connect(PESSOAL)
    rows = p.execute(
        "SELECT servidor_nome, sigla, unidade, tipo, data_efeito, data_ato_iso "
        "FROM movimentos WHERE funcao LIKE '%erente%' AND sigla<>'' "
        "ORDER BY COALESCE(data_efeito,data_ato_iso) DESC").fetchall()
    p.close()
    ger = {}
    for nome, sig, uni, tipo, de, da in rows:
        s = (sig or "").upper()
        if not s:
            continue
        g = ger.setdefault(s, {"nome": uni or s, "hist": []})
        if uni and len(uni) > len(g["nome"]):
            g["nome"] = uni
        g["hist"].append({"nome": (nome or "").strip(), "tipo": tipo,
                          "data": de or da or ""})
    c = sqlite3.connect(CONDUTA)
    _ensure_tables(c)
    c.execute("DELETE FROM agentes_gerencia")
    n = 0
    for s, g in sorted(ger.items()):
        # chefe atual = designacao/nomeacao mais recente sem exoneracao posterior
        chefe, desde = "", ""
        for h in g["hist"]:
            if _na(h["tipo"]) in ("designacao", "nomeacao"):
                chefe, desde = h["nome"], h["data"]
                break
        si = _si_de_gerencia(s, g["nome"])
        c.execute("INSERT INTO agentes_gerencia(sigla,nome,si_pai,chefe_atual,"
                  "chefe_desde,historico,atualizado_em) VALUES(?,?,?,?,?,?,"
                  "datetime('now')) ON CONFLICT(sigla) DO UPDATE SET nome="
                  "excluded.nome, si_pai=excluded.si_pai, chefe_atual="
                  "excluded.chefe_atual, chefe_desde=excluded.chefe_desde, "
                  "historico=excluded.historico, atualizado_em="
                  "excluded.atualizado_em",
                  (s, g["nome"], si, chefe, desde,
                   json.dumps(g["hist"][:12], ensure_ascii=False)))
        n += 1
    c.commit()
    c.close()
    print(f"[gerencias] {n} gerencias -> agentes_gerencia")


# ------------------------------------------------------------------ recursal
def _recursos_por_si():
    """{SI: [descricoes de recursos contra decisao da SI]} a partir do texto."""
    out = collections.defaultdict(list)
    if not os.path.exists(DECISOES):
        return out
    c = sqlite3.connect(DECISOES)
    p1 = re.compile(r"decis[ao~][eo]s?\s+d[ao]s?\s+(?:superintendencia[^.]{0,80}?)?"
                    r"\bs([a-z]{2})\b")
    p2 = re.compile(r"contra\s+(?:a\s+)?decis[ao~][eo]s?[^.]{0,90}?\bs([a-z]{2})\b")
    val = {_na(s)[1:] for s in SIS}  # 'ep','in',...
    for em, de, di in c.execute(
            "SELECT ementa, descricao, data_iso FROM decisoes ORDER BY data_iso DESC"):
        t = _na((em or "") + " " + (de or ""))
        if not re.search(r"\brecurs|recorr", t):
            continue
        sigs = {("S" + s.upper()) for s in (set(p1.findall(t)) | set(p2.findall(t)))
                if s in val}
        for s in sigs:
            if len(out[s]) < 30:
                txt = (de or em or "").strip()
                out[s].append({"data": di, "txt": txt[:650]})
    c.close()
    return out


# ------------------------------------------------------------------ packets
def packets(outdir):
    os.makedirs(outdir, exist_ok=True)
    rec = _recursos_por_si()
    # contagem total de recursos por SI (nao so amostra):
    tot = collections.Counter()
    if os.path.exists(DECISOES):
        c = sqlite3.connect(DECISOES)
        p1 = re.compile(r"decis[ao~][eo]s?\s+d[ao]s?\s+(?:superintendencia[^.]{0,80}?)?"
                        r"\bs([a-z]{2})\b")
        p2 = re.compile(r"contra\s+(?:a\s+)?decis[ao~][eo]s?[^.]{0,90}?\bs([a-z]{2})\b")
        val = {_na(s)[1:] for s in SIS}
        for em, de in c.execute("SELECT ementa, descricao FROM decisoes"):
            t = _na((em or "") + " " + (de or ""))
            if not re.search(r"\brecurs|recorr", t):
                continue
            for s in {("S" + x.upper()) for x in
                      (set(p1.findall(t)) | set(p2.findall(t))) if x in val}:
                tot[s] += 1
        c.close()

    ger_by_si = _gerencias_por_si()
    gerados = 0
    for sig in SIS_IA:
        nome, tipo, comp = SIS[sig]
        pkt = {
            "sigla": sig, "nome": nome, "tipo": tipo, "competencia": comp,
            "titulares": _titulares(sig)[:8],
            "gerencias": ger_by_si.get(sig, []),
            "recursos_total": tot.get(sig, 0),
            "recursos_amostra": rec.get(sig, [])[:25],
            "sancionadora": tipo == "sancionadora",
        }
        with open(os.path.join(outdir, f"in_{sig}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(pkt, f, ensure_ascii=False, indent=1)
        gerados += 1
        print(f"  {sig}: recursos~{tot.get(sig,0)} | {len(pkt['titulares'])} "
              f"titulares | {len(pkt['gerencias'])} gerencias")
    # CTC packet
    ctc = _ctc_packet()
    with open(os.path.join(outdir, "in_CTC.json"), "w", encoding="utf-8") as f:
        json.dump(ctc, f, ensure_ascii=False, indent=1)
    gerados += 1
    print(f"  CTC: {ctc['total']} TCs ({ctc['situacao']}) | "
          f"{len(ctc['amostra_decisoes'])} decisoes de amostra")
    print(f"[packets] {gerados} pacotes em {outdir}")


def _gerencias_por_si():
    out = collections.defaultdict(list)
    if not os.path.exists(CONDUTA):
        return out
    c = sqlite3.connect(CONDUTA)
    _ensure_tables(c)
    for s, nome, si, chefe in c.execute(
            "SELECT sigla,nome,si_pai,chefe_atual FROM agentes_gerencia"):
        out[si].append({"sigla": s, "nome": nome, "chefe": chefe})
    c.close()
    return out


def _ctc_packet():
    pkt = {"nome": "Comitê de Termo de Compromisso", "total": 0,
           "situacao": {}, "por_ano": {}, "amostra_decisoes": []}
    if os.path.exists(TERMOS):
        c = sqlite3.connect(TERMOS)
        pkt["total"] = c.execute("SELECT COUNT(*) FROM termos").fetchone()[0]
        pkt["situacao"] = dict(c.execute(
            "SELECT situacao,COUNT(*) FROM termos GROUP BY situacao"))
        pkt["por_ano"] = dict(c.execute(
            "SELECT substr(data_decisao_iso,1,4),COUNT(*) FROM termos WHERE "
            "data_decisao_iso<>'' GROUP BY 1 ORDER BY 1"))
        c.close()
    if os.path.exists(DECISOES):
        c = sqlite3.connect(DECISOES)
        rows = c.execute(
            "SELECT descricao, ementa, data_iso FROM decisoes WHERE "
            "LOWER(descricao) LIKE '%proposta de termo de compromisso%' OR "
            "LOWER(ementa) LIKE '%termo de compromisso%' ORDER BY data_iso DESC "
            "LIMIT 22").fetchall()
        for de, em, di in rows:
            pkt["amostra_decisoes"].append(
                {"data": di, "txt": (de or em or "").strip()[:650]})
        c.close()
    return pkt


# ------------------------------------------------------------------ cards adm
def cards():
    """Cards deterministicos das SI administrativas (sem IA)."""
    if not os.path.exists(CONDUTA):
        print("conduta.db ausente."); return
    ger_by_si = _gerencias_por_si()
    c = sqlite3.connect(CONDUTA)
    _ensure_tables(c)
    n = 0
    for sig, (nome, tipo, comp) in SIS.items():
        if sig in SIS_IA:
            continue
        dossie = {"competencia": comp,
                  "titulares": _titulares(sig)[:6],
                  "gerencias": ger_by_si.get(sig, []),
                  "_card": True}
        c.execute("INSERT INTO agentes_unidade(sigla,nome,tipo,dossie,"
                  "n_recursos,ai_feito,atualizado_em) VALUES(?,?,?,?,0,0,"
                  "datetime('now')) ON CONFLICT(sigla) DO UPDATE SET "
                  "nome=excluded.nome, tipo=excluded.tipo, dossie=excluded.dossie,"
                  " atualizado_em=excluded.atualizado_em WHERE "
                  "agentes_unidade.ai_feito=0",
                  (sig, nome, tipo, json.dumps(dossie, ensure_ascii=False)))
        n += 1
    c.commit()
    c.close()
    print(f"[cards] {n} cards administrativos -> agentes_unidade")


def aplicar(paths):
    if not os.path.exists(CONDUTA):
        print("conduta.db ausente."); return
    c = sqlite3.connect(CONDUTA)
    _ensure_tables(c)
    ap = 0
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ! {p}: {e}"); continue
        for d in (data if isinstance(data, list) else [data]):
            sig = (d.get("sigla") or "").upper()
            nome = d.get("nome") or (SIS.get(sig, ("", "", ""))[0]
                                     if sig in SIS else d.get("nome"))
            tipo = d.get("tipo") or (SIS.get(sig, ("", "", ""))[1]
                                     if sig in SIS else "")
            dossie = d.get("dossie", d)
            c.execute("INSERT INTO agentes_unidade(sigla,nome,tipo,dossie,"
                      "n_recursos,ai_feito,atualizado_em) VALUES(?,?,?,?,?,1,"
                      "datetime('now')) ON CONFLICT(sigla) DO UPDATE SET "
                      "nome=excluded.nome, tipo=excluded.tipo, "
                      "dossie=excluded.dossie, n_recursos=excluded.n_recursos, "
                      "ai_feito=1, atualizado_em=excluded.atualizado_em",
                      (sig, nome, tipo,
                       json.dumps(dossie, ensure_ascii=False),
                       d.get("recursos_total") or d.get("n_recursos") or 0))
            ap += 1
    c.commit()
    n = c.execute("SELECT COUNT(*) FROM agentes_unidade WHERE ai_feito=1"
                  ).fetchone()[0]
    c.close()
    print(f"[aplicar] {ap} dossies aplicados. agentes_unidade(IA): {n}")


def status():
    if not os.path.exists(CONDUTA):
        print("conduta.db ausente."); return
    c = sqlite3.connect(CONDUTA)
    _ensure_tables(c)
    nu = c.execute("SELECT COUNT(*) FROM agentes_unidade").fetchone()[0]
    ni = c.execute("SELECT COUNT(*) FROM agentes_unidade WHERE ai_feito=1"
                   ).fetchone()[0]
    ng = c.execute("SELECT COUNT(*) FROM agentes_gerencia").fetchone()[0]
    print(f"  unidades: {nu} ({ni} com dossie IA) | gerencias: {ng}")
    for sig, nome, tipo, air, nr in c.execute(
            "SELECT sigla,nome,tipo,ai_feito,n_recursos FROM agentes_unidade "
            "ORDER BY ai_feito DESC, n_recursos DESC"):
        print(f"    {sig:5} ia={air} rec={nr:5} {tipo:12} {nome[:40]}")
    c.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "gerencias":
        gerencias()
    elif cmd == "packets":
        packets(sys.argv[2] if len(sys.argv) > 2
                else os.path.join(DIR, "unidade_packets"))
    elif cmd == "cards":
        cards()
    elif cmd == "aplicar":
        ps = []
        for a in sys.argv[2:]:
            ps.extend(glob.glob(a))
        aplicar(ps)
    else:
        status()
