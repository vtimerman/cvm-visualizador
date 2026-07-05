# -*- coding: utf-8 -*-
"""Visualizador das Audiencias Particulares da CVM."""
import os
import re
import sqlite3
import datetime as dt

import pandas as pd
import streamlit as st

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiencias.db"))

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
    return df


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


def filtrar(df, de, ate, assunto, siglas, excluir_siglas, termos):
    m = pd.Series(True, index=df.index)
    if de:
        m &= df["data_dt"] >= pd.Timestamp(de)
    if ate:
        m &= df["data_dt"] <= pd.Timestamp(ate)
    if assunto:
        m &= df["assunto"].str.contains(assunto, case=False, na=False)
    if siglas:
        m &= df["sigla"].isin(siglas)
    if excluir_siglas:
        m &= ~df["sigla"].isin(excluir_siglas)
    if termos:
        alvo = _texto_escopo(df, "any")
        mt = pd.Series(False, index=df.index)
        for t in termos:
            t = (t or "").strip().lower()
            if t:
                mt |= alvo.str.contains(re.escape(t), na=False)
        m &= mt
    return df[m]


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
        validas = df.dropna(subset=["data_dt"])
        dmin = validas["data_dt"].min().date() if len(validas) else dt.date(1948, 1, 1)
        dmax = validas["data_dt"].max().date() if len(validas) else dt.date.today()
        periodo = st.date_input("Período da audiência", value=(dmin, dmax),
                                min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
        de = ate = None
        if isinstance(periodo, (tuple, list)) and len(periodo) == 2:
            de, ate = periodo
        assunto = st.text_input("Assunto contém")
        # nomes por sigla, p/ mostrar o significado (ex.: PTE → PTE - Presidência)
        mapa = (df.dropna(subset=["sigla"]).query("sigla != ''")
                  .groupby("sigla")["componente"].first().to_dict())
        rot = lambda s: f"{s} — {mapa.get(s, '')[len(s) + 3:]}".rstrip(" —")
        siglas = st.multiselect(
            "Componente (sigla)", sorted(mapa),
            help="Mostra só estes componentes. Ex.: PTE = Presidência, SRE = Registro.",
            format_func=rot)
        excluir = st.multiselect(
            "Excluir componentes (siglas)", sorted(mapa),
            help="Remove dos resultados as audiências destes componentes. Ex.: buscar "
                 "'henrique machado' e excluir 'DHM' tira as que ele conduziu como diretor.",
            format_func=rot)

        st.markdown("**Pessoa**")
        pessoa_txt = st.text_input(
            "Nome ou parte do nome (busca em todos os campos)",
            placeholder="ex.: henrique machado")
        termos = []
        if pessoa_txt.strip():
            cands = nomes_no_escopo(df, "any", pessoa_txt)
            if cands:
                escolhidas = st.multiselect(
                    f"Variantes do nome ({len(cands)}) — desmarque as que não quiser",
                    cands, default=cands)
                termos = escolhidas or [pessoa_txt]
            else:
                termos = [pessoa_txt]

        st.divider()
        st.caption("Dica: combine tudo (E). Para tirar um papel específico, use "
                   "'Excluir componentes' — ex.: excluir a sigla do próprio diretor.")

    res = filtrar(df, de, ate, assunto, siglas, excluir, termos)

    # ---- métricas ----
    c1, c2, c3 = st.columns(3)
    c1.metric("Resultados", f"{len(res):,}".replace(",", "."))
    c2.metric("Total na base", f"{len(df):,}".replace(",", "."))
    if len(res.dropna(subset=["data_dt"])):
        rr = res.dropna(subset=["data_dt"])
        c3.metric("Período dos resultados",
                  f"{rr['data_dt'].min():%d/%m/%Y} – {rr['data_dt'].max():%d/%m/%Y}")

    aba_lista, aba_panorama = st.tabs(["📋 Resultados", "📊 Panorama"])

    with aba_lista:
        cols = ["id", "data", "hora", "componente", "assunto",
                "solicitante_nome", "acompanhantes", "status", "observacoes"]
        vis = res[cols].sort_values("id", ascending=False).rename(columns={
            "id": "Nº", "data": "Data", "hora": "Hora", "componente": "Componente",
            "assunto": "Assunto", "solicitante_nome": "Solicitante",
            "acompanhantes": "Acompanhantes", "status": "Status",
            "observacoes": "Observações"})
        st.dataframe(vis, use_container_width=True, hide_index=True, height=520)
        st.download_button(
            "⬇️ Baixar resultados (CSV)",
            data=vis.to_csv(index=False).encode("utf-8-sig"),
            file_name="audiencias_cvm.csv", mime="text/csv")

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


if __name__ == "__main__":
    main()
