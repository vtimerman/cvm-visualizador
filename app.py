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
    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<style>{CSS_CVM} td{{font-size:13px}} h3{{font-family:Arial;font-size:1rem}}'
        '</style></head><body><div id="width">'
        f'<h2>Processo Sancionador nº {e(row["numero"])}</h2>'
        f'<table>{corpo}</table>'
        f'<h3>Acusados ({len(ac)})</h3>'
        '<table><tr class="header"><td>Nome/Razão social</td><td>Situação</td>'
        f'<td>Data</td><td>Histórico de situações</td></tr>{ac_rows}</table>'
        '</div></body></html>')
    components.html(doc, height=520 + len(ac) * 70, scrolling=True)


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
    st.metric("Processos encontrados", f"{len(res):,}".replace(",", "."))

    cols = ["numero", "data_abertura", "fase", "encarregado", "acusados", "link"]
    show = res[cols].rename(columns={
        "numero": "Processo", "data_abertura": "Abertura", "fase": "Fase",
        "encarregado": "Encarregado", "acusados": "Acusados", "link": "Link"})
    st.caption("👆 Clique numa linha para ver o processo e os acusados (com histórico).")
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
    cols = ["numero", "tipo", "data", "resumo", "link"]
    show = res[cols].rename(columns={
        "numero": "Nº", "tipo": "Tipo", "data": "Data",
        "resumo": "Resumo (IA)", "link": "Link"})
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


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
def main():
    if not autenticado():
        return

    if not os.path.exists(DB_PATH):
        st.warning("Banco de dados ainda não disponível.")
        return

    df = carregar()

    st.title("🏛️ Audiências Particulares — CVM")
    st.caption(f"Base local de dados públicos da CVM • {len(df):,} audiências • "
               f"atualizada em {df['coletado_em'].max()}".replace(",", "."))

    # ---- filtros (barra lateral) ----
    with st.sidebar:
        st.header("Filtros")
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
                                      "Futuro", "Personalizado"], index=0, key="f_periodo")
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
            de = st.date_input("De (data inicial)", value=None, key="f_de",
                               min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
            ate = st.date_input("Até (data final)", value=None, key="f_ate",
                                min_value=dmin, max_value=dmax, format="DD/MM/YYYY")

        sugerir = st.checkbox(
            "💡 Autocomplete (sugerir da base)", value=False, key="f_sugerir",
            help="Mostra nomes/assuntos da base que combinam. As sugestões só entram "
                 "se você CLICAR — você sempre pode digitar um texto livre.")
        nomes_idx = assuntos_idx = []
        if sugerir:
            nomes_idx, assuntos_idx = indices()

        st.markdown("**Assunto**")
        if sugerir:
            _asel = st.multiselect(
                "Assunto", assuntos_idx, key="f_ms_assunto",
                label_visibility="collapsed",
                placeholder="digite p/ filtrar e marque vários…")
            assunto_q = ", ".join(f'"{x}"' for x in _asel)
        else:
            assunto_q = st.text_input(
                "Assunto", key="q_assunto", label_visibility="collapsed",
                help='Palavras juntas = E. "aspas" = frase exata. Vírgula = OU.')

        status_disp = sorted(s for s in df["status"].dropna().unique() if s)
        status_sel = st.multiselect("Status", status_disp, key="f_status",
                                    help="Ex.: Confirmada, Cancelada. Vazio = todos.")

        mapa = (df.dropna(subset=["sigla"]).query("sigla != ''")
                  .groupby("sigla")["componente"].first().to_dict())
        rot = lambda s: f"{s} — {mapa.get(s, '')[len(s) + 3:]}".rstrip(" —")
        siglas = st.multiselect("Componente (sigla)", sorted(mapa), key="f_siglas",
                                help="Mostra só estes componentes.", format_func=rot)
        excluir = st.multiselect("Excluir componentes (siglas)", sorted(mapa), key="f_excluir",
                                 help="Remove estes componentes. Ex.: buscar 'henrique "
                                      "machado' e excluir 'DHM' tira as que ele conduziu "
                                      "como diretor.", format_func=rot)

        st.markdown("**Pessoa (nome)**")
        if sugerir:
            _psel = st.multiselect(
                "Pessoa", nomes_idx, key="f_ms_pessoa",
                label_visibility="collapsed",
                placeholder="digite p/ filtrar e marque vários…")
            pessoa_q = ", ".join(f'"{x}"' for x in _psel)
        else:
            pessoa_q = st.text_input(
                "Pessoa (nome)", key="q_pessoa", label_visibility="collapsed",
                help='Ex.: felipe claudino → tem as duas palavras. "felipe claudino" = '
                     'exato. Vírgula = OU (ex.: vorcaro, machado).')

        st.divider()
        st.caption("Dica: com o Autocomplete ligado, digite e as sugestões aparecem ao "
                   "vivo; clique para adicionar (o item some da lista). Desligado, é texto "
                   "livre. Clique no cabeçalho da coluna para ordenar.")

    res = filtrar(df, de, ate, assunto_q, siglas, excluir, pessoa_q, status_sel)

    aba_aud, aba_pas, aba_atas = st.tabs(
        ["🏛️ Audiências Particulares", "⚖️ Processos Sancionadores",
         "📋 Atas do CGE"])

    with aba_aud:
        c1, c2 = st.columns(2)
        c1.metric("Resultados", f"{len(res):,}".replace(",", "."))
        c2.metric("Total na base", f"{len(df):,}".replace(",", "."))
        rr = res.dropna(subset=["data_dt"])
        if len(rr):
            st.caption(f"📅 Período dos resultados: **{rr['data_dt'].min():%d/%m/%Y}** "
                       f"a **{rr['data_dt'].max():%d/%m/%Y}**")

        aba_lista, aba_panorama = st.tabs(["📋 Resultados", "📊 Panorama"])

        with aba_lista:
            st.caption("👆 Clique em uma linha para ver os detalhes (como no site da CVM). "
                       "⚠️ Os filtros na barra lateral são desta aba (Audiências).")
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

    with aba_atas:
        render_atas()


if __name__ == "__main__":
    main()
