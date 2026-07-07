# -*- coding: utf-8 -*-
"""Visualizador das Audiencias Particulares da CVM."""
import os
import re
import sqlite3
import unicodedata
import datetime as dt

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import noticias_cruzamento as _nx

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiencias.db"))
URL_BASE = "https://sistemas.cvm.gov.br/aplicacoes/cap/consulta/audiencia.asp?id="
PAS_DB_PATH = os.environ.get("PAS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "processos.db"))
URL_PAS = "https://sistemas.cvm.gov.br/asp/cvmwww/inqueritos/DetPasAndamentoSSI.asp?idProc="
ATAS_DB_PATH = os.environ.get("ATAS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "atas.db"))
INF_DB_PATH = os.environ.get("INF_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "informativos.db"))
TERMOS_DB_PATH = os.environ.get("TERMOS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "termos.db"))
JULGAR_DB_PATH = os.environ.get("JULGAR_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "julgar.db"))
QUEM_DB_PATH = os.environ.get("QUEM_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "quem.db"))
PAUTAS_DB_PATH = os.environ.get("PAUTAS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pautas.db"))
NOTICIAS_DB_PATH = os.environ.get("NOTICIAS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "noticias.db"))
DECISOES_DB_PATH = os.environ.get("DECISOES_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "decisoes.db"))

st.set_page_config(page_title="Motumbo CVM", page_icon="🏛️", layout="wide")


# --------------------------------------------------------------------------
# Login por senha unica (definida em st.secrets["app_password"])
# --------------------------------------------------------------------------
def autenticado() -> bool:
    try:
        senha_certa = st.secrets.get("app_password", "")
    except Exception:
        senha_certa = ""
    senha_certa = senha_certa or os.environ.get("APP_PASSWORD", "")
    if not senha_certa:
        st.error("Senha do app não configurada (defina `app_password` em Secrets).")
        return False
    if st.session_state.get("auth"):
        return True

    def _conferir():
        st.session_state["auth"] = st.session_state.get("pw", "") == senha_certa

    st.title("🏛️ Motumbo CVM")
    st.text_input("Senha de acesso", type="password", key="pw", on_change=_conferir)
    if st.session_state.get("auth") is False:
        st.error("Senha incorreta.")
    st.caption("Ferramenta interna de consulta a dados públicos da CVM.")
    return False


# --------------------------------------------------------------------------
# Dados
# --------------------------------------------------------------------------
@st.cache_data(ttl=300)
def carregar() -> pd.DataFrame:
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM audiencias WHERE estado='valido'", con)
    con.close()
    df["data_dt"] = pd.to_datetime(df["data_iso"], errors="coerce")
    # sigla = parte antes do " - " (ex.: "PTE - Presidência" -> "PTE")
    df["sigla"] = df["componente"].fillna("").str.split(" - ").str[0].str.strip()
    df["link"] = URL_BASE + df["id"].astype(str)
    return df


@st.cache_data(ttl=300)
def indices():
    """Listas distintas de nomes e assuntos, para o autocomplete."""
    df = carregar()
    nomes = set(df["solicitante_nome"].dropna())
    for a in df["acompanhantes"].dropna():
        nomes |= {x.strip() for x in a.split("|") if x.strip()}
    nomes |= set(df["componente"].dropna())
    nomes = sorted(n for n in nomes if n and n.strip())
    assuntos = sorted(a for a in set(df["assunto"].dropna()) if a and a.strip())
    return nomes, assuntos


# Onde a pessoa pode aparecer:
#  part = participante externo (solicitante + acompanhantes)
#  dir  = diretor/órgão da CVM que recebeu (campo "componente")
#  obs  = texto livre das observações
#  any  = qualquer um desses campos
ESCOPOS = {
    "part": ["solicitante_nome", "acompanhantes"],
    "dir": ["componente"],
    "obs": ["observacoes"],
    "any": ["solicitante_nome", "acompanhantes", "componente", "observacoes"],
}


def _texto_escopo(df, onde):
    cols = ESCOPOS[onde]
    s = df[cols[0]].fillna("")
    for c in cols[1:]:
        s = s + " ||| " + df[c].fillna("")
    return s.str.lower()


def nomes_no_escopo(df, onde, texto):
    """Nomes distintos, no escopo, que contêm o texto — para o usuário escolher variantes."""
    nomes = set()
    if onde in ("part", "any"):
        nomes |= set(df["solicitante_nome"].dropna())
        for a in df["acompanhantes"].dropna():
            nomes |= {x.strip() for x in a.split("|") if x.strip()}
    if onde in ("dir", "any"):
        nomes |= set(df["componente"].dropna())
    t = texto.strip().lower()
    return sorted(n for n in nomes if n and t in n.lower())


def parse_busca(q):
    """Interpreta a consulta.
    - vírgula separa alternativas (OU)
    - "entre aspas" = frase exata
    - várias palavras soltas = todas juntas (E)
    Retorna lista de grupos: ("frase", texto) ou ("e", [palavras]).
    """
    grupos = []
    for parte in str(q).split(","):
        parte = parte.strip()
        if not parte:
            continue
        m = re.fullmatch(r'"(.*)"', parte)
        if m:
            frase = m.group(1).strip().lower()
            if frase:
                grupos.append(("frase", frase))
        else:
            palavras = [w.lower() for w in parte.split() if w]
            if palavras:
                grupos.append(("e", palavras))
    return grupos


def match_busca(df, cols, q):
    """True p/ linhas em que ALGUM campo de `cols` satisfaz ALGUM grupo da busca."""
    grupos = parse_busca(q)
    if not grupos:
        return pd.Series(True, index=df.index)
    colunas = [df[c].fillna("").str.lower() for c in cols]
    total = pd.Series(False, index=df.index)
    for tipo, val in grupos:
        gmatch = pd.Series(False, index=df.index)
        for col in colunas:
            if tipo == "frase":
                gmatch |= col.str.contains(re.escape(val), na=False)
            else:  # todas as palavras no mesmo campo
                campo = pd.Series(True, index=df.index)
                for w in val:
                    campo &= col.str.contains(re.escape(w), na=False)
                gmatch |= campo
        total |= gmatch
    return total


CAMPOS_PESSOA = ["solicitante_nome", "acompanhantes", "componente", "observacoes"]


def filtrar(df, de, ate, assunto_q, siglas, excluir_siglas, pessoa_q, status_sel):
    m = pd.Series(True, index=df.index)
    if de:
        m &= df["data_dt"] >= pd.Timestamp(de)
    if ate:
        m &= df["data_dt"] <= pd.Timestamp(ate)
    if assunto_q and assunto_q.strip():
        m &= match_busca(df, ["assunto"], assunto_q)
    if siglas:
        m &= df["sigla"].isin(siglas)
    if excluir_siglas:
        m &= ~df["sigla"].isin(excluir_siglas)
    if status_sel:
        m &= df["status"].isin(status_sel)
    if pessoa_q and pessoa_q.strip():
        m &= match_busca(df, CAMPOS_PESSOA, pessoa_q)
    return df[m]


def _esc(v):
    s = str(v if v is not None else "").replace("|", "／").strip()
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


CSS_CVM = """
body { margin:0; background:#fff; color:#000; font-family:"Open Sans",Arial,sans-serif; }
#width { width:100%; max-width:1024px; margin:0 auto; box-sizing:border-box; }
h2 { text-align:center; padding:5px; font-family:Arial; font-size:1.3rem; }
table { border-collapse:collapse; margin:10px 0; width:100%; }
.cards { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:0 20px; }
.acomp { width:100%; }
td { padding:10px; border:1px solid black; vertical-align:top; }
.header { font-weight:bold; background:#eee; }
.upperHeader { background:#346DBC; color:white; font-weight:600; }
"""


def _card(titulo, nome, empresa, cargo):
    return (f'<table class="acomp">'
            f'<tr><td colspan="2" class="upperHeader">{titulo}</td></tr>'
            f'<tr><td class="header">Nome</td><td>{nome}</td></tr>'
            f'<tr><td class="header">Empresa</td><td>{empresa}</td></tr>'
            f'<tr><td class="header">Cargo</td><td>{cargo}</td></tr>'
            f'</table>')


def conteudo_detalhe(row):
    """Reproduz a página da CVM (mesmo HTML/CSS) com os dados da audiência."""
    st.link_button("Abrir no site da CVM ↗", row["link"])

    e = _esc
    acs = [a.strip() for a in str(row["acompanhantes"] or "").split(" | ") if a.strip()]
    cards = [_card("SOLICITANTE", e(row["solicitante_nome"]),
                   e(row.get("solicitante_empresa", "")), e(row.get("solicitante_cargo", "")))]
    for a in acs:
        cards.append(_card("ACOMPANHANTE", e(a), "", ""))

    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<style>{CSS_CVM}</style></head><body>'
        f'<h2>AUDIÊNCIA PARTICULAR Nº {row["id"]}</h2>'
        '<div id="width"><table>'
        '<tr class="header">'
        '<td class="upperHeader">COMPONENTE ORGANIZACIONAL </td>'
        f'<td>{e(row["componente"])}</td>'
        '<td class="upperHeader">DATA DE AUDIÊNCIA</td>'
        f'<td>{e(row["data"])}</td>'
        '<td class="upperHeader">HORA DE AUDIÊNCIA</td>'
        f'<td>{e(row["hora"])}</td></tr>'
        f'<tr><td class="header">Local</td><td colspan="5">{e(row["local"])}</td></tr>'
        f'<tr><td class="header">Assunto</td><td colspan="5">{e(row["assunto"])}</td></tr>'
        f'<tr><td class="header">Urgente</td><td colspan="5">{e(row["urgente"])}</td></tr>'
        f'<tr><td class="header">Observações</td><td colspan="5">{e(row["observacoes"])}</td></tr>'
        f'<tr><td class="header">Status:</td><td colspan="5">{e(row["status"])}</td></tr>'
        '</table>'
        + '<div class="cards">' + "".join(cards) + '</div>'
        + '</div></body></html>'
    )
    n_linhas_cards = (len(cards) + 1) // 2
    components.html(doc, height=110 + 300 + n_linhas_cards * 200, scrolling=True)


@st.dialog("Audiência Particular", width="large")
def dialog_detalhe(row):
    conteudo_detalhe(row)


# --------------------------------------------------------------------------
# Processos Sancionadores
# --------------------------------------------------------------------------
@st.cache_data(ttl=300)
def carregar_pas():
    if not os.path.exists(PAS_DB_PATH):
        return None, None
    con = sqlite3.connect(PAS_DB_PATH)
    proc = pd.read_sql("SELECT * FROM processos WHERE estado='valido'", con)
    acus = pd.read_sql("SELECT * FROM acusados", con)
    con.close()
    proc["data_dt"] = pd.to_datetime(proc["data_iso"], errors="coerce")
    proc["link"] = URL_PAS + proc["idproc"].astype(str)
    return proc, acus


@st.dialog("Processo Sancionador", width="large")
def dialog_processo(row, acus):
    st.link_button("Abrir no site da CVM ↗", row["link"])
    e = _esc
    linhas = [("Número", row["numero"]), ("Data de abertura", row["data_abertura"]),
              ("Encarregado", row["encarregado"]), ("Fase atual", row["fase"]),
              ("Subfase atual", row["subfase"]), ("Local atual", row["local_atual"]),
              ("Objeto", row["objeto"]), ("Ementa", row["ementa"])]
    corpo = "".join(
        f'<tr><td class="upperHeader" style="white-space:nowrap">{k}</td>'
        f'<td>{e(v)}</td></tr>' for k, v in linhas)
    ac = acus[acus["idproc"] == row["idproc"]] if acus is not None else []
    ac_rows = "".join(
        f'<tr><td>{e(a["nome"])}</td><td>{e(a["situacao"])}</td>'
        f'<td>{e(a["data"])}</td><td>{e(a["historico"])}</td></tr>'
        for _, a in ac.iterrows()) if len(ac) else \
        '<tr><td colspan="4">— sem acusados —</td></tr>'
    # histórico de relatoria cruzado dos Informativos do Colegiado
    hist = historico_relator(_norm_proc(row["numero"]))
    if hist:
        rel_rows = "".join(
            f'<tr><td>{e(d)}</td><td>{e(rel)}</td><td>{e(ev)}</td>'
            f'<td>Informativo nº {e(inf)}</td></tr>' for d, rel, ev, inf in hist)
        atual = hist[-1]
        rel_html = (
            f'<h3>Relatoria (Informativos do Colegiado)</h3>'
            f'<p style="font-size:13px">Relator na última atribuição registrada: '
            f'<b>{e(atual[1])}</b> (em {e(atual[0])}).</p>'
            '<table><tr class="header"><td>Data</td><td>Relator</td>'
            f'<td>Evento</td><td>Fonte</td></tr>{rel_rows}</table>')
    else:
        rel_html = ('<h3>Relatoria (Informativos do Colegiado)</h3>'
                    '<p style="font-size:13px">— ainda sem relator identificado nos '
                    'informativos para este processo —</p>')
    # Termo de Compromisso (portais de TC)
    tc = carregar_termos()
    tc_rows = ""
    if tc is not None:
        tcm = tc[tc["proc_norm"] == _norm_proc(row["numero"])]
        for _, x in tcm.iterrows():
            tc_rows += (f'<tr><td>{e(x["situacao"])}</td><td>{e(x["data_decisao"])}</td>'
                        f'<td>{e(x["data_assinatura"])}</td><td>{e(x["partes"])}</td></tr>')
    tc_html = (
        '<h3>Termo de Compromisso</h3>'
        + ('<table><tr class="header"><td>Situação</td><td>Decisão</td>'
           f'<td>Assinatura</td><td>Compromitentes/Proponentes</td></tr>{tc_rows}</table>'
           if tc_rows else
           '<p style="font-size:13px">— sem termo de compromisso registrado —</p>'))
    # Despachos/audiências relacionados (cruzamento por nº, tolera parcial)
    desp = despachos_do_processo(row["numero"])
    if desp:
        dr = "".join(
            f'<tr><td style="white-space:nowrap">{e(d["data"])}</td>'
            f'<td>{e(d["componente"])}</td><td>{e(d["solicitante"])}</td>'
            f'<td>{e(d["assunto"])}</td>'
            f'<td><a href="{e(d["link"])}" target="_blank">abrir ↗</a></td></tr>'
            for d in desp[:40])
        desp_html = (
            f'<h3>Despachos/audiências relacionados ({len(desp)})</h3>'
            '<p style="font-size:12px">Cruzados pelo número do processo (tolera '
            'número parcial citado no despacho). Clique em "abrir" para ver a '
            'audiência no site da CVM.</p>'
            '<table><tr class="header"><td>Data</td><td>Componente</td>'
            f'<td>Solicitante</td><td>Assunto</td><td>Link</td></tr>{dr}</table>')
    else:
        desp_html = ('<h3>Despachos/audiências relacionados</h3>'
                     '<p style="font-size:13px">— nenhum despacho relacionado —</p>')
    # Decisões do Colegiado (cruzamento por nº do processo)
    decs = decisoes_do_processo(_norm_proc(row["numero"]))
    if decs:
        dcr = "".join(
            f'<tr><td style="white-space:nowrap">{e(data)}</td><td>{e(tipo)}</td>'
            f'<td>{e(ementa)}</td>'
            f'<td><a href="{e(link)}" target="_blank">abrir ↗</a></td></tr>'
            for data, tipo, ementa, link in decs[:40])
        dec_html = (
            f'<h3>Decisões do Colegiado ({len(decs)})</h3>'
            '<table><tr class="header"><td>Data</td><td>Tipo</td>'
            f'<td>Ementa</td><td>Link</td></tr>{dcr}</table>')
    else:
        dec_html = ('<h3>Decisões do Colegiado</h3>'
                    '<p style="font-size:13px">— nenhuma decisão do Colegiado '
                    'relacionada (a base ainda cresce) —</p>')
    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<style>{CSS_CVM} td{{font-size:13px}} h3{{font-family:Arial;font-size:1rem}}'
        '</style></head><body><div id="width">'
        f'<h2>Processo Sancionador nº {e(row["numero"])}</h2>'
        f'<table>{corpo}</table>'
        f'<h3>Acusados ({len(ac)})</h3>'
        '<table><tr class="header"><td>Nome/Razão social</td><td>Situação</td>'
        f'<td>Data</td><td>Histórico de situações</td></tr>{ac_rows}</table>'
        f'{rel_html}{tc_html}{dec_html}{desp_html}</div></body></html>')
    components.html(doc, height=620 + len(ac) * 70 + len(hist) * 34
                    + len(decs[:40]) * 30
                    + len(desp[:40]) * 34, scrolling=True)
    nomes_ac = [a["nome"] for _, a in ac.iterrows()] if len(ac) else []
    _bloco_noticias_md(_norm_proc(row["numero"]), nomes=nomes_ac)


def render_processos():
    aba1, aba2, aba3 = st.tabs(
        ["⚖️ Processos", "✅ Julgados", "📊 Prazos por relator"])
    with aba1:
        render_processos_lista()
    with aba2:
        render_julgados_lista()
    with aba3:
        render_prazos()


def render_julgados_lista():
    """Lista TODOS os processos julgados pelo Colegiado (planilha oficial),
    com o relator do julgamento — inclusive ex-diretores. Marca quem ainda
    está no Colegiado atual, para cruzar com a aba de Prazos."""
    julg = carregar_julgados()
    _, meta = carregar_julgar()
    if julg is None or len(julg) == 0:
        st.info("⏳ A base de processos julgados ainda não foi carregada.")
        return
    df = julg.copy()
    df["_ano"] = df["data_julg"].astype(str).str.slice(6, 10)
    df["_membro"] = df["relator_nome"].map(
        lambda n: (relator_no_colegiado(n) or {}).get("nome"))
    df["Colegiado atual"] = df["_membro"].map(lambda x: "✓" if x else "")
    fonte = meta.get("fonte_julgados", "")
    st.caption(
        (f"{len(df):,} processos julgados pelo Colegiado".replace(",", "."))
        + (f" · fonte: {fonte}" if fonte else "")
        + " · o relator é o do **julgamento** (pode ser ex-diretor).")

    with st.expander("🔎 Filtros", expanded=True):
        c1, c2, c3 = st.columns(3)
        q_txt = c1.text_input("Nº do processo contém", key="jg_txt")
        anos = sorted((a for a in df["_ano"].unique() if a), reverse=True)
        f_ano = c2.multiselect("Ano do julgamento", anos, key="jg_ano")
        tipos = sorted(t for t in df["tipo"].dropna().unique() if t)
        f_tipo = c3.multiselect("Tipo", tipos, key="jg_tipo")
        c4, c5 = st.columns([3, 2])
        rels = sorted(r for r in df["relator_nome"].dropna().unique() if r)
        f_rel = c4.multiselect("Relator (no julgamento)", rels, key="jg_rel")
        so_atual = c5.checkbox("Só relatores do Colegiado atual", key="jg_atual")

    m = pd.Series(True, index=df.index)
    if q_txt.strip():
        m &= df["processo"].str.contains(q_txt, case=False, na=False)
    if f_ano:
        m &= df["_ano"].isin(f_ano)
    if f_tipo:
        m &= df["tipo"].isin(f_tipo)
    if f_rel:
        m &= df["relator_nome"].isin(f_rel)
    if so_atual:
        m &= df["_membro"].notna()

    res = df[m].copy()
    res["_d"] = pd.to_datetime(res["data_julg"], dayfirst=True, errors="coerce")
    res = res.sort_values("_d", ascending=False).reset_index(drop=True)
    st.metric("Julgados encontrados", f"{len(res):,}".replace(",", "."))

    # resumo por relator (nome canônico do board quando for do Colegiado atual)
    if len(res):
        chave = res["_membro"].fillna(res["relator_nome"])
        resumo = (chave.value_counts().rename_axis("Relator")
                  .reset_index(name="Julgados"))
        resumo["Colegiado atual"] = resumo["Relator"].map(
            lambda n: "✓" if any(res.loc[chave == n, "_membro"].notna()) else "")
        st.markdown("**Julgados por relator:**")
        st.dataframe(resumo, use_container_width=True, hide_index=True)

    cols = ["data_julg", "relator_nome", "processo", "tipo", "rito", "sup",
            "Colegiado atual"]
    show = res[[c for c in cols if c in res.columns]].rename(columns={
        "data_julg": "Julgado em", "relator_nome": "Relator (julgamento)",
        "processo": "Processo", "tipo": "Tipo", "rito": "Rito",
        "sup": "Superintendência"})
    tabela(show, datas=["Julgado em"], use_container_width=True,
           hide_index=True, height=460)
    st.download_button(
        "⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
        file_name="processos_julgados_cvm.csv", mime="text/csv")


def _to_date(s):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(s or ""))
    return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _dias_desde(s):
    """Nº de dias de uma data DD/MM/AAAA até hoje (int, ordenável). None se inválida."""
    d = _to_date(s)
    if not d:
        return None
    n = (dt.date.today() - d).days
    return n if n >= 0 else None


def tabela(show, *, datas=(), dias=(), column_config=None, **kw):
    """st.dataframe com ORDENAÇÃO temporal correta: colunas em `datas` viram
    datetime (ordena cronologicamente, exibe DD/MM/AAAA) e colunas em `dias` viram
    número (exibe 'N dias', ordena pelo total). Evita o bug de ordenar texto."""
    show = show.copy()
    cfg = dict(column_config or {})
    for c in datas:
        if c in show.columns:
            show[c] = pd.to_datetime(show[c].astype(str).str.strip(),
                                     dayfirst=True, errors="coerce")
            cfg.setdefault(c, st.column_config.DateColumn(c, format="DD/MM/YYYY"))
    for c in dias:
        if c in show.columns:
            cfg.setdefault(c, st.column_config.NumberColumn(c, format="%d dias"))
    return st.dataframe(show, column_config=cfg, **kw)


@st.cache_data(ttl=300)
def estatisticas_prazos():
    """Estoque (A Julgar) e julgados por relator — SÓ do Colegiado atual, com o
    relator vindo dos Informativos (a planilha 'A Julgar' é usada só como lista de
    estoque; o relator/datas dela são pouco confiáveis)."""
    jul, _ = carregar_julgar()
    julg = carregar_julgados()
    rel_df = carregar_relatores()
    mrel = mapa_relator_atual()
    aber = _mapa_abertura()
    hoje = dt.date.today()
    eventos = {}
    if rel_df is not None:
        for _, r in rel_df.sort_values("data_iso").iterrows():
            d = _to_date(r["data"])
            if d:
                eventos.setdefault(r["proc_norm"], []).append(d)
    # base de sancionadores (para o gap-check)
    proc_pas, _ = carregar_pas()
    base_pn = set()
    if proc_pas is not None:
        base_pn = {_norm_proc(n) for n in proc_pas["numero"]} - {""}

    estoque, fora_base = [], []
    if jul is not None:
        for _, r in jul.iterrows():
            pn = r["proc_norm"]
            info = mrel.get(pn)
            # relator ATUAL dos informativos; se não houver, cai p/ a planilha
            nome_rel = pessoa_de_sigla(info[0], info[1]) if info \
                else r["relator_nome"]
            # casa por sobrenome+primeiro nome com o Colegiado atual (robusto a
            # variações de grafia, ex.: "Otto Lobo" x nome completo do Presidente)
            membro = relator_no_colegiado(nome_rel)
            if not membro:
                continue
            # "relator desde": última distribuição registrada (informativos)
            desde = info[1] if info else r["dt_inicio"]
            d0 = _to_date(desde)
            estoque.append({
                "Relator": membro["nome"], "Processo": r["processo"],
                "Relator desde": desde,
                "Como relator há (dias)": (hoje - d0).days if d0 else None,
                "Abertura do processo": aber.get(pn, "—"), "proc_norm": pn})
            if pn and base_pn and pn not in base_pn:
                fora_base.append(r["processo"])
    julgados = []
    if julg is not None:
        for _, r in julg.iterrows():
            membro = relator_no_colegiado(r["relator_nome"])
            if not membro:
                continue
            dj = _to_date(r["data_julg"])
            evs = [d for d in eventos.get(r["proc_norm"], []) if not dj or d <= dj]
            drel = max(evs) if evs else (min(eventos.get(r["proc_norm"], []))
                                         if eventos.get(r["proc_norm"]) else None)
            julgados.append({
                "Relator": membro["nome"], "Processo": r["processo"],
                "Recebeu relatoria": drel.strftime("%d/%m/%Y") if drel else "—",
                "Julgado em": r["data_julg"],
                "Tempo até julgar (dias)": (dj - drel).days if (dj and drel) else None})
    return pd.DataFrame(estoque), pd.DataFrame(julgados), fora_base


def render_prazos():
    est, jug, fora_base = estatisticas_prazos()
    if (est is None or len(est) == 0) and (jug is None or len(jug) == 0):
        st.info("⏳ Sem dados de 'a julgar' / 'julgados' para as estatísticas ainda.")
        return
    st.caption("Prazos por relator do **Colegiado atual**. Estoque = lista da planilha "
               "'A Julgar'; o relator e as datas vêm dos **Informativos** (a planilha é "
               "usada só como lista de estoque). *Abertura* = da base de Sancionadores.")
    if fora_base:
        with st.expander(f"⚠️ {len(set(fora_base))} processo(s) no estoque que NÃO estão "
                         "na nossa base de Sancionadores (investigar)"):
            st.write(sorted(set(fora_base)))
    # visão geral por relator
    linhas = []
    relatores = sorted(set(list(est.get("Relator", [])) + list(jug.get("Relator", []))))
    for rel in relatores:
        e = est[est["Relator"] == rel] if len(est) else est
        j = jug[jug["Relator"] == rel] if len(jug) else jug
        idade = e["Como relator há (dias)"].dropna() if len(e) else pd.Series(dtype=float)
        tj = j["Tempo até julgar (dias)"].dropna() if len(j) else pd.Series(dtype=float)
        linhas.append({
            "Relator": rel, "Em estoque": len(e),
            "Há mais tempo com o relator (dias)": int(idade.max()) if len(idade) else 0,
            "Média no estoque (dias)": int(idade.mean()) if len(idade) else 0,
            "Julgados": len(j),
            "Tempo médio até julgar (dias)": int(tj.mean()) if len(tj) else 0})
    resumo = pd.DataFrame(linhas)
    st.markdown("#### 📊 Visão geral por relator (Colegiado atual)")
    st.dataframe(resumo, use_container_width=True, hide_index=True)
    if len(resumo):
        st.markdown("**Estoque (processos a julgar) por relator:**")
        st.bar_chart(resumo.set_index("Relator")["Em estoque"])

    st.divider()
    st.markdown("#### 🔎 Detalhe por relator")
    if not relatores:
        st.info("Nenhum relator do Colegiado atual com processos.")
        return
    sel = st.selectbox("Relator", relatores, key="prz_rel")
    e = est[est["Relator"] == sel].sort_values("Como relator há (dias)",
                                               ascending=False) if len(est) else est
    j = jug[jug["Relator"] == sel].sort_values("Julgado em",
                                               ascending=False) if len(jug) else jug
    c1, c2, c3 = st.columns(3)
    c1.metric("Em estoque (a julgar)", len(e))
    idade = e["Como relator há (dias)"].dropna() if len(e) else pd.Series(dtype=float)
    c2.metric("Há mais tempo com ele",
              f"{int(idade.max())} dias" if len(idade) else "—")
    tj = j["Tempo até julgar (dias)"].dropna() if len(j) else pd.Series(dtype=float)
    c3.metric("Tempo médio até julgar", f"{int(tj.mean())} dias" if len(tj) else "—")
    st.markdown("**📥 Estoque — o que está há mais tempo com o relator (topo = mais antigo):**")
    if len(e):
        tabela(e.drop(columns=["Relator", "proc_norm"]),
               datas=["Relator desde", "Abertura do processo"],
               dias=["Como relator há (dias)"], use_container_width=True, hide_index=True)
        st.bar_chart(e.set_index("Processo")["Como relator há (dias)"].head(20))
    else:
        st.info("Sem processos em estoque para este relator.")
    st.markdown("**✅ Julgados — quando recebeu a relatoria × quando julgou:**")
    if len(j):
        tabela(j.drop(columns=["Relator"]), datas=["Recebeu relatoria", "Julgado em"],
               dias=["Tempo até julgar (dias)"], use_container_width=True, hide_index=True)
    else:
        st.info("Sem julgados atribuídos a este relator na planilha de julgados.")


def render_processos_lista():
    proc, acus = carregar_pas()
    if proc is None or len(proc) == 0:
        st.info("⏳ A base de Processos Sancionadores ainda está sendo coletada. "
                "Volte em breve — ela cresce sozinha na nuvem.")
        return
    st.caption(f"{len(proc):,} processos • {len(acus):,} acusados na base"
               .replace(",", "."))

    # relator (pessoa) por processo — deduzido dos Informativos
    mapa_rel = mapa_relator_atual()
    proc = proc.copy()
    proc["_relator"] = proc["numero"].map(
        lambda n: pessoa_de_sigla(*mapa_rel.get(_norm_proc(n), ("", ""))[:2])
        if mapa_rel.get(_norm_proc(n)) else "")
    with st.expander("🔎 Filtros", expanded=True):
        c1, c2 = st.columns(2)
        q_txt = c1.text_input("Número ou objeto contém", key="p_txt")
        q_acus = c2.text_input("Acusado (nome)", key="p_acus",
                               placeholder="ex.: eduardo levy")
        c3, c4, c5 = st.columns(3)
        fases = sorted(f for f in proc["fase"].dropna().unique() if f)
        f_fase = c3.multiselect("Fase atual", fases, key="p_fase")
        encs = sorted(x for x in proc["encarregado"].dropna().unique() if x)
        f_enc = c4.multiselect("Encarregado (área)", encs, key="p_enc")
        rels = sorted(r for r in proc["_relator"].dropna().unique() if r)
        f_rel = c5.multiselect("Relator (informativos)", rels, key="p_rel",
                               help="Relator atual deduzido dos Informativos.")

    m = pd.Series(True, index=proc.index)
    if q_txt.strip():
        m &= (proc["numero"].str.contains(q_txt, case=False, na=False)
              | proc["objeto"].str.contains(q_txt, case=False, na=False)
              | proc["ementa"].str.contains(q_txt, case=False, na=False))
    if f_fase:
        m &= proc["fase"].isin(f_fase)
    if f_enc:
        m &= proc["encarregado"].isin(f_enc)
    if f_rel:
        m &= proc["_relator"].isin(f_rel)
    if q_acus.strip():
        col = acus["nome"].fillna("").str.lower()
        mk = pd.Series(True, index=acus.index)
        for w in q_acus.lower().split():
            mk &= col.str.contains(re.escape(w), na=False)
        m &= proc["idproc"].isin(set(acus[mk]["idproc"]))

    res = proc[m].sort_values("idproc", ascending=False).reset_index(drop=True)
    m_tc = mapa_tc()
    res["relator_atual"] = res["numero"].map(
        lambda n: mapa_rel.get(_norm_proc(n), ("",))[0])
    res["tc"] = res["numero"].map(lambda n: m_tc.get(_norm_proc(n), ""))
    st.metric("Processos encontrados", f"{len(res):,}".replace(",", "."))
    ncruz = int((res["relator_atual"] != "").sum())
    if len(mapa_rel):
        st.caption(f"🧭 Relator cruzado dos Informativos em {ncruz} dos {len(res)} "
                   "processos · TC cruzado dos portais de Termos de Compromisso · "
                   "despachos relacionados aparecem no detalhe (a cobertura cresce "
                   "conforme as bases avançam).")

    cols = ["numero", "data_abertura", "fase", "encarregado", "relator_atual",
            "tc", "acusados", "link"]
    show = res[cols].rename(columns={
        "numero": "Processo", "data_abertura": "Abertura", "fase": "Fase",
        "encarregado": "Encarregado", "relator_atual": "Relator (inform.)",
        "tc": "Termo Compr.", "acusados": "Acusados", "link": "Link"})
    st.caption("👆 Clique numa linha para ver acusados, relatoria, Termo de "
               "Compromisso e despachos relacionados.")
    ev = tabela(
        show, datas=["Abertura"], use_container_width=True, hide_index=True, height=460,
        on_select="rerun", selection_mode="single-row",
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="abrir ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="processos_cvm.csv", mime="text/csv")
    sel = ev.selection.rows if getattr(ev, "selection", None) else []
    if sel:
        sid = int(res.iloc[sel[0]]["idproc"])
        if sid != st.session_state.get("dlg_proc"):
            st.session_state["dlg_proc"] = sid
            dialog_processo(res.iloc[sel[0]], acus)


# --------------------------------------------------------------------------
# Atas do CGE
# --------------------------------------------------------------------------
@st.cache_data(ttl=300)
def carregar_atas():
    if not os.path.exists(ATAS_DB_PATH):
        return None
    con = sqlite3.connect(ATAS_DB_PATH)
    df = pd.read_sql("SELECT * FROM atas", con)
    con.close()
    df["data_dt"] = pd.to_datetime(df["data_iso"], errors="coerce")
    return df


@st.dialog("Ata do CGE", width="large")
def dialog_ata(row):
    if row["link"]:
        st.link_button("Abrir PDF no gov.br ↗", row["link"])
    st.markdown(f"### Ata {row['numero']}ª — {row['tipo']} — {row['data']}")
    if str(row["resumo"]).strip():
        st.markdown(f"**Resumo (IA):** {row['resumo']}")
    if str(row["deliberacoes"]).strip():
        st.markdown(f"**Deliberações (IA):** {row['deliberacoes']}")
    if str(row["palavras_chave"]).strip():
        st.markdown(f"**Palavras-chave:** {row['palavras_chave']}")
    if str(row["membros"]).strip():
        st.markdown("**Membros presentes:** " + str(row["membros"]).replace(" | ", ", "))
    with st.expander("📄 Texto completo da ata"):
        st.text(row["texto"])


def render_atas():
    df = carregar_atas()
    if df is None or len(df) == 0:
        st.info("⏳ A base de Atas do CGE ainda não está disponível.")
        return
    com_ia = int((df["ai_feito"] == 1).sum())
    st.caption(f"{len(df)} atas do CGE • {com_ia} já com análise de IA (o resto entra "
               "conforme a rotina local roda).")
    with st.expander("🔎 Filtros", expanded=True):
        c1, c2, c3 = st.columns(3)
        q = c1.text_input("Buscar (texto, resumo, deliberações)", key="a_q")
        tipos = sorted(t for t in df["tipo"].dropna().unique() if t)
        f_tipo = c2.multiselect("Tipo", tipos, key="a_tipo")
        membro = c3.text_input("Membro (nome)", key="a_membro",
                               placeholder="ex.: berwanger")
    m = pd.Series(True, index=df.index)
    if q.strip():
        alvo = (df["texto"].fillna("") + " " + df["resumo"].fillna("") + " "
                + df["deliberacoes"].fillna("") + " "
                + df["palavras_chave"].fillna("")).str.lower()
        for w in q.lower().split():
            m &= alvo.str.contains(re.escape(w), na=False)
    if f_tipo:
        m &= df["tipo"].isin(f_tipo)
    if membro.strip():
        m &= df["membros"].fillna("").str.lower().str.contains(
            re.escape(membro.lower()), na=False)
    res = df[m].sort_values("data_iso", ascending=False).reset_index(drop=True)
    st.metric("Atas encontradas", len(res))
    cols = ["numero", "tipo", "data", "palavras_chave", "resumo", "link"]
    show = res[cols].rename(columns={
        "numero": "Nº", "tipo": "Tipo", "data": "Data",
        "palavras_chave": "Palavras-chave", "resumo": "Resumo (IA)", "link": "Link"})
    st.caption("👆 Clique numa linha para ver a ata completa (metadados, análise de IA e texto).")
    ev = tabela(
        show, datas=["Data"], use_container_width=True, hide_index=True, height=440,
        on_select="rerun", selection_mode="single-row",
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="PDF ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="atas_cge.csv", mime="text/csv")
    sel = ev.selection.rows if getattr(ev, "selection", None) else []
    if sel:
        aid = res.iloc[sel[0]]["arquivo"]
        if aid != st.session_state.get("dlg_ata"):
            st.session_state["dlg_ata"] = aid
            dialog_ata(res.iloc[sel[0]])


def carregar_informativos():
    if not os.path.exists(INF_DB_PATH):
        return None
    con = sqlite3.connect(INF_DB_PATH)
    df = pd.read_sql("SELECT * FROM deliberacoes", con)
    con.close()
    df["data_dt"] = pd.to_datetime(df["data_iso"], errors="coerce")
    return df


@st.dialog("Deliberação do Colegiado", width="large")
def dialog_deliberacao(row):
    if row["link"]:
        st.link_button("Abrir PDF do Informativo ↗", row["link"])
    cab = f"Informativo nº {row['inf_numero']}" if str(row["inf_numero"]).strip() else "Informativo"
    st.markdown(f"### {cab} — {row['reuniao_tipo']} — {row['data']}  ·  item {row['item']}")
    if str(row["assunto"]).strip():
        st.markdown(f"**Assunto:** {row['assunto']}")
    linha = []
    if str(row["tipo"]).strip():
        linha.append(f"**Tipo:** {row['tipo']}")
    if str(row["processo"]).strip():
        linha.append(f"**Processo:** {row['processo']}")
    if str(row["reg"]).strip():
        linha.append(f"**Reg.:** {row['reg']}")
    if str(row["relator"]).strip():
        linha.append(f"**Relator:** {row['relator']}")
    if linha:
        st.markdown(" · ".join(linha))
    if str(row["resumo"]).strip():
        st.markdown(f"**Resumo (IA):** {row['resumo']}")
    if str(row["partes"]).strip():
        st.markdown(f"**Partes/empresas (IA):** {row['partes']}")
    if str(row["resultado"]).strip():
        st.markdown(f"**Resultado (IA):** {row['resultado']}")
    if str(row["palavras_chave"]).strip():
        st.markdown(f"**Palavras-chave:** {row['palavras_chave']}")
    if str(row["decisao"]).strip():
        st.markdown("**Decisão:**")
        st.info(row["decisao"])
    with st.expander("📄 Texto completo da deliberação"):
        st.text(row["texto"])


def render_informativos():
    df = carregar_informativos()
    if df is None or len(df) == 0:
        st.info("⏳ A base dos Informativos do Colegiado ainda não está disponível.")
        return
    com_ia = int((df["ai_feito"] == 1).sum())
    n_inf = df["arquivo"].nunique()
    st.caption(f"{len(df):,} deliberações de {n_inf} informativos (2017–hoje) • "
               f"{com_ia} já com análise de IA (o resto entra conforme a rotina roda)."
               .replace(",", "."))
    with st.expander("🔎 Filtros", expanded=True):
        if st.button("🧹 Limpar filtros", use_container_width=True, key="i_limpar"):
            for k in list(st.session_state.keys()):
                if k.startswith("i_"):
                    del st.session_state[k]
            st.rerun()
        c1, c2 = st.columns([2, 1])
        q = c1.text_input(
            "Buscar (assunto, partes, decisão, texto…)", key="i_q",
            help='Palavras juntas = E. "aspas" = frase exata. Vírgula = OU.')
        tipos = sorted(t for t in df["tipo"].dropna().unique() if t)
        f_tipo = c2.multiselect("Tipo de assunto", tipos, key="i_tipo",
                                help="Recurso, Termo de Compromisso, Consulta, etc.")
        c3, c4, c5 = st.columns(3)
        proc = c3.text_input("Processo (nº)", key="i_proc",
                             placeholder="ex.: 19957.008954 ou RJ2013")
        rel = c4.text_input("Relator / área", key="i_rel",
                            placeholder="ex.: SIN, SRE, DHM")
        anos = sorted((df["data_dt"].dropna().dt.year.unique()), reverse=True)
        f_anos = c5.multiselect("Ano", [int(a) for a in anos], key="i_anos")
    m = pd.Series(True, index=df.index)
    if q.strip():
        m &= match_busca(df, ["assunto", "texto", "resumo", "partes", "decisao",
                              "palavras_chave", "processo"], q)
    if f_tipo:
        m &= df["tipo"].isin(f_tipo)
    if proc.strip():
        m &= df["processo"].fillna("").str.lower().str.contains(
            re.escape(proc.lower().strip()), na=False)
    if rel.strip():
        m &= df["relator"].fillna("").str.lower().str.contains(
            re.escape(rel.lower().strip()), na=False)
    if f_anos:
        m &= df["data_dt"].dt.year.isin(f_anos)
    res = df[m].sort_values(["data_iso", "item"],
                            ascending=[False, True]).reset_index(drop=True)
    c1, c2 = st.columns(2)
    c1.metric("Deliberações encontradas", f"{len(res):,}".replace(",", "."))
    c2.metric("Total na base", f"{len(df):,}".replace(",", "."))
    cols = ["data", "inf_numero", "item", "tipo", "assunto", "relator",
            "processo", "link"]
    show = res[cols].rename(columns={
        "data": "Data", "inf_numero": "Informativo", "item": "Item",
        "tipo": "Tipo", "assunto": "Assunto", "relator": "Relator",
        "processo": "Processo", "link": "Link"})
    st.caption("👆 Clique numa linha para ver a deliberação completa (assunto, "
               "decisão, análise de IA e texto).")
    ev = tabela(
        show, datas=["Data"], use_container_width=True, hide_index=True, height=460,
        on_select="rerun", selection_mode="single-row",
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="PDF ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="informativos_colegiado.csv", mime="text/csv")
    sel = ev.selection.rows if getattr(ev, "selection", None) else []
    if sel:
        did = int(res.iloc[sel[0]]["id"])
        if did != st.session_state.get("dlg_delib"):
            st.session_state["dlg_delib"] = did
            dialog_deliberacao(res.iloc[sel[0]])


def _norm_proc(p):
    if not p:
        return ""
    m = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}|"
                  r"SP\s?\d{4}/\d{3,6}|\d{1,4}/\d{4}", str(p))
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def _sigs_processo(texto):
    """Assinaturas (sequencia, ano) de um nº de processo, tolerando nº PARCIAL.

    Nos despachos a CVM às vezes cita só um pedaço do número; então casamos pela
    sequência distintiva (sem o código de unidade 19957, que é comum a todos) mais
    o ano, aceitando ano ausente de um dos lados.
    """
    if not texto:
        return set()
    s = str(texto)
    sigs = set()
    # SEI completo/parcial: 19957.011547/2017-16  ou  19957.011547
    for m in re.finditer(r"1\d{4}\.(\d{4,6})(?:/(\d{4}))?", s):
        sigs.add((m.group(1).lstrip("0") or m.group(1), m.group(2) or ""))
    # RJ / SP: RJ2013/5638 , TA-RJ2002/1153
    for m in re.finditer(r"(?:RJ|SP)\s?(\d{4})/(\d{3,6})", s, re.I):
        sigs.add((m.group(2).lstrip("0") or m.group(2), m.group(1)))
    # antigo com ponto: 2002.6691
    for m in re.finditer(r"\b(19|20)(\d{2})\.(\d{3,4})\b", s):
        sigs.add((m.group(3).lstrip("0") or m.group(3), m.group(1) + m.group(2)))
    # generico NNNN/YYYY (malandragem: pedaço + ano)
    for m in re.finditer(r"\b(\d{3,6})/(\d{4})\b", s):
        if 1990 <= int(m.group(2)) <= 2035:
            sigs.add((m.group(1).lstrip("0") or m.group(1), m.group(2)))
    return {(seq, ano) for seq, ano in sigs if len(seq) >= 3}


@st.cache_data(ttl=300)
def indice_despachos():
    """Índice seq -> lista de (ano, despacho) para cruzar processos com despachos."""
    df = carregar()
    idx = {}
    for _, r in df.iterrows():
        campo = f"{r.get('assunto', '')} {r.get('observacoes', '')}"
        rec = {"id": int(r["id"]), "data": r.get("data", ""),
               "componente": r.get("componente", ""),
               "solicitante": r.get("solicitante_nome", ""),
               "assunto": r.get("assunto", ""), "status": r.get("status", ""),
               "link": r.get("link", "")}
        for seq, ano in _sigs_processo(campo):
            idx.setdefault(seq, []).append((ano, rec))
    return idx


def despachos_do_processo(processo):
    """Despachos (audiências) cujo assunto referencia este processo (mesmo parcial)."""
    idx = indice_despachos()
    if not idx:
        return []
    vistos, out = set(), []
    for seq, ano_q in _sigs_processo(processo):
        for ano_d, rec in idx.get(seq, []):
            # ano deve bater; se um dos lados não tem ano, exige seq >= 4 dígitos
            ok = (ano_q and ano_d and ano_q == ano_d) or \
                 ((not ano_q or not ano_d) and len(seq) >= 4)
            if ok and rec["id"] not in vistos:
                vistos.add(rec["id"])
                out.append(rec)
    return sorted(out, key=lambda r: r["id"])


@st.cache_data(ttl=300)
def carregar_termos():
    if not os.path.exists(TERMOS_DB_PATH):
        return None
    con = sqlite3.connect(TERMOS_DB_PATH)
    df = pd.read_sql("SELECT * FROM termos", con)
    con.close()
    return df


@st.cache_data(ttl=300)
def indices_termos():
    """Lista de nomes distintos de compromitentes/proponentes (para autocomplete)."""
    df = carregar_termos()
    if df is None or len(df) == 0:
        return []
    nomes = set()
    for p in df["partes"].dropna():
        for parte in re.split(r"\s+e\s+|;|/|,| - ", str(p)):
            parte = parte.strip(" . ")
            if len(parte) >= 4 and not parte.isdigit():
                nomes.add(parte)
    return sorted(nomes)


@st.cache_data(ttl=300)
def mapa_tc():
    """proc_norm -> 'Aceito' / 'Rejeitado' / 'Aceito+Rejeitado' (situações do TC)."""
    df = carregar_termos()
    if df is None or len(df) == 0:
        return {}
    out = {}
    for pn, g in df[df["proc_norm"] != ""].groupby("proc_norm"):
        sits = sorted(set(g["situacao"]))
        out[pn] = "+".join(sits)
    return out


def deliberacoes_tc(proc_norm):
    """Deliberações de Termo de Compromisso nos Informativos para este processo."""
    if not proc_norm or not os.path.exists(INF_DB_PATH):
        return []
    con = sqlite3.connect(INF_DB_PATH)
    try:
        rows = con.execute(
            "SELECT data,inf_numero,assunto,resumo,decisao,link FROM deliberacoes "
            "WHERE proc_norm=? AND tipo='Termo de Compromisso' ORDER BY data_iso",
            (proc_norm,)).fetchall()
    except Exception:
        rows = []
    con.close()
    return rows


@st.dialog("Termo de Compromisso", width="large")
def dialog_termo(row):
    pn = row["proc_norm"]
    badge = "✅ Aceito" if row["situacao"] == "Aceito" else "❌ Rejeitado"
    st.markdown(f"### Processo {row['processo']} — {badge}")
    linha = [f"**Decisão:** {row['data_decisao']}"]
    if str(row["data_assinatura"]).strip():
        linha.append(f"**Assinatura:** {row['data_assinatura']}")
    if str(row["data_publicacao"]).strip():
        linha.append(f"**Publicação:** {row['data_publicacao']}")
    if str(row["data_arquivamento"]).strip():
        linha.append(f"**Arquivamento:** {row['data_arquivamento']}")
    st.markdown(" · ".join(linha))
    if str(row["partes"]).strip():
        st.markdown(f"**Compromitentes/Proponentes:** {row['partes']}")
    if row["link"]:
        st.link_button("Abrir Decisão/Parecer do Colegiado ↗", row["link"])
    rel = mapa_relator_atual().get(pn)
    if rel:
        st.markdown(f"**Relator (informativos):** {rel[0]} (desde {rel[1]})")
    delibs = deliberacoes_tc(pn)
    if delibs:
        st.markdown("#### 📰 Deliberação(ões) do Colegiado (Informativos)")
        for data, inf, assunto, resumo, decisao, link in delibs:
            st.markdown(f"**{data}** — Informativo nº {inf}")
            if str(resumo).strip():
                st.markdown(f"**Resumo (IA):** {resumo}")
            elif str(assunto).strip():
                st.markdown(f"*{assunto}*")
            if str(decisao).strip():
                st.info(decisao)
    decs = decisoes_do_processo(pn)
    if decs:
        st.markdown(f"#### 📜 Decisões do Colegiado ({len(decs)})")
        for data, tipo, ementa, link in decs[:15]:
            st.markdown(f"- **{data}** ({tipo}): {ementa}  [abrir ↗]({link})")
    desp = despachos_do_processo(row["processo"])
    if desp:
        st.markdown(f"#### 🏛️ Despachos/audiências relacionados ({len(desp)})")
        st.caption("Cruzamento pelo número do processo (tolera número parcial).")
        for d in desp[:30]:
            st.markdown(f"- **{d['data']}** · {d['componente']} · "
                        f"{d['solicitante']} — {d['assunto']}  "
                        f"[abrir ↗]({d['link']})")
    _bloco_noticias_md(pn, nomes=_nomes_partes(row["partes"]))


def render_termos():
    df = carregar_termos()
    if df is None or len(df) == 0:
        st.info("⏳ A base de Termos de Compromisso ainda não está disponível.")
        return
    na = int((df["situacao"] == "Aceito").sum())
    nr = int((df["situacao"] == "Rejeitado").sum())
    st.caption(f"{len(df):,} termos de compromisso consolidados • {na} aceitos • "
               f"{nr} rejeitados (fonte: portais de TC da CVM)."
               .replace(",", "."))
    with st.expander("🔎 Filtros", expanded=True):
        if st.button("🧹 Limpar filtros", use_container_width=True, key="tc_limpar"):
            for k in list(st.session_state.keys()):
                if k.startswith("tc_"):
                    del st.session_state[k]
            st.rerun()
        c1, c2 = st.columns([2, 1])
        q = c1.text_input("Buscar (nº do processo, compromitente/proponente)",
                          key="tc_q",
                          help='Palavras juntas = E. "aspas" = frase exata. Vírgula = OU.')
        f_sit = c2.multiselect("Situação", ["Aceito", "Rejeitado"], key="tc_sit")
        sug = st.checkbox(
            "💡 Autocomplete de nomes (sugerir da base)", value=False, key="tc_sug",
            help="Sugere compromitentes/proponentes da base; marque um ou mais (variantes "
                 "do nome). Basta um nome bater para o TC aparecer.")
        if sug:
            nomes_sel = st.multiselect(
                "Compromitente / proponente", indices_termos(), key="tc_ms_nome",
                placeholder="digite p/ filtrar e marque um ou mais…")
            nome_livre = ""
        else:
            nomes_sel = []
            nome_livre = st.text_input(
                "Compromitente / proponente", key="tc_nome",
                placeholder="basta um nome (quem tentou ou foi beneficiado pelo TC)…")
        c3, c4 = st.columns(2)
        anos = sorted({int(a[-4:]) for a in df["data_decisao"].dropna()
                       if re.search(r"\d{4}$", str(a))}, reverse=True)
        f_anos = c3.multiselect("Ano da decisão", anos, key="tc_anos")
        so_desp = c4.checkbox("Só com despacho/audiência cruzado", key="tc_desp",
                              help="Mostra apenas TCs com audiência particular relacionada.")
    m = pd.Series(True, index=df.index)
    if q.strip():
        m &= match_busca(df, ["processo", "partes"], q)
    partes_l = df["partes"].fillna("").str.lower()
    if nomes_sel:  # OU entre os nomes escolhidos (basta um bater)
        alvo = pd.Series(False, index=df.index)
        for nome in nomes_sel:
            alvo |= partes_l.str.contains(re.escape(nome.lower()), na=False)
        m &= alvo
    if nome_livre.strip():
        m &= partes_l.str.contains(re.escape(nome_livre.lower().strip()), na=False)
    if f_sit:
        m &= df["situacao"].isin(f_sit)
    if f_anos:
        m &= df["data_decisao"].apply(
            lambda a: bool(re.search(r"\d{4}$", str(a)))
            and int(str(a)[-4:]) in f_anos)
    res = df[m].copy()
    if so_desp:
        res = res[res["processo"].apply(lambda p: len(despachos_do_processo(p)) > 0)]
    res = res.sort_values("data_decisao_iso", ascending=False).reset_index(drop=True)
    c1, c2 = st.columns(2)
    c1.metric("Termos encontrados", f"{len(res):,}".replace(",", "."))
    c2.metric("Total na base", f"{len(df):,}".replace(",", "."))
    cols = ["processo", "situacao", "data_decisao", "data_assinatura",
            "data_publicacao", "data_arquivamento", "partes", "link"]
    show = res[cols].rename(columns={
        "processo": "Processo", "situacao": "Situação", "data_decisao": "Decisão",
        "data_assinatura": "Assinatura", "data_publicacao": "Publicação",
        "data_arquivamento": "Arquivamento", "partes": "Compromitentes/Proponentes",
        "link": "Decisão/Parecer"})
    st.caption("👆 Clique numa linha para ver o TC, a deliberação do Colegiado e "
               "os despachos relacionados.")
    ev = tabela(
        show, datas=["Decisão", "Assinatura", "Publicação", "Arquivamento"],
        use_container_width=True, hide_index=True, height=460,
        on_select="rerun", selection_mode="single-row",
        column_config={"Decisão/Parecer": st.column_config.LinkColumn(
            "Decisão/Parecer", display_text="abrir ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="termos_compromisso.csv", mime="text/csv")
    sel = ev.selection.rows if getattr(ev, "selection", None) else []
    if sel:
        tid = int(res.iloc[sel[0]]["id"])
        if tid != st.session_state.get("dlg_tc"):
            st.session_state["dlg_tc"] = tid
            dialog_termo(res.iloc[sel[0]])


@st.cache_data(ttl=300)
def carregar_relatores():
    if not os.path.exists(INF_DB_PATH):
        return None
    con = sqlite3.connect(INF_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM relatores", con)
    except Exception:
        df = None
    con.close()
    return df


@st.cache_data(ttl=300)
def mapa_relator_atual():
    """proc_norm -> (relator, data, data_iso, n_eventos, inf_numero, n_trocas)
    do evento mais recente. n_trocas = quantas vezes o relator mudou ao longo do tempo."""
    df = carregar_relatores()
    if df is None or len(df) == 0:
        return {}
    d = df.sort_values("data_iso")
    out = {}
    for pn, g in d.groupby("proc_norm"):
        last = g.iloc[-1]
        seq = list(g["relator"])
        n_trocas = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
        out[pn] = (last["relator"], last["data"], last["data_iso"], len(g),
                   last["inf_numero"], n_trocas)
    return out


def historico_relator(proc_norm):
    """Lista de eventos (data, relator, evento, informativo) de um processo."""
    df = carregar_relatores()
    if df is None or not proc_norm:
        return []
    g = df[df["proc_norm"] == proc_norm].sort_values("data_iso")
    return [(r["data"], r["relator"], r["evento"], r["inf_numero"])
            for _, r in g.iterrows()]


@st.cache_data(ttl=300)
def agregar_nao_sancionadores():
    """Uma linha por processo não-sancionador, agregando todas as deliberações."""
    df = carregar_informativos()
    if df is None or len(df) == 0:
        return None
    ns = df[df["natureza"] == "Nao-sancionador"].copy()
    if len(ns) == 0:
        return ns
    ns["chave"] = ns.apply(
        lambda r: r["proc_norm"] if r["proc_norm"] else f"__id{r['id']}", axis=1)
    campos = ["assunto", "texto", "resumo", "partes", "decisao",
              "palavras_chave", "relator", "tipo"]
    linhas = []
    for chave, g in ns.sort_values("data_iso").groupby("chave"):
        last = g.iloc[-1]
        tipos = " · ".join(sorted({t for t in g["tipo"] if t}))
        areas = " · ".join(sorted({a for a in g["relator"] if a}))
        blob = " ".join(g[campos].fillna("").astype(str).values.ravel()).lower()
        anos = sorted({int(a) for a in
                       pd.to_datetime(g["data_iso"], errors="coerce").dt.year.dropna()})
        linhas.append({
            "chave": chave,
            "processo": last["processo"] if str(last["processo"]).strip() else "(sem número)",
            "tipos": tipos, "assunto": last["assunto"], "area": areas,
            "n": len(g), "primeira": g.iloc[0]["data"], "ultima": last["data"],
            "ultima_iso": last["data_iso"], "link": last["link"],
            "_blob": blob, "_anos": anos})
    return pd.DataFrame(linhas)


RE_PENDENTE = re.compile(
    r"suspens|pedido de vista|\bvista\b|dilig[êe]ncia|retomad|adiad|sobrestad|"
    r"retorno d[oa]|retorno ao|convertido em dilig|reabertura|aguard|"
    r"sess[ãa]o.*suspens|n[ãa]o concluíd", re.I)


def _decisao_pendente(g):
    """Heurística: a última decisão indica que o assunto ainda não se encerrou."""
    if len(g) == 0:
        return False, ""
    ult = g.iloc[-1]
    txt = f"{ult['decisao']} {ult['resumo']}"
    if RE_PENDENTE.search(txt):
        return True, f"Última decisão ({ult['data']}, Inf. nº {ult['inf_numero']})"
    return False, ""


@st.dialog("Processo não-sancionador", width="large")
def dialog_ns(chave, processo):
    df = carregar_informativos()
    g = df[(df["natureza"] == "Nao-sancionador")].copy()
    g["chave"] = g.apply(
        lambda r: r["proc_norm"] if r["proc_norm"] else f"__id{r['id']}", axis=1)
    g = g[g["chave"] == chave].sort_values(["data_iso", "item"])
    st.markdown(f"### Processo {processo}")
    # resumo do que se trata (assunto/resumo mais recente)
    ult = g.iloc[-1] if len(g) else None
    sobre = ""
    if ult is not None:
        sobre = str(ult["resumo"]).strip() or str(ult["assunto"]).strip()
    if sobre:
        st.markdown(f"**Sobre:** {sobre}")
    # aviso de decisão pendente
    pendente, quando = _decisao_pendente(g)
    if pendente:
        st.warning(f"⏳ **Possível decisão pendente** — {quando} indica que o assunto "
                   "ainda não se encerrou (vista/diligência/suspensão/retorno).")
    else:
        st.success("✅ Sem pendência aparente na última decisão registrada.")
    tipos = sorted({t for t in g["tipo"] if t})
    areas = sorted({a for a in g["relator"] if a})
    meta = []
    if tipos:
        meta.append("**Tipo(s):** " + " · ".join(tipos))
    if areas:
        meta.append("**Área/relator:** " + " · ".join(areas))
    meta.append(f"**Decisões registradas:** {len(g)}")
    st.markdown("  \n".join(meta))
    # Decisões do Colegiado (cruzamento por nº do processo)
    pn = _norm_proc(processo)
    decs = decisoes_do_processo(pn)
    if decs:
        st.markdown(f"#### 📜 Decisões do Colegiado ({len(decs)})")
        for data, tipo, ementa, link in decs[:15]:
            st.markdown(f"- **{data}** ({tipo}): {ementa}  [abrir ↗]({link})")
    _bloco_noticias_md(pn)
    # links de TODAS as decisões, num bloco
    links = [(r["data"], r["inf_numero"], r["link"]) for _, r in g.iterrows() if r["link"]]
    if links:
        st.markdown("**🔗 Links de todas as decisões:** " + " · ".join(
            f"[{d} (Inf. {inf})]({lk})" for d, inf, lk in links))
    st.markdown("#### 🗂️ Linha do tempo das decisões")
    for _, r in g.iterrows():
        titulo = f"**{r['data']}** — Informativo nº {r['inf_numero']}, item {r['item']} · {r['tipo']}"
        st.markdown(titulo)
        if str(r["assunto"]).strip():
            st.markdown(f"*{r['assunto']}*")
        if str(r["resumo"]).strip():
            st.markdown(f"**Resumo (IA):** {r['resumo']}")
        if str(r["decisao"]).strip():
            st.info(r["decisao"])
        if r["link"]:
            st.caption(f"[Abrir PDF do informativo ↗]({r['link']})")
        st.divider()


def render_nao_sancionadores():
    ag = agregar_nao_sancionadores()
    if ag is None or len(ag) == 0:
        st.info("⏳ A base de Informativos ainda não está disponível.")
        return
    st.caption(f"{len(ag):,} processos não-sancionadores (recursos, consultas, "
               "ofertas, normas, propostas…) consolidados dos Informativos do "
               "Colegiado. Uma linha por processo; clique para ver a linha do tempo."
               .replace(",", "."))
    with st.expander("🔎 Filtros", expanded=True):
        if st.button("🧹 Limpar filtros", use_container_width=True, key="ns_limpar"):
            for k in list(st.session_state.keys()):
                if k.startswith("ns_"):
                    del st.session_state[k]
            st.rerun()
        c1, c2 = st.columns([2, 1])
        q = c1.text_input("Buscar (nº, assunto, área, decisão, texto…)", key="ns_q",
                          help='Palavras juntas = E. "aspas" = frase exata. Vírgula = OU.')
        tipos_disp = sorted({t for ts in ag["tipos"] for t in ts.split(" · ") if t})
        f_tipo = c2.multiselect("Tipo", tipos_disp, key="ns_tipo")
        c3, c4 = st.columns(2)
        area = c3.text_input("Área / relator", key="ns_area",
                             placeholder="ex.: SIN, SRE, SNC")
        anos_disp = sorted({a for ans in ag["_anos"] for a in ans}, reverse=True)
        f_anos = c4.multiselect("Ano", anos_disp, key="ns_anos")
    m = pd.Series(True, index=ag.index)
    if q.strip():
        m &= match_busca(ag, ["_blob"], q)
    if f_tipo:
        m &= ag["tipos"].apply(lambda s: any(t in s for t in f_tipo))
    if area.strip():
        m &= ag["area"].str.lower().str.contains(re.escape(area.lower().strip()), na=False)
    if f_anos:
        m &= ag["_anos"].apply(lambda a: any(y in a for y in f_anos))
    res = ag[m].sort_values("ultima_iso", ascending=False).reset_index(drop=True)
    c1, c2 = st.columns(2)
    c1.metric("Processos encontrados", f"{len(res):,}".replace(",", "."))
    c2.metric("Total na base", f"{len(ag):,}".replace(",", "."))
    cols = ["processo", "tipos", "assunto", "area", "n", "primeira", "ultima", "link"]
    show = res[cols].rename(columns={
        "processo": "Processo", "tipos": "Tipo(s)", "assunto": "Assunto (último)",
        "area": "Área/Relator", "n": "Nº decisões", "primeira": "1ª decisão",
        "ultima": "Última", "link": "Link"})
    st.caption("👆 Clique numa linha para ver a linha do tempo completa do processo.")
    ev = tabela(
        show, datas=["1ª decisão", "Última"], use_container_width=True,
        hide_index=True, height=460, on_select="rerun", selection_mode="single-row",
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="PDF ↗")})
    st.download_button("⬇️ Baixar (CSV)",
                       show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="nao_sancionadores.csv", mime="text/csv")
    sel = ev.selection.rows if getattr(ev, "selection", None) else []
    if sel:
        chave = res.iloc[sel[0]]["chave"]
        if chave != st.session_state.get("dlg_ns"):
            st.session_state["dlg_ns"] = chave
            dialog_ns(chave, res.iloc[sel[0]]["processo"])


def render_filtros_aud(df):
    """Filtros da aba Audiências — renderiza no container atual e retorna os valores."""
    if st.button("🧹 Limpar filtros", use_container_width=True):
        for k in list(st.session_state.keys()):
            if k.startswith(("f_", "q_assunto", "q_pessoa", "ac_assunto", "ac_pessoa")):
                del st.session_state[k]
        st.rerun()
    validas = df.dropna(subset=["data_dt"])
    dmin = validas["data_dt"].min().date() if len(validas) else dt.date(1948, 1, 1)
    dmax = validas["data_dt"].max().date() if len(validas) else dt.date.today()
    hoje = dt.date.today()
    atalho = st.radio("Período", ["Tudo", "Hoje", "Esta semana", "Este mês",
                                  "Futuro", "Personalizado"], index=0,
                      key="f_periodo", horizontal=True)
    de = ate = None
    if atalho == "Hoje":
        de = ate = hoje
    elif atalho == "Esta semana":
        de = hoje - dt.timedelta(days=hoje.weekday())
        ate = de + dt.timedelta(days=6)
    elif atalho == "Este mês":
        de = hoje.replace(day=1)
        ate = (de + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
    elif atalho == "Futuro":
        de = hoje + dt.timedelta(days=1)
    elif atalho == "Personalizado":
        cda, cdb = st.columns(2)
        de = cda.date_input("De (data inicial)", value=None, key="f_de",
                            min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
        ate = cdb.date_input("Até (data final)", value=None, key="f_ate",
                             min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
    sugerir = st.checkbox(
        "💡 Autocomplete (sugerir da base)", value=False, key="f_sugerir",
        help="Sugestões da base ao digitar; clicar adiciona. Desligado = texto livre.")
    nomes_idx = assuntos_idx = []
    if sugerir:
        nomes_idx, assuntos_idx = indices()
    ca, cb = st.columns(2)
    with ca:
        st.markdown("**Assunto**")
        if sugerir:
            _asel = st.multiselect("Assunto", assuntos_idx, key="f_ms_assunto",
                                   label_visibility="collapsed",
                                   placeholder="digite p/ filtrar e marque vários…")
            assunto_q = ", ".join(f'"{x}"' for x in _asel)
        else:
            assunto_q = st.text_input("Assunto", key="q_assunto",
                                      label_visibility="collapsed",
                                      help='Palavras juntas = E. "aspas" = frase exata. Vírgula = OU.')
    with cb:
        st.markdown("**Pessoa (nome)**")
        if sugerir:
            _psel = st.multiselect("Pessoa", nomes_idx, key="f_ms_pessoa",
                                   label_visibility="collapsed",
                                   placeholder="digite p/ filtrar e marque vários…")
            pessoa_q = ", ".join(f'"{x}"' for x in _psel)
        else:
            pessoa_q = st.text_input("Pessoa (nome)", key="q_pessoa",
                                     label_visibility="collapsed",
                                     help='Ex.: felipe claudino (as duas). "felipe claudino" = exato. Vírgula = OU.')
    cc, cd2, ce = st.columns(3)
    status_disp = sorted(s for s in df["status"].dropna().unique() if s)
    status_sel = cc.multiselect("Status", status_disp, key="f_status",
                                help="Ex.: Confirmada, Cancelada. Vazio = todos.")
    mapa = (df.dropna(subset=["sigla"]).query("sigla != ''")
              .groupby("sigla")["componente"].first().to_dict())
    rot = lambda s: f"{s} — {mapa.get(s, '')[len(s) + 3:]}".rstrip(" —")
    siglas = cd2.multiselect("Componente (sigla)", sorted(mapa), key="f_siglas",
                             help="Mostra só estes componentes.", format_func=rot)
    excluir = ce.multiselect("Excluir componentes", sorted(mapa), key="f_excluir",
                             help="Remove estes componentes (ex.: excluir 'DHM').", format_func=rot)
    return de, ate, assunto_q, siglas, excluir, pessoa_q, status_sel


# --------------------------------------------------------------------------
# Painel / Dashboard
# --------------------------------------------------------------------------
@st.cache_data(ttl=300)
def carregar_julgar():
    if not os.path.exists(JULGAR_DB_PATH):
        return None, {}
    con = sqlite3.connect(JULGAR_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM a_julgar", con)
        meta = dict(con.execute("SELECT chave,valor FROM julgar_meta").fetchall())
    except Exception:
        df, meta = None, {}
    con.close()
    return df, meta


def _slug(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower()).strip()


# ruído comum nas mencoes que NAO sao membros do Colegiado
_RUIDO_NOME = {"substituto", "substituta", "presidente", "relatora", "relator",
               "interino", "interina", "companhia", "geral", "executivo"}
# linha do tempo dos PRESIDENTES (curada; pequena e publica) para resolver 'PTE'
PRES_TIMELINE = [
    ("Marcelo Barbosa", "", "2021-07-31"),
    ("João Pedro Nascimento", "2021-08-01", "2025-07-31"),
    ("Otto Lobo", "2025-08-01", "9999-12-31"),
]


@st.cache_data(ttl=300)
def carregar_julgados():
    if not os.path.exists(JULGAR_DB_PATH):
        return None
    con = sqlite3.connect(JULGAR_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM julgados", con)
    except Exception:
        df = None
    con.close()
    return df


@st.cache_data(ttl=300)
def carregar_quem():
    if not os.path.exists(QUEM_DB_PATH):
        return None
    con = sqlite3.connect(QUEM_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM quem", con)
    except Exception:
        df = None
    con.close()
    return df


@st.cache_data(ttl=300)
def colegiado_atual():
    """Membros atuais do Colegiado (Presidente + Diretores) — os únicos que
    PODEM ser relator agora, conforme o Quem é Quem. Retorna lista de dicts."""
    df = carregar_quem()
    if df is None or len(df) == 0:
        return []
    board = df[df["e_relator"] == 1]
    return [{"nome": r["nome"], "sigla": r["sigla"], "cargo": r["cargo"]}
            for _, r in board.iterrows()]


def relator_no_colegiado(nome_oficial):
    """Casa o nome oficial de um relator com um membro atual do Colegiado
    (por sobrenome). Retorna o membro ou None."""
    alvo = set(_slug(nome_oficial).split())
    for m in colegiado_atual():
        toks = [t for t in _slug(m["nome"]).split() if len(t) >= 3]
        if toks and toks[-1] in alvo and (len(toks) == 1 or toks[0] in alvo):
            return m
    return None


@st.cache_data(ttl=300)
def relatores_validos():
    """Nomes (slug) que PODEM relatar: Colegiado (Presidente + Diretores),
    o SGE e os Diretores Substitutos citados recentemente nos informativos."""
    nomes = set()
    df = carregar_quem()
    if df is not None:
        for _, r in df.iterrows():
            if r["e_relator"] == 1 or str(r["sigla"]).upper() == "SGE":
                nomes.add(_slug(r["nome"]))
    if os.path.exists(INF_DB_PATH):
        con = sqlite3.connect(INF_DB_PATH)
        try:
            rows = con.execute("SELECT texto FROM deliberacoes "
                               "ORDER BY data_iso DESC LIMIT 300").fetchall()
        except Exception:
            rows = []
        con.close()
        pat = re.compile(
            r"Diretor[ae]?\s+Substitut[ao]:?\s+"
            r"([A-ZÀ-Ú][A-Za-zÀ-ú]+(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][A-Za-zÀ-ú]+){1,3})")
        for (txt,) in rows:
            for m in pat.finditer(txt or ""):
                nomes.add(_slug(m.group(1)))
    return nomes


def relator_valido(nome_oficial):
    """True se o relator oficial é do Colegiado, SGE ou Diretor Substituto."""
    validos = relatores_validos()
    if not validos:
        return True
    alvo = set(_slug(nome_oficial).split())
    for nome in validos:
        toks = [t for t in nome.split() if len(t) >= 3]
        if toks and toks[-1] in alvo and (len(toks) == 1 or toks[0] in alvo):
            return True
    return False


@st.cache_data(ttl=300)
def mapa_diretores():
    """sigla de diretor -> nome. Combina os informativos (diretores históricos, pelo
    mais frequente) com o Quem é Quem (diretores ATUAIS, autoritativo — cobre novos
    diretores como Igor Muniz/DIM que aparecem em poucos informativos)."""
    out = {}
    if os.path.exists(INF_DB_PATH):
        con = sqlite3.connect(INF_DB_PATH)
        try:
            rows = con.execute("""SELECT sigla,nome,COUNT(*) n FROM siglas
                WHERE papel='diretor' GROUP BY sigla,nome ORDER BY sigla,n DESC""").fetchall()
        except Exception:
            rows = []
        con.close()
        for sigla, nome, n in rows:
            if sigla in out or n < 3:
                continue
            if nome.split()[0].lower() in _RUIDO_NOME:
                continue
            out[sigla] = nome
    # Quem é Quem tem prioridade para os diretores atuais (sigla oficial + nome)
    board = carregar_quem()
    if board is not None:
        for _, r in board[board["e_relator"] == 1].iterrows():
            sg = str(r["sigla"]).upper()
            if re.match(r"^(PTE|D[A-Z]{1,3})$", sg):
                out[sg] = r["nome"]
    return out


def presidente_em(data_iso):
    di = data_iso or "2026-01-01"
    for nome, a, b in PRES_TIMELINE:
        if (not a or di >= a) and di <= b:
            return nome
    return PRES_TIMELINE[-1][0]


def pessoa_de_sigla(sigla, data_iso=""):
    """Nome da pessoa por trás de uma sigla de relator (PTE resolve por data)."""
    if not sigla:
        return ""
    if sigla == "PTE":
        return presidente_em(data_iso)
    return mapa_diretores().get(sigla, sigla)


def pessoa_de_nome(nome_oficial):
    """Nome oficial (planilha) -> nome canônico do diretor conhecido."""
    alvo = set(_slug(nome_oficial).split())
    for sigla, nome in mapa_diretores().items():
        toks = [t for t in _slug(nome).split() if len(t) >= 3]
        if toks and toks[-1] in alvo and (len(toks) == 1 or toks[0] in alvo):
            return nome
    return nome_oficial


@st.cache_data(ttl=300)
def calcular_inconsistencias():
    """A planilha 'A Julgar' serve só para (a) achar processos que NÃO conhecemos
    (ausentes da nossa base de Sancionadores) e (b) conflitos objetivos com outras
    fontes oficiais (TC aceito / já julgado). Divergência de relator planilha ×
    informativo é IRRELEVANTE e não entra aqui."""
    jul, _ = carregar_julgar()
    res = {"ausente_base": [], "tc_aceito": [], "ja_julgado": []}
    if jul is None or len(jul) == 0:
        return res
    mtc = mapa_tc()
    julg = carregar_julgados()
    set_julg = set(julg["proc_norm"]) - {""} if julg is not None else set()
    proc_pas, _ = carregar_pas()
    base_pn = ({_norm_proc(n) for n in proc_pas["numero"]} - {""}
               if proc_pas is not None else set())
    for _, r in jul.iterrows():
        pn = r["proc_norm"]
        if pn and base_pn and pn not in base_pn:
            res["ausente_base"].append({
                "processo": r["processo"], "relator": r["relator_nome"],
                "tipo": r["tipo"]})
        if pn and "Aceito" in mtc.get(pn, ""):
            res["tc_aceito"].append({
                "processo": r["processo"], "relator": r["relator_nome"]})
        if pn and pn in set_julg:
            res["ja_julgado"].append({
                "processo": r["processo"], "relator": r["relator_nome"]})
    return res


def _card(col, titulo, valor, sub=""):
    col.metric(titulo, valor, help=sub if sub else None)
    if sub:
        col.caption(sub)


def render_painel():
    st.subheader("📊 Painel — visão geral")
    aud = carregar()
    proc, _ = carregar_pas()
    tc = carregar_termos()
    inf = carregar_informativos()
    atas = carregar_atas()
    jul, jmeta = carregar_julgar()
    ns = agregar_nao_sancionadores()

    st.markdown("#### 🗂️ Bases")
    c = st.columns(4)
    _card(c[0], "🏛️ Audiências", f"{len(aud):,}".replace(",", ".") if aud is not None else "—",
          f"até {aud['data_dt'].max():%d/%m/%Y}" if aud is not None and aud["data_dt"].notna().any() else "")
    _card(c[1], "⚖️ Sancionadores", f"{len(proc):,}".replace(",", ".") if proc is not None else "—",
          "base em coleta")
    ntc = len(tc) if tc is not None else 0
    na = int((tc["situacao"] == "Aceito").sum()) if tc is not None else 0
    _card(c[2], "🤝 Termos Compr.", f"{ntc:,}".replace(",", "."),
          f"{na} aceitos · {ntc - na} rejeitados" if tc is not None else "")
    _card(c[3], "📂 Não-Sancion.", f"{len(ns):,}".replace(",", ".") if ns is not None else "—",
          "processos")
    c = st.columns(4)
    ninf = len(inf) if inf is not None else 0
    iia = int((inf["ai_feito"] == 1).sum()) if inf is not None else 0
    _card(c[0], "📰 Informativos", f"{ninf:,}".replace(",", "."),
          f"deliberações · {iia} com IA")
    _card(c[1], "📋 Atas CGE", f"{len(atas):,}".replace(",", ".") if atas is not None else "—",
          f"{int((atas['ai_feito'] == 1).sum())} com IA" if atas is not None else "")
    njul = len(jul) if jul is not None else 0
    _card(c[2], "⚖️ A Julgar", f"{njul}", jmeta.get("atualizado_em", ""))
    julgd = carregar_julgados()
    _card(c[3], "✔️ Julgados", f"{len(julgd):,}".replace(",", ".") if julgd is not None else "—",
          jmeta.get("fonte_julgados", "").replace(".xlsx", ""))

    st.divider()
    st.markdown("#### ⚠️ Inconsistências")
    if jul is None or len(jul) == 0:
        st.info("A base 'Processos a Julgar' ainda não está disponível.")
    else:
        inc = calcular_inconsistencias()
        ab, t, jj = inc["ausente_base"], inc["tc_aceito"], inc["ja_julgado"]
        st.caption("A planilha oficial **A Julgar** é usada só para achar processos que "
                   "**não conhecemos** e conflitos objetivos com outras fontes. "
                   "Divergência de relator planilha × informativo é irrelevante e foi removida.")
        m1, m2, m3 = st.columns(3)
        m1.metric("🔍 A julgar fora da nossa base", len(ab),
                  help="Processo na planilha A Julgar que NÃO está na nossa base de "
                       "Sancionadores — pode ser um processo que ainda não conhecemos.")
        m2.metric("✅ TC aceito mas a julgar", len(t),
                  help="Processo com Termo de Compromisso aceito ainda listado a julgar.")
        m3.metric("♻️ Já julgado mas a julgar", len(jj),
                  help="Processo que consta na lista oficial de Julgados E na de A Julgar.")
        if ab:
            st.markdown("**🔍 Processos na planilha 'A Julgar' que NÃO estão na nossa "
                        "base de Sancionadores** (investigar — talvez não conheçamos):")
            tabela(pd.DataFrame(ab).rename(columns={
                "processo": "Processo", "relator": "Relator (planilha)", "tipo": "Tipo"}),
                use_container_width=True, hide_index=True)
        if t:
            st.markdown("**✅ TC aceito mas ainda listado a julgar:**")
            tabela(pd.DataFrame(t).rename(columns={
                "processo": "Processo", "relator": "Relator (planilha)"}),
                use_container_width=True, hide_index=True)
        if jj:
            st.markdown("**♻️ Consta como já julgado e ainda listado a julgar** "
                        "(cruzamento Julgados × A Julgar):")
            tabela(pd.DataFrame(jj).rename(columns={
                "processo": "Processo", "relator": "Relator (planilha)"}),
                use_container_width=True, hide_index=True)
        if not (ab or t or jj):
            st.success("Nenhuma inconsistência detectada. 🎉")


@st.cache_data(ttl=300)
def carregar_pautas():
    """Retorna (processos da pauta ATUAL, retirados, meta) ou (None, None, {})."""
    if not os.path.exists(PAUTAS_DB_PATH):
        return None, None, {}
    con = sqlite3.connect(PAUTAS_DB_PATH)
    try:
        ult = con.execute("SELECT fonte,atualizada_em FROM snapshots "
                          "ORDER BY coletado_em DESC LIMIT 1").fetchone()
        if not ult:
            con.close()
            return None, None, {}
        fonte, atualizada = ult
        pau = pd.read_sql("SELECT * FROM pauta_processos WHERE fonte=:f",
                          con, params={"f": fonte})
        ret = pd.read_sql("SELECT * FROM retirados ORDER BY detectado_em DESC", con)
        n_snaps = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    except Exception:
        con.close()
        return None, None, {}
    con.close()
    return pau, ret, {"fonte": fonte, "atualizada": atualizada, "snapshots": n_snaps}


@st.cache_data(ttl=300)
def carregar_decisoes():
    if not os.path.exists(DECISOES_DB_PATH):
        return None
    con = sqlite3.connect(DECISOES_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM decisoes", con)
    except Exception:
        df = None
    con.close()
    return df


@st.cache_data(ttl=300)
def carregar_atas_colegiado():
    if not os.path.exists(DECISOES_DB_PATH):
        return None
    con = sqlite3.connect(DECISOES_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM atas_colegiado", con)
    except Exception:
        df = None
    con.close()
    return df


def decisoes_do_processo(pn):
    """Decisões do Colegiado (data, tipo, ementa, link) de um processo."""
    if not pn or not os.path.exists(DECISOES_DB_PATH):
        return []
    con = sqlite3.connect(DECISOES_DB_PATH)
    try:
        rows = con.execute(
            "SELECT data,tipo,ementa,link FROM decisoes WHERE proc_norm=? "
            "ORDER BY data_iso DESC", (pn,)).fetchall()
    except Exception:
        rows = []
    con.close()
    return rows


@st.cache_data(ttl=600)
def _base_noticias():
    """Índice das notícias dos últimos ~2 anos (carregado uma vez, com corpo)."""
    return _nx.carregar_noticias()


def noticias_do_processo(pn, nomes=None):
    """Notícias (últimos 2 anos) ligadas ao processo por número e/ou por nome.

    `nomes`: razões sociais/nomes das partes (acusados, proponentes) para o match por nome.
    """
    return _nx.noticias_relacionadas(pn=pn, nomes=nomes, base=_base_noticias())


def _nomes_partes(texto):
    """Quebra uma string de partes em nomes multi-palavra (>=10 chars) p/ o match por nome."""
    pedacos = re.split(r"[;,\n]|\be\b", str(texto or ""))
    return [x.strip(" .;,") for x in pedacos
            if len(x.strip(" .;,")) >= 10 and " " in x.strip(" .;,")]


def _bloco_noticias_md(pn, nomes=None, titulo="#### 📰 Notícias relacionadas"):
    """Renderiza (em markdown) as notícias relacionadas; nada se não houver."""
    nots = noticias_do_processo(pn, nomes=nomes)
    if not nots:
        return
    st.markdown(f"{titulo} ({len(nots)})")
    st.caption("Notícias da CVM (últimos 2 anos) que citam o nº do processo ou o nome "
               "de uma das partes.")
    for n in nots[:15]:
        cat = f" · _{n['categoria']}_" if n.get("categoria") else ""
        st.markdown(f"- **{n['data']}**{cat} — [{n['titulo']}]({n['url']})  "
                    f"<span style='color:#888;font-size:11px'>({n['motivo']})</span>",
                    unsafe_allow_html=True)


@st.dialog("Decisão do Colegiado", width="large")
def dialog_decisao(row):
    if row["link"]:
        st.link_button("Abrir a decisão no site da CVM ↗", row["link"])
    st.markdown(f"### {row['ementa']}")
    linha = [f"**Data:** {row['data']}", f"**Tipo:** {row['tipo']}"]
    if str(row["processo"]).strip():
        linha.append(f"**Processo:** {row['processo']}")
    st.markdown(" · ".join(linha))
    if str(row["descricao"]).strip():
        st.markdown("**Resumo da decisão:**")
        st.info(row["descricao"])
    pn = row["proc_norm"]
    if pn:
        # cruzamentos por número do processo
        lk = _mapa_link_pas().get(pn)
        if lk:
            st.markdown(f"🔗 [Página do processo sancionador ↗]({lk})")
        tc = mapa_tc().get(pn)
        if tc:
            st.markdown(f"🤝 **Termo de Compromisso:** {tc}")
        _, delibs = sobre_processo(pn)
        if delibs:
            st.markdown(f"📰 **{len(delibs)} deliberação(ões) nos Informativos** deste processo:")
            for data, inf, tipo, assunto, resumo, decisao, link in delibs[:8]:
                txt = str(resumo).strip() or str(assunto).strip()
                st.markdown(f"- **{data}** (Inf. nº {inf}): {txt[:160]}"
                            + (f" [ver ↗]({link})" if link else ""))


def render_decisoes():
    st.subheader("⚖️ Decisões do Colegiado + Atas — CVM")
    dec = carregar_decisoes()
    atc = carregar_atas_colegiado()
    if dec is None and atc is None:
        st.info("⏳ A base de Decisões/Atas do Colegiado ainda está sendo coletada.")
        return
    nd = len(dec) if dec is not None else 0
    na = len(atc) if atc is not None else 0
    st.caption(f"{nd:,} decisões do Colegiado • {na:,} atas de reuniões. "
               "Fonte: conteudo.cvm.gov.br/decisoes.".replace(",", "."))
    aba_d, aba_a = st.tabs(["📜 Decisões", "📋 Atas do Colegiado"])

    with aba_d:
        if dec is None or len(dec) == 0:
            st.info("Base de decisões ainda não disponível.")
        else:
            with st.expander("🔎 Filtros", expanded=True):
                c1, c2 = st.columns([2, 1])
                q = c1.text_input("Buscar (ementa, resumo, nº do processo)", key="dc_q",
                                  help='Palavras juntas = E. "aspas" = frase exata. Vírgula = OU.')
                tipos = sorted(x for x in dec["tipo"].dropna().unique() if x)
                f_tipo = c2.multiselect("Tipo", tipos, key="dc_tipo")
                c3, c4 = st.columns(2)
                proc = c3.text_input("Processo (nº)", key="dc_proc",
                                     placeholder="ex.: 19957.008636")
                anos = sorted({a[:4] for a in dec["data_iso"].dropna() if a}, reverse=True)
                f_ano = c4.multiselect("Ano", anos, key="dc_ano")
            m = pd.Series(True, index=dec.index)
            if q.strip():
                m &= match_busca(dec, ["ementa", "descricao", "processo"], q)
            if f_tipo:
                m &= dec["tipo"].isin(f_tipo)
            if proc.strip():
                m &= dec["processo"].fillna("").str.contains(
                    re.escape(proc.strip()), case=False, na=False)
            if f_ano:
                m &= dec["data_iso"].str[:4].isin(f_ano)
            res = dec[m].sort_values("data_iso", ascending=False).reset_index(drop=True)
            st.metric("Decisões encontradas", f"{len(res):,}".replace(",", "."))
            show = res[["data", "tipo", "processo", "ementa", "link"]].rename(columns={
                "data": "Data", "tipo": "Tipo", "processo": "Processo",
                "ementa": "Ementa", "link": "Link"})
            st.caption("👆 Clique numa linha para ver o resumo e os cruzamentos.")
            ev = tabela(show, datas=["Data"], use_container_width=True, hide_index=True,
                        height=460, on_select="rerun", selection_mode="single-row",
                        column_config={"Link": st.column_config.LinkColumn(
                            "Link", display_text="abrir ↗")})
            st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                               file_name="decisoes_colegiado.csv", mime="text/csv")
            sel = ev.selection.rows if getattr(ev, "selection", None) else []
            if sel:
                lk = res.iloc[sel[0]]["link"]
                if lk != st.session_state.get("dlg_dec"):
                    st.session_state["dlg_dec"] = lk
                    dialog_decisao(res.iloc[sel[0]])

    with aba_a:
        if atc is None or len(atc) == 0:
            st.info("Base de atas do Colegiado ainda não disponível.")
        else:
            c1, c2 = st.columns([2, 1])
            q = c1.text_input("Buscar (título)", key="at_q")
            anos = sorted({a[:4] for a in atc["data_iso"].dropna() if a}, reverse=True)
            f_ano = c2.multiselect("Ano", anos, key="at_ano")
            m = pd.Series(True, index=atc.index)
            if q.strip():
                for w in q.lower().split():
                    m &= atc["titulo"].fillna("").str.lower().str.contains(
                        re.escape(w), na=False)
            if f_ano:
                m &= atc["data_iso"].str[:4].isin(f_ano)
            r2 = atc[m].sort_values("data_iso", ascending=False).reset_index(drop=True)
            st.metric("Atas encontradas", f"{len(r2):,}".replace(",", "."))
            show = r2[["data", "tipo", "titulo", "link"]].rename(columns={
                "data": "Data", "tipo": "Tipo", "titulo": "Título", "link": "Link"})
            tabela(show, datas=["Data"], use_container_width=True, hide_index=True,
                   height=460, column_config={"Link": st.column_config.LinkColumn(
                       "Link", display_text="abrir ↗")})
            st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                               file_name="atas_colegiado.csv", mime="text/csv")


@st.cache_data(ttl=300)
def carregar_noticias():
    if not os.path.exists(NOTICIAS_DB_PATH):
        return None
    con = sqlite3.connect(NOTICIAS_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM noticias", con)
    except Exception:
        df = None
    con.close()
    return df


def render_noticias():
    st.subheader("📰 Notícias da CVM")
    df = carregar_noticias()
    if df is None or len(df) == 0:
        st.info("⏳ A base de notícias ainda está sendo populada.")
        return
    nsanc = int(df["categoria"].str.contains("SANCION", case=False, na=False).sum())
    # nº de processo citado no corpo (só nos últimos ~2 anos, que têm corpo)
    tem_corpo = "corpo" in df.columns
    if tem_corpo:
        df = df.copy()
        df["_procs"] = df["corpo"].apply(
            lambda c: "; ".join(_nx.extrair_procs(c)[:6]) if c else "")
        n_cita = int((df["_procs"] != "").sum())
    else:
        df["_procs"] = ""
        n_cita = 0
    st.caption(f"{len(df):,} notícias publicadas pela CVM • {nsanc} de atividade "
               f"sancionadora • {n_cita} citam um nº de processo (corpo lido nos "
               "últimos 2 anos). Fonte: gov.br/cvm • Notícias.".replace(",", "."))
    with st.expander("🔎 Filtros", expanded=True):
        c1, c2 = st.columns([2, 1])
        q = c1.text_input("Buscar (título, resumo, corpo, tags)", key="nt_q",
                          help='Palavras juntas = E. "aspas" = frase exata. Vírgula = OU. '
                               "Busca também no corpo das notícias dos últimos 2 anos.")
        cats = sorted(c for c in df["categoria"].dropna().unique() if c)
        f_cat = c2.multiselect("Categoria", cats, key="nt_cat",
                               help="Ex.: ATIVIDADE SANCIONADORA, ALERTA AO MERCADO…")
        c3, c4 = st.columns(2)
        anos = sorted({a[:4] for a in df["data_iso"].dropna() if a}, reverse=True)
        f_ano = c3.multiselect("Ano", anos, key="nt_ano")
        so_proc = c4.checkbox("📌 Só as que citam um processo", value=False,
                              key="nt_soproc",
                              help="Notícias (últimos 2 anos) que mencionam um nº de "
                                   "processo no corpo.")
    campos_busca = ["titulo", "resumo", "tags"] + (["corpo"] if tem_corpo else [])
    m = pd.Series(True, index=df.index)
    if q.strip():
        m &= match_busca(df, campos_busca, q)
    if f_cat:
        m &= df["categoria"].isin(f_cat)
    if f_ano:
        m &= df["data_iso"].str[:4].isin(f_ano)
    if so_proc:
        m &= df["_procs"] != ""
    res = df[m].sort_values("data_iso", ascending=False).reset_index(drop=True)
    st.metric("Notícias encontradas", f"{len(res):,}".replace(",", "."))
    show = res[["data", "categoria", "titulo", "_procs", "url"]].rename(columns={
        "data": "Data", "categoria": "Categoria", "titulo": "Título",
        "_procs": "Processos citados", "url": "Link"})
    tabela(
        show, datas=["Data"], use_container_width=True, hide_index=True, height=460,
        column_config={"Link": st.column_config.LinkColumn("Link", display_text="abrir ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="noticias_cvm.csv", mime="text/csv")


def render_pautas():
    st.subheader("🗓️ Pautas de Julgamento — CVM")
    pau, ret, meta = carregar_pautas()
    if pau is None:
        st.info("⏳ A base de Pautas de Julgamento ainda não está disponível.")
        return
    st.caption(f"Pauta atual atualizada em **{meta['atualizada']}** · fonte: "
               f"{meta['fonte']} · {meta['snapshots']} snapshot(s) acumulado(s). "
               "O rastreio de 'tirado de pauta' começa a partir do 1º snapshot.")
    hoje = dt.date.today()
    jul, _ = carregar_julgar()
    a_julgar = set(jul["proc_norm"]) - {""} if jul is not None else set()
    julg = carregar_julgados()
    set_julg = set(julg["proc_norm"]) - {""} if julg is not None else set()

    # futuras x passadas
    pau = pau.copy()
    pau["fut"] = pau["data_sessao_iso"].apply(
        lambda d: bool(d) and d >= hoje.isoformat())
    pau["Também em 'A Julgar'"] = pau["proc_norm"].apply(
        lambda p: "sim" if p in a_julgar else "")
    fut = pau[pau["fut"]].sort_values("data_sessao_iso")
    st.markdown(f"#### 📅 Próximas sessões ({len(fut)})")
    if len(fut):
        cols = ["data_sessao", "horario", "processo", "relator", "superintendencia",
                "objeto", "Também em 'A Julgar'"]
        show = fut[cols].rename(columns={
            "data_sessao": "Sessão", "horario": "Hora", "processo": "Processo",
            "relator": "Relator", "superintendencia": "Superintendência",
            "objeto": "Objeto"})
        tabela(show, datas=["Sessão"], use_container_width=True, hide_index=True)
    else:
        st.info("Nenhuma sessão futura na pauta atual.")

    st.markdown("#### 🚨 Tirados de pauta (alerta)")
    if ret is not None and len(ret):
        r2 = ret.copy()
        r2["status"] = r2["proc_norm"].apply(
            lambda p: "✅ consta como julgado" if p in set_julg
            else ("⏳ ainda a julgar" if p in a_julgar else "⚠️ sumiu sem julgamento"))
        show = r2[["processo", "data_sessao", "relator", "status", "detectado_em",
                   "objeto"]].rename(columns={
            "processo": "Processo", "data_sessao": "Estava p/ sessão",
            "relator": "Relator", "status": "Status", "detectado_em": "Saiu em",
            "objeto": "Objeto"})
        st.caption("Processos que estavam pautados e saíram da pauta seguinte. "
                   "⚠️ 'sumiu sem julgamento' é o que merece atenção.")
        tabela(show, datas=["Estava p/ sessão", "Saiu em"],
               use_container_width=True, hide_index=True)
    else:
        st.success("Nenhum processo tirado de pauta detectado até agora "
                   "(o rastreio começa quando surge a 2ª versão da pauta).")
    st.markdown("#### 📋 Pauta atual completa")
    show = pau[["data_sessao", "processo", "relator", "superintendencia",
                "objeto"]].rename(columns={
        "data_sessao": "Sessão", "processo": "Processo", "relator": "Relator",
        "superintendencia": "Superintendência", "objeto": "Objeto"})
    tabela(show, datas=["Sessão"], use_container_width=True, hide_index=True)
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="pautas_julgamento.csv", mime="text/csv")


@st.cache_data(ttl=300)
def _mapa_abertura():
    """proc_norm -> data de abertura (dos Processos Sancionadores), quando houver."""
    proc, _ = carregar_pas()
    out = {}
    if proc is not None:
        for _, r in proc.iterrows():
            pn = _norm_proc(r["numero"])
            if pn and str(r["data_abertura"]).strip():
                out[pn] = r["data_abertura"]
    return out


@st.cache_data(ttl=300)
def _mapa_link_pas():
    """proc_norm -> URL da página do processo sancionador no site da CVM."""
    proc, _ = carregar_pas()
    out = {}
    if proc is not None:
        for _, r in proc.iterrows():
            pn = _norm_proc(r["numero"])
            if pn:
                out[pn] = URL_PAS + str(r["idproc"])
    return out


def _siglas_da_pessoa(nome_pessoa):
    """Todas as siglas que resolvem para a mesma pessoa (ex.: Otto = DOL e PTE)."""
    alvo = _slug(nome_pessoa)
    out = set()
    for sg, nome in mapa_diretores().items():
        if _slug(nome) == alvo:
            out.add(sg)
    # PTE se a pessoa é o presidente atual
    if _slug(presidente_em(dt.date.today().isoformat())) == alvo:
        out.add("PTE")
    return out


def processos_do_relator(nome_pessoa):
    """Processos em que a pessoa é o relator ATUAL (deduzido dos informativos)."""
    mrel = mapa_relator_atual()
    aber = _mapa_abertura()
    links = _mapa_link_pas()
    df = carregar_relatores()
    proc_disp = {}
    if df is not None:
        for _, r in df.iterrows():
            proc_disp.setdefault(r["proc_norm"], r["processo"])
    alvo = _slug(nome_pessoa)
    out = []
    for pn, info in mrel.items():
        if _slug(pessoa_de_sigla(info[0], info[1])) != alvo:
            continue
        ano = ""
        m = re.search(r"/(\d{4})-\d{2}$", pn) or re.search(r"RJ(\d{4})", pn)
        if m:
            ano = m.group(1)
        abertura = aber.get(pn, "")
        out.append({
            "Processo": proc_disp.get(pn, pn), "Sigla": info[0],
            "Relator desde": info[1], "Relator há (dias)": _dias_desde(info[1]),
            "Abertura do processo": abertura or (f"(ano {ano})" if ano else "—"),
            "Aberto há (dias)": _dias_desde(abertura), "Trocas": info[5],
            "Sancionador": links.get(pn, ""), "_pn": pn})
    return sorted(out, key=lambda x: (x["Relator há (dias)"] is None,
                                      -(x["Relator há (dias)"] or 0)))


def sobre_processo(pn):
    """Do que se trata: objeto (base de Sancionadores) + deliberações nos Informativos."""
    objeto = ""
    proc, _ = carregar_pas()
    if proc is not None and pn:
        hit = proc[proc["numero"].map(_norm_proc) == pn]
        if len(hit):
            r0 = hit.iloc[0]
            objeto = str(r0.get("objeto", "") or "").strip() or str(r0.get("ementa", "") or "").strip()
    delibs = []
    if pn and os.path.exists(INF_DB_PATH):
        con = sqlite3.connect(INF_DB_PATH)
        try:
            delibs = con.execute(
                "SELECT data,inf_numero,tipo,assunto,resumo,decisao,link "
                "FROM deliberacoes WHERE proc_norm=? ORDER BY data_iso DESC", (pn,)
            ).fetchall()
        except Exception:
            delibs = []
        con.close()
    return objeto, delibs


def impedimentos_da_pessoa(nome_pessoa):
    """Impedimentos/suspeições declarados pela pessoa (todas as suas siglas)."""
    siglas = _siglas_da_pessoa(nome_pessoa)
    if not siglas or not os.path.exists(INF_DB_PATH):
        return []
    con = sqlite3.connect(INF_DB_PATH)
    q = ("SELECT processo,tipo,assunto,inf_numero,data FROM impedimentos "
         f"WHERE sigla IN ({','.join('?' * len(siglas))}) ORDER BY data_iso DESC")
    try:
        rows = con.execute(q, tuple(siglas)).fetchall()
    except Exception:
        rows = []
    con.close()
    return rows


@st.dialog("Relator — processos e impedimentos", width="large")
def dialog_relator(nome, cargo, sigla):
    st.markdown(f"### {nome}")
    st.caption(f"{cargo} · {sigla}")
    procs = processos_do_relator(nome)
    imped = impedimentos_da_pessoa(nome)
    c1, c2 = st.columns(2)
    c1.metric("Processos como relator atual", len(procs))
    c2.metric("Impedimentos / suspeições declarados", len(imped))
    st.markdown("#### ⚖️ Processos sob relatoria")
    st.caption("*Relator desde* = última vez sorteado/redistribuído relator. *Abertura* "
               "= do processo sancionador. Colunas de tempo em **dias**. "
               "👆 **Clique em 'abrir ↗' para ir direto à página do processo no site da CVM.**")
    if procs:
        dfp = pd.DataFrame(procs).drop(columns=["_pn"])
        tabela(dfp, datas=["Relator desde"],
               dias=["Relator há (dias)", "Aberto há (dias)"],
               use_container_width=True, hide_index=True,
               column_config={"Sancionador": st.column_config.LinkColumn(
                   "Página do processo", display_text="abrir ↗")})
    else:
        st.info("Nenhum processo com esta pessoa como relator atual nos informativos.")
    st.markdown("#### 🚫 Impedimentos e suspeições")
    if imped:
        dfi = pd.DataFrame(imped, columns=["Processo", "Tipo", "Assunto",
                                           "Informativo", "Data"])
        tabela(dfi, datas=["Data"], use_container_width=True, hide_index=True)
    else:
        st.info("Nenhum impedimento/suspeição registrado nos informativos.")


def render_quem():
    st.subheader("👥 Quem é Quem — CVM")
    df = carregar_quem()
    if df is None or len(df) == 0:
        st.info("⏳ A base do Quem é Quem ainda não está disponível.")
        return
    nrel = int((df["e_relator"] == 1).sum())
    st.caption(f"{len(df)} pessoas do organograma da CVM • {nrel} no Colegiado "
               "(Presidente + Diretores — os únicos que podem ser relator). "
               "Fonte: gov.br/cvm • Quem é Quem.")
    # relatoria: 'a julgar' (planilha oficial) e relatoria total (dos informativos)
    jul, _ = carregar_julgar()
    cont_jul = {}
    if jul is not None and len(jul):
        for _, r in jul.iterrows():
            m = relator_no_colegiado(r["relator_nome"])
            if m:
                cont_jul[m["sigla"]] = cont_jul.get(m["sigla"], 0) + 1
    # nº de processos em que a pessoa é o relator ATUAL (deduzido dos informativos)
    cont_rel = {}
    for pn, info in mapa_relator_atual().items():
        nome = pessoa_de_sigla(info[0], info[1])
        if nome:
            cont_rel[_slug(nome)] = cont_rel.get(_slug(nome), 0) + 1
    with st.expander("🔎 Filtros", expanded=True):
        c1, c2 = st.columns([2, 1])
        q = c1.text_input("Buscar (nome, cargo, sigla, e-mail)", key="qq_q")
        so_col = c2.checkbox("Só o Colegiado (relatores)", key="qq_col")
    m = pd.Series(True, index=df.index)
    if q.strip():
        alvo = (df["nome"].fillna("") + " " + df["cargo"].fillna("") + " "
                + df["sigla"].fillna("") + " " + df["email"].fillna("")).str.lower()
        for w in q.lower().split():
            m &= alvo.str.contains(re.escape(w), na=False)
    if so_col:
        m &= df["e_relator"] == 1
    res = df[m].copy()
    res["a_julgar"] = res.apply(
        lambda r: cont_jul.get(r["sigla"], 0) if r["e_relator"] == 1 else None, axis=1)
    res["relatoria"] = res.apply(
        lambda r: cont_rel.get(_slug(pessoa_de_sigla(r["sigla"])), 0)
        if r["e_relator"] == 1 else None, axis=1)
    res["impedimentos"] = res.apply(
        lambda r: len(impedimentos_da_pessoa(r["nome"])) if r["e_relator"] == 1 else None,
        axis=1)
    res = res.sort_values(["e_relator", "cargo"], ascending=[False, True])
    st.metric("Pessoas encontradas", len(res))
    if nrel:
        st.markdown("**⚖️ Colegiado atual** (quem pode ser relator):")
        st.caption("*Processos a julgar* = planilha oficial 'A Julgar' (snapshot). "
                   "*Relator de* = processos em que é o relator atual, deduzido dos "
                   "Informativos. *Impedimentos* = vezes que se declarou impedido/suspeito. "
                   "👆 Clique numa linha para abrir a ficha do relator.")
        board = res[res["e_relator"] == 1].reset_index(drop=True)
        show_b = board[["nome", "cargo", "sigla", "email", "a_julgar", "relatoria",
                        "impedimentos"]].rename(columns={
            "nome": "Nome", "cargo": "Cargo", "sigla": "Sigla", "email": "E-mail",
            "a_julgar": "A julgar", "relatoria": "Relator de",
            "impedimentos": "Impedimentos"})
        evb = st.dataframe(show_b, use_container_width=True, hide_index=True,
                           on_select="rerun", selection_mode="single-row")
        selb = evb.selection.rows if getattr(evb, "selection", None) else []
        if selb:
            r = board.iloc[selb[0]]
            if r["nome"] != st.session_state.get("dlg_rel"):
                st.session_state["dlg_rel"] = r["nome"]
                dialog_relator(r["nome"], r["cargo"], r["sigla"])
    cols = ["nome", "cargo", "sigla", "email", "telefone", "perfil"]
    show = res[cols].rename(columns={
        "nome": "Nome", "cargo": "Cargo", "sigla": "Sigla", "email": "E-mail",
        "telefone": "Telefone", "perfil": "Perfil"})
    st.caption("Organograma completo:")
    st.dataframe(
        show, use_container_width=True, hide_index=True, height=420,
        column_config={"Perfil": st.column_config.LinkColumn("Perfil", display_text="abrir ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="quem_e_quem_cvm.csv", mime="text/csv")


def render_audiencias():
    if not os.path.exists(DB_PATH):
        st.warning("Banco de dados ainda não disponível.")
        return
    df = carregar()
    st.subheader("🏛️ Audiências Particulares — CVM")
    st.caption(f"Base local de dados públicos da CVM • {len(df):,} audiências • "
               f"atualizada em {df['coletado_em'].max()}".replace(",", "."))
    with st.expander("🔎 Filtros", expanded=True):
        (de, ate, assunto_q, siglas, excluir, pessoa_q,
         status_sel) = render_filtros_aud(df)
    res = filtrar(df, de, ate, assunto_q, siglas, excluir, pessoa_q, status_sel)

    c1, c2 = st.columns(2)
    c1.metric("Resultados", f"{len(res):,}".replace(",", "."))
    c2.metric("Total na base", f"{len(df):,}".replace(",", "."))
    rr = res.dropna(subset=["data_dt"])
    if len(rr):
        st.caption(f"📅 Período dos resultados: **{rr['data_dt'].min():%d/%m/%Y}** "
                   f"a **{rr['data_dt'].max():%d/%m/%Y}**")

    aba_lista, aba_panorama = st.tabs(["📋 Resultados", "📊 Panorama"])
    with aba_lista:
        st.caption("👆 Clique em uma linha para ver os detalhes (como no site da CVM).")
        ordenado = res.sort_values("id", ascending=False).reset_index(drop=True)
        cols = ["id", "data", "hora", "componente", "assunto",
                "solicitante_nome", "acompanhantes", "status", "link"]
        show = ordenado[cols].rename(columns={
            "id": "Nº", "data": "Data", "hora": "Hora", "componente": "Componente",
            "assunto": "Assunto", "solicitante_nome": "Solicitante",
            "acompanhantes": "Acompanhantes", "status": "Status", "link": "Link"})
        event = tabela(
            show, datas=["Data"], use_container_width=True, hide_index=True, height=480,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "Link": st.column_config.LinkColumn("Link", display_text="abrir ↗")})
        st.download_button(
            "⬇️ Baixar resultados (CSV)",
            data=show.to_csv(index=False).encode("utf-8-sig"),
            file_name="audiencias_cvm.csv", mime="text/csv")
        sel = event.selection.rows if getattr(event, "selection", None) else []
        if sel:
            sel_id = int(ordenado.iloc[sel[0]]["id"])
            if sel_id != st.session_state.get("dlg_id"):
                st.session_state["dlg_id"] = sel_id
                dialog_detalhe(ordenado.iloc[sel[0]])
    with aba_panorama:
        rr = res.dropna(subset=["data_dt"])
        if len(rr):
            por_mes = (rr.set_index("data_dt").resample("MS").size()
                       .rename("Audiências").to_frame())
            st.subheader("Audiências por mês")
            st.line_chart(por_mes)
            st.subheader("Top 15 componentes")
            top = res["componente"].value_counts().head(15).sort_values()
            st.bar_chart(top)
        else:
            st.info("Sem datas para exibir no panorama com os filtros atuais.")


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
SECOES = ["📊 Painel", "🏛️ Audiências Particulares", "⚖️ Processos Sancionadores",
          "🤝 Termos de Compromisso", "📂 Processos Não-Sancionadores",
          "🗓️ Pautas de Julgamento", "📜 Decisões do Colegiado", "📋 Atas do CGE",
          "📰 Informativos do Colegiado", "👥 Quem é Quem", "🗞️ Notícias"]


def main():
    if not autenticado():
        return
    # Navegacao PREGUICOSA: so a secao selecionada e' renderizada (o st.tabs
    # executaria todas as abas a cada carregamento, pesado demais na nuvem).
    if hasattr(st, "segmented_control"):
        sel = st.segmented_control("Seção", SECOES, key="nav",
                                   default=SECOES[0], label_visibility="collapsed")
    else:
        sel = st.radio("Seção", SECOES, key="nav", horizontal=True,
                       label_visibility="collapsed")
    sel = sel or SECOES[0]
    if sel == SECOES[0]:
        render_painel()
    elif sel == SECOES[1]:
        render_audiencias()
    elif sel == SECOES[2]:
        render_processos()
    elif sel == SECOES[3]:
        st.subheader("🤝 Termos de Compromisso — CVM")
        render_termos()
    elif sel == SECOES[4]:
        st.subheader("📂 Processos Não-Sancionadores — CVM")
        render_nao_sancionadores()
    elif sel == SECOES[5]:
        render_pautas()
    elif sel == SECOES[6]:
        render_decisoes()
    elif sel == SECOES[7]:
        render_atas()
    elif sel == SECOES[8]:
        st.subheader("📰 Informativos do Colegiado — CVM")
        render_informativos()
    elif sel == SECOES[9]:
        render_quem()
    elif sel == SECOES[10]:
        render_noticias()


if __name__ == "__main__":
    main()
