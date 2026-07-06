# -*- coding: utf-8 -*-
"""Visualizador das Audiencias Particulares da CVM."""
import os
import re
import sqlite3
import datetime as dt

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiencias.db"))
URL_BASE = "https://sistemas.cvm.gov.br/aplicacoes/cap/consulta/audiencia.asp?id="
PAS_DB_PATH = os.environ.get("PAS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "processos.db"))
URL_PAS = "https://sistemas.cvm.gov.br/asp/cvmwww/inqueritos/DetPasAndamentoSSI.asp?idProc="
ATAS_DB_PATH = os.environ.get("ATAS_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "atas.db"))
INF_DB_PATH = os.environ.get("INF_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "informativos.db"))

st.set_page_config(page_title="Audiências CVM", page_icon="🏛️", layout="wide")


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

    st.title("🏛️ Audiências Particulares — CVM")
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
    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<style>{CSS_CVM} td{{font-size:13px}} h3{{font-family:Arial;font-size:1rem}}'
        '</style></head><body><div id="width">'
        f'<h2>Processo Sancionador nº {e(row["numero"])}</h2>'
        f'<table>{corpo}</table>'
        f'<h3>Acusados ({len(ac)})</h3>'
        '<table><tr class="header"><td>Nome/Razão social</td><td>Situação</td>'
        f'<td>Data</td><td>Histórico de situações</td></tr>{ac_rows}</table>'
        f'{rel_html}</div></body></html>')
    components.html(doc, height=560 + len(ac) * 70 + len(hist) * 34, scrolling=True)


def render_processos():
    proc, acus = carregar_pas()
    if proc is None or len(proc) == 0:
        st.info("⏳ A base de Processos Sancionadores ainda está sendo coletada. "
                "Volte em breve — ela cresce sozinha na nuvem.")
        return
    st.caption(f"{len(proc):,} processos • {len(acus):,} acusados na base"
               .replace(",", "."))

    with st.expander("🔎 Filtros", expanded=True):
        c1, c2 = st.columns(2)
        q_txt = c1.text_input("Número ou objeto contém", key="p_txt")
        q_acus = c2.text_input("Acusado (nome)", key="p_acus",
                               placeholder="ex.: eduardo levy")
        c3, c4 = st.columns(2)
        fases = sorted(f for f in proc["fase"].dropna().unique() if f)
        f_fase = c3.multiselect("Fase atual", fases, key="p_fase")
        encs = sorted(x for x in proc["encarregado"].dropna().unique() if x)
        f_enc = c4.multiselect("Encarregado (área)", encs, key="p_enc")

    m = pd.Series(True, index=proc.index)
    if q_txt.strip():
        m &= (proc["numero"].str.contains(q_txt, case=False, na=False)
              | proc["objeto"].str.contains(q_txt, case=False, na=False)
              | proc["ementa"].str.contains(q_txt, case=False, na=False))
    if f_fase:
        m &= proc["fase"].isin(f_fase)
    if f_enc:
        m &= proc["encarregado"].isin(f_enc)
    if q_acus.strip():
        col = acus["nome"].fillna("").str.lower()
        mk = pd.Series(True, index=acus.index)
        for w in q_acus.lower().split():
            mk &= col.str.contains(re.escape(w), na=False)
        m &= proc["idproc"].isin(set(acus[mk]["idproc"]))

    res = proc[m].sort_values("idproc", ascending=False).reset_index(drop=True)
    # cruza o relator atual (última atribuição registrada nos Informativos)
    mapa_rel = mapa_relator_atual()
    res["relator_atual"] = res["numero"].map(
        lambda n: mapa_rel.get(_norm_proc(n), ("",))[0])
    st.metric("Processos encontrados", f"{len(res):,}".replace(",", "."))
    ncruz = int((res["relator_atual"] != "").sum())
    if len(mapa_rel):
        st.caption(f"🧭 Relator cruzado dos Informativos do Colegiado em "
                   f"{ncruz} dos {len(res)} processos exibidos (a cobertura cresce "
                   "conforme a base de sancionadores e os informativos avançam).")

    cols = ["numero", "data_abertura", "fase", "encarregado", "relator_atual",
            "acusados", "link"]
    show = res[cols].rename(columns={
        "numero": "Processo", "data_abertura": "Abertura", "fase": "Fase",
        "encarregado": "Encarregado", "relator_atual": "Relator (informativos)",
        "acusados": "Acusados", "link": "Link"})
    st.caption("👆 Clique numa linha para ver o processo, os acusados e o histórico "
               "de relatoria.")
    ev = st.dataframe(
        show, use_container_width=True, hide_index=True, height=460,
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
    ev = st.dataframe(
        show, use_container_width=True, hide_index=True, height=440,
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
    ev = st.dataframe(
        show, use_container_width=True, hide_index=True, height=460,
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
    """proc_norm -> (relator, data, data_iso, n_eventos) do evento mais recente."""
    df = carregar_relatores()
    if df is None or len(df) == 0:
        return {}
    d = df.sort_values("data_iso")
    out = {}
    for pn, g in d.groupby("proc_norm"):
        last = g.iloc[-1]
        out[pn] = (last["relator"], last["data"], last["data_iso"], len(g))
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


@st.dialog("Processo não-sancionador", width="large")
def dialog_ns(chave, processo):
    df = carregar_informativos()
    g = df[(df["natureza"] == "Nao-sancionador")].copy()
    g["chave"] = g.apply(
        lambda r: r["proc_norm"] if r["proc_norm"] else f"__id{r['id']}", axis=1)
    g = g[g["chave"] == chave].sort_values(["data_iso", "item"])
    st.markdown(f"### Processo {processo}")
    tipos = sorted({t for t in g["tipo"] if t})
    areas = sorted({a for a in g["relator"] if a})
    meta = []
    if tipos:
        meta.append("**Tipo(s):** " + " · ".join(tipos))
    if areas:
        meta.append("**Área/relator:** " + " · ".join(areas))
    meta.append(f"**Decisões registradas:** {len(g)}")
    st.markdown("  \n".join(meta))
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
    ev = st.dataframe(
        show, use_container_width=True, hide_index=True, height=460,
        on_select="rerun", selection_mode="single-row",
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
# App
# --------------------------------------------------------------------------
def main():
    if not autenticado():
        return

    aba_aud, aba_pas, aba_ns, aba_atas, aba_inf = st.tabs(
        ["🏛️ Audiências Particulares", "⚖️ Processos Sancionadores",
         "📂 Processos Não-Sancionadores", "📋 Atas do CGE",
         "📰 Informativos do Colegiado"])

    with aba_aud:
        if not os.path.exists(DB_PATH):
            st.warning("Banco de dados ainda não disponível.")
        else:
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
                event = st.dataframe(
                    show, use_container_width=True, hide_index=True, height=480,
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

    with aba_pas:
        render_processos()

    with aba_ns:
        st.subheader("📂 Processos Não-Sancionadores — CVM")
        render_nao_sancionadores()

    with aba_atas:
        render_atas()

    with aba_inf:
        st.subheader("📰 Informativos do Colegiado — CVM")
        render_informativos()


if __name__ == "__main__":
    main()
