# -*- coding: utf-8 -*-
"""Visualizador das Audiencias Particulares da CVM."""
import os
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


def filtrar(df, de, ate, assunto, pessoa, siglas):
    m = pd.Series(True, index=df.index)
    if de:
        m &= df["data_dt"] >= pd.Timestamp(de)
    if ate:
        m &= df["data_dt"] <= pd.Timestamp(ate)
    if assunto:
        m &= df["assunto"].str.contains(assunto, case=False, na=False)
    if pessoa:
        alvo = (df["solicitante_nome"].fillna("") + " | "
                + df["acompanhantes"].fillna("") + " | "
                + df["observacoes"].fillna(""))
        m &= alvo.str.contains(pessoa, case=False, na=False)
    if siglas:
        m &= df["sigla"].isin(siglas)
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
        pessoa = st.text_input("Pessoa (solicitante/acompanhante)")
        # nomes por sigla, p/ mostrar o significado (ex.: PTE → PTE - Presidência)
        mapa = (df.dropna(subset=["sigla"]).query("sigla != ''")
                  .groupby("sigla")["componente"].first().to_dict())
        siglas_disp = sorted(mapa)
        siglas = st.multiselect(
            "Componente (sigla)", siglas_disp,
            help="Ex.: PTE = Presidência, SRE = Superint. de Registro. "
                 "Escolha uma ou várias e combine com o período.",
            format_func=lambda s: f"{s} — {mapa.get(s, '')[len(s)+3:]}".rstrip(" —"))
        st.divider()
        st.caption("Dica: combine filtros — sigla + período + assunto/pessoa funcionam em conjunto (E).")

    res = filtrar(df, de, ate, assunto, pessoa, siglas)

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
