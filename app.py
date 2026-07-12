# -*- coding: utf-8 -*-
"""Visualizador das Audiencias Particulares da CVM."""
import os
import re
import sys
import sqlite3
import json
import unicodedata
import datetime as dt
from collections import Counter
from urllib.parse import quote

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
PUBLICACOES_DB_PATH = os.environ.get("PUBLICACOES_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "publicacoes.db"))
PESSOAL_DB_PATH = os.environ.get("PESSOAL_DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "pessoal.db"))

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


def _card_pessoa(titulo, nome, empresa, cargo):
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
    cards = [_card_pessoa("SOLICITANTE", e(row["solicitante_nome"]),
                          e(row.get("solicitante_empresa", "")),
                          e(row.get("solicitante_cargo", "")))]
    for a in acs:
        cards.append(_card_pessoa("ACOMPANHANTE", e(a), "", ""))

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


def julgamentos_do_processo(proc_norm):
    """Registros de julgamento (planilha oficial 'Processos Julgados por Relator')
    de um processo: relator do julgamento, data, tipo de peça, rito e sup."""
    if not proc_norm or not os.path.exists(JULGAR_DB_PATH):
        return []
    con = sqlite3.connect(JULGAR_DB_PATH)
    try:
        rows = con.execute(
            "SELECT relator_nome,data_julg,tipo,rito,sup FROM julgados "
            "WHERE proc_norm=? ORDER BY data_julg", (proc_norm,)).fetchall()
    except Exception:
        rows = []
    con.close()
    return [{"relator": r[0], "data": r[1], "tipo": r[2], "rito": r[3], "sup": r[4]}
            for r in rows]


@st.cache_data(ttl=300)
def _mapa_link_julgamento():
    """proc_norm -> URL da pagina do julgamento (relatorio/voto/decisao) na CVM,
    coletada de conteudo.cvm.gov.br/sancionadores para a tabela julgamento_paginas."""
    if not os.path.exists(JULGAR_DB_PATH):
        return {}
    con = sqlite3.connect(JULGAR_DB_PATH)
    try:
        rows = con.execute(
            "SELECT proc_norm, link FROM julgamento_paginas").fetchall()
    except Exception:
        rows = []
    con.close()
    return {pn: lk for pn, lk in rows if pn and lk}


def _desfecho_acusado(situacao, historico=""):
    """Rótulo curto do desfecho de mérito a partir da situação/histórico do
    acusado. A situação (status mais recente) tem prioridade; o histórico
    completa o desfecho (ex.: 'GRU para pagamento de multa' = condenado)."""
    s = _slug(situacao or "")
    h = _slug(str(historico or ""))
    both = s + " || " + h
    tem_multa = "multa" in both
    # sinais fortes na situação atual têm prioridade
    if "absolvi" in s and "condena" not in s:
        return "Absolvido"
    if "condena" in s:
        return "Condenado" + (" c/ multa" if tem_multa else "")
    if "extincao de punibilidade" in s or "extinta a punibilidade" in s:
        return "Extinção de punibilidade"
    # senão, desfecho de mérito pelo histórico
    if "condena" in h:
        return "Condenado" + (" c/ multa" if tem_multa else "")
    if "absolvi" in h:
        return "Absolvido"
    if "extincao de punibilidade" in h or "extinta a punibilidade" in h:
        return "Extinção de punibilidade"
    if "termo de compromisso" in both or "cumprimento do termo" in both:
        return "Termo de Compromisso"
    if tem_multa:  # GRU/pagamento de multa é penalidade de condenação
        return "Condenado c/ multa"
    return "—"


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
        f'<tr><td>{e(a["nome"])}</td>'
        f'<td><b>{e(_desfecho_acusado(a["situacao"], a["historico"]))}</b></td>'
        f'<td>{e(a["situacao"])}</td>'
        f'<td>{e(a["data"])}</td><td>{e(a["historico"])}</td></tr>'
        for _, a in ac.iterrows()) if len(ac) else \
        '<tr><td colspan="5">— sem acusados —</td></tr>'
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
    mata = _mapa_ata_por_data()  # data_iso -> link da ata da reuniao
    decs = decisoes_do_processo(_norm_proc(row["numero"]))
    if decs:
        dcr = ""
        for data, data_iso, tipo, ementa, link in decs[:40]:
            la = mata.get(str(data_iso or ""), "")
            cel_ata = _ver_ata_link(la)
            dcr += (f'<tr><td style="white-space:nowrap">{e(data)}</td>'
                    f'<td>{e(tipo)}</td><td>{e(ementa)}</td>'
                    f'<td><a href="{e(link)}" target="_blank">abrir ↗</a></td>'
                    f'<td>{cel_ata}</td></tr>')
        dec_html = (
            f'<h3>Decisões do Colegiado ({len(decs)})</h3>'
            '<table><tr class="header"><td>Data</td><td>Tipo</td>'
            f'<td>Ementa</td><td>Decisão</td><td>Ata da reunião</td></tr>{dcr}</table>')
    else:
        dec_html = ('<h3>Decisões do Colegiado</h3>'
                    '<p style="font-size:13px">— nenhuma decisão do Colegiado '
                    'relacionada (a base ainda cresce) —</p>')
    # ⚖️ Julgamento (planilha oficial de julgados) + desfecho consolidado
    julgs = julgamentos_do_processo(_norm_proc(row["numero"]))
    if julgs:
        jr = ""
        for x in julgs:
            d0 = _to_date(x["data"])
            la = mata.get(d0.isoformat(), "") if d0 else ""
            cel_ata = _ver_ata_link(la)
            jr += (f'<tr><td style="white-space:nowrap">{e(x["data"])}</td>'
                   f'<td>{e(x["relator"])}</td><td>{e(x["tipo"])}</td>'
                   f'<td>{e(x["rito"])}</td><td>{e(x["sup"])}</td>'
                   f'<td>{cel_ata}</td></tr>')
        desf = [_desfecho_acusado(a["situacao"], a["historico"])
                for _, a in ac.iterrows()] if len(ac) else []
        cont = Counter(d for d in desf if d and d != "—")
        resumo_desf = " · ".join(f"{v} {k.lower()}" for k, v in cont.items()) or "—"
        lj = _mapa_link_julgamento().get(_norm_proc(row["numero"]), "")
        link_julg = (
            f'<p style="font-size:13px"><b>📄 Decisão do julgamento '
            f'(relatório e voto):</b> <a href="{e(lj)}" target="_blank">'
            f'abrir no site da CVM ↗</a></p>') if lj else (
            '<p style="font-size:12px">Inteiro teor (votos e resultado) na pasta '
            'de Sancionadores — botão <b>“Abrir no site da CVM ↗”</b> acima.</p>')
        julg_html = (
            f'<h3>⚖️ Julgamento ({len(julgs)})</h3>'
            '<p style="font-size:12px">Fonte: planilha oficial "Processos Julgados '
            'por Relator".</p>'
            '<table><tr class="header"><td>Data</td><td>Relator</td><td>Peça</td>'
            f'<td>Rito</td><td>Sup.</td><td>Ata</td></tr>{jr}</table>'
            f'<p style="font-size:13px"><b>Desfecho (por acusado):</b> '
            f'{e(resumo_desf)}</p>{link_julg}')
    else:
        julg_html = ''
    # 📑 Publicações no Diário Eletrônico (SEI): intimações, editais, despachos,
    # extratos de julgamento/termo/ata ligados a este processo.
    pubs = _pubs_do_processo(row["numero"])
    if pubs:
        pr = ""
        for p in pubs[:60]:
            res_p = (p["resumo"] or "")[:200]
            pr += (f'<tr><td style="white-space:nowrap">{e(p["data"])}</td>'
                   f'<td>{e(p["tipo"])}</td><td>{e(p["unidade"])}</td>'
                   f'<td>{e(res_p)}</td>'
                   f'<td><a href="{e(p["link"])}" target="_blank">abrir ↗</a></td></tr>')
        pubs_html = (
            f'<h3>📑 Publicações no Diário (SEI) ({len(pubs)})</h3>'
            '<p style="font-size:12px">Intimações, editais, despachos e extratos '
            'publicados no Diário Eletrônico da CVM para este processo.</p>'
            '<table><tr class="header"><td>Data</td><td>Tipo</td><td>Unidade</td>'
            f'<td>Resumo</td><td>Documento</td></tr>{pr}</table>')
    else:
        pubs_html = ''
    # 🗓️ Pautas de julgamento (Diário/SEI): inclusões e retiradas de pauta.
    pautas = _mapa_pauta_sei().get(_norm_proc(row["numero"]), [])
    if pautas:
        pl = ""
        for pt in sorted(pautas, key=lambda x: str(x.get("data_sessao_iso") or ""),
                         reverse=True):
            sit = str(pt.get("situacao") or "incluido")
            badge = ("🚫 retirado" if sit.startswith("retirado") else "🗓️ incluído")
            adv = str(pt.get("advogados") or "")
            pl += (f'<tr><td style="white-space:nowrap">{e(pt.get("data_sessao",""))}'
                   f'</td><td>{badge}{" (sine die)" if "sine" in sit else ""}</td>'
                   f'<td>{e(pt.get("relator",""))}</td>'
                   f'<td>{e(adv[:120])}</td>'
                   f'<td><a href="{e(pt.get("link_sei",""))}" target="_blank">'
                   'abrir ↗</a></td></tr>')
        pautas_html = (
            f'<h3>🗓️ Pautas de julgamento (Diário/SEI) ({len(pautas)})</h3>'
            '<p style="font-size:12px">Inclusões e retiradas de pauta publicadas no '
            'Diário Eletrônico (fonte oficial das sessões de julgamento).</p>'
            '<table><tr class="header"><td>Sessão</td><td>Situação</td>'
            f'<td>Relator</td><td>Advogados</td><td>Documento</td></tr>{pl}</table>')
    else:
        pautas_html = ''
    # 📅 Linha do tempo unificada: todos os eventos do processo em ordem.
    eventos = []

    def _ev(data_txt, rotulo, detalhe):
        d = _to_date(data_txt)
        if d:
            eventos.append((d.isoformat(), data_txt, rotulo, detalhe))
    _ev(row.get("data_abertura", ""), "📂 Abertura do processo",
        str(row.get("fase") or ""))
    for d0 in desp[:40]:
        _ev(d0.get("data", ""), "🏛️ Audiência/Despacho",
            f'{d0.get("componente", "")} — {str(d0.get("assunto") or "")[:90]}')
    if tc is not None and len(tcm):
        for _, t in tcm.iterrows():
            _ev(t["data_decisao"], f'🤝 Termo de Compromisso {t["situacao"]}',
                str(t["partes"] or "")[:90])
    for data, data_iso, tipo, ementa, _lk in decs[:40]:
        _ev(data, f'📜 Decisão do Colegiado ({tipo})', str(ementa or "")[:90])
    for pt in pautas:
        sit = str(pt.get("situacao") or "incluido")
        _ev(pt.get("data_sessao", ""),
            "🚫 Retirado de pauta" if sit.startswith("retirado")
            else "🗓️ Incluído em pauta", f'Relator: {pt.get("relator", "")}')
    for x in julgs:
        _ev(x["data"], "⚖️ JULGADO", f'Relator: {x["relator"]} · {x["rito"]}')
    for p0 in pubs[:60]:
        _ev(p0["data"], f'📑 {p0["tipo"]}', str(p0["resumo"] or "")[:90])
    eventos.sort(key=lambda x: x[0])
    if eventos:
        tl = "".join(
            f'<tr><td style="white-space:nowrap">{e(dt_txt)}</td>'
            f'<td style="white-space:nowrap"><b>{rot}</b></td><td>{e(det)}</td></tr>'
            for _iso2, dt_txt, rot, det in eventos)
        tl_html = (
            f'<h3>📅 Linha do tempo ({len(eventos)} eventos)</h3>'
            '<table><tr class="header"><td>Data</td><td>Evento</td>'
            f'<td>Detalhe</td></tr>{tl}</table>')
    else:
        tl_html = ''
    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<style>{CSS_CVM} td{{font-size:13px}} h3{{font-family:Arial;font-size:1rem}}'
        '</style></head><body><div id="width">'
        f'<h2>Processo Sancionador nº {e(row["numero"])}</h2>'
        f'<table>{corpo}</table>'
        f'{_analise_html(_mapa_analises().get((_norm_proc(row["numero"]), "julgado")), e)}'
        f'{_teses_html(_norm_proc(row["numero"]), e)}'
        f'{tl_html}'
        f'{julg_html}'
        f'<h3>Acusados ({len(ac)})</h3>'
        '<table><tr class="header"><td>Nome/Razão social</td><td>Desfecho</td>'
        '<td>Situação</td>'
        f'<td>Data</td><td>Histórico de situações</td></tr>{ac_rows}</table>'
        f'{rel_html}{tc_html}{dec_html}{desp_html}{pubs_html}{pautas_html}'
        '</div></body></html>')
    components.html(doc, height=620 + len(ac) * 70 + len(hist) * 34
                    + len(decs[:40]) * 30 + len(julgs) * 32
                    + len(desp[:40]) * 34 + len(pubs[:60]) * 30
                    + len(pautas) * 30 + len(eventos) * 28, scrolling=True)
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


@st.cache_data(ttl=300)
def _mapa_desfecho():
    """proc_norm -> resumo do desfecho (contagem por tipo), lido das situações/
    históricos dos acusados na base de Sancionadores."""
    proc, acus = carregar_pas()
    if proc is None or acus is None:
        return {}
    pn_por_id = {r["idproc"]: _norm_proc(r["numero"]) for _, r in proc.iterrows()}
    agg = {}
    for _, a in acus.iterrows():
        pn = pn_por_id.get(a["idproc"])
        if not pn:
            continue
        d = _desfecho_acusado(a["situacao"], a["historico"])
        if d and d != "—":
            agg.setdefault(pn, Counter())[d] += 1
    return {pn: " · ".join(f"{v} {k.lower()}" for k, v in c.items())
            for pn, c in agg.items()}


@st.dialog("Processo julgado", width="large")
def dialog_julgado_simples(row):
    """Ficha mínima para processo julgado que não está na base de Sancionadores."""
    st.markdown(f"### Processo nº {row.get('processo', '—')}")
    st.write(f"**Relator (julgamento):** {row.get('relator_nome', '—')}")
    st.write(f"**Julgado em:** {row.get('data_julg', '—')}  ·  "
             f"**Peça:** {row.get('tipo', '—')}  ·  "
             f"**Rito:** {row.get('rito', '—')}  ·  "
             f"**Sup.:** {row.get('sup', '—')}")
    pubs = _pubs_do_processo(row.get("proc_norm") or row.get("processo", ""))
    if pubs:
        st.markdown(f"**📑 Publicações no Diário (SEI) ({len(pubs)}):**")
        tp = pd.DataFrame([{"Data": p["data"], "Tipo": p["tipo"],
                            "Unidade": p["unidade"], "Resumo": (p["resumo"] or "")[:200],
                            "Link": p["link"]} for p in pubs[:60]])
        tabela(tp, datas=["Data"], use_container_width=True, hide_index=True,
               column_config={"Link": st.column_config.LinkColumn(
                   "Link", display_text="abrir ↗")})
    else:
        st.info("Este processo não está na base de Sancionadores coletada — a ficha "
                "completa (objeto, acusados, desfecho e links) ainda não está disponível.")


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

    dmap = _mapa_desfecho()  # proc_norm -> resumo do desfecho por acusado
    res["Desfecho"] = res["proc_norm"].map(lambda pn: dmap.get(pn, "")) \
        if "proc_norm" in res.columns else ""

    def _extrato_julg(pn):
        """Link do 'Extrato de Sessão de Julgamento' publicado no Diário (SEI)."""
        pubs = _mapa_pubs_por_processo().get(pn, [])
        for p in pubs:
            if "extrato de sess" in p["tipo"].lower():
                return p["link"]
        return ""
    res["Extrato (SEI)"] = res["proc_norm"].map(_extrato_julg) \
        if "proc_norm" in res.columns else ""
    mmul = _mapa_multas()
    res["Multas (R$)"] = res["proc_norm"].map(
        lambda pn: mmul.get(pn, "")) if "proc_norm" in res.columns else ""
    cols = ["data_julg", "relator_nome", "processo", "tipo", "rito", "sup",
            "Desfecho", "Multas (R$)", "Extrato (SEI)", "Colegiado atual"]
    show = res[[c for c in cols if c in res.columns]].rename(columns={
        "data_julg": "Julgado em", "relator_nome": "Relator (julgamento)",
        "processo": "Processo", "tipo": "Tipo", "rito": "Rito",
        "sup": "Superintendência"})
    st.caption("👆 Clique numa linha para abrir a **ficha do processo** — desfecho "
               "por acusado, objeto/ementa, acusados, e links para a pasta de "
               "Sancionadores e o julgamento.")
    ev = tabela(show, datas=["Julgado em"], use_container_width=True, hide_index=True,
                height=460, on_select="rerun", selection_mode="single-row",
                column_config={"Extrato (SEI)": st.column_config.LinkColumn(
                    "Extrato (SEI)", display_text="abrir ↗")})
    st.download_button(
        "⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
        file_name="processos_julgados_cvm.csv", mime="text/csv")
    selr = ev.selection.rows if getattr(ev, "selection", None) else []
    if selr:
        linha = res.iloc[selr[0]]
        pn = _norm_proc(linha.get("proc_norm", "")) or \
            _norm_proc(linha.get("processo", ""))
        if pn and pn != st.session_state.get("dlg_julg"):
            st.session_state["dlg_julg"] = pn
            proc_pas, acus_pas = carregar_pas()
            hit = proc_pas[proc_pas["numero"].map(_norm_proc) == pn] \
                if proc_pas is not None else None
            if hit is not None and len(hit):
                dialog_processo(hit.iloc[0], acus_pas)
            else:
                dialog_julgado_simples(linha)


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
    lk = _mapa_link_pas()  # proc_norm -> URL da página do processo na CVM
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
                "Abertura do processo": aber.get(pn, "—"),
                "Link": lk.get(pn, ""), "proc_norm": pn})
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
                "Tempo até julgar (dias)": (dj - drel).days if (dj and drel) else None,
                "Link": lk.get(r["proc_norm"], "")})
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
    link_cfg = {"Link": st.column_config.LinkColumn("CVM", display_text="abrir ↗")}
    st.markdown("**📥 Estoque — o que está há mais tempo com o relator (topo = mais antigo):**")
    if len(e):
        tabela(e.drop(columns=["Relator", "proc_norm"]),
               datas=["Relator desde", "Abertura do processo"],
               dias=["Como relator há (dias)"], column_config=link_cfg,
               use_container_width=True, hide_index=True)
        st.caption("👆 Clique em **abrir ↗** para ver o processo no site da CVM.")
        st.bar_chart(e.set_index("Processo")["Como relator há (dias)"].head(20))
    else:
        st.info("Sem processos em estoque para este relator.")
    st.markdown("**✅ Julgados — quando recebeu a relatoria × quando julgou:**")
    if len(j):
        tabela(j.drop(columns=["Relator"]), datas=["Recebeu relatoria", "Julgado em"],
               dias=["Tempo até julgar (dias)"], column_config=link_cfg,
               use_container_width=True, hide_index=True)
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
    a_tc = _mapa_analises().get((pn, "tc"))
    if a_tc:
        with st.container(border=True):
            st.markdown("**🧠 Análise (IA):** " + str(a_tc.get("resumo") or ""))
            rac = str(a_tc.get("racional") or "").strip()
            if rac and rac != "-":
                st.caption(f"Racional: {rac}")
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
        _mt = _mapa_ata_por_data()
        for data, data_iso, tipo, ementa, link in decs[:15]:
            _la = _mt.get(str(data_iso or ""), "")
            _ata = (f' · <a href="?ata={quote(_la, safe="")}" target="_blank">'
                    "ver ata ↗</a>") if _la else ""
            st.markdown(f"- **{data}** ({tipo}): {ementa}  [abrir ↗]({link}){_ata}",
                        unsafe_allow_html=True)
    desp = despachos_do_processo(row["processo"])
    if desp:
        st.markdown(f"#### 🏛️ Despachos/audiências relacionados ({len(desp)})")
        st.caption("Cruzamento pelo número do processo (tolera número parcial).")
        for d in desp[:30]:
            st.markdown(f"- **{d['data']}** · {d['componente']} · "
                        f"{d['solicitante']} — {d['assunto']}  "
                        f"[abrir ↗]({d['link']})")
    pubs = _pubs_do_processo(row["processo"])
    if pubs:
        st.markdown(f"#### 📑 Publicações no Diário (SEI) ({len(pubs)})")
        st.caption("Inclui o Extrato de Termo de Compromisso e demais atos "
                   "publicados no Diário Eletrônico da CVM para este processo.")
        for p in pubs[:30]:
            _res = f" — {p['resumo'][:160]}" if str(p["resumo"]).strip() else ""
            st.markdown(f"- **{p['data']}** · {p['tipo']} ({p['unidade']})"
                        f"{_res}  [abrir ↗]({p['link']})")
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
        f_paut = st.selectbox(
            "🗓️ Pautado para julgamento", ["Todos",
            "Só com processo pautado (qualquer sessão)",
            "Só em pauta futura (indo a julgamento)"], key="tc_paut",
            help="Cruza o processo do TC com a Pauta de Sessão de Julgamento (SEI).")
    # mapa: proc_norm -> situação de pauta (última sessão)
    hoje_iso = dt.date.today().isoformat()
    paut = {}
    for pn_p, lst in _mapa_pauta_sei().items():
        u = max(lst, key=lambda x: str(x.get("data_sessao_iso") or ""))
        ds = str(u.get("data_sessao_iso") or "")
        sit = str(u.get("situacao") or "")
        fut = bool(ds) and ds >= hoje_iso and not sit.startswith("retirado")
        paut[pn_p] = {"data": u.get("data_sessao", ""), "situacao": sit,
                      "futuro": fut}
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
    if f_paut != "Todos":
        if f_paut.startswith("Só em pauta futura"):
            res = res[res["proc_norm"].map(
                lambda p: paut.get(p, {}).get("futuro", False))]
        else:
            res = res[res["proc_norm"].isin(paut.keys())]
    res["Pautado p/ sessão"] = res["proc_norm"].map(
        lambda p: (f"{paut[p]['data']} "
                   + ("🚫" if paut[p]["situacao"].startswith("retirado")
                      else "🗓️" if paut[p]["futuro"] else "•"))
        if p in paut else "")
    res = res.sort_values("data_decisao_iso", ascending=False).reset_index(drop=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Termos encontrados", f"{len(res):,}".replace(",", "."))
    c2.metric("Total na base", f"{len(df):,}".replace(",", "."))
    c3.metric("🗓️ Pautados p/ julgamento",
              int(res["Pautado p/ sessão"].astype(bool).sum()))
    cols = ["processo", "situacao", "Pautado p/ sessão", "data_decisao",
            "data_assinatura", "data_publicacao", "data_arquivamento",
            "partes", "link"]
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
        _mt = _mapa_ata_por_data()
        for data, data_iso, tipo, ementa, link in decs[:15]:
            _la = _mt.get(str(data_iso or ""), "")
            _ata = (f' · <a href="?ata={quote(_la, safe="")}" target="_blank">'
                    "ver ata ↗</a>") if _la else ""
            st.markdown(f"- **{data}** ({tipo}): {ementa}  [abrir ↗]({link}){_ata}",
                        unsafe_allow_html=True)
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
    ("Otto Lobo", "2025-08-01", "2025-12-31"),      # interino; mandato encerrou
    ("João Accioly", "2026-01-01", "9999-12-31"),   # presidente interino
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
def carregar_pauta_sei():
    """Histórico de pautas publicadas no Diário (SEI): inclusões e retiradas."""
    if not os.path.exists(PAUTAS_DB_PATH):
        return None
    con = sqlite3.connect(PAUTAS_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM pauta_sei ORDER BY data_sessao_iso DESC", con)
    except Exception:
        df = None
    con.close()
    return df


@st.cache_data(ttl=300)
def _mapa_pauta_sei():
    """proc_norm -> lista de inclusões/retiradas em pauta (Diário/SEI)."""
    df = carregar_pauta_sei()
    out = {}
    if df is None or len(df) == 0:
        return out
    for _, r in df.iterrows():
        pn = str(r.get("proc_norm") or "")
        if pn:
            out.setdefault(pn, []).append(r.to_dict())
    return out


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
    """Decisões do Colegiado (data, data_iso, tipo, ementa, link) de um processo."""
    if not pn or not os.path.exists(DECISOES_DB_PATH):
        return []
    con = sqlite3.connect(DECISOES_DB_PATH)
    try:
        rows = con.execute(
            "SELECT data,data_iso,tipo,ementa,link FROM decisoes WHERE proc_norm=? "
            "ORDER BY data_iso DESC", (pn,)).fetchall()
    except Exception:
        rows = []
    con.close()
    return rows


@st.cache_data(ttl=600)
def _mapa_ata_por_data():
    """data_iso -> link da ata da reuniao do Colegiado daquela data."""
    df = carregar_atas_colegiado()
    if df is None or "data_iso" not in df.columns:
        return {}
    out = {}
    for _, r in df.iterrows():
        di, lk = str(r.get("data_iso") or ""), str(r.get("link") or "")
        if di and lk and di not in out:
            out[di] = lk
    return out


def _ver_ata_link(la):
    """HTML do 'ver ata' que abre o popup da ata DO NOSSO APP em nova aba
    (window.top é same-origin no iframe do components.html)."""
    if not la:
        return "—"
    return ("<a href=\"#\" onclick=\"window.open(window.top.location.pathname"
            "+'?ata='+encodeURIComponent('" + la + "'),'_blank');return false;\" "
            "style=\"cursor:pointer\">ver ata ↗</a>")


def _ata_por_link(link):
    """Linha (dict) da ata pelo seu link, para abrir o popup por ?ata=."""
    if not link or not os.path.exists(DECISOES_DB_PATH):
        return None
    con = sqlite3.connect(DECISOES_DB_PATH)
    try:
        cur = con.execute(
            "SELECT link,titulo,data,data_iso,tipo,texto,ficha,anexos "
            "FROM atas_colegiado WHERE link=?", (link,))
        cols = [d[0] for d in cur.description]
        r = cur.fetchone()
    except Exception:
        cols, r = [], None
    con.close()
    return dict(zip(cols, r)) if r else None


def _abrir_ata_por_query():
    """Se a URL tiver ?ata=<link>, abre o popup daquela ata (uma vez)."""
    al = st.query_params.get("ata")
    if not al or st.session_state.get("_ata_q") == al:
        return
    st.session_state["_ata_q"] = al
    row = _ata_por_link(al)
    if row is not None:
        dialog_ata_colegiado(row)


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


def _ficha_ata_html(row, e):
    """Bloco HTML da ficha (IA) da ata — resumo, participantes e, por item,
    posição da área técnica e como cada diretor votou. '' se não houver ficha."""
    try:
        f = json.loads(row.get("ficha") or "")
    except (ValueError, TypeError):
        return ""
    if not isinstance(f, dict):
        return ""
    partes = []
    if f.get("resumo"):
        partes.append(f'<p style="font-size:13px"><b>Resumo:</b> '
                      f'{e(f["resumo"])}</p>')
    if f.get("participantes"):
        partes.append(f'<p style="font-size:12px"><b>Participantes:</b> '
                      f'{e(f["participantes"])}</p>')
    itens = f.get("itens") or []
    if itens:
        partes.append(f'<h3>Itens da reunião ({len(itens)})</h3>')
    for it in itens:
        cab = e(it.get("assunto", "")) or "—"
        proc = e(it.get("processo", ""))
        rel = e(it.get("relator", ""))
        # Link do extrato no Diário (SEI) para o processo do item, se houver.
        lk_pub = ""
        for p in _pubs_do_processo(it.get("processo", "")):
            if "extrato de ata" in p["tipo"].lower():
                lk_pub = p["link"]
                break
        meta = " &nbsp;·&nbsp; ".join(x for x in (
            f"<b>Proc.</b> {proc}" if proc else "",
            f"<b>Relator:</b> {rel}" if rel else "",
            (f'<a href="{e(lk_pub)}" target="_blank">Extrato no Diário (SEI) ↗</a>'
             if lk_pub else "")) if x)
        partes.append(
            f'<div style="margin:8px 0 4px 0"><b>{cab}</b>'
            + (f'<br><span style="font-size:12px">{meta}</span>' if meta else "")
            + '</div><table>'
            f'<tr><td class="upperHeader" style="white-space:nowrap">Área técnica</td>'
            f'<td>{e(it.get("area_tecnica", "—"))}</td></tr>'
            f'<tr><td class="upperHeader">Decisão</td>'
            f'<td>{e(it.get("decisao", "—"))}</td></tr>'
            f'<tr><td class="upperHeader">Como votaram</td>'
            f'<td>{e(it.get("votos", "—"))}</td></tr></table>')
    return "".join(partes)


@st.dialog("Ata do Colegiado", width="large")
def dialog_ata_colegiado(row):
    e = _esc
    st.link_button("Abrir no site da CVM ↗", row["link"])
    ficha_html = _ficha_ata_html(row, e)
    texto = str(row.get("texto") or "").strip()
    if texto:
        corpo = re.sub(r"(\s)(PROC\.|PROCESSO|Reg\. n)", r"<br><br>\2", e(texto))
        # com ficha, a integra fica recolhida num <details>
        if ficha_html:
            texto_html = ('<details><summary style="cursor:pointer;font-size:13px">'
                          '<b>Íntegra da ata</b></summary>'
                          f'<div style="text-align:justify;margin-top:6px">{corpo}'
                          '</div></details>')
        else:
            texto_html = f'<div style="text-align:justify">{corpo}</div>'
    else:
        texto_html = ('<p style="font-size:13px">— conteúdo integral ainda não '
                      'coletado para esta ata. Use o botão acima. —</p>')
    if ficha_html:
        ficha_html = ('<p style="font-size:11px;color:#777">Ficha resumida por IA '
                      '(área técnica e votos) — confira a íntegra em caso de '
                      'dúvida.</p>' + ficha_html + '<hr>')
    # 📎 Anexos: manifestação da área técnica e VOTOS dos diretores (PDFs)
    try:
        anx = json.loads(row.get("anexos") or "[]")
    except (ValueError, TypeError):
        anx = []
    if isinstance(anx, list) and anx:
        li = "".join(
            f'<li><a href="{e(a.get("link", ""))}" target="_blank">'
            f'{e(a.get("titulo", "documento"))} ↗</a></li>'
            for a in anx if a.get("link"))
        anexos_html = (f'<h3>📎 Votos e relatórios ({len(anx)})</h3>'
                       '<p style="font-size:12px">Documentos oficiais da reunião '
                       '(manifestação da área técnica e voto de cada diretor).</p>'
                       f'<ul style="font-size:13px">{li}</ul><hr>')
    else:
        anexos_html = ""
    doc = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<style>{CSS_CVM} #width{{font-size:13px;line-height:1.55}}</style>'
        '</head><body><div id="width">'
        f'<h2>{e(row["titulo"])}</h2>'
        f'<p style="font-size:12px"><b>Data:</b> {e(row["data"])} &nbsp;·&nbsp; '
        f'<b>Tipo:</b> {e(row["tipo"])}</p>'
        f'{ficha_html}{anexos_html}{texto_html}</div></body></html>')
    components.html(doc, height=640, scrolling=True)


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
    aba_d, aba_a, aba_v = st.tabs(["📜 Decisões", "📋 Atas do Colegiado",
                                   "🗳️ Perfil de votação"])
    with aba_v:
        stats_v, tot_v, det_v = _minerar_votos()
        if not tot_v.get("itens"):
            st.info("⏳ As fichas IA das atas ainda não estão disponíveis.")
        else:
            st.caption(f"Minerado das fichas IA de {tot_v['itens']} itens de pauta "
                       "das atas do Colegiado (2022+). Dado inédito: como cada "
                       "diretor vota.")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Itens analisados", tot_v["itens"])
            pu = 100 * tot_v["unanime"] / max(1, tot_v["itens"])
            c2.metric("Unânimes", f"{tot_v['unanime']} ({pu:.0f}%)")
            c3.metric("Por maioria", tot_v["maioria"])
            c4.metric("Com pedido de vista", tot_v["vista"])
            ca, cb = st.columns(2)
            ca.metric("Colegiado ACOMPANHOU a área técnica",
                      tot_v["acompanha_area"])
            cb.metric("Colegiado DIVERGIU da área técnica",
                      tot_v["diverge_area"])
            cond = _conduta_resumo()
            if cond is not None and len(cond):
                st.markdown("**Conduta decisória consolidada (base do agente "
                            "por diretor):**")
                st.caption("Cruza julgados × multas dos extratos × fichas das "
                           "atas × retiradas de pauta (conduta.db).")
                st.dataframe(cond, use_container_width=True, hide_index=True)
            elif stats_v:
                st.markdown("**Por diretor:**")
                rk = pd.DataFrame([
                    {"Diretor": d, "Votos vencidos": s["vencido"],
                     "Pedidos de vista": s["vista"],
                     "Divergências abertas": s["divergencia"]}
                    for d, s in stats_v.items()]).sort_values(
                        ["Votos vencidos", "Pedidos de vista"], ascending=False)
                st.dataframe(rk, use_container_width=True, hide_index=True)
            if det_v is not None and len(det_v):
                with st.expander(f"🔍 Casos detectados ({len(det_v)})"):
                    st.dataframe(det_v, use_container_width=True, hide_index=True,
                                 column_config={"Ata": st.column_config.LinkColumn(
                                     "Ata", display_text="abrir ↗")})
            # Dossiês de conduta (IA) — base do "agente" de cada diretor
            if os.path.exists(CONDUTA_DB_PATH):
                conp = sqlite3.connect(CONDUTA_DB_PATH)
                try:
                    perfis = {d: json.loads(j) for d, j in conp.execute(
                        "SELECT diretor, dossie FROM perfis WHERE ai_feito=1")}
                except Exception:
                    perfis = {}
                conp.close()
                if perfis:
                    st.divider()
                    st.markdown("#### 🤖 Dossiê de conduta por diretor (base do agente)")
                    dsel = st.selectbox("Diretor", sorted(perfis), key="dossie_dir",
                                        index=None, placeholder="escolha um diretor…")
                    if dsel:
                        dd = perfis[dsel]
                        for rot, campo in [
                                ("Perfil", "perfil_resumido"),
                                ("Padrão decisório", "padrao_decisorio"),
                                ("Severidade / dosimetria", "severidade_dosimetria"),
                                ("Temas recorrentes", "temas_recorrentes"),
                                ("Divergências e vistas", "divergencias_e_vistas")]:
                            v = str(dd.get(campo) or "").strip()
                            if v:
                                st.markdown(f"**{rot}:** {v}")
                        casos = dd.get("casos_marcantes") or []
                        if casos:
                            st.markdown("**Casos marcantes:**")
                            for c in casos:
                                st.markdown(f"- {c}")
                        sp = str(dd.get("system_prompt") or "").strip()
                        if sp:
                            with st.expander("🤖 System prompt do agente (copiar)"):
                                st.code(sp, language=None)

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
            temtxt = "texto" in atc.columns
            c1, c2 = st.columns([2, 1])
            q = c1.text_input(
                "Buscar (título" + (" e conteúdo da ata" if temtxt else "") + ")",
                key="at_q")
            anos = sorted({a[:4] for a in atc["data_iso"].dropna() if a}, reverse=True)
            f_ano = c2.multiselect("Ano", anos, key="at_ano")
            m = pd.Series(True, index=atc.index)
            if q.strip():
                alvo = atc["titulo"].fillna("")
                if temtxt:
                    alvo = alvo + " " + atc["texto"].fillna("")
                alvo = alvo.str.lower()
                for w in q.lower().split():
                    m &= alvo.str.contains(re.escape(w), na=False)
            if f_ano:
                m &= atc["data_iso"].str[:4].isin(f_ano)
            r2 = atc[m].sort_values("data_iso", ascending=False).reset_index(drop=True)
            st.metric("Atas encontradas", f"{len(r2):,}".replace(",", "."))
            if temtxt:
                n_txt = int(r2["texto"].fillna("").str.len().gt(0).sum())
                st.caption("👆 Clique numa linha para ler a ata inteira. Conteúdo "
                           f"integral em {n_txt} de {len(r2)} (varredura desde 2022).")
            show = r2[["data", "tipo", "titulo", "link"]].rename(columns={
                "data": "Data", "tipo": "Tipo", "titulo": "Título", "link": "Link"})
            ev = tabela(show, datas=["Data"], use_container_width=True, hide_index=True,
                        height=460, on_select="rerun", selection_mode="single-row",
                        column_config={"Link": st.column_config.LinkColumn(
                            "Link", display_text="abrir ↗")})
            st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                               file_name="atas_colegiado.csv", mime="text/csv")
            selr = ev.selection.rows if getattr(ev, "selection", None) else []
            if selr:
                lk = r2.iloc[selr[0]]["link"]
                if lk != st.session_state.get("dlg_ata_col"):
                    st.session_state["dlg_ata_col"] = lk
                    dialog_ata_colegiado(r2.iloc[selr[0]])


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
    # --- Pautas publicadas no Diário (SEI): 1 linha por PROCESSO (última situação) ---
    psei = carregar_pauta_sei()
    if psei is not None and len(psei):
        st.divider()
        st.markdown("#### 📜 Pautas de julgamento publicadas no Diário (SEI)")
        st.caption(
            f"Fonte oficial · {len(psei)} inclusões/retiradas. Agregado por processo "
            "(situação mais recente). **Já julgados** vão para a aba ✅ Julgados; aqui "
            "ficam **pendentes** e **retirados de pauta**.")
        hoje_iso = hoje.isoformat()
        p2 = psei.copy()
        p2["_ret"] = p2["situacao"].fillna("").str.startswith("retirado")
        p2["_ord"] = p2["data_sessao_iso"].replace("", "0000-00-00")
        ever_ret = p2.groupby("proc_norm")["_ret"].any()
        # última linha (sessão mais recente) por processo
        ult = p2.sort_values("_ord").groupby("proc_norm", as_index=False).tail(1).copy()
        ult["_julg"] = ult["proc_norm"].isin(set_julg)
        ult["_ever_ret"] = ult["proc_norm"].map(ever_ret)
        tem_data = ult["data_sessao_iso"] != ""
        fut = tem_data & (ult["data_sessao_iso"] >= hoje_iso)
        passou = tem_data & (ult["data_sessao_iso"] < hoje_iso)
        cols_sei = {"data_sessao": "Sessão", "processo": "Processo",
                    "relator": "Relator", "situacao": "Situação", "objeto": "Objeto",
                    "link_sei": "Documento"}
        lc = {"Documento": st.column_config.LinkColumn("Documento",
                                                       display_text="abrir ↗")}
        # Tirados: não julgado E (última ação = retirada OU sessão passou sem julgar)
        tir = ult[(~ult["_julg"]) & (ult["_ret"] | passou)]
        st.markdown(f"##### 🚨 Tirados de pauta pelo Diário ({len(tir)})")
        if len(tir):
            st.caption("Processo não julgado cuja situação mais recente é retirada "
                       "('retirado da pauta', inclusive *sine die*) ou cuja sessão "
                       "passou sem julgamento. Já consta no popup do processo.")
            tabela(tir.sort_values("_ord", ascending=False)[list(cols_sei)]
                   .rename(columns=cols_sei), datas=["Sessão"], hide_index=True,
                   use_container_width=True, column_config=lc)
        else:
            st.success("Nenhum retirado de pauta pendente detectado no Diário.")
        # Pendentes: não julgado, última sessão futura, última ação não é retirada
        pend = ult[(~ult["_julg"]) & fut & (~ult["_ret"])]
        st.markdown(f"##### ⏳ Pautados pendentes de julgamento ({len(pend)})")
        if len(pend):
            tabela(pend.sort_values("_ord")[list(cols_sei)].rename(columns=cols_sei),
                   datas=["Sessão"], hide_index=True, use_container_width=True,
                   column_config=lc)
        else:
            st.info("Nenhum processo pautado pendente no momento.")
        # Advogados que atuam nas sessões (extraído das pautas do Diário)
        adv = _advogados_pautas()
        if adv is not None and len(adv):
            with st.expander(f"⚖️ Advogados nas sessões de julgamento "
                             f"({adv['Advogado'].nunique()})"):
                rk = (adv.groupby(["Advogado", "OAB"]).size()
                      .rename("Processos").reset_index()
                      .sort_values("Processos", ascending=False))
                st.markdown("**Ranking (mais atuantes):**")
                st.dataframe(rk.head(25), use_container_width=True, hide_index=True)
                st.markdown("**Por processo:**")
                st.dataframe(adv.sort_values("Sessão", ascending=False),
                             use_container_width=True, hide_index=True, height=300)

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

    # Cruzamento com o Boletim de Pessoal: movimentações e viagens da pessoa.
    st.divider()
    with st.expander("📋 Cruzar com o Boletim de Pessoal (movimentações e viagens)"):
        nome_sel = st.selectbox("Pessoa", sorted(df["nome"].dropna().unique()),
                                key="qq_bp_nome", index=None,
                                placeholder="escolha alguém do organograma…")
        if nome_sel:
            k = _ent_key(nome_sel)
            if os.path.exists(PESSOAL_DB_PATH):
                con = sqlite3.connect(PESSOAL_DB_PATH)
                try:
                    vg = pd.read_sql(
                        "SELECT * FROM viagens WHERE servidor_key=:k", con,
                        params={"k": k})
                    bols = pd.read_sql(
                        "SELECT numero, data, data_iso, pdf_url FROM boletins "
                        "WHERE texto LIKE :n ORDER BY data_iso DESC LIMIT 60",
                        con, params={"n": f"%{nome_sel}%"})
                except Exception:
                    vg, bols = pd.DataFrame(), pd.DataFrame()
                con.close()
                if len(vg):
                    st.markdown(f"**✈️ Viagens ({len(vg)}):**")
                    st.dataframe(
                        vg[["boletim_data_iso", "tipo", "origem", "destino",
                            "periodo_ini", "periodo_fim", "valor_diarias",
                            "motivo"]].rename(columns={
                                "boletim_data_iso": "Boletim", "tipo": "Tipo",
                                "origem": "Origem", "destino": "Destino",
                                "periodo_ini": "Início", "periodo_fim": "Fim",
                                "valor_diarias": "Diárias (R$)", "motivo": "Motivo"})
                        .sort_values("Boletim", ascending=False),
                        use_container_width=True, hide_index=True)
                if len(bols):
                    st.markdown(f"**📋 Citado em {len(bols)} boletins de pessoal** "
                                "(nomeações, designações, afastamentos…):")
                    st.dataframe(bols.rename(columns={
                        "numero": "Boletim", "data": "Data",
                        "pdf_url": "PDF"})[["Boletim", "Data", "PDF"]],
                        use_container_width=True, hide_index=True,
                        column_config={"PDF": st.column_config.LinkColumn(
                            "PDF", display_text="abrir ↗")})
                if not len(vg) and not len(bols):
                    st.info("Nenhuma menção no Boletim de Pessoal (2000–hoje).")


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


@st.cache_data(ttl=300)
def carregar_publicacoes():
    if not os.path.exists(PUBLICACOES_DB_PATH):
        return None
    con = sqlite3.connect(PUBLICACOES_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM publicacoes", con)
    except Exception:
        df = None
    con.close()
    return df


def _pub_tipo(descricao):
    """Tipo da publicação = descrição sem o número final (ex.: 'Intimação 12')."""
    return re.sub(r"\s*\d+\s*$", "", str(descricao or "")).strip()


_RE_PROC_SEI = re.compile(r"1\d{4}\.\d{6}/\d{4}-\d{2}")


@st.cache_data(ttl=300)
def _mapa_pubs_por_processo():
    """proc_norm -> lista de publicações (Diário Eletrônico/SEI) daquele processo.

    Casa pelo número SEI completo (19957.xxxxxx/aaaa-dd) citado na descrição ou no
    resumo da publicação. 1037/1544 publicações trazem número de processo.
    """
    df = carregar_publicacoes()
    out = {}
    if df is None or len(df) == 0:
        return out
    for _, r in df.iterrows():
        blob = f"{r.get('descricao', '') or ''} {r.get('resumo', '') or ''}"
        for pn in {m.group(0).upper() for m in _RE_PROC_SEI.finditer(blob)}:
            out.setdefault(pn, []).append({
                "data": r.get("data", "") or "",
                "data_iso": r.get("data_iso", "") or "",
                "tipo": _pub_tipo(r.get("descricao", "")),
                "descricao": r.get("descricao", "") or "",
                "resumo": r.get("resumo", "") or "",
                "unidade": r.get("unidade", "") or "",
                "link": r.get("link", "") or "",
            })
    for pn in out:
        out[pn].sort(key=lambda x: x["data_iso"], reverse=True)
    return out


def _pubs_do_processo(numero):
    """Atalho: publicações SEI de um processo, dado o número (qualquer formato)."""
    return _mapa_pubs_por_processo().get(_norm_proc(numero), [])


def render_publicacoes():
    st.subheader("📑 Publicações Eletrônicas (SEI) — CVM")
    df = carregar_publicacoes()
    if df is None or len(df) == 0:
        st.info("⏳ A base de Publicações Eletrônicas (Diário Eletrônico da CVM) "
                "ainda está sendo coletada.")
        return
    df = df.copy()
    df["_tipo"] = (df["descricao"].fillna("")
                   .str.replace(r"\s*\d+\s*$", "", regex=True).str.strip())
    st.caption(f"{len(df):,} publicações do Diário Eletrônico da CVM (SEI). "
               "Fonte: sei.cvm.gov.br/publicacoes.".replace(",", "."))
    with st.expander("🔎 Filtros", expanded=True):
        c1, c2 = st.columns([2, 1])
        q = c1.text_input("Buscar (descrição, protocolo, unidade, resumo)", key="pub_q")
        anos = sorted({a[:4] for a in df["data_iso"].dropna() if a}, reverse=True)
        f_ano = c2.multiselect("Ano", anos, key="pub_ano")
        c3, c4 = st.columns(2)
        tipos = sorted(t for t in df["_tipo"].dropna().unique() if t)
        f_tipo = c3.multiselect("Tipo de publicação", tipos, key="pub_tipo")
        uns = sorted(u for u in df["unidade"].dropna().unique() if u)
        f_un = c4.multiselect("Unidade", uns, key="pub_un")
    m = pd.Series(True, index=df.index)
    if q.strip():
        col = (df["descricao"].fillna("") + " " + df["protocolo"].fillna("") + " "
               + df["unidade"].fillna("") + " " + df["resumo"].fillna("")).str.lower()
        for w in q.lower().split():
            m &= col.str.contains(re.escape(w), na=False)
    if f_ano:
        m &= df["data_iso"].str[:4].isin(f_ano)
    if f_tipo:
        m &= df["_tipo"].isin(f_tipo)
    if f_un:
        m &= df["unidade"].isin(f_un)
    res = df[m].sort_values("data_iso", ascending=False).reset_index(drop=True)
    st.metric("Publicações encontradas", f"{len(res):,}".replace(",", "."))
    show = res[["data", "descricao", "unidade", "orgao", "veiculo", "link"]].rename(
        columns={"data": "Data", "descricao": "Descrição", "unidade": "Unidade",
                 "orgao": "Órgão", "veiculo": "Veículo", "link": "Link"})
    tabela(show, datas=["Data"], use_container_width=True, hide_index=True, height=460,
           column_config={"Link": st.column_config.LinkColumn(
               "Link", display_text="abrir ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="publicacoes_cvm.csv", mime="text/csv")


@st.cache_data(ttl=300)
def carregar_viagens():
    """Viagens (afastamentos do país e diárias) extraídas do Boletim de Pessoal."""
    if not os.path.exists(PESSOAL_DB_PATH):
        return None
    con = sqlite3.connect(PESSOAL_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM viagens", con)
    except Exception:
        df = None
    con.close()
    return df


@st.cache_data(ttl=300)
def carregar_movimentos():
    """Movimentos de cargo (nomeação/exoneração/designação) do Boletim de Pessoal."""
    if not os.path.exists(PESSOAL_DB_PATH):
        return None
    con = sqlite3.connect(PESSOAL_DB_PATH)
    try:
        df = pd.read_sql("SELECT * FROM movimentos", con)
    except Exception:
        df = None
    con.close()
    return df


def _ocupacao_atual(mov):
    """Posição mais atual de CADA servidor: seu último movimento; quando é
    nomeação/designação com função, representa a posição vigente/última
    conhecida (uma linha por servidor). Exonerações fora das seções lidas
    podem não aparecer, então é a última posição *conhecida*."""
    if mov is None or not len(mov):
        return pd.DataFrame()
    m = mov.sort_values("boletim_data_iso")
    ult = m.groupby("servidor_key", as_index=False).tail(1)
    return ult[ult["tipo"].isin(["nomeacao", "designacao"])
               & ult["funcao"].astype(bool)].copy()


def _superintendentes_atuais(mov):
    """Titular mais recente da função de Superintendente em cada unidade (o
    superintendente de cada área). Exclui adjuntos/substitutos."""
    if mov is None or not len(mov):
        return pd.DataFrame()
    sup = mov[mov["funcao"].str.match(r"(?i)\s*Superintendente", na=False)
              & ~mov["funcao"].str.contains(r"(?i)adjunt|substitut", na=False)
              & mov["tipo"].isin(["designacao", "nomeacao"])
              & (mov["sigla"] != "")]
    if not len(sup):
        return pd.DataFrame()
    # titular mais recente por área…
    sup = sup.sort_values("boletim_data_iso").groupby(
        "sigla", as_index=False).tail(1)
    # …e cada pessoa em UMA só área (a mais recente): remove entradas obsoletas
    # de quem depois assumiu outra superintendência.
    return sup.sort_values("boletim_data_iso").groupby(
        "servidor_key", as_index=False).tail(1)


@st.dialog("🗂️ Ficha do servidor", width="large")
def dialog_servidor(nome):
    """Ficha individual completa do servidor (aberta ao clicar no nome)."""
    _ficha_servidor(nome)


def _abrir_ficha_sel(ev, disp, statekey, col="Servidor"):
    """Abre a ficha do servidor da linha selecionada num st.dataframe."""
    rows = ev.selection.rows if getattr(ev, "selection", None) else []
    if rows:
        nome = disp.iloc[rows[0]][col]
        if nome and nome != st.session_state.get(statekey):
            st.session_state[statekey] = nome
            dialog_servidor(nome)


_MOV_LABEL = {"nomeacao": "🟢 Nomeação", "exoneracao": "🔴 Exoneração",
              "designacao": "🔵 Designação"}


def render_organograma():
    st.markdown("#### 🏛️ Organograma — posição atual dos servidores")
    mov = carregar_movimentos()
    if mov is None or len(mov) == 0:
        st.info("⏳ Movimentos de cargo ainda não extraídos do Boletim de Pessoal.")
        return
    mov = mov.copy()
    mov["_ano"] = mov["boletim_data_iso"].astype(str).str[:4]
    nsig = int(mov[mov["sigla"] != ""]["sigla"].nunique())
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Nomeações", int((mov["tipo"] == "nomeacao").sum()))
    c2.metric("🔴 Exonerações", int((mov["tipo"] == "exoneracao").sum()))
    c3.metric("🔵 Designações", int((mov["tipo"] == "designacao").sum()))
    c4.metric("🏢 Unidades", nsig)
    st.caption("👉 **Clique em qualquer servidor** para abrir a ficha individual "
               "(trajetória de cargos, viagens, boletins e menções no Diário).")
    # headline: superintendentes das areas (atuais)
    sup = _superintendentes_atuais(mov)
    if len(sup):
        st.markdown("##### 🎖️ Superintendentes das áreas (atuais)")
        supd = sup.sort_values("sigla")[
            ["sigla", "unidade", "servidor_nome", "codigo",
             "boletim_data_iso"]].rename(columns={
                 "sigla": "Sigla", "unidade": "Superintendência",
                 "servidor_nome": "Superintendente", "codigo": "Código",
                 "boletim_data_iso": "Desde (boletim)"})
        evs = st.dataframe(supd, use_container_width=True, hide_index=True,
                           height=min(430, 45 + 35 * len(supd)), on_select="rerun",
                           selection_mode="single-row", key="org_sup_sel")
        _abrir_ficha_sel(evs, supd, "_srv_open_sup", "Superintendente")
        st.caption(f"{len(supd)} unidades — titular mais recente da função de "
                   "Superintendente em cada área. Clique para a ficha. ⚠️ estimado "
                   "a partir dos boletins.")
        st.divider()
    sub = st.radio("Ver", ["Quadro atual (por servidor)",
                           "Histórico de movimentos"], horizontal=True,
                   key="org_sub", label_visibility="collapsed")
    if sub == "Quadro atual (por servidor)":
        pos = _ocupacao_atual(mov)
        if not len(pos):
            st.info("Sem posições estimáveis na base atual.")
            return
        nomeun = pos[pos["sigla"] != ""].groupby("sigla")["unidade"].agg(
            lambda s: s.value_counts().index[0] if len(s.value_counts()) else "")
        with st.expander("🔎 Filtros", expanded=True):
            c1, c2 = st.columns(2)
            q = c1.text_input("Servidor ou função contém", key="org_q")
            sigs = sorted(s for s in pos["sigla"].dropna().unique() if s)
            f_sig = c2.multiselect(
                "Unidade (sigla)", sigs, key="org_fsig",
                format_func=lambda s: f"{s} — {nomeun.get(s, '')}"[:50])
        m = pd.Series(True, index=pos.index)
        if q.strip():
            m &= (pos["servidor_nome"].str.contains(re.escape(q), case=False,
                                                    na=False)
                  | pos["funcao"].str.contains(re.escape(q), case=False, na=False))
        if f_sig:
            m &= pos["sigla"].isin(f_sig)
        r = pos[m].sort_values(["sigla", "funcao", "servidor_nome"])
        st.caption(f"{len(r):,} servidores — cada um na sua posição mais recente "
                   "conhecida (uma linha por servidor).".replace(",", "."))
        show = r[["sigla", "unidade", "funcao", "codigo", "servidor_nome",
                  "boletim_data_iso"]].rename(columns={
                      "sigla": "Sigla", "unidade": "Unidade", "funcao": "Função",
                      "codigo": "Código", "servidor_nome": "Servidor",
                      "boletim_data_iso": "Desde (boletim)"})
        ev = st.dataframe(show, use_container_width=True, hide_index=True,
                          height=460, on_select="rerun",
                          selection_mode="single-row", key="org_pos_sel")
        _abrir_ficha_sel(ev, show, "_srv_open_pos")
        st.download_button(
            "⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
            file_name="posicao_atual_servidores_cvm.csv", mime="text/csv")
        st.caption("⚠️ 'Posição atual' = último movimento captado de cada servidor; "
                   "exonerações fora das seções lidas podem não aparecer. O "
                   "**Colegiado** (presidente e diretores) é autoritativo na aba "
                   "Colegiado.")
    else:
        with st.expander("🔎 Filtros", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            q = c1.text_input("Servidor (nome contém)", key="org_hq")
            f_tipo = c2.multiselect("Tipo", list(_MOV_LABEL),
                                    format_func=lambda t: _MOV_LABEL[t],
                                    key="org_htipo")
            sigs = sorted(s for s in mov["sigla"].dropna().unique() if s)
            f_sig = c3.multiselect("Unidade (sigla)", sigs, key="org_hsig")
            anos = sorted((a for a in mov["_ano"].unique() if a and a != "None"),
                          reverse=True)
            f_ano = c4.multiselect("Ano", anos, key="org_hano")
        m = pd.Series(True, index=mov.index)
        if q.strip():
            m &= mov["servidor_nome"].str.contains(re.escape(q), case=False, na=False)
        if f_tipo:
            m &= mov["tipo"].isin(f_tipo)
        if f_sig:
            m &= mov["sigla"].isin(f_sig)
        if f_ano:
            m &= mov["_ano"].isin(f_ano)
        res = mov[m].copy()
        res["Movimento"] = res["tipo"].map(_MOV_LABEL).fillna(res["tipo"])
        res["Cargo/Função"] = res["funcao"].where(
            res["funcao"].astype(bool), res["cargo_efetivo"])
        st.caption(f"{len(res):,} movimentos no filtro.".replace(",", "."))
        show = res.sort_values("boletim_data_iso", ascending=False)[
            ["boletim_data_iso", "Movimento", "servidor_nome", "Cargo/Função",
             "codigo", "sigla", "unidade", "portaria"]].rename(
                columns={"boletim_data_iso": "Data", "servidor_nome": "Servidor",
                         "codigo": "Código", "sigla": "Sigla", "unidade": "Unidade",
                         "portaria": "Portaria"})
        ev = st.dataframe(show, use_container_width=True, hide_index=True,
                          height=460, on_select="rerun",
                          selection_mode="single-row", key="org_hist_sel")
        _abrir_ficha_sel(ev, show, "_srv_open_hist")
        st.download_button(
            "⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
            file_name="movimentos_cargo_cvm.csv", mime="text/csv")


def _ficha_servidor(nome):
    """Tudo sobre um servidor: viagens, boletins que o citam e menções no SEI."""
    st.markdown(f"### 🗂️ {nome}")
    k = _ent_key(nome)
    con = sqlite3.connect(PESSOAL_DB_PATH)
    vg = pd.read_sql("SELECT * FROM viagens WHERE servidor_key=:k", con,
                     params={"k": k})
    try:
        mov = pd.read_sql(
            "SELECT tipo, funcao, codigo, sigla, unidade, cargo_efetivo, portaria, "
            "data_ato_iso, boletim_data_iso, link_boletim FROM movimentos WHERE "
            "servidor_key=:k ORDER BY boletim_data_iso DESC", con, params={"k": k})
    except Exception:
        mov = pd.DataFrame()
    bols = pd.read_sql(
        "SELECT numero, data, pdf_url FROM boletins WHERE texto LIKE :n "
        "ORDER BY data_iso DESC LIMIT 60", con, params={"n": f"%{nome}%"})
    con.close()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🏛️ Movimentos", len(mov))
    c2.metric("✈️ Viagens", len(vg))
    try:
        tot = sum(float(str(v).replace(".", "").replace(",", "."))
                  for v in vg["valor_diarias"] if str(v).strip())
    except Exception:
        tot = 0
    c3.metric("💰 Diárias (R$)", f"{tot:,.0f}".replace(",", "."))
    c4.metric("📋 Citado em boletins", len(bols))
    # trajetória de cargos: o que o servidor ocupa/ocupou
    if len(mov):
        atual = mov[mov["tipo"].isin(["nomeacao", "designacao"])
                    & (mov["funcao"] != "")]
        if len(atual):
            top = atual.iloc[0]
            st.success(f"**Posição mais recente:** {top['funcao']}"
                       + (f" ({top['codigo']})" if top['codigo'] else "")
                       + (f" — {top['unidade']} ({top['sigla']})" if top['sigla']
                          else "") + f"  ·  {top['boletim_data_iso']}")
        st.markdown("**🏛️ Trajetória de cargos** (nomeações 🟢, designações 🔵, "
                    "exonerações 🔴):")
        _TL = {"nomeacao": "🟢 Nomeação", "exoneracao": "🔴 Exoneração",
               "designacao": "🔵 Designação"}
        disp = mov.copy()
        disp["Movimento"] = disp["tipo"].map(_TL).fillna(disp["tipo"])
        disp["Cargo/Função"] = disp["funcao"].where(
            disp["funcao"].astype(bool), disp["cargo_efetivo"])
        st.dataframe(
            disp[["boletim_data_iso", "Movimento", "Cargo/Função", "codigo",
                  "sigla", "unidade", "link_boletim"]].rename(columns={
                      "boletim_data_iso": "Data", "codigo": "Código",
                      "sigla": "Sigla", "unidade": "Unidade",
                      "link_boletim": "Boletim"}),
            use_container_width=True, hide_index=True, height=240,
            column_config={"Boletim": st.column_config.LinkColumn(
                "Boletim", display_text="abrir ↗")})
    if len(vg):
        st.markdown("**✈️ Viagens:**")
        st.dataframe(vg[["boletim_data_iso", "tipo", "origem", "destino",
                         "periodo_ini", "valor_diarias", "motivo"]]
                     .rename(columns={"boletim_data_iso": "Boletim",
                                      "tipo": "Tipo", "origem": "Origem",
                                      "destino": "Destino", "periodo_ini": "Início",
                                      "valor_diarias": "Diárias (R$)",
                                      "motivo": "Motivo"})
                     .sort_values("Boletim", ascending=False),
                     use_container_width=True, hide_index=True, height=240)
    if len(bols):
        st.markdown("**📋 Boletins de Pessoal que o citam** (nomeações, "
                    "designações, afastamentos…):")
        st.dataframe(bols.rename(columns={"numero": "Boletim", "data": "Data",
                                          "pdf_url": "PDF"}),
                     use_container_width=True, hide_index=True, height=200,
                     column_config={"PDF": st.column_config.LinkColumn(
                         "PDF", display_text="abrir ↗")})
    pub = carregar_publicacoes()
    if pub is not None:
        m = pub["resumo"].fillna("").str.contains(re.escape(nome), case=False)
        hits = pub[m]
        if len(hits):
            st.markdown(f"**📑 Menções no Diário (SEI) ({len(hits)}):**")
            st.dataframe(hits[["data", "descricao", "unidade", "link"]]
                         .rename(columns={"data": "Data", "descricao": "Descrição",
                                          "unidade": "Unidade", "link": "Link"}),
                         use_container_width=True, hide_index=True,
                         column_config={"Link": st.column_config.LinkColumn(
                             "Link", display_text="abrir ↗")})


def _render_contratacoes():
    """Contratações/projetos: portarias GELIC/SAD (SEI) × atas do CGE (IA)."""
    st.markdown("#### 🏗️ Contratações e projetos internos")
    st.caption("Cruza as portarias administrativas do Diário (GELIC = licitações "
               "e contratos; SAD = administração) com as deliberações das Atas "
               "do CGE (projetos, orçamento, contratações).")
    tema = st.text_input("Filtrar por tema (ex.: sistema, contrato, projeto, "
                         "orçamento…)", key="ctr_tema")
    pub = carregar_publicacoes()
    if pub is not None:
        adm = pub[pub["unidade"].isin(["GELIC", "SAD", "DICON",
                                       "GELIC-Restrito"])].copy()
        if tema.strip():
            adm = adm[(adm["descricao"].fillna("") + " " + adm["resumo"]
                       .fillna("")).str.contains(re.escape(tema), case=False)]
        st.markdown(f"**📑 Portarias e atos administrativos (SEI) — "
                    f"{len(adm)}:**")
        st.dataframe(adm.sort_values("data_iso", ascending=False)[
            ["data", "descricao", "unidade", "resumo", "link"]].rename(
                columns={"data": "Data", "descricao": "Descrição",
                         "unidade": "Unidade", "resumo": "Resumo",
                         "link": "Link"}).head(40),
            use_container_width=True, hide_index=True, height=260,
            column_config={"Link": st.column_config.LinkColumn(
                "Link", display_text="abrir ↗")})
    # atas do CGE (fichas IA)
    if os.path.exists(ATAS_DB_PATH):
        con = sqlite3.connect(ATAS_DB_PATH)
        try:
            cge = pd.read_sql(
                "SELECT numero, data, resumo, deliberacoes, link FROM atas "
                "WHERE resumo IS NOT NULL AND resumo<>'' ORDER BY data_iso DESC",
                con)
        except Exception:
            cge = pd.DataFrame()
        con.close()
        if len(cge):
            if tema.strip():
                cge = cge[(cge["resumo"].fillna("") + " "
                           + cge["deliberacoes"].fillna(""))
                          .str.contains(re.escape(tema), case=False)]
            st.markdown(f"**📋 Deliberações do CGE relacionadas ({len(cge)}):**")
            for _, r in cge.head(10).iterrows():
                st.markdown(f"- **CGE {r['numero']} ({r['data']})** — "
                            f"{r['resumo']}  \n  ↳ *{r['deliberacoes']}*  "
                            f"[ata ↗]({r['link']})")


def render_servidores():
    st.subheader("🏢 Servidores da CVM (Boletim de Pessoal)")
    df = carregar_viagens()
    mov = carregar_movimentos()
    if (df is None or len(df) == 0) and (mov is None or len(mov) == 0):
        st.info("⏳ A base do Boletim de Pessoal ainda está sendo montada.")
        return
    # ficha individual (viagens + trajetória de cargos)
    nomes = sorted(set(
        (list(df["servidor_nome"].dropna().unique()) if df is not None else [])
        + (list(mov["servidor_nome"].dropna().unique()) if mov is not None else [])))
    sel_srv = st.selectbox("🗂️ Buscar servidor (abre a ficha completa)", nomes,
                           key="srv_ficha", index=None,
                           placeholder="digite um nome…")
    if sel_srv and sel_srv != st.session_state.get("_srv_open_sel"):
        st.session_state["_srv_open_sel"] = sel_srv
        dialog_servidor(sel_srv)
    vis = st.segmented_control(
        "Seção", ["🏛️ Organograma e cargos", "✈️ Viagens", "🏗️ Contratações"],
        key="srv_sub", default="🏛️ Organograma e cargos",
        label_visibility="collapsed")
    st.divider()
    if vis == "🏛️ Organograma e cargos":
        render_organograma()
        return
    if vis == "🏗️ Contratações":
        _render_contratacoes()
        return
    if df is None or len(df) == 0:
        st.info("Sem viagens na base ainda.")
        return
    # Colegiado — fiscalização: dias no cargo × dias viajando, por papel
    col = _diretores_mandatos()
    if len(col):
        st.markdown("**🏛️ Diretores do Colegiado — viagens ao exterior × mandato** "
                    "*(para fiscalizar o trabalho)*")
        atuais = col[col["Status"] == "Em exercício"]
        if len(atuais):
            tot_d = int(atuais["Dias viajando"].sum())
            st.caption(f"Membros em exercício: {atuais['Diretor(a)'].nunique()} · "
                       f"juntos somam **{tot_d} dias** viajando ao exterior.")
        show_col = col.copy()
        show_col["% do mandato viajando"] = show_col["% do mandato viajando"].map(
            lambda x: f"{x:.1f}%")
        ev = st.dataframe(
            show_col, use_container_width=True, hide_index=True, height=430,
            on_select="rerun", selection_mode="single-row", key="col_via_sel",
            column_config={
                "Dias úteis (mandato)": st.column_config.NumberColumn(format="%d"),
                "Dias viajando": st.column_config.NumberColumn(format="%d"),
                "Custo real (R$)": st.column_config.NumberColumn(format="R$ %.0f"),
                "Diárias Boletim (R$)": st.column_config.NumberColumn(
                    format="R$ %.0f")})
        _abrir_ficha_sel(ev, show_col, "_srv_open_col", "Diretor(a)")
        st.caption("**Dias úteis (mandato)** = dias de trabalho (seg–sex); "
                   "**Dias viajando** = dias corridos de afastamento do país; "
                   "**%** = dias viajando ÷ dias úteis. **Custo real (R$)** = "
                   "diárias + passagens do Portal da Transparência (2014+, inclui "
                   "**internacionais**), atribuído à fase pela data da viagem; "
                   "**Diárias Boletim** = diárias nacionais do Boletim (cobre todo "
                   "o período, inclusive pré-2014). Inclui atuais e ex-diretores; "
                   "Otto e Accioly com o mandato separado por papel. Clique p/ ficha.")
        st.divider()
    # superintendentes (últimos 5 anos) — custo de viagens
    sup = _transparencia_superintendentes()
    if len(sup):
        st.markdown("**🧑‍💼 Superintendentes (últimos 5 anos) — custo de viagens**")
        s1, s2, s3 = st.columns(3)
        s1.metric("Custo total (R$)",
                  f"{sup['Total (R$)'].sum():,.0f}".replace(",", "."))
        s2.metric("Superintendentes", len(sup))
        s3.metric("Internacionais", int(sup["Internac."].sum()))
        st.dataframe(
            sup, use_container_width=True, hide_index=True,
            column_config={c: st.column_config.NumberColumn(format="R$ %.0f")
                           for c in ["Diárias (R$)", "Passagens (R$)",
                                     "Total (R$)"]})
        st.caption("Servidores que ocuparam cargo de superintendente com ato nos "
                   "últimos 5 anos (Boletim), cruzados com as viagens da CVM no "
                   "Portal da Transparência (2014–2026).")
        st.divider()
    # evolução do custo de viagens: anual e mensal, por grupo
    anual, mensal = _transparencia_custo_serie()
    if len(anual):
        st.markdown("**📈 Custo de viagens da CVM — evolução (anual e mensal)**")
        st.caption("Portal da Transparência (viagens a serviço, 2014–2026), "
                   "segmentado em Diretores / Superintendentes / Demais servidores.")
        st.markdown("*Custo anual (R$):*")
        st.bar_chart(anual)
        st.markdown("*Custo mensal (R$):*")
        st.line_chart(mensal)
        st.divider()
    df = df.copy()
    df["_ano"] = df["boletim_data_iso"].astype(str).str[:4]
    st.caption(f"{len(df):,} viagens de {df['servidor_key'].nunique()} servidores "
               "(fonte: Boletim de Pessoal — Afastamentos do País e Concessões de "
               "Diárias). O **custo real** (com passagens e internacionais, do "
               "Portal da Transparência) está nas tabelas acima."
               .replace(",", "."))
    with st.expander("🔎 Filtros", expanded=True):
        c1, c2, c3 = st.columns([2, 1, 1])
        q = c1.text_input("Servidor (nome contém)", key="srv_q")
        tipos = {"afastamento_pais": "Afastamento do país", "diaria": "Diária"}
        f_tipo = c2.multiselect("Tipo", list(tipos), format_func=lambda t: tipos[t],
                                key="srv_tipo")
        anos = sorted((a for a in df["_ano"].unique() if a and a != "None"),
                      reverse=True)
        f_ano = c3.multiselect("Ano", anos, key="srv_ano")
    m = pd.Series(True, index=df.index)
    if q.strip():
        m &= df["servidor_nome"].str.contains(re.escape(q), case=False, na=False)
    if f_tipo:
        m &= df["tipo"].isin(f_tipo)
    if f_ano:
        m &= df["_ano"].isin(f_ano)
    res = df[m].copy()
    res["_val"] = res["valor_diarias"].map(_parse_valor)
    res["_dias"] = res.apply(
        lambda r: _dias_periodo(r["periodo_ini"])
        if r["tipo"] == "afastamento_pais" else 0, axis=1)
    # ranking por servidor: viagens, dias viajando (exterior) e custo de diárias
    rk = (res.groupby("servidor_nome").agg(
        Viagens=("servidor_nome", "size"),
        **{"Dias viajando (exterior)": ("_dias", "sum"),
           "Custo diárias (R$)": ("_val", "sum")})
        .reset_index().sort_values("Custo diárias (R$)", ascending=False))
    c1, c2, c3 = st.columns(3)
    c1.metric("Viagens encontradas", f"{len(res):,}".replace(",", "."))
    c2.metric("Servidores", f"{res['servidor_key'].nunique():,}".replace(",", "."))
    c3.metric("💰 Custo diárias (R$)",
              f"{res['_val'].sum():,.0f}".replace(",", "."))
    st.markdown("**Servidores por custo de diárias / viagens (no filtro):**")
    st.dataframe(rk.head(30), use_container_width=True, hide_index=True,
                 column_config={"Custo diárias (R$)": st.column_config.NumberColumn(
                     format="R$ %.2f")})
    st.caption("Custo = diárias (viagens nacionais). Afastamentos internacionais "
               "não trazem valor no Boletim — o custo virá do Portal da "
               "Transparência.")
    st.markdown("**Viagens:**")
    cols = ["boletim_data_iso", "servidor_nome", "tipo", "origem", "destino",
            "periodo_ini", "periodo_fim", "valor_diarias", "motivo", "link_boletim"]
    show = res[cols].rename(columns={
        "boletim_data_iso": "Boletim", "servidor_nome": "Servidor", "tipo": "Tipo",
        "origem": "Origem", "destino": "Destino", "periodo_ini": "Início",
        "periodo_fim": "Fim", "valor_diarias": "Diárias (R$)", "motivo": "Motivo",
        "link_boletim": "Boletim (PDF)"})
    st.dataframe(
        show.sort_values("Boletim", ascending=False), use_container_width=True,
        hide_index=True, height=460,
        column_config={"Boletim (PDF)": st.column_config.LinkColumn(
            "Boletim (PDF)", display_text="abrir ↗")})
    st.download_button("⬇️ Baixar (CSV)", show.to_csv(index=False).encode("utf-8-sig"),
                       file_name="viagens_servidores_cvm.csv", mime="text/csv")


# --------------------------------------------------------------------------
# Análises (IA) de julgados/TCs + conduta por diretor (conduta.db)
# --------------------------------------------------------------------------
CONDUTA_DB_PATH = os.environ.get("CONDUTA_DB_PATH", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "conduta.db"))


@st.cache_data(ttl=600)
def _mapa_analises():
    """(proc_norm, tipo) -> análise IA (dict) do julgado/TC."""
    if not os.path.exists(CONDUTA_DB_PATH):
        return {}
    con = sqlite3.connect(CONDUTA_DB_PATH)
    out = {}
    try:
        for pn, tipo, a in con.execute(
                "SELECT proc_norm, tipo, analise FROM analises WHERE ai_feito=1"):
            try:
                out[(pn, tipo)] = json.loads(a)
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    con.close()
    return out


@st.cache_data(ttl=600)
def _conduta_resumo():
    """DataFrame agregado da conduta decisória por diretor (conduta.db)."""
    if not os.path.exists(CONDUTA_DB_PATH):
        return None
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        df = pd.read_sql("""
            SELECT diretor AS "Diretor",
              SUM(evento='julgou') AS "Julgou",
              ROUND(SUM(CASE WHEN evento='julgou' THEN valor ELSE 0 END))
                  AS "Multas (R$)",
              SUM(evento='relatou_item') AS "Itens relatados",
              SUM(evento='voto_vencido') AS "Vencido",
              SUM(evento='pediu_vista') AS "Vistas",
              SUM(evento='retirou_de_pauta') AS "Retirou de pauta"
            FROM eventos WHERE diretor<>'(Colegiado)'
            GROUP BY diretor ORDER BY 2 DESC""", con)
    except Exception:
        df = None
    con.close()
    return df


@st.cache_data(ttl=600)
def _agentes_tese():
    """tema -> dossie do agente especialista; e proc_norm -> temas do caso."""
    ags, casos = {}, {}
    if not os.path.exists(CONDUTA_DB_PATH):
        return ags, casos
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        for tema, d, n in con.execute(
                "SELECT tema, dossie, n_casos FROM agentes_tese WHERE ai_feito=1"):
            try:
                ags[tema] = {**json.loads(d), "_n": n}
            except (ValueError, TypeError):
                pass
        for pn, tema in con.execute(
                "SELECT proc_norm, tema FROM caso_tema WHERE dominante=1"):
            casos.setdefault(pn, []).append(tema)
    except Exception:
        pass
    con.close()
    return ags, casos


_TEMA_LABEL = {
    "insider_trading": "Insider trading", "manipulacao_fraude": "Manipulação/fraude",
    "fato_relevante_dri": "Fato relevante / DRI",
    "dever_diligencia_adm": "Dever de diligência (adm.)",
    "abuso_controle": "Abuso de controle", "carteira_irregular": "Carteira irregular",
    "fundos_investimento": "Fundos", "ofertas_publicas": "Ofertas públicas",
    "auditoria": "Auditoria", "intermediarios": "Intermediários",
    "informacoes_periodicas": "Informações periódicas",
    "assembleia_conflito": "Assembleia / conflito",
    "agentes_autonomos": "Agentes autônomos", "rito_processual": "Rito/prescrição",
    "outros": "Outros"}


def _teses_html(pn, e):
    """No popup do processo: temas do caso + tese vigente do agente especialista."""
    ags, casos = _agentes_tese()
    temas = [t for t in casos.get(pn, []) if t != "outros"]
    if not temas:
        return ""
    blocos = ""
    for t in temas:
        a = ags.get(t, {})
        tv = str(a.get("tese_vigente") or "").strip()
        dm = str(a.get("dosimetria") or "").strip()
        blocos += (f'<tr><td class="upperHeader" style="white-space:nowrap">'
                   f'{e(_TEMA_LABEL.get(t, t))}</td><td>'
                   + (f'<b>Tese vigente:</b> {e(tv[:400])}' if tv else "")
                   + (f'<br><b>Dosimetria:</b> {e(dm[:250])}' if dm else "")
                   + '</td></tr>')
    return ('<h3>🧠 Teses aplicáveis (agente especialista)</h3>'
            '<p style="font-size:11px">O caso foi classificado nestes temas; '
            'segue o entendimento consolidado do Colegiado (base dos agentes de '
            'tese). Confira sempre no inteiro teor.</p>'
            f'<table>{blocos}</table>')


@st.cache_data(ttl=600)
def _tema_timeline(tema):
    """Linha do tempo dos julgados de um tema (data, relator, severidade,
    desfecho) — para VER a evolução do entendimento ao longo dos anos."""
    if not os.path.exists(CONDUTA_DB_PATH):
        return pd.DataFrame()
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        rows = con.execute(
            "SELECT DISTINCT ct.proc_norm, e.data_iso, e.diretor, e.valor, "
            "a.analise FROM caso_tema ct JOIN eventos e ON "
            "e.proc_norm=ct.proc_norm AND e.evento='julgou' LEFT JOIN analises a "
            "ON a.proc_norm=ct.proc_norm AND a.tipo='julgado' WHERE ct.tema=? "
            "AND ct.dominante=1 AND e.data_iso<>''", (tema,)).fetchall()
    except Exception:
        rows = []
    con.close()
    out = []
    for pn, di, dire, val, an in rows:
        try:
            d = json.loads(an) if an else {}
        except (ValueError, TypeError):
            d = {}
        out.append({"Data": di, "Ano": di[:4], "Relator": dire, "Processo": pn,
                    "Multa (R$)": val or 0,
                    "Severidade": str(d.get("severidade") or "?").lower()
                    .replace("média", "media"),
                    "Desfecho": str(d.get("desfecho") or "")[:140]})
    df = pd.DataFrame(out)
    return df.sort_values("Data") if len(df) else df


def render_agentes_tese():
    st.markdown("#### 🧠 Agentes especialistas por tese")
    ags, casos = _agentes_tese()
    if not ags:
        st.info("⏳ Os agentes de tese ainda estão sendo construídos.")
        return
    st.caption(f"{len(ags)} temas com entendimento consolidado. Quando chega um "
               "processo novo, ele é classificado no tema e o agente da tese "
               "correspondente é consultado.")
    # roteador: cola o objeto de um caso novo -> tema(s)
    with st.expander("🧭 Roteador — classifique um caso novo"):
        txt = st.text_area("Cole o objeto/ementa do processo", key="rot_txt",
                           height=100)
        if txt.strip():
            import subprocess
            try:
                out = subprocess.run(
                    [sys.executable, os.path.join(os.path.dirname(
                        os.path.abspath(__file__)), "agentes_tese.py"),
                     "roteia", txt], capture_output=True, text=True, timeout=30)
                st.code(out.stdout.strip() or "outros")
            except Exception as ex:
                st.caption(f"(roteador indisponível: {ex})")
    tema = st.selectbox("Ver agente do tema", sorted(ags),
                        format_func=lambda t: f"{_TEMA_LABEL.get(t, t)} "
                        f"({ags[t].get('_n', 0)} casos)", key="ag_tema",
                        index=None, placeholder="escolha um tema…")
    if tema:
        a = ags[tema]
        tv = str(a.get("tese_vigente") or "").strip()
        if tv and tv != "-":
            st.success(f"**⚖️ Tese vigente:** {tv}")
        ev_txt = str(a.get("evolucao") or "").strip()
        if ev_txt and ev_txt != "-":
            st.warning(f"**📈 Como o entendimento mudou:** {ev_txt}")
        marcos = a.get("marcos") or []
        if isinstance(marcos, list) and marcos:
            st.markdown("**🏛️ Marcos da evolução** — precedentes que firmaram/"
                        "mudaram o entendimento (e quem):")
            for mk in marcos:
                if not isinstance(mk, dict):
                    st.markdown(f"- {mk}")
                    continue
                ano = str(mk.get("ano") or "").strip()
                proc = str(mk.get("processo") or "").strip()
                rel = str(mk.get("relator") or "").strip()
                mud = str(mk.get("mudanca") or "").strip()
                cab = " · ".join(x for x in [
                    ano, (f"rel. {rel}" if rel and rel != "-" else ""),
                    (proc if proc and proc != "-" else "")] if x)
                st.markdown(f"- **{cab}** — {mud}" if cab else f"- {mud}")
        # linha do tempo: VER a mudança ao longo dos anos
        tl = _tema_timeline(tema)
        if len(tl) >= 2:
            st.markdown("**📈 Evolução ao longo do tempo** — severidade dos "
                        "julgados por ano (endurecimento/abrandamento):")
            piv = pd.crosstab(tl["Ano"], tl["Severidade"])
            ordem = [c for c in ["alta", "media", "baixa", "?"] if c in piv.columns]
            st.bar_chart(piv[ordem + [c for c in piv.columns if c not in ordem]])
            with st.expander(f"🕰️ Linha do tempo dos {len(tl)} julgados do tema"):
                st.dataframe(tl[["Data", "Relator", "Severidade", "Multa (R$)",
                                 "Desfecho"]], use_container_width=True,
                             hide_index=True)
            st.caption("O gráfico de severidade acima usa as análises de conduta "
                       "(2022–2026); a **tese vigente, a evolução e os marcos** "
                       "acima já refletem **toda a jurisprudência 1999–2025**.")
        for rot, campo in [("Dosimetria", "dosimetria"),
                           ("Posição por diretor", "posicao_por_diretor"),
                           ("Padrão de TC", "tc_padrao")]:
            v = str(a.get(campo) or "").strip()
            if v and v != "-":
                st.markdown(f"**{rot}:** {v}")
        ctr = str(a.get("controversias") or "").strip()
        if ctr and ctr != "-":
            st.info(f"**❓ Controvérsias / pontos em aberto:** {ctr}")
        ck = a.get("casos_chave") or []
        if ck:
            st.markdown("**Casos-chave:**")
            for c in ck:
                st.markdown(f"- {c}")
        sp = str(a.get("system_prompt") or "").strip()
        if sp:
            with st.expander("🤖 System prompt do agente de tese (copiar)"):
                st.code(sp, language=None)


@st.cache_data(ttl=600)
def _agentes_area():
    """sigla -> dossie do agente da área técnica (Fase D da jurisprudência)."""
    ags = {}
    if not os.path.exists(CONDUTA_DB_PATH):
        return ags
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS agentes_area(sigla TEXT PRIMARY "
                    "KEY, nome TEXT, dossie TEXT, n_casos INTEGER, "
                    "acolhimento REAL, ai_feito INTEGER, atualizado_em TEXT)")
        for sig, nome, d, n, ac in con.execute(
                "SELECT sigla, nome, dossie, n_casos, acolhimento FROM "
                "agentes_area WHERE ai_feito=1"):
            try:
                ags[sig] = {**json.loads(d), "_nome": nome, "_n": n, "_ac": ac}
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    con.close()
    return ags


def render_agentes_area():
    st.markdown("#### 🏢 Agentes das áreas técnicas (acusação × acolhimento)")
    ags = _agentes_area()
    if not ags:
        st.info("⏳ Os agentes de área ainda estão sendo construídos.")
        return
    st.caption("Cada superintendência é a **acusadora** perante o Colegiado. "
               "Aqui está o perfil de cada uma e **quanto de sua acusação o "
               "Colegiado acolhe** — base: toda a jurisprudência 1999–2025.")
    # ranking comparativo por acolhimento
    rk = sorted(ags.items(), key=lambda kv: (kv[1].get("_ac") or 0),
                reverse=True)
    df = pd.DataFrame([{"Área": s, "Superintendência": a.get("_nome", ""),
                        "Casos": a.get("_n") or 0,
                        "Acolhimento (%)": a.get("_ac")} for s, a in rk])
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={"Acolhimento (%)": st.column_config.ProgressColumn(
                     "Acolhimento (%)", min_value=0, max_value=100,
                     format="%.1f%%")})
    st.caption("Acolhimento = % dos casos de mérito (fora TAC) em que o "
               "Colegiado condenou (total ou parcialmente) o que a área acusou. "
               "Média geral ≈ 84%.")
    sig = st.selectbox("Ver agente da área", [s for s, _ in rk],
                       format_func=lambda s: f"{s} — {ags[s].get('_nome','')} "
                       f"({ags[s].get('_n',0)} casos · {ags[s].get('_ac','?')}%)",
                       key="ag_area", index=None, placeholder="escolha uma área…")
    if not sig:
        return
    a = ags[sig]
    c1, c2 = st.columns(2)
    c1.metric("Casos julgados", a.get("_n") or 0)
    c2.metric("Acolhimento pelo Colegiado", f"{a.get('_ac','?')}%")
    perfil = str(a.get("perfil") or "").strip()
    if perfil:
        st.success(f"**Perfil:** {perfil}")
    for rot, campo, kind in [
            ("🎯 Foco acusatório", "foco_acusatorio", "md"),
            ("⚖️ Teses defendidas", "teses_defendidas", "md"),
            ("📊 Taxa de acolhimento", "taxa_acolhimento", "md"),
            ("📈 Evolução do rigor", "evolucao_rigor", "warn"),
            ("⚔️ Atritos com o Colegiado", "atritos_colegiado", "info")]:
        v = str(a.get(campo) or "").strip()
        if not v or v == "-":
            continue
        if kind == "warn":
            st.warning(f"**{rot}:** {v}")
        elif kind == "info":
            st.info(f"**{rot}:** {v}")
        else:
            st.markdown(f"**{rot}:** {v}")
    ck = a.get("casos_chave")
    if ck:
        st.markdown("**Casos-chave:**")
        if isinstance(ck, list):
            for c in ck:
                st.markdown(f"- {c}")
        else:
            st.markdown(str(ck))
    sup = a.get("superintendentes")
    if sup:
        with st.expander("👤 Superintendentes (Boletim de Pessoal)"):
            if isinstance(sup, list):
                for s in sup:
                    st.markdown(f"- {s}")
            else:
                st.markdown(str(sup))
    sp = str(a.get("system_prompt") or "").strip()
    if sp:
        with st.expander("🤖 System prompt do agente de área (copiar)"):
            st.code(sp, language=None)


@st.cache_data(ttl=600)
def _agentes_unidade():
    """sigla -> dossie institucional da SI/CTC (agentes_unidade)."""
    ags = {}
    if not os.path.exists(CONDUTA_DB_PATH):
        return ags
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS agentes_unidade(sigla TEXT "
                    "PRIMARY KEY, nome TEXT, tipo TEXT, dossie TEXT, "
                    "n_recursos INTEGER, ai_feito INTEGER, atualizado_em TEXT)")
        for sig, nome, tipo, d, nr, air in con.execute(
                "SELECT sigla, nome, tipo, dossie, n_recursos, ai_feito FROM "
                "agentes_unidade"):
            try:
                ags[sig] = {**json.loads(d), "_nome": nome, "_tipo": tipo,
                            "_nr": nr or 0, "_ia": air}
            except (ValueError, TypeError):
                pass
    except Exception:
        pass
    con.close()
    return ags


@st.cache_data(ttl=600)
def _agentes_gerencia():
    """si_pai -> [gerências]; cada uma com sigla, nome, chefe."""
    out = {}
    if not os.path.exists(CONDUTA_DB_PATH):
        return out
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS agentes_gerencia(sigla TEXT "
                    "PRIMARY KEY, nome TEXT, si_pai TEXT, chefe_atual TEXT, "
                    "chefe_desde TEXT, historico TEXT, atualizado_em TEXT)")
        for sig, nome, si, chefe, desde in con.execute(
                "SELECT sigla, nome, si_pai, chefe_atual, chefe_desde FROM "
                "agentes_gerencia ORDER BY sigla"):
            out.setdefault(si or "?", []).append(
                {"sigla": sig, "nome": nome, "chefe": chefe, "desde": desde})
    except Exception:
        pass
    con.close()
    return out


@st.cache_data(ttl=600)
def _ctc_por_ano():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "termos.db")
    if not os.path.exists(p):
        return pd.DataFrame()
    con = sqlite3.connect(p)
    try:
        rows = con.execute(
            "SELECT substr(data_decisao_iso,1,4) ano, situacao, COUNT(*) FROM "
            "termos WHERE data_decisao_iso<>'' GROUP BY ano, situacao").fetchall()
    except Exception:
        rows = []
    con.close()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["Ano", "Situação", "n"])
    return df.pivot_table(index="Ano", columns="Situação", values="n",
                          fill_value=0)


def _render_ctc(a):
    st.markdown("### 🤝 Comitê de Termo de Compromisso (CTC)")
    st.caption("O CTC negocia e opina sobre as propostas de termo de "
               "compromisso (TAC); o Colegiado decide aceitar ou rejeitar.")
    total = a.get("_nr") or 0
    c1, c2 = st.columns(2)
    c1.metric("Termos de compromisso", total)
    ta = str(a.get("taxa_aceitacao") or "")
    c2.metric("Situação", "804 aceitos · 215 rejeitados"
              if total >= 1000 else "—")
    for rot, campo, kind in [("🎯 Papel", "papel", "ok"),
                             ("📊 Taxa de aceitação", "taxa_aceitacao", "md"),
                             ("📈 Evolução", "evolucao", "warn"),
                             ("⚖️ Critérios do parecer", "criterios", "md"),
                             ("🗂️ Tipos de caso", "tipos_de_caso", "info")]:
        v = str(a.get(campo) or "").strip()
        if not v or v == "-":
            continue
        (st.success if kind == "ok" else st.warning if kind == "warn"
         else st.info if kind == "info" else st.markdown)(f"**{rot}:** {v}")
    piv = _ctc_por_ano()
    if len(piv):
        st.markdown("**TCs por ano (aceitos × rejeitados):**")
        st.bar_chart(piv)
    sp = str(a.get("system_prompt") or "").strip()
    if sp:
        with st.expander("🤖 System prompt do agente do CTC (copiar)"):
            st.code(sp, language=None)


_TIPO_LABEL = {"sancionadora": "🛡️ Sancionadora", "registro": "📝 Registro",
               "normativa": "📐 Normativa", "orientacao": "🧭 Orientação",
               "apoio": "⚙️ Apoio/administrativa", "comite": "🤝 Comitê"}


def render_agentes_unidade():
    st.markdown("#### 🗂️ Unidades da CVM — superintendências, gerências e CTC")
    ags = _agentes_unidade()
    ger = _agentes_gerencia()
    if not ags:
        st.info("⏳ Os agentes de unidade ainda estão sendo construídos.")
        return
    st.caption("Perfil institucional de cada superintendência (competência, "
               "titulares, gerências e **footprint recursal** — quantos recursos "
               "o Colegiado julgou contra decisões da área), das gerências e do "
               "Comitê de Termo de Compromisso.")
    # CTC em destaque
    if "CTC" in ags:
        with st.container(border=True):
            _render_ctc(ags["CTC"])
    st.divider()
    # superintendências
    sis = [(s, a) for s, a in ags.items() if s != "CTC"]
    sis.sort(key=lambda kv: (-(kv[1].get("_nr") or 0), kv[0]))
    sig = st.selectbox(
        "Ver superintendência", [s for s, _ in sis],
        format_func=lambda s: f"{s} — {ags[s].get('_nome','')} "
        f"· {_TIPO_LABEL.get(ags[s].get('_tipo'),'')}"
        + (f" · {ags[s].get('_nr')} recursos" if ags[s].get('_nr') else ""),
        key="ag_unidade", index=None, placeholder="escolha uma superintendência…")
    if not sig:
        # visão geral: árvore de gerências por SI
        st.markdown("**Estrutura — gerências por superintendência:**")
        for s, a in sis:
            gl = ger.get(s, [])
            if not gl:
                continue
            with st.expander(f"{s} — {a.get('_nome','')} ({len(gl)} gerências)"):
                for g in gl:
                    ch = f" — chefe: {g['chefe']}" if g.get("chefe") else ""
                    st.markdown(f"- **{g['sigla']}** {g['nome']}{ch}")
        outras = ger.get("?", [])
        if outras:
            with st.expander(f"Outras gerências ({len(outras)})"):
                for g in outras:
                    ch = f" — chefe: {g['chefe']}" if g.get("chefe") else ""
                    st.markdown(f"- **{g['sigla']}** {g['nome']}{ch}")
        return
    a = ags[sig]
    if a.get("_nr"):
        st.metric("Recursos julgados pelo Colegiado (decisões da área)",
                  a.get("_nr"))
    comp = str(a.get("competencia_pratica") or a.get("competencia") or "").strip()
    if comp:
        st.success(f"**Competência:** {comp}")
    if not a.get("_ia"):
        st.caption("Unidade de apoio/administrativa — card institucional "
                   "(sem análise de jurisprudência).")
    for rot, campo, kind in [
            ("⚖️ Papel no enforcement", "papel_enforcement", "md"),
            ("📊 Footprint recursal", "footprint_recursal", "info"),
            ("⚔️ Onde o mercado mais recorre", "atritos_recursais", "warn"),
            ("🗂️ Temas recorrentes", "temas_ou_casos", "md"),
            ("📜 Histórico de titulares", "titulares_historia", "md")]:
        v = str(a.get(campo) or "").strip()
        if not v or v == "-":
            continue
        (st.info if kind == "info" else st.warning if kind == "warn"
         else st.markdown)(f"**{rot}:** {v}")
    # gerências da SI (dado estruturado)
    gl = ger.get(sig, [])
    if gl:
        with st.expander(f"🏢 Gerências subordinadas ({len(gl)})"):
            for g in gl:
                ch = f" — chefe atual: {g['chefe']}" if g.get("chefe") else ""
                st.markdown(f"- **{g['sigla']}** {g['nome']}{ch}")
    elif not a.get("_ia"):
        tit = a.get("titulares") or []
        if tit:
            st.markdown("**Titulares (Boletim de Pessoal):**")
            for t in tit[:6]:
                if isinstance(t, dict):
                    st.markdown(f"- {t.get('nome','')} ({t.get('data','')})")
    if a.get("_tipo") == "sancionadora":
        st.caption("🛡️ Esta SI também tem um **agente de acusação × acolhimento** "
                   "(aba “Agentes de área”), com a estatística dos julgados.")
    sp = str(a.get("system_prompt") or "").strip()
    if sp:
        with st.expander("🤖 System prompt do agente da unidade (copiar)"):
            st.code(sp, language=None)


def _analise_html(a, e):
    """Bloco HTML da análise IA para o popup do processo."""
    if not a:
        return ""
    linhas = ""
    for rot, campo in [("Resumo", "resumo"), ("Conduta imputada", "conduta_imputada"),
                       ("Desfecho", "desfecho"), ("Severidade", "severidade"),
                       ("Racional da decisão", "racional"),
                       ("Conduta do relator", "conduta_relator")]:
        v = str(a.get(campo) or "").strip()
        if v and v != "-":
            linhas += (f'<tr><td class="upperHeader" style="white-space:nowrap">'
                       f'{rot}</td><td>{e(v)}</td></tr>')
    if not linhas:
        return ""
    return ('<h3>🧠 Análise do caso (IA)</h3>'
            '<p style="font-size:11px">Gerada por IA a partir do material oficial '
            '(objeto, acusados, extrato da sessão, atas). Confira sempre na fonte.</p>'
            f'<table>{linhas}</table>')


# --------------------------------------------------------------------------
# Multas dos extratos de julgamento (Diário/SEI)
# --------------------------------------------------------------------------
@st.cache_data(ttl=600)
def _mapa_multas():
    """proc_norm -> total de multas (R$, formatado) do Extrato de Sessão."""
    if not os.path.exists(JULGAR_DB_PATH):
        return {}
    con = sqlite3.connect(JULGAR_DB_PATH)
    out = {}
    try:
        for pn, tot in con.execute(
                "SELECT proc_norm, SUM(multas_total) FROM extratos_julgamento "
                "GROUP BY proc_norm"):
            if tot:
                out[pn] = f"{tot:,.2f}".replace(",", "X").replace(".", ",") \
                    .replace("X", ".")
    except Exception:
        pass
    con.close()
    return out


# --------------------------------------------------------------------------
# Advogados nas pautas (Diário/SEI)
# --------------------------------------------------------------------------
@st.cache_data(ttl=600)
def _advogados_pautas():
    """Pares (advogado, OAB) por processo, a partir do bloco Acusados/Advogados
    das pautas do Diário. Retorna DataFrame [Advogado, OAB, Processo, Sessão]."""
    df = carregar_pauta_sei()
    out = []
    if df is None or not len(df):
        return pd.DataFrame()
    for _, r in df.iterrows():
        segs = [s.strip() for s in str(r.get("acusados") or "").split("|")]
        for i, s in enumerate(segs[:-1]):
            m = re.match(r"OAB/([A-Z]{2})\s*n?[ºo\.]?\s*([\d.]+)", segs[i + 1])
            if m and s and not s.upper().startswith(("OAB", "ADVOGADO", "ACUSADO")):
                out.append({"Advogado": s, "OAB": f"OAB/{m.group(1)} {m.group(2)}",
                            "Processo": r.get("processo", ""),
                            "Sessão": r.get("data_sessao", "")})
    return pd.DataFrame(out).drop_duplicates()


# --------------------------------------------------------------------------
# Mineração dos votos (fichas IA das atas do Colegiado)
# --------------------------------------------------------------------------
_DIRETORES_VOTO = ["Otto Lobo", "Joao Accioly", "João Accioly", "Marina Copola",
                   "Joao Pedro Nascimento", "João Pedro Nascimento", "Daniel Maeda",
                   "Otavio Yazbek", "Flavia Perlingeiro", "Flávia Perlingeiro",
                   "Alexandre Rangel", "Marcelo Barbosa", "Thiago Paiva Chaves",
                   "Luis Felipe Marques Lobianco", "Luís Felipe Marques Lobianco",
                   "Andre Passaro", "André Passaro", "Igor Muniz"]


def _dir_canon(nome):
    """Nome canônico (sem acento) do diretor citado num trecho de voto."""
    k = _ent_key(nome)
    for d in _DIRETORES_VOTO:
        if _ent_key(d) in k or k in _ent_key(d):
            return _ent_key(d).title()
    return ""


@st.cache_data(ttl=600)
def _minerar_votos():
    """Estatísticas por diretor a partir das fichas IA das atas (2022+):
    vencido (voto vencido), vista (pedidos de vista), diverge_area (item em que o
    Colegiado divergiu da área técnica) e itens totais analisados."""
    atc = carregar_atas_colegiado()
    stats = {}
    tot = {"itens": 0, "unanime": 0, "maioria": 0, "vista": 0,
           "diverge_area": 0, "acompanha_area": 0}
    if atc is None or "ficha" not in atc.columns:
        return stats, tot, pd.DataFrame()
    detalhes = []
    for _, r in atc.iterrows():
        try:
            f = json.loads(r.get("ficha") or "")
        except (ValueError, TypeError):
            continue
        for it in (f.get("itens") or []):
            tot["itens"] += 1
            votos = str(it.get("votos") or "")
            vota = str(it.get("votacao") or "")
            dec = str(it.get("decisao") or "") + " " + votos
            vlow = (vota + " " + votos).lower()
            if "unanime" in vlow or "unânime" in vlow:
                tot["unanime"] += 1
            if "maioria" in vlow:
                tot["maioria"] += 1
            dl = dec.lower()
            if re.search(r"divergindo da .rea|contrariando a .rea|"
                         r"divergiu da .rea", dl):
                tot["diverge_area"] += 1
            elif re.search(r"acompanhando (a|o) (manifesta|parecer|.rea|conclus)", dl):
                tot["acompanha_area"] += 1

            def _reg(nome, campo, trecho):
                dcanon = _dir_canon(nome)
                if not dcanon:
                    return
                s = stats.setdefault(dcanon, {"vencido": 0, "vista": 0,
                                              "divergencia": 0})
                s[campo] += 1
                detalhes.append({"Diretor": dcanon, "Evento": campo,
                                 "Data": r.get("data", ""),
                                 "Processo": it.get("processo", ""),
                                 "Assunto": str(it.get("assunto") or "")[:80],
                                 "Trecho": trecho[:160],
                                 "Ata": r.get("link", "")})
            for m in re.finditer(r"vencid[oa][s]?(?:,)? (?:o |a )?(?:Diretor[a]? |"
                                 r"Presidente(?: Interino)? |Diretor Substituto )?"
                                 r"([A-ZÀ-Ú][a-zà-ú]+(?: [A-ZÀ-Úa-zà-ú]+){0,4})",
                                 votos):
                _reg(m.group(1), "vencido", votos)
            for m in re.finditer(r"(?:pedido de vista d[oa]|solicitou vista|"
                                 r"pediu vista)[^.]*?([A-ZÀ-Ú][a-zà-ú]+"
                                 r"(?: [A-ZÀ-Úa-zà-ú]+){0,4})", votos + " " + dec):
                _reg(m.group(1), "vista", votos or dec)
            if re.search(r"\bvista\b", vlow):
                tot["vista"] += 1
            for m in re.finditer(r"(?:divergiu|abriu diverg[eê]ncia|voto divergente"
                                 r")[^.]*?([A-ZÀ-Ú][a-zà-ú]+(?: [A-ZÀ-Úa-zà-ú]+)"
                                 r"{0,4})", votos):
                _reg(m.group(1), "divergencia", votos)
    return stats, tot, pd.DataFrame(detalhes)


# --------------------------------------------------------------------------
# Entidades (pessoas/empresas) + Busca global
# --------------------------------------------------------------------------
def _ent_key(nome):
    """Nome normalizado p/ casar a mesma pessoa/empresa entre bases."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(nome or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


@st.cache_data(ttl=600)
def _indice_entidades():
    """entidade_key -> aparições em todas as bases (acusado, TC, audiências)."""
    idx = {}

    def add(nome, tipo, item):
        k = _ent_key(nome)
        if len(k) < 6:
            return
        e = idx.setdefault(k, {"nome": str(nome).strip(), "acusado": [],
                               "tc": [], "aud": []})
        e[tipo].append(item)

    proc, acus = carregar_pas()
    if proc is not None and acus is not None:
        pn_por_id = {r["idproc"]: (r["numero"], _norm_proc(r["numero"]))
                     for _, r in proc.iterrows()}
        for _, a in acus.iterrows():
            num, pn = pn_por_id.get(a["idproc"], ("", ""))
            if num:
                add(a["nome"], "acusado",
                    {"processo": num, "proc_norm": pn, "situacao": a["situacao"],
                     "desfecho": _desfecho_acusado(a["situacao"], a["historico"])})
    tc = carregar_termos()
    if tc is not None:
        for _, t in tc.iterrows():
            for nome in re.split(r"[;|]| e ", str(t["partes"] or "")):
                if len(nome.strip()) > 5:
                    add(nome, "tc", {"processo": t["processo"],
                                     "situacao": t["situacao"],
                                     "data": t["data_decisao"], "link": t["link"]})
    aud = carregar()
    if aud is not None:
        for _, r in aud.iterrows():
            item = {"id": r["id"], "data": r["data"], "componente": r["componente"],
                    "assunto": r["assunto"]}
            if str(r["solicitante_nome"]).strip():
                add(r["solicitante_nome"], "aud", item)
            if str(r["solicitante_empresa"]).strip():
                add(r["solicitante_empresa"], "aud", item)
    return idx


def _render_ficha_entidade(k, e):
    """Ficha consolidada de uma pessoa/empresa (todas as bases)."""
    st.markdown(f"### 👤 {e['nome']}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Acusado em", len(e["acusado"]))
    c2.metric("Termos de Compromisso", len(e["tc"]))
    c3.metric("Audiências", len(e["aud"]))
    if e["acusado"]:
        st.markdown("**⚖️ Processos em que é acusado:**")
        st.dataframe(pd.DataFrame(e["acusado"])[["processo", "situacao", "desfecho"]]
                     .rename(columns={"processo": "Processo", "situacao": "Situação",
                                      "desfecho": "Desfecho"}),
                     use_container_width=True, hide_index=True)
    if e["tc"]:
        st.markdown("**🤝 Termos de Compromisso (como parte):**")
        st.dataframe(pd.DataFrame(e["tc"])[["processo", "situacao", "data"]]
                     .rename(columns={"processo": "Processo", "situacao": "Situação",
                                      "data": "Decisão"}),
                     use_container_width=True, hide_index=True)
    if e["aud"]:
        st.markdown(f"**🏛️ Audiências particulares ({len(e['aud'])}):**")
        st.dataframe(pd.DataFrame(e["aud"])[["data", "componente", "assunto"]]
                     .rename(columns={"data": "Data", "componente": "Área",
                                      "assunto": "Assunto"}).head(40),
                     use_container_width=True, hide_index=True)
    _bloco_noticias_md("", nomes=e.get("_nomes") or [e["nome"]],
                       titulo="**🗞️ Notícias que citam o nome:**")


def _merge_entidades(idx, nomes):
    """Une várias grafias (variantes) do mesmo nome numa ficha só."""
    nomes = set(nomes)
    m = {"nome": " / ".join(sorted(nomes)), "_nomes": sorted(nomes),
         "acusado": [], "tc": [], "aud": []}
    for _, e in idx.items():
        if e["nome"] in nomes:
            for k in ("acusado", "tc", "aud"):
                m[k] += e[k]
    return m


def render_busca():
    st.subheader("🔎 Busca global — todas as bases")
    q = st.text_input("Nome, empresa, nº de processo ou tema",
                      key="bg_q", placeholder="ex.: XP Investimentos, 19957.003747/2023-43, insider…")
    ql = q.lower().strip()
    idx = _indice_entidades()
    # Autofill de VARIANTES: digite o nome acima e escolha as grafias que sao a
    # mesma pessoa/empresa; a busca combina todas (OU) e consolida a ficha.
    cands = sorted({e["nome"] for k, e in idx.items()
                    if ql and (ql in e["nome"].lower() or ql.upper() in k)})[:60]
    variantes = st.multiselect(
        "🔗 Variantes do nome (autofill) — junte grafias diferentes da mesma "
        "pessoa/empresa", cands, key="bg_vars",
        help="Digite o nome no campo acima; aqui aparecem as grafias encontradas. "
             "Marque todas que forem a mesma entidade — a busca combina tudo.")
    termos = list(variantes) if variantes else ([q.strip()] if q.strip() else [])
    if not termos:
        st.caption("A busca varre processos, acusados, termos, audiências, decisões, "
                   "atas, pautas, publicações (SEI), notícias, servidores e Quem é Quem. "
                   "Dica: digite um nome e use o **autofill** para juntar grafias diferentes.")
        return
    rgx = "|".join(re.escape(t) for t in termos)
    pn_q = _norm_proc(q) if q.strip() else ""

    # 1) Entidades (pessoas/empresas) — ficha consolidada
    if variantes:
        st.markdown(f"#### 👤 Ficha consolidada ({len(variantes)} grafia(s) unida(s))")
        _render_ficha_entidade("", _merge_entidades(idx, variantes))
        st.divider()
    else:
        hits_ent = [(k, e) for k, e in idx.items() if ql in e["nome"].lower()
                    or ql.upper() in k][:30]
        if hits_ent:
            st.markdown(f"#### 👤 Pessoas/Empresas ({len(hits_ent)})")
            nomes = {e["nome"]: k for k, e in hits_ent}
            sel = st.selectbox("Ver ficha consolidada de:", list(nomes), key="bg_ent")
            if sel:
                _render_ficha_entidade(nomes[sel], idx[nomes[sel]])
            st.divider()

    def _tab(titulo, df, cols, ren=None, links=None):
        if df is None or not len(df):
            return
        st.markdown(f"#### {titulo} ({len(df)})")
        cols = [c for c in cols if c in df.columns]   # robusto a schema drift
        show = df[cols].rename(columns=ren or {})
        cc = {c: st.column_config.LinkColumn(c, display_text="abrir ↗")
              for c in (links or [])}
        st.dataframe(show.head(50), use_container_width=True, hide_index=True,
                     column_config=cc or None)

    # 2) Processos sancionadores
    proc, _ = carregar_pas()
    if proc is not None:
        m = (proc["numero"].str.contains(rgx, case=False, na=False)
             | proc["objeto"].fillna("").str.contains(rgx, case=False)
             | proc["ementa"].fillna("").str.contains(rgx, case=False)
             | proc["acusados"].fillna("").str.contains(rgx, case=False))
        if pn_q:
            m |= proc["numero"].map(_norm_proc) == pn_q
        _tab("⚖️ Processos sancionadores", proc[m],
             ["numero", "fase", "objeto"], {"numero": "Processo", "fase": "Fase",
                                            "objeto": "Objeto"})
    # 3) Termos
    tc = carregar_termos()
    if tc is not None:
        m = (tc["processo"].str.contains(rgx, case=False, na=False)
             | tc["partes"].fillna("").str.contains(rgx, case=False))
        _tab("🤝 Termos de Compromisso", tc[m],
             ["processo", "situacao", "data_decisao", "partes"],
             {"processo": "Processo", "situacao": "Situação",
              "data_decisao": "Decisão", "partes": "Partes"})
    # 4) Audiências
    aud = carregar()
    if aud is not None:
        m = (aud["solicitante_nome"].fillna("").str.contains(rgx, case=False)
             | aud["solicitante_empresa"].fillna("").str.contains(rgx, case=False)
             | aud["assunto"].fillna("").str.contains(rgx, case=False))
        _tab("🏛️ Audiências particulares", aud[m].sort_values("data_iso", ascending=False),
             ["data", "componente", "solicitante_nome", "solicitante_empresa", "assunto"],
             {"data": "Data", "componente": "Área", "solicitante_nome": "Solicitante",
              "solicitante_empresa": "Empresa", "assunto": "Assunto"})
    # 5) Decisões do Colegiado
    dec = carregar_decisoes()
    if dec is not None and len(dec):
        m = (dec.get("descricao", pd.Series("", index=dec.index)).fillna("")
             .str.contains(rgx, case=False)
             | dec.get("ementa", pd.Series("", index=dec.index)).fillna("")
             .str.contains(rgx, case=False))
        _tab("📜 Decisões do Colegiado", dec[m].sort_values("data_iso", ascending=False),
             ["data", "tipo", "ementa", "link"],
             {"data": "Data", "tipo": "Tipo", "ementa": "Ementa", "link": "Link"},
             links=["Link"])
    # 6) Publicações SEI
    pub = carregar_publicacoes()
    if pub is not None:
        m = (pub["descricao"].fillna("").str.contains(rgx, case=False)
             | pub["resumo"].fillna("").str.contains(rgx, case=False))
        _tab("📑 Publicações (SEI)", pub[m].sort_values("data_iso", ascending=False),
             ["data", "descricao", "unidade", "link"],
             {"data": "Data", "descricao": "Descrição", "unidade": "Unidade",
              "link": "Link"}, links=["Link"])
    # 7) Notícias
    nt = carregar_noticias()
    if nt is not None and len(nt):
        m = (nt["titulo"].fillna("").str.contains(rgx, case=False)
             | nt.get("resumo", pd.Series("", index=nt.index)).fillna("")
             .str.contains(rgx, case=False))
        _tab("🗞️ Notícias", nt[m].sort_values("data_iso", ascending=False),
             ["data", "titulo", "categoria", "url"],
             {"data": "Data", "titulo": "Título", "categoria": "Categoria",
              "url": "Link"}, links=["Link"])
    # 8) Servidores (viagens)
    vg = carregar_viagens()
    if vg is not None:
        m = vg["servidor_nome"].fillna("").str.contains(rgx, case=False)
        _tab("🏢 Servidores — viagens", vg[m].sort_values("boletim_data_iso",
                                                          ascending=False),
             ["servidor_nome", "tipo", "destino", "periodo_ini", "valor_diarias"],
             {"servidor_nome": "Servidor", "tipo": "Tipo", "destino": "Destino",
              "periodo_ini": "Início", "valor_diarias": "Diárias (R$)"})
    # 9) Quem é Quem
    qq = carregar_quem()
    if qq is not None:
        m = (qq["nome"].fillna("").str.contains(rgx, case=False)
             | qq["cargo"].fillna("").str.contains(rgx, case=False))
        _tab("👥 Quem é Quem", qq[m], ["nome", "cargo", "sigla"],
             {"nome": "Nome", "cargo": "Cargo", "sigla": "Sigla"})


# --------------------------------------------------------------------------
# Abas por CASO DE USO (Radar / Enforcement / Colegiado / Bastidores)
# --------------------------------------------------------------------------
def _novidades_7d():
    """Coleta 'o que mudou' nos últimos 7 dias em todas as bases (robusto a
    ausência de base/coluna). Retorna lista de seções (título, DataFrame)."""
    corte = (dt.date.today() - dt.timedelta(days=7)).isoformat()
    secoes = []

    def q(db, sql, params=()):
        try:
            con = sqlite3.connect(db)
            df = pd.read_sql(sql, con, params=params)
            con.close()
            return df
        except Exception:
            return pd.DataFrame()

    d = q(DB_PATH, "SELECT data, componente, solicitante_nome AS solicitante, "
          "solicitante_empresa AS empresa, assunto FROM audiencias WHERE "
          "estado='valido' AND coletado_em>=? AND data_iso>=? ORDER BY data_iso",
          (corte, corte))
    if len(d):
        secoes.append(("🏛️ Audiências novas/futuras", d))
    d = q(JULGAR_DB_PATH, "SELECT data_julg AS julgado_em, relator_nome AS relator, "
          "processo, tipo FROM julgados WHERE coletado_em>=? "
          "ORDER BY id DESC LIMIT 30", (corte,))
    if len(d):
        secoes.append(("⚖️ Julgamentos que entraram na base", d))
    d = q(TERMOS_DB_PATH,
          "SELECT processo, situacao, data_decisao, partes FROM termos WHERE "
          "coletado_em>=? ORDER BY data_decisao_iso DESC LIMIT 30", (corte,))
    if len(d):
        secoes.append(("🤝 Termos de Compromisso novos", d))
    # pauta: pela data de PUBLICACAO no Diario (dd/mm/aaaa -> iso), nao pelo
    # coletado_em (um backfill marcaria o acervo inteiro como novidade)
    d = q(PAUTAS_DB_PATH,
          "SELECT data_publicacao AS publicado, data_sessao AS sessao, processo, "
          "relator, situacao FROM pauta_sei WHERE "
          "substr(data_publicacao,7,4)||'-'||substr(data_publicacao,4,2)||'-'||"
          "substr(data_publicacao,1,2) >= ? ORDER BY data_sessao_iso DESC",
          (corte,))
    if len(d):
        secoes.append(("🗓️ Movimentos de pauta publicados", d))
    # SEI: so o pertinente a enforcement (pautas, extratos, intimacoes, editais,
    # despachos em PAS) — portarias administrativas/reformas ficam no acervo
    d = q(PUBLICACOES_DB_PATH, "SELECT data, descricao, unidade, resumo, link "
          "FROM publicacoes WHERE coletado_em>=? AND ("
          "descricao LIKE 'Pauta%' OR descricao LIKE 'Extrato de Sess%' OR "
          "descricao LIKE 'Extrato de Termo%' OR descricao LIKE 'Extrato de Ata%' "
          "OR descricao LIKE 'Intima%' OR descricao LIKE 'Edital%' OR "
          "descricao LIKE 'Despacho%') ORDER BY data_iso DESC LIMIT 40",
          (corte,))
    if len(d):
        secoes.append(("📑 Publicações relevantes no Diário (SEI)", d))
    d = q(PESSOAL_DB_PATH, "SELECT numero AS boletim, data, pdf_url AS link FROM "
          "boletins WHERE coletado_em>=? AND texto<>'' ORDER BY data_iso DESC "
          "LIMIT 15", (corte,))
    if len(d):
        secoes.append(("🏢 Boletins de Pessoal novos", d))
    return secoes


def render_radar():
    st.subheader("🏠 Radar — o que está acontecendo na CVM")
    hoje = dt.date.today()
    # 1) Próximas sessões de julgamento + tirados de pauta (do Diário/SEI)
    psei = carregar_pauta_sei()
    jl = carregar_julgados()
    set_julg = set(jl["proc_norm"]) - {""} if jl is not None else set()
    if psei is not None and len(psei):
        p2 = psei.copy()
        p2["_ret"] = p2["situacao"].fillna("").str.startswith("retirado")
        p2["_ord"] = p2["data_sessao_iso"].replace("", "0000-00-00")
        ult = p2.sort_values("_ord").groupby("proc_norm", as_index=False).tail(1)
        fut = ult[(~ult["proc_norm"].isin(set_julg)) & (~ult["_ret"]) &
                  (ult["data_sessao_iso"] >= hoje.isoformat())]
        tir = ult[(~ult["proc_norm"].isin(set_julg)) &
                  (ult["_ret"] | ((ult["data_sessao_iso"] != "") &
                                  (ult["data_sessao_iso"] < hoje.isoformat())))]
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"#### 🗓️ Próximos julgamentos ({len(fut)})")
            if len(fut):
                st.dataframe(fut.sort_values("_ord")[
                    ["data_sessao", "processo", "relator"]].rename(columns={
                        "data_sessao": "Sessão", "processo": "Processo",
                        "relator": "Relator"}),
                    use_container_width=True, hide_index=True)
            else:
                st.info("Nenhuma sessão futura pautada.")
        with c2:
            st.markdown(f"#### 🚨 Tirados de pauta pendentes ({len(tir)})")
            if len(tir):
                st.dataframe(tir.sort_values("_ord", ascending=False)[
                    ["data_sessao", "processo", "situacao"]].rename(columns={
                        "data_sessao": "Estava p/", "processo": "Processo",
                        "situacao": "Situação"}),
                    use_container_width=True, hide_index=True)
            else:
                st.success("Nenhum retirado de pauta pendente.")
    # 2) O que mudou (7 dias)
    st.divider()
    st.markdown("#### 🆕 O que mudou nos últimos 7 dias")
    novidades = _novidades_7d()
    if not novidades:
        st.info("Nada novo registrado nos últimos 7 dias.")
    for titulo, df in novidades:
        with st.expander(f"{titulo} ({len(df)})",
                         expanded=(len(novidades) <= 3)):
            cc = {"link": st.column_config.LinkColumn("link",
                                                      display_text="abrir ↗")} \
                if "link" in df.columns else None
            st.dataframe(df, use_container_width=True, hide_index=True,
                         column_config=cc)
    # 3) Notícias recentes
    nt = carregar_noticias()
    if nt is not None and len(nt):
        corte14 = (hoje - dt.timedelta(days=14)).isoformat()
        rec = nt[nt["data_iso"] >= corte14].sort_values("data_iso",
                                                        ascending=False)
        if len(rec):
            st.divider()
            st.markdown(f"#### 🗞️ Notícias da CVM ({len(rec)} em 14 dias)")
            st.dataframe(rec[["data", "titulo", "categoria", "url"]].rename(
                columns={"data": "Data", "titulo": "Título",
                         "categoria": "Categoria", "url": "Link"}).head(25),
                use_container_width=True, hide_index=True,
                column_config={"Link": st.column_config.LinkColumn(
                    "Link", display_text="abrir ↗")})
    # 4) Painel de métricas e fontes brutas (acervo)
    st.divider()
    with st.expander("📊 Painel de métricas (audiências e visão geral)"):
        render_painel()
    with st.expander("📚 Acervo bruto — Publicações Eletrônicas (SEI)"):
        render_publicacoes()
    with st.expander("🗞️ Acervo de notícias (busca completa)"):
        render_noticias()


def render_enforcement():
    st.subheader("⚖️ Enforcement — o funil sancionador")
    # funil: contadores
    proc_pas, _ = carregar_pas()
    jl = carregar_julgados()
    tc = carregar_termos()
    psei = carregar_pauta_sei()
    hoje_iso = dt.date.today().isoformat()
    n_estoque = len(proc_pas) if proc_pas is not None else 0
    n_julg = len(jl) if jl is not None else 0
    n_tc = len(tc) if tc is not None else 0
    n_paut = 0
    if psei is not None and len(psei):
        set_j = set(jl["proc_norm"]) - {""} if jl is not None else set()
        p2 = psei[(~psei["proc_norm"].isin(set_j)) &
                  (psei["data_sessao_iso"] >= hoje_iso)]
        n_paut = p2["proc_norm"].nunique()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📥 Sancionadores na base", f"{n_estoque:,}".replace(",", "."))
    c2.metric("🗓️ Pautados p/ julgamento", n_paut)
    c3.metric("⚖️ Julgados", n_julg)
    c4.metric("🤝 Termos de Compromisso", f"{n_tc:,}".replace(",", "."))
    st.caption("👆 O funil: processo aberto → pauta → julgamento (ou TC). "
               "Clique em qualquer linha para abrir o dossiê completo do processo.")
    visao = st.segmented_control(
        "Etapa", ["📥 Processos", "🗓️ Pautas", "⚖️ Julgados", "🤝 Termos",
                  "📂 Não-sancionadores", "📊 Relatores"],
        key="enf_nav", default="📥 Processos", label_visibility="collapsed")
    st.divider()
    if visao == "📥 Processos":
        render_processos_lista()
    elif visao == "🗓️ Pautas":
        render_pautas()
    elif visao == "⚖️ Julgados":
        render_julgados_lista()
    elif visao == "🤝 Termos":
        render_termos()
    elif visao == "📂 Não-sancionadores":
        render_nao_sancionadores()
    elif visao == "📊 Relatores":
        render_prazos()


def _dados_ficha_diretor(d):
    """Junta tudo de um diretor (conduta.db + análises) para a ficha visual."""
    con = sqlite3.connect(CONDUTA_DB_PATH)
    ev = pd.read_sql("SELECT * FROM eventos WHERE diretor=:d", con,
                     params={"d": d})
    pz = pd.read_sql("SELECT * FROM prazos WHERE diretor=:d", con,
                     params={"d": d})
    perfil = pd.read_sql("SELECT * FROM julgado_perfil", con)
    try:
        dossie = con.execute("SELECT dossie FROM perfis WHERE diretor=?",
                             (d,)).fetchone()
    except Exception:
        dossie = None
    con.close()
    ana = {pn: a for (pn, t), a in _mapa_analises().items() if t == "julgado"}
    return ev, pz, perfil, (json.loads(dossie[0]) if dossie else {}), ana


# palavras-chave por tema, para mapear uma hipótese textual da IA a casos concretos
_TEMA_KW = {
    "insider_trading": ["insider", "informacao privilegiada", "informação privilegiada",
                        "art. 155", "155"],
    "manipulacao_fraude": ["manipula", "fraud", "artificiais", "nao equitativ",
                           "não equitativ", "spoofing", "layering"],
    "fato_relevante_dri": ["fato relevante", "dri", "art. 157", "157", "divulga"],
    "dever_diligencia_adm": ["diligencia", "diligência", "lealdade", "administrador",
                             "conselheir", "diretor estatut", "153", "154"],
    "abuso_controle": ["abuso de poder", "poder de controle", "controlador", "116", "117"],
    "carteira_irregular": ["carteira"],
    "fundos_investimento": ["fundo", "fidc", "fii", "gestor", "fiduci", "cotista"],
    "ofertas_publicas": ["oferta"],
    "auditoria": ["auditor"],
    "intermediarios": ["corretora", "churning", "suitability", "intermedia",
                       "distribuidora"],
    "informacoes_periodicas": ["formulario de referencia", "formulário", "demonstracoes",
                               "demonstrações", "periodic", "itr", "dfp", "atraso na"],
    "assembleia_conflito": ["assembleia", "conflito de interess", "direito de voto"],
    "agentes_autonomos": ["agente autonomo", "agente autônomo", "assessor de invest"],
    "rito_processual": ["prescri", "nulidade", "cerceamento"],
}


def _tema_de_texto(txt):
    """Temas prováveis de uma hipótese textual (para linkar a casos concretos)."""
    t = str(txt or "").lower()
    return [tema for tema, kws in _TEMA_KW.items() if any(k in t for k in kws)]


def _procs_no_texto(txt):
    """Números de processo (proc_norm) citados literalmente num texto."""
    return list(dict.fromkeys(
        re.findall(r"\d{4,5}\.\d{6}/\d{4}-\d{2}", str(txt or ""))))


def _conduta_analise_do_proc(pn):
    """Reúne a análise IA + metadados de um processo julgado (conduta.db)."""
    out = {"proc_norm": pn}
    if not os.path.exists(CONDUTA_DB_PATH):
        return out
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        r = con.execute("SELECT analise FROM analises WHERE proc_norm=? AND "
                        "tipo='julgado'", (pn,)).fetchone()
        try:
            out["analise"] = json.loads(r[0]) if r and r[0] else {}
        except (ValueError, TypeError):
            out["analise"] = {}
        ev = con.execute("SELECT diretor, data_iso, valor FROM eventos WHERE "
                         "evento='julgou' AND proc_norm=? LIMIT 1", (pn,)).fetchone()
        if ev:
            out["relator"], out["data"], out["valor"] = ev[0], ev[1], ev[2]
        jp = con.execute("SELECT n_pf, n_empresa, n_financeira FROM julgado_perfil "
                         "WHERE proc_norm=?", (pn,)).fetchone()
        if jp:
            out["n_pf"], out["n_empresa"], out["n_financeira"] = jp
        out["temas"] = [t[0] for t in con.execute(
            "SELECT tema FROM caso_tema WHERE proc_norm=? AND dominante=1", (pn,))]
    except Exception:
        pass
    # AGREGAÇÃO: análise do inteiro teor (jurisprudência) quando disponível
    try:
        jr = con.execute(
            "SELECT resultado, area_tecnica, votos, teses, resumo, relator, "
            "data_julg FROM juris_analise WHERE proc_norm=?", (pn,)).fetchone()
        if jr:
            try:
                teses = json.loads(jr[3]) if jr[3] else []
            except (ValueError, TypeError):
                teses = []
            out["juris"] = {"resultado": jr[0], "area_tecnica": jr[1],
                            "votos": jr[2], "teses": teses, "resumo": jr[4],
                            "relator": jr[5], "data": jr[6]}
            # completa metadados vazios com a melhor fonte
            if not out.get("relator") and jr[5]:
                out["relator"] = jr[5]
            if not out.get("data") and jr[6]:
                out["data"] = jr[6]
    except Exception:
        pass
    con.close()
    return out


def _melhor(*vals):
    """Melhor valor entre fontes: o primeiro não-vazio e mais informativo."""
    cands = [str(v).strip() for v in vals if v and str(v).strip()
             and str(v).strip() != "-"]
    return max(cands, key=len) if cands else ""


@st.dialog("Análise do caso (IA)", width="large")
def dialog_conduta_analise(pn):
    """Popup com a análise IA completa de um processo julgado — acessível a
    partir de cada hipótese do relatório de inconsistências."""
    dd = _conduta_analise_do_proc(pn)
    a = dd.get("analise") or {}
    st.markdown(f"### Processo nº {pn}")
    meta = []
    if dd.get("relator"):
        meta.append(f"**Relator:** {dd['relator']}")
    if dd.get("data"):
        meta.append(f"**Julgado em:** {dd['data']}")
    if dd.get("valor"):
        meta.append(f"**Multas:** R$ {dd['valor']:,.0f}".replace(",", "."))
    if meta:
        st.markdown("  ·  ".join(meta))
    cls = []
    if (dd.get("n_financeira") or 0) > 0:
        cls.append(f"{int(dd['n_financeira'])} inst. financeira(s)")
    if (dd.get("n_empresa") or 0) > 0:
        cls.append(f"{int(dd['n_empresa'])} empresa(s)")
    if (dd.get("n_pf") or 0) > 0:
        cls.append(f"{int(dd['n_pf'])} pessoa(s) física(s)")
    if cls:
        st.caption("Réus: " + ", ".join(cls))
    sev = str(a.get("severidade") or "").strip()
    if sev and sev != "-":
        cor = {"alta": "🔴", "media": "🟠", "média": "🟠",
               "baixa": "🟢"}.get(sev.lower(), "⚪")
        st.markdown(f"**Severidade (IA):** {cor} `{sev}`")
    jr = dd.get("juris") or {}
    if not a and not jr:
        st.info("A análise deste processo ainda não está disponível nas bases.")
    resumo = _melhor(a.get("resumo"), jr.get("resumo"))
    if resumo:
        st.markdown("**Resumo:**")
        st.write(resumo)
    cond = str(a.get("conduta_imputada") or "").strip()
    if cond and cond != "-":
        st.markdown("**Conduta imputada:**")
        st.write(cond)
    if jr.get("area_tecnica"):
        st.markdown("**Área técnica / acusação:**")
        st.write(str(jr["area_tecnica"]).strip())
    resultado = _melhor(jr.get("resultado"), a.get("desfecho"))
    if resultado:
        st.markdown("**Resultado / desfecho:**")
        st.write(resultado)
    if jr.get("votos"):
        st.markdown("**🗳️ Votação:**")
        st.write(str(jr["votos"]).strip().strip('"'))
    for rot, campo in [("Racional da decisão", "racional"),
                       ("Conduta do relator", "conduta_relator")]:
        v = str(a.get(campo) or "").strip()
        if v and v != "-":
            st.markdown(f"**{rot}:**")
            st.write(v)
    if jr.get("teses"):
        st.markdown("**⚖️ Teses do julgado:**")
        for t in jr["teses"]:
            tm = _TEMA_LABEL.get(t.get("tema", ""), t.get("tema", ""))
            te = str(t.get("tese") or "").strip()
            if te:
                st.markdown(f"- *{tm}:* {te}")
    temas = [t for t in dd.get("temas", []) if t and t != "outros"]
    if temas:
        st.caption("Temas: " + ", ".join(_TEMA_LABEL.get(t, t) for t in temas))
    fontes = (["análise de conduta"] if a else []) + \
             (["inteiro teor da jurisprudência"] if jr else [])
    if fontes:
        st.caption("🔗 Fontes agregadas: " + " + ".join(fontes)
                   + ". Confira sempre no inteiro teor.")
    pubs = _pubs_do_processo(pn)
    if pubs:
        st.markdown(f"**📑 Publicações no Diário (SEI) ({len(pubs)}):**")
        tp = pd.DataFrame([{"Data": p["data"], "Tipo": p["tipo"],
                            "Unidade": p["unidade"], "Link": p["link"]}
                           for p in pubs[:40]])
        tabela(tp, datas=["Data"], use_container_width=True, hide_index=True,
               column_config={"Link": st.column_config.LinkColumn(
                   "Link", display_text="abrir ↗")})


def _btn_processos(g, keyprefix):
    """Renderiza cada processo de um grupo como botão que abre sua análise IA."""
    for i, (_, row) in enumerate(g.iterrows()):
        pn = row["Processo"]
        val = row.get("Multa (R$)", 0) or 0
        extra = []
        if row.get("Data"):
            extra.append(str(row["Data"])[:10])
        if val:
            extra.append(f"R$ {val:,.0f}".replace(",", "."))
        lab = f"📄 {pn}" + (" · " + " · ".join(extra) if extra else "")
        if st.button(lab, key=f"{keyprefix}_{i}_{pn}", use_container_width=True):
            dialog_conduta_analise(pn)


@st.cache_data(ttl=600)
def _inconsistencias_diretor(d):
    """Detector determinístico: julgados do diretor onde o MESMO tema teve
    severidades opostas (alta e baixa) — candidatos a inconsistência, com os
    casos concretos para verificação. Retorna (df, clusters)."""
    if not os.path.exists(CONDUTA_DB_PATH):
        return pd.DataFrame(), []
    con = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        rows = con.execute(
            "SELECT e.proc_norm, e.data_iso, e.valor, ct.tema, a.analise, "
            "jp.n_pf, jp.n_empresa, jp.n_financeira FROM eventos e "
            "LEFT JOIN caso_tema ct ON ct.proc_norm=e.proc_norm AND ct.dominante=1 "
            "LEFT JOIN analises a ON a.proc_norm=e.proc_norm AND a.tipo='julgado' "
            "LEFT JOIN julgado_perfil jp ON jp.proc_norm=e.proc_norm "
            "WHERE e.diretor=? AND e.evento='julgou'", (d,)).fetchall()
    except Exception:
        rows = []
    con.close()
    recs = []
    for pn, di, val, tema, an, pf, emp, fin in rows:
        try:
            a = json.loads(an) if an else {}
        except (ValueError, TypeError):
            a = {}
        cls = ("Inst. financeira" if (fin or 0) > 0 else
               "Empresa" if (emp or 0) > 0 else "Pessoa física")
        recs.append({"Processo": pn, "Data": di or "", "tema": tema or "outros",
                     "Réu": cls, "Severidade": str(a.get("severidade") or "?")
                     .lower().replace("média", "media"), "Multa (R$)": val or 0,
                     "Conduta": str(a.get("conduta_imputada") or "")[:160],
                     "Desfecho": str(a.get("desfecho") or "")[:160]})
    df = pd.DataFrame(recs)
    clusters = []
    if len(df):
        for tema, g in df.groupby("tema"):
            sevs = set(g["Severidade"])
            if tema != "outros" and len(g) >= 2 and {"alta", "baixa"} <= sevs:
                clusters.append((tema, g.sort_values("Severidade")))
    return df, clusters


def _parse_trecho_div(t):
    """Quebra o trecho didático (ASSUNTO/RELATOR/DECISAO/VOTOS) em seções."""
    out = {}
    t = str(t or "")
    for key in ["ASSUNTO", "RELATOR", "DECISAO", "VOTOS"]:
        m = re.search(key + r":\s*(.*?)(?=\s*(?:ASSUNTO|RELATOR|DECISAO|VOTOS):|$)",
                      t, re.S)
        if m and m.group(1).strip():
            out[key] = m.group(1).strip()
    return out


@st.dialog("⚖️ Divergência no Colegiado", width="large")
def dialog_divergencia(row):
    """Ficha didática de uma divergência: o que se discutia, o que o Colegiado
    decidiu e como cada diretor votou (aberta ao clicar na divergência)."""
    a = row.get("diretor_a", "")
    b = row.get("diretor_b", "")
    proc = str(row.get("processo") or "").strip() or "—"
    data = str(row.get("data_iso") or "")
    tipo = row.get("tipo")
    st.markdown(f"### Processo {proc}")
    if data:
        st.caption(f"Sessão de julgamento em {data}")
    if tipo == "divergiu_de":
        st.warning(f"🗳️ **{a}** divergiu e ficou **vencido(a)**; prevaleceu a "
                   f"posição de **{b}**.")
    else:
        st.info(f"👁️ **{a}** pediu vista sobre a relatoria de **{b}**.")
    secoes = _parse_trecho_div(row.get("trecho"))
    rotulos = [("ASSUNTO", "📌 O que se discutia"),
               ("RELATOR", "👤 Relatoria"),
               ("DECISAO", "⚖️ O que o Colegiado decidiu"),
               ("VOTOS", "🗳️ Como votaram — a divergência")]
    if secoes:
        for key, label in rotulos:
            if secoes.get(key):
                st.markdown(f"**{label}:**")
                st.write(secoes[key])
    else:
        st.write(str(row.get("trecho") or "—"))
    dd = _conduta_analise_do_proc(proc)
    temas = [t for t in dd.get("temas", []) if t and t != "outros"]
    if temas:
        st.caption("Tema: " + ", ".join(_TEMA_LABEL.get(t, t) for t in temas))
    rac = str((dd.get("analise") or {}).get("racional") or "").strip()
    if rac and rac != "-":
        st.markdown("**🧠 Racional da decisão (análise IA):**")
        st.write(rac)
    pubs = _pubs_do_processo(proc)
    if pubs:
        st.markdown(f"**📑 Publicações no Diário (SEI) ({len(pubs)}):**")
        tp = pd.DataFrame([{"Data": p["data"], "Tipo": p["tipo"], "Link": p["link"]}
                           for p in pubs[:20]])
        tabela(tp, datas=["Data"], use_container_width=True, hide_index=True,
               column_config={"Link": st.column_config.LinkColumn(
                   "Link", display_text="abrir ↗")})
    st.caption("Fonte: votos minerados das Atas do Colegiado — confira sempre no "
               "inteiro teor da ata.")


def _card_caso(r):
    """Card de um julgado na ficha da disparidade, com a análise AGREGADA
    (conduta + inteiro teor da jurisprudência)."""
    pn = str(r.get("Processo", "") or "")
    dd = _conduta_analise_do_proc(pn)
    a = dd.get("analise") or {}
    jr = dd.get("juris") or {}
    with st.container(border=True):
        val = r.get("Multa (R$)", 0) or 0
        sev = str(r.get("Severidade", "")).strip()
        cabec = (f"**{pn}**  ·  {str(r.get('Data', ''))[:10]}  ·  "
                 f"{r.get('Réu', '')}  ·  "
                 + (f"R$ {val:,.0f}".replace(",", ".") if val else "sem multa")
                 + (f"  ·  severidade **{sev}**" if sev else ""))
        st.markdown(cabec)
        cond = _melhor(a.get("conduta_imputada"))
        if cond:
            st.markdown(f"**Conduta imputada:** {cond}")
        if jr.get("area_tecnica"):
            st.caption("Acusação (área técnica): " + str(jr["area_tecnica"])[:300])
        resultado = _melhor(jr.get("resultado"), a.get("desfecho"))
        if resultado:
            st.markdown(f"**Resultado:** {resultado}")
        votos = str(jr.get("votos") or "").strip().strip('"')
        teses = jr.get("teses") or []
        if votos or teses:
            with st.expander("🗳️ Votação e teses (inteiro teor)"):
                if votos:
                    st.write(votos)
                for t in teses:
                    te = str(t.get("tese") or "").strip()
                    if te:
                        st.markdown(f"- *{_TEMA_LABEL.get(t.get('tema', ''), t.get('tema', ''))}:* {te}")
        fontes = (["conduta"] if a else []) + (["inteiro teor"] if jr else [])
        if fontes:
            st.caption("🔗 Fontes: " + " + ".join(fontes))


def _anos(serie):
    a = serie.astype(str).str[:4]
    a = a[a.str.match(r"\d{4}", na=False)]
    return a.astype(int) if len(a) else a


def _contraste_disparidade(alta, baixa):
    """Leitura didática do que difere entre os polos grave e brando."""
    out = []
    fa = alta["Multa (R$)"][alta["Multa (R$)"] > 0]
    fb = baixa["Multa (R$)"][baixa["Multa (R$)"] > 0]
    ta = (f"R$ {fa.min():,.0f} a R$ {fa.max():,.0f}".replace(",", ".")
          if len(fa) else "sem multa")
    tb = (f"R$ {fb.min():,.0f} a R$ {fb.max():,.0f}".replace(",", ".")
          if len(fb) else "sem multa")
    out.append(f"**Multas** — graves: {ta}; brandos: {tb}.")
    ra = alta["Réu"].mode()
    rb = baixa["Réu"].mode()
    if len(ra) and len(rb) and ra.iloc[0] != rb.iloc[0]:
        out.append(f"**Perfil de réu** — nos graves predomina **{ra.iloc[0]}**; "
                   f"nos brandos, **{rb.iloc[0]}** — o tratamento parece variar com "
                   "o tipo de réu.")
    ya, yb = _anos(alta["Data"]), _anos(baixa["Data"])
    if len(ya) and len(yb):
        out.append(f"**Período** — graves entre {ya.min()}–{ya.max()}; brandos "
                   f"entre {yb.min()}–{yb.max()}.")
        if yb.mean() > ya.mean() + 0.5:
            out.append("Os casos **brandos são mais recentes** → possível "
                       "abrandamento do entendimento ao longo do tempo.")
        elif ya.mean() > yb.mean() + 0.5:
            out.append("Os casos **graves são mais recentes** → possível "
                       "endurecimento ao longo do tempo.")
    return out


@st.dialog("⚖️ Ficha da disparidade", width="large")
def dialog_disparidade(d, tema, g):
    """Ficha detalhada de UMA disparidade (tema com severidades opostas): o
    contraste e os casos dos dois polos, lado a lado."""
    alta = g[g["Severidade"] == "alta"]
    baixa = g[g["Severidade"] == "baixa"]
    st.markdown(f"### {_TEMA_LABEL.get(tema, tema)}")
    st.caption(f"Disparidade na atuação de {d}")
    st.warning(f"O mesmo tema recebeu de **{d}** severidades **opostas**: "
               f"**{len(alta)} caso(s) grave(s)** × **{len(baixa)} brando(s)**.")
    st.markdown("**🔎 Contraste (o que difere entre os polos):**")
    for ins in _contraste_disparidade(alta, baixa):
        st.markdown(f"- {ins}")
    try:
        ags, _ = _agentes_tese()
        tv = str((ags.get(tema) or {}).get("tese_vigente") or "").strip()
        if tv and tv != "-":
            st.info(f"**⚖️ Tese vigente do tema:** {tv}")
    except Exception:
        pass
    st.markdown("#### ⬆️ Tratados como GRAVES (severidade alta)")
    for _, r in alta.sort_values("Data").iterrows():
        _card_caso(r)
    st.markdown("#### ⬇️ Tratados como BRANDOS (severidade baixa)")
    for _, r in baixa.sort_values("Data").iterrows():
        _card_caso(r)
    st.caption("Indício a verificar — cada caso pode ter especificidades (provas, "
               "dosimetria, contexto) que os dados agregados não capturam.")


_MESES_PT = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
             "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
             "outubro": 10, "novembro": 11, "dezembro": 12}


def _dias_periodo(t):
    """Dias (inclusive) de um período tipo '02 a 06 de março de 2026' ou
    '31 de janeiro a 07 de fevereiro de 2026'."""
    m = re.search(r"(\d{1,2})(?:\s+de\s+([a-zç]+))?(?:\s+de\s+(\d{4}))?\s+a\s+"
                  r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})", str(t or "").lower())
    if not m:
        return 0
    try:
        d1, mes1s, a1s, d2, mes2s, a2 = m.groups()
        mes2 = _MESES_PT.get(mes2s)
        mes1 = _MESES_PT.get(mes1s) if mes1s else mes2
        a1 = int(a1s) if a1s else int(a2)
        if not (mes1 and mes2):
            return 0
        return (dt.date(int(a2), mes2, int(d2))
                - dt.date(a1, mes1, int(d1))).days + 1
    except (ValueError, TypeError):
        return 0


@st.cache_data(ttl=300)
def _diretor_boletim(d):
    """Casa o nome curto do diretor com o nome completo do Boletim de Pessoal e
    retorna (mandato, n_viagens, dias_viajando). Mandato vem dos movimentos
    estatutários (Diretor/Presidente); viagens da tabela viagens."""
    toks = [_ent_key(t) for t in str(d).split() if len(t) > 2]
    if not toks or not os.path.exists(PESSOAL_DB_PATH):
        return None, 0, 0
    con = sqlite3.connect(PESSOAL_DB_PATH)
    try:
        nomes = [r[0] for r in con.execute(
            "SELECT DISTINCT servidor_nome FROM movimentos "
            "UNION SELECT DISTINCT servidor_nome FROM viagens")]
    except Exception:
        con.close()
        return None, 0, 0
    # agrega TODAS as variantes de grafia do nome (chaves distintas)
    skeys = sorted({_ent_key(n) for n in nomes
                    if all(t in _ent_key(n) for t in toks)})
    if not skeys:
        con.close()
        return None, 0, 0
    ph = ",".join("?" * len(skeys))
    rows = con.execute(
        "SELECT tipo, boletim_data_iso FROM movimentos WHERE servidor_key IN "
        f"({ph}) AND (funcao LIKE '%Diretor%' OR funcao LIKE '%Presidente%') AND "
        "boletim_data_iso<>'' ORDER BY boletim_data_iso", skeys).fetchall()
    pers = [r[0] for r in con.execute(
        f"SELECT periodo_ini FROM viagens WHERE servidor_key IN ({ph}) AND "
        "tipo='afastamento_pais'", skeys)]
    con.close()
    noms = [x[1] for x in rows if x[0] == "nomeacao"]
    exos = [x[1] for x in rows if x[0] == "exoneracao"]
    mand = None
    if noms:
        start = noms[0]
        exo_after = [e for e in exos if e >= noms[-1]]
        ativo = not exo_after
        end = exo_after[0] if exo_after else dt.date.today().isoformat()
        mand = {"start": start, "end": end, "ativo": ativo,
                "dias": _busdays(start, end)}
    return mand, len(pers), sum(_dias_periodo(p) for p in pers)


def _pk_nome(n):
    """Chave de pessoa (1º + último token), robusta a variantes de grafia."""
    toks = [t for t in _ent_key(n).split() if len(t) > 2]
    return (toks[0], toks[-1]) if len(toks) >= 2 else tuple(toks)


def _parse_valor(s):
    s = str(s or "").strip()
    if not s:
        return 0.0
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def _periodo_datas(t):
    """(início, fim) ISO de um período textual, ou (None, None)."""
    m = re.search(r"(\d{1,2})(?:\s+de\s+([a-zç]+))?(?:\s+de\s+(\d{4}))?\s+a\s+"
                  r"(\d{1,2})\s+de\s+([a-zç]+)\s+de\s+(\d{4})",
                  str(t or "").lower())
    if not m:
        return (None, None)
    try:
        d1, m1, a1, d2, m2, a2 = m.groups()
        mo2 = _MESES_PT.get(m2)
        mo1 = _MESES_PT.get(m1) if m1 else mo2
        y1 = int(a1) if a1 else int(a2)
        if not (mo1 and mo2):
            return (None, None)
        return (dt.date(y1, mo1, int(d1)).isoformat(),
                dt.date(int(a2), mo2, int(d2)).isoformat())
    except (ValueError, TypeError):
        return (None, None)


def _busdays(ini, fim):
    """Dias úteis (seg–sex) em [ini, fim] inclusive. Feriados não deduzidos."""
    try:
        d0 = dt.date.fromisoformat(str(ini)[:10])
        d1 = dt.date.fromisoformat(str(fim)[:10])
    except (ValueError, TypeError):
        return 0
    if d1 < d0:
        return 0
    total = (d1 - d0).days + 1
    semanas, extra = divmod(total, 7)
    bd = semanas * 5
    wd = d0.weekday()
    for i in range(extra):
        if (wd + i) % 7 < 5:
            bd += 1
    return bd


def _uteis_periodo(t):
    """Dias úteis de um período de viagem textual ('02 a 06 de março de 2026')."""
    ini, fim = _periodo_datas(t)
    return _busdays(ini, fim) if ini and fim else 0


@st.cache_data(ttl=300)
def _interino_periodos():
    """Períodos em que cada diretor exerceu a Presidência interinamente, do
    Boletim: (a) atos formais 'designado para responder pela/exercer a
    Presidência ... no período de X a Y'; (b) o contínuo após a vacância
    (renúncia do presidente), atribuído a quem assina como Presidente Interino."""
    if not os.path.exists(PESSOAL_DB_PATH):
        return {}
    con = sqlite3.connect(PESSOAL_DB_PATH)
    per = {}
    VERBO = re.compile(r"designad[oa]\s+para\s+(?:responder pela|exercer[, ]+"
                       r"(?:interinamente[, ]+)?a)\s+Presid[eê]ncia", re.I)
    NAMEBEF = re.compile(r"([A-ZÀ-Ú][A-ZÀ-Ú'’ ]{8,55})[, ]+"
                         r"(?:Diretor[a]?,?\s*)?$")
    PERRE = re.compile(r"no per[ií]odo de\s+(.*?)(?:,?\s*inclusive|,\s*com|,\s*por "
                       r"motivo|\.|;|conforme|\()", re.I)
    for (texto,) in con.execute("SELECT texto FROM boletins WHERE texto LIKE "
                                "'%designad%Presid%' AND data_iso<>''"):
        flat = re.sub(r"\s+", " ", texto)
        for m in VERBO.finditer(flat):
            nm = NAMEBEF.search(flat[max(0, m.start() - 70):m.start()].strip() + " ")
            if not nm:
                continue
            nome = re.sub(r".*SUBSTITU\w+\s*", "", nm.group(1).strip(), flags=re.I)
            pm = PERRE.search(flat[m.end():m.end() + 170])
            if not pm:
                continue
            ini, fim = _periodo_datas(pm.group(1))
            if ini and fim:
                per.setdefault(_pk_nome(nome), []).append((ini, fim))
    vac = con.execute("SELECT MAX(boletim_data_iso) FROM movimentos WHERE "
                      "tipo='exoneracao' AND funcao LIKE '%Presidente%' AND "
                      "funcao NOT LIKE '%Interino%'").fetchone()[0]
    if vac:
        hoje = dt.date.today().isoformat()
        for (texto,) in con.execute("SELECT texto FROM boletins WHERE data_iso>=? "
                                    "AND texto<>''", (vac,)):
            for m in re.finditer(r"[Aa]ssinad[oa][^.\n]{0,40}por\s+([A-ZÀ-Ú]"
                                 r"[A-ZÀ-Ú ]{6,55}?)\s+Presidente Interino\b", texto):
                per.setdefault(_pk_nome(m.group(1)), []).append((vac, hoje))
    con.close()
    out = {}
    for k, lst in per.items():
        merged = []
        for ini, fim in sorted(set(lst)):
            if merged and ini <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], fim))
            else:
                merged.append((ini, fim))
        out[k] = merged
    return out


@st.cache_data(ttl=300)
def _mandato_ate():
    """{pessoa: data-fim de mandato mais recente} do texto das nomeações
    ('nomeado/reconduzido para exercer o cargo de Diretor/Presidente ... com
    mandato até DD de mês de AAAA'). É o fim autoritativo do mandato."""
    if not os.path.exists(PESSOAL_DB_PATH):
        return {}
    con = sqlite3.connect(PESSOAL_DB_PATH)
    PAT = re.compile(
        r"([A-ZÀ-Ú][A-ZÀ-Ú'’]+(?:\s+[A-ZÀ-Ú'’]+){1,6}),\s*(?:cedid[oa][^,]*,\s*)?"
        r"(?:nomead[oa]|reconduzid[oa])\b.{0,240}?mandato\s+at[eé]\s+(\d{1,2})\s+de"
        r"\s+([a-zç]+)\s+de\s+(\d{4})", re.I)
    out = {}
    for (texto,) in con.execute("SELECT texto FROM boletins WHERE texto LIKE "
                                "'%mandato at%' AND data_iso<>''"):
        flat = re.sub(r"\s+", " ", texto)
        for m in PAT.finditer(flat):
            nome = re.sub(r".*NOMEA[CÇ][AÃ]O\s*", "", m.group(1), flags=re.I).strip()
            mo = _MESES_PT.get(m.group(3).lower())
            if not mo:
                continue
            fim = f"{int(m.group(4)):04d}-{mo:02d}-{int(m.group(2)):02d}"
            k = _pk_nome(nome)
            if k not in out or fim > out[k]:
                out[k] = fim
    con.close()
    return out


@st.cache_data(ttl=300)
def _presidencia_plena():
    """{pessoa: data em que passou a Presidente PLENO} — detectada pela
    assinatura dos boletins (primeira vez que assina 'Presidente' sem 'Interino'
    APÓS a última assinatura como 'Presidente Interino')."""
    if not os.path.exists(PESSOAL_DB_PATH):
        return {}
    con = sqlite3.connect(PESSOAL_DB_PATH)
    sig = {}
    for data, texto in con.execute("SELECT data_iso, texto FROM boletins WHERE "
                                   "data_iso<>'' AND texto<>''"):
        flat = re.sub(r"\s+", " ", texto)
        for m in re.finditer(r"[Aa]ssinad[oa][^.\n]{0,40}por\s+([A-ZÀ-Ú]"
                             r"[A-ZÀ-Ú ]{6,55}?)\s+(Presidente Interino|Presidente)"
                             r"\b", flat):
            k = _pk_nome(m.group(1))
            role = "int" if "Interino" in m.group(2) else "ple"
            sig.setdefault(k, {"int": [], "ple": []})[role].append(data)
    con.close()
    out = {}
    for k, d in sig.items():
        if not d["ple"]:
            continue
        ultimo_int = max(d["int"]) if d["int"] else ""
        aposteriori = sorted(x for x in d["ple"] if x > ultimo_int)
        if len(aposteriori) >= 2:   # sustentado (evita 'Presidente' de substituto)
            out[k] = aposteriori[0]
    return out


def _role_intervals(inicio, fim, mine, pleno_start):
    """Divide [inicio, fim] em intervalos por papel (Diretor × Presidência
    interina × Presidente), sem sobreposição."""
    out = {"Diretor(a)": [], "Presidência (interina)": [], "Presidente": []}
    cap = fim
    if pleno_start and inicio < pleno_start < fim:
        out["Presidente"] = [(pleno_start, fim)]
        cap = pleno_start
    interina = []
    for a, b in mine:
        a2, b2 = max(a, inicio), min(b, cap)
        if a2 < b2:
            interina.append((a2, b2))
    interina.sort()
    out["Presidência (interina)"] = interina
    ocupado = sorted(interina + out["Presidente"])
    cur, dire = inicio, []
    for a, b in ocupado:
        if cur < a:
            dire.append((cur, a))
        cur = max(cur, b)
    if cur < fim:
        dire.append((cur, fim))
    out["Diretor(a)"] = dire
    return out


def _ativo_recente(con, nome, skeys, meses=15):
    """A pessoa mostra atividade nos últimos ~15 meses? (viagem, ou assinatura/
    citação como Presidente/Diretor num boletim recente.) Distingue quem foi
    reconduzido e continua de quem apenas atuou logo após um mandato parcial."""
    corte = (dt.date.today() - dt.timedelta(days=int(meses * 30.4))).isoformat()
    if skeys:
        ph = ",".join("?" * len(skeys))
        if con.execute(f"SELECT 1 FROM viagens WHERE servidor_key IN ({ph}) AND "
                       "boletim_data_iso>? LIMIT 1", (*skeys, corte)).fetchone():
            return True
    toks = _ent_key(nome).split()
    if len(toks) < 2:
        return False
    first, last = toks[0], toks[-1]
    for (texto,) in con.execute("SELECT texto FROM boletins WHERE data_iso>? AND "
                                "texto<>''", (corte,)):
        flat = re.sub(r"\s+", " ", texto)
        for m in re.finditer(r"[A-ZÀ-Ú][A-ZÀ-Ú ]{6,55}?\s+(?:Presidente(?: Interino)?"
                             r"|Diretora?)\b", flat):
            k = _ent_key(m.group(0))
            if first in k and last in k:
                return True
    return False


@st.cache_data(ttl=300)
def _transp_db():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "transparencia.db")


@st.cache_data(ttl=600)
def _transp_trips():
    """pk(nome) -> lista de viagens (data_iso, tipo, diárias, passagens, total)
    do Portal da Transparência, para enxertar o custo real por fase/pessoa."""
    out = {}
    tp = _transp_db()
    if not os.path.exists(tp):
        return out
    con = sqlite3.connect(tp)
    try:
        for nome, di, tipo, vd, vp, vt in con.execute(
                "SELECT beneficiario, data_inicio, tipo, valor_diarias, "
                "valor_passagem, valor_total FROM viagens_gov"):
            k = _pk_nome(nome)
            if len(k) < 2:
                continue
            out.setdefault(k, []).append(
                (str(di or "")[:10], str(tipo or ""), vd or 0.0, vp or 0.0,
                 vt or 0.0))
    except Exception:
        pass
    con.close()
    return out


@st.cache_data(ttl=600)
def _cvm_grupos():
    """(dir_keys:set, sup_nomes:dict pk->nome) — diretores/presidentes e
    superintendentes (com ato nos últimos 5 anos), por chave 1º+último nome."""
    dirs, sups = set(), {}
    if not os.path.exists(PESSOAL_DB_PATH):
        return dirs, sups
    con = sqlite3.connect(PESSOAL_DB_PATH)
    corte = (dt.date.today() - dt.timedelta(days=int(5 * 365.25))).isoformat()
    try:
        for nome, fun, bd, da in con.execute(
                "SELECT servidor_nome, funcao, boletim_data_iso, data_ato_iso "
                "FROM movimentos WHERE funcao LIKE '%Diretor%' OR funcao LIKE "
                "'%Presidente%' OR funcao LIKE '%uperinten%'"):
            k = _pk_nome(nome)
            if len(k) < 2:
                continue
            fl = str(fun or "").lower()
            if "diretor" in fl or "presidente" in fl:
                dirs.add(k)
            if "superinten" in fl and (bd or da or "") >= corte:
                if k not in sups or len(nome) > len(sups[k]):
                    sups[k] = nome
    except Exception:
        pass
    con.close()
    return dirs, sups


@st.cache_data(ttl=600)
def _transparencia_superintendentes():
    """Custo de viagens (Transparência) dos superintendentes dos últimos 5
    anos, por pessoa. Vazio se as bases não existirem."""
    _, sups = _cvm_grupos()
    trips = _transp_trips()
    if not (sups and trips):
        return pd.DataFrame()
    recs = []
    for k, nome in sups.items():
        tr = trips.get(k, [])
        if not tr:
            continue
        recs.append({
            "Superintendente": nome.title(), "Viagens": len(tr),
            "Internac.": sum(1 for x in tr if x[1].lower().startswith("intern")),
            "Diárias (R$)": round(sum(x[2] for x in tr), 2),
            "Passagens (R$)": round(sum(x[3] for x in tr), 2),
            "Total (R$)": round(sum(x[4] for x in tr), 2)})
    if not recs:
        return pd.DataFrame()
    return pd.DataFrame(recs).sort_values("Total (R$)", ascending=False)


@st.cache_data(ttl=600)
def _transparencia_custo_serie():
    """Série do custo de viagens (Transparência): por ano e por mês, segmentada
    em Diretores / Superintendentes / Demais servidores."""
    tp = _transp_db()
    if not os.path.exists(tp):
        return pd.DataFrame(), pd.DataFrame()
    dirs, sups = _cvm_grupos()
    con = sqlite3.connect(tp)
    try:
        rows = con.execute("SELECT beneficiario, data_inicio, valor_total "
                           "FROM viagens_gov").fetchall()
    except Exception:
        rows = []
    con.close()
    reg = []
    for nome, di, vt in rows:
        di = str(di or "")[:10]
        if len(di) < 7:
            continue
        k = _pk_nome(nome)
        grp = ("Diretores" if k in dirs else
               "Superintendentes" if k in sups else "Demais servidores")
        reg.append({"ano": di[:4], "mes": di[:7], "grupo": grp,
                    "valor": vt or 0.0})
    if not reg:
        return pd.DataFrame(), pd.DataFrame()
    d = pd.DataFrame(reg)
    anual = d.pivot_table(index="ano", columns="grupo", values="valor",
                          aggfunc="sum", fill_value=0)
    mensal = d.pivot_table(index="mes", columns="grupo", values="valor",
                           aggfunc="sum", fill_value=0)
    return anual, mensal


def _diretores_mandatos():
    """TODOS os diretores/presidentes (atuais + ex), com o mandato segmentado
    por papel (Diretor × Presidência interina) a partir dos atos do Boletim.
    Dias no cargo × dias viajando × custo de diárias por fase, p/ fiscalização."""
    if not os.path.exists(PESSOAL_DB_PATH):
        return pd.DataFrame()
    con = sqlite3.connect(PESSOAL_DB_PATH)
    try:
        stat = con.execute(
            "SELECT tipo, servidor_nome, boletim_data_iso, funcao FROM movimentos "
            "WHERE (funcao LIKE '%Diretor%' OR funcao LIKE '%Presidente%') AND "
            "boletim_data_iso<>'' ORDER BY boletim_data_iso").fetchall()
        nomes_vg = [r[0] for r in con.execute(
            "SELECT DISTINCT servidor_nome FROM viagens")]
    except Exception:
        con.close()
        return pd.DataFrame()
    hoje = dt.date.today()
    interinos = _interino_periodos()
    mate = _mandato_ate()
    plenos = _presidencia_plena()
    ttrips_all = _transp_trips()   # custo real (Transparência) por pessoa
    pessoas = {}
    for t, nome, data, fun in stat:
        p = pessoas.setdefault(_pk_nome(nome),
                               {"nome": nome, "noms": [], "exos": [], "pres": False})
        if len(nome) > len(p["nome"]):
            p["nome"] = nome
        (p["noms"] if t == "nomeacao" else p["exos"]).append(data)
        if "presidente" in fun.lower():
            p["pres"] = True
    recs = []
    for k, p in pessoas.items():
        if not p["noms"]:
            continue
        inicio, last_nom = min(p["noms"]), max(p["noms"])
        toks = [t for t in _ent_key(p["nome"]).split() if len(t) > 2]
        skeys = sorted({_ent_key(n) for n in nomes_vg
                        if all(t in _ent_key(n) for t in toks)})
        exo = [e for e in p["exos"] if e >= last_nom]
        hoje_iso = hoje.isoformat()
        fim_decl = mate.get(k)
        if exo:
            # exoneração/término explícito é autoritativo: saiu.
            fim, status = min(exo), "Ex-diretor(a)"
        elif fim_decl:
            if fim_decl >= hoje_iso:
                fim, status = hoje_iso, "Em exercício"
            elif _ativo_recente(con, p["nome"], skeys):
                fim, status = hoje_iso, "Em exercício"   # reconduzido/continua
            else:
                fim, status = fim_decl, "Ex-diretor(a)"
        elif (hoje - dt.date.fromisoformat(inicio)).days > 5.6 * 365:
            fim = (dt.date.fromisoformat(inicio)
                   + dt.timedelta(days=int(5 * 365.25))).isoformat()
            status = "Ex-diretor(a) (fim estimado)"
        else:
            fim, status = hoje_iso, "Em exercício"
        vg = []
        if skeys:
            ph = ",".join("?" * len(skeys))
            vg = con.execute(
                f"SELECT tipo, periodo_ini, boletim_data_iso, valor_diarias FROM "
                f"viagens WHERE servidor_key IN ({ph})", skeys).fetchall()
        ptr = ttrips_all.get(k, [])   # viagens da Transparencia desta pessoa
        mine = []
        for a, b in interinos.get(k, []):
            a2, b2 = max(a, inicio), min(b, fim)
            if a2 < b2:
                mine.append((a2, b2))
        ps = plenos.get(k)
        # presidência plena por assinatura só p/ diretor ATUAL (evita confundir
        # substituições eventuais de ex-diretores com virada de presidencia)
        if ps and status == "Em exercício":
            ps = max(ps, inicio)
            if ps >= fim:
                ps = None
        else:
            ps = None

        def bloco(papel, intervals):
            if not intervals:
                return None
            bdias = sum(_busdays(a, b) for a, b in intervals)

            def dentro(d):
                return any(a <= d <= b for a, b in intervals)
            sub = [x for x in vg if dentro(x[2] or "")]
            dv = sum(_dias_periodo(x[1]) for x in sub
                     if x[0] == "afastamento_pais")
            nv = sum(1 for x in sub if x[0] == "afastamento_pais")
            custo = sum(_parse_valor(x[3]) for x in sub)
            # custo REAL da Transparencia, atribuido a esta fase pela data
            tsub = [x for x in ptr if dentro(x[0])]
            treal = sum(x[4] for x in tsub)
            tintl = sum(1 for x in tsub if x[1].lower().startswith("intern"))
            return {"Diretor(a)": p["nome"].title(), "Papel": papel,
                    "Início": min(a for a, _ in intervals),
                    "Dias úteis (mandato)": bdias, "Viagens": nv,
                    "Dias viajando": dv,
                    "% do mandato viajando":
                    round(100 * dv / bdias, 1) if bdias else 0.0,
                    "Custo real (R$)": round(treal, 2), "Intl": tintl,
                    "Diárias Boletim (R$)": round(custo, 2), "Status": status}

        if p["pres"]:
            # presidente estatutário: mandato inteiro como Presidente
            r = bloco("Presidente", [(inicio, fim)])
            if r:
                recs.append(r)
        elif mine or ps:
            # diretor que exerceu a presidência (interina e/ou plena — ex.: Otto)
            segs = _role_intervals(inicio, fim, mine, ps)
            for papel in ("Diretor(a)", "Presidência (interina)", "Presidente"):
                r = bloco(papel, segs.get(papel, []))
                if r:
                    recs.append(r)
        else:
            r = bloco("Diretor(a)", [(inicio, fim)])
            if r:
                recs.append(r)
    con.close()
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs)
    df["_ord"] = df["Status"].map({"Em exercício": 0}).fillna(1)
    return df.sort_values(["_ord", "Dias viajando"],
                          ascending=[True, False]).drop(columns="_ord")


def render_ficha_diretor():
    st.markdown("#### 🎯 Ficha visual do diretor")
    if not os.path.exists(CONDUTA_DB_PATH):
        st.info("Base de conduta ainda não disponível.")
        return
    con = sqlite3.connect(CONDUTA_DB_PATH)
    dirs = [r[0] for r in con.execute(
        "SELECT DISTINCT diretor FROM eventos WHERE diretor<>'(Colegiado)' "
        "AND diretor<>'' ORDER BY diretor")]
    con.close()
    d = st.selectbox("Diretor", dirs, key="fd_dir", index=None,
                     placeholder="escolha um diretor…")
    if not d:
        return
    ev, pz, perfil, dossie, ana = _dados_ficha_diretor(d)
    julgs = ev[ev["evento"] == "julgou"].copy()
    # ---- PERFIL DO AGENTE (o que o agente é) ------------------------------
    st.markdown(f"## 🤖 {d}")
    if dossie:
        with st.container(border=True):
            st.markdown("##### 🧬 Perfil do agente")
            st.caption("Este é o perfil que **define o agente de IA** deste diretor "
                       "— o mesmo conhecimento que ele usa ao ser consultado sobre "
                       "um caso. As métricas e gráficos abaixo são a evidência.")
            pr = str(dossie.get("perfil_resumido") or "").strip()
            if pr and pr != "-":
                st.markdown(pr)
            for rot, campo in [("⚖️ Padrão decisório", "padrao_decisorio"),
                               ("💰 Dosimetria", "severidade_dosimetria"),
                               ("🎯 Perfil dos réus", "perfil_dos_reus"),
                               ("📚 Temas recorrentes", "temas_recorrentes"),
                               ("🗳️ Divergências e vistas", "divergencias_e_vistas"),
                               ("⏱️ Prazos e pautas", "prazos_e_pautas")]:
                v = dossie.get(campo)
                if isinstance(v, list):
                    v = " · ".join(str(x) for x in v)
                v = str(v or "").strip()
                if v and v != "-":
                    st.markdown(f"**{rot}:** {v}")
            if dossie.get("incoerencias"):
                st.caption("⚠️ **Incoerências e hipóteses a verificar:** veja o "
                           "*Relatório de inconsistências* ao final — lá cada "
                           "hipótese abre os processos antagônicos e a análise "
                           "de cada um.")
            ck = dossie.get("casos_marcantes") or []
            if ck:
                st.markdown("**Casos marcantes:** " + " · ".join(str(x) for x in ck))
            sp = str(dossie.get("system_prompt") or "").strip()
            if sp:
                st.markdown("🤖 **Este perfil É o agente.** Copie o *system prompt* "
                            "abaixo e cole numa conversa (comigo ou em qualquer LLM) "
                            "para consultá-lo sobre um caso concreto.")
                with st.expander("📋 System prompt do agente (copiar)"):
                    st.code(sp, language=None)
    else:
        st.info("O dossiê/agente deste diretor ainda não foi gerado.")
    st.divider()
    # ---- métricas de cabeçalho (evidência) -------------------------------
    pj = pz[pz["situacao"] == "julgado"].dropna(subset=["dias"])
    est = pz[pz["situacao"] == "em estoque"]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("⚖️ Julgou", len(julgs))
    c2.metric("💰 Multas (R$ mi)", f"{julgs['valor'].sum() / 1e6:,.1f}"
              .replace(",", "."))
    c3.metric("⏱️ Prazo médio (dias)*",
              int(pj["dias"].mean()) if len(pj) else "—")
    c4.metric("📥 Estoque atual", len(est))
    c5.metric("🕰️ Idade média do estoque",
              f"{int(est['dias'].mean())} d" if len(est) else "—")
    st.caption("*Prazo = sorteio da relatoria (informativos) → julgamento, "
               f"nos {len(pj)} casos com as duas datas.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🗳️ Votos vencidos", int((ev["evento"] == "voto_vencido").sum()))
    c2.metric("👁️ Pedidos de vista", int((ev["evento"] == "pediu_vista").sum()))
    c3.metric("🚫 Retiradas de pauta",
              int((ev["evento"] == "retirou_de_pauta").sum()))
    c4.metric("⛔ Impedimentos", int((ev["evento"] == "impedimento").sum()))
    # ---- mandato e viagens internacionais (cruzamento com o Boletim) ------
    mand, n_vi, dias_vi = _diretor_boletim(d)
    if mand or n_vi:
        st.markdown("**🌍 Mandato e viagens internacionais** "
                    "*(cruzado com o Boletim de Pessoal)*")
        m1, m2, m3, m4 = st.columns(4)
        if mand:
            anos = (dt.date.fromisoformat(mand["end"])
                    - dt.date.fromisoformat(mand["start"])).days / 365.25
            situ = ("em exercício" if mand["ativo"] else f"até {mand['end']}")
            m1.metric("🗓️ Tempo de mandato", f"{anos:.1f} anos",
                      help=f"Início {mand['start']} · {situ} · "
                      f"{mand['dias']} dias úteis")
        else:
            m1.metric("🗓️ Tempo de mandato", "—")
        m2.metric("✈️ Viagens ao exterior", n_vi)
        m3.metric("📅 Dias viajando", dias_vi)
        if mand and mand["dias"] > 0:
            m4.metric("📊 % do mandato viajando",
                      f"{100 * dias_vi / mand['dias']:.1f}%")
        st.caption("Tempo de mandato em **dias úteis** (trabalho, seg–sex; feriados "
                   "não deduzidos); **dias viajando em dias corridos** (afastamentos "
                   "do país); % = dias viajando ÷ dias úteis do mandato. Depende do "
                   "que consta nos boletins.")
    st.divider()
    # ---- quem ele julga (parametrização por réu) + severidade -------------
    cA, cB = st.columns(2)
    with cA:
        st.markdown("**Quem é julgado por ele(a):**")
        jp = julgs.merge(perfil, on="proc_norm", how="left")
        tot = {"Pessoas físicas": jp["n_pf"].fillna(0).sum(),
               "Empresas": jp["n_empresa"].fillna(0).sum(),
               "Instituições financeiras": jp["n_financeira"].fillna(0).sum()}
        st.bar_chart(pd.Series(tot), horizontal=True)
        # multa média por classe dominante do processo
        def _cls(r):
            if (r.get("n_financeira") or 0) > 0:
                return "c/ instituição financeira"
            if (r.get("n_empresa") or 0) > 0:
                return "c/ empresa"
            return "só pessoas físicas"
        if len(jp):
            jp["classe"] = jp.apply(_cls, axis=1)
            med = jp.groupby("classe")["valor"].mean() / 1e3
            st.markdown("**Multa média por tipo de réu (R$ mil):**")
            st.bar_chart(med, horizontal=True)
    with cB:
        st.markdown("**Severidade das decisões (análises IA):**")
        sev = pd.Series([str((ana.get(pn) or {}).get("severidade") or "?")
                         for pn in julgs["proc_norm"]]).value_counts()
        st.bar_chart(sev, horizontal=True)
        st.markdown("**Julgamentos por ano:**")
        anos = julgs["data_iso"].str[:4].value_counts().sort_index()
        st.bar_chart(anos)
    # ---- severidade POR perfil de réu (o cruzamento) ----------------------
    if len(jp):
        jp["severidade"] = jp["proc_norm"].map(
            lambda pn: str((ana.get(pn) or {}).get("severidade") or "?").lower()
            .replace("média", "media"))
        ct = pd.crosstab(jp["classe"], jp["severidade"])
        ordem = [c for c in ["alta", "media", "baixa", "?"] if c in ct.columns]
        ct = ct[ordem + [c for c in ct.columns if c not in ordem]]
        st.markdown("**🎯 Severidade POR perfil de réu** — revela se instituições/"
                    "empresas recebem tratamento diferente das pessoas físicas:")
        c1, c2 = st.columns([3, 2])
        with c1:
            # % por classe (linha soma 100%) para comparar entre perfis
            pct = ct.div(ct.sum(axis=1), axis=0).mul(100).round(0)
            st.dataframe(pct.rename(columns=lambda c: f"{c} %"),
                         use_container_width=True)
        with c2:
            st.bar_chart(ct)
        st.caption(f"Contagem de casos por severidade e tipo de réu "
                   f"(n={int(ct.values.sum())} julgados com perfil e severidade).")
    # ---- estoque: o que deixou de pautar ----------------------------------
    if len(est):
        st.markdown(f"**📥 Estoque sem julgamento ({len(est)}) — o que ainda "
                    "não pautou:**")
        st.dataframe(est.sort_values("dias", ascending=False)[
            ["proc_norm", "recebido_iso", "dias"]].rename(columns={
                "proc_norm": "Processo", "recebido_iso": "Recebido em",
                "dias": "Dias parado"}),
            use_container_width=True, hide_index=True, height=220)
    # ---- correlações com os demais diretores -------------------------------
    con2 = sqlite3.connect(CONDUTA_DB_PATH)
    try:
        ali = pd.read_sql("SELECT * FROM alinhamentos", con2)
    except Exception:
        ali = pd.DataFrame()
    con2.close()
    if len(ali):
        st.divider()
        st.markdown("**🔗 Correlações com os demais diretores** "
                    "(mineradas dos votos das atas):")
        cX, cY = st.columns(2)
        div_de = ali[(ali["diretor_a"] == d) & (ali["tipo"] == "divergiu_de")]
        div_dele = ali[(ali["diretor_b"] == d) & (ali["tipo"] == "divergiu_de")]
        with cX:
            st.markdown(f"*{d} ficou vencido contra:*")
            if len(div_de):
                st.dataframe(div_de.groupby("diretor_b").size()
                             .rename("vezes").reset_index().rename(
                                 columns={"diretor_b": "Prevaleceu"}),
                             use_container_width=True, hide_index=True)
            else:
                st.caption("— nenhum registro —")
        with cY:
            st.markdown(f"*Divergiram de {d} (ele/ela prevaleceu):*")
            if len(div_dele):
                st.dataframe(div_dele.groupby("diretor_a").size()
                             .rename("vezes").reset_index().rename(
                                 columns={"diretor_a": "Vencido"}),
                             use_container_width=True, hide_index=True)
            else:
                st.caption("— nenhum registro —")
        casos = ali[((ali["diretor_a"] == d) | (ali["diretor_b"] == d))].copy()
        casos = casos.reset_index(drop=True)
        if len(casos):
            st.markdown(f"**🔍 Conflitos de decisão de {d} — clique para a ficha "
                        f"didática da divergência ({len(casos)}):**")
            _TIPOL = {"divergiu_de": "divergiu de",
                      "pediu_vista_sobre": "pediu vista s/"}
            casos["Quem × Quem"] = casos.apply(
                lambda r: f"{r['diretor_a']} {_TIPOL.get(r['tipo'], r['tipo'])} "
                f"{r['diretor_b']}", axis=1)
            casos["Resumo"] = (casos["trecho"].fillna("").str.replace(
                r"\s+", " ", regex=True).str.replace("ASSUNTO: ", "", regex=False)
                .str.slice(0, 110))
            disp = casos[["data_iso", "Quem × Quem", "processo", "Resumo"]].rename(
                columns={"data_iso": "Data", "processo": "Processo"})
            ev = st.dataframe(disp, use_container_width=True, hide_index=True,
                              on_select="rerun", selection_mode="single-row",
                              key="div_sel")
            rows = ev.selection.rows if getattr(ev, "selection", None) else []
            if rows:
                idx = rows[0]
                cs = casos.iloc[idx]
                chave = f"{cs['processo']}|{cs['diretor_a']}|{cs['diretor_b']}"
                if chave != st.session_state.get("_div_open"):
                    st.session_state["_div_open"] = chave
                    dialog_divergencia(cs)
    # ---- RELATÓRIO DE INCONSISTÊNCIAS -------------------------------------
    st.divider()
    st.markdown("### ⚠️ Relatório de inconsistências")
    st.caption("Cada hipótese abaixo é **clicável**: abra para ver os processos "
               "antagônicos e, em cada um, a análise completa. São indícios a "
               "verificar — casos podem ter especificidades que os dados não "
               "capturam.")
    df_inc, clusters = _inconsistencias_diretor(d)
    inc = (dossie.get("incoerencias") if dossie else None) or []
    inc = [c for c in (inc if isinstance(inc, list) else [inc])
           if str(c).strip() and str(c).strip() != "-"]
    algo = False
    # A) hipóteses qualitativas do agente -> casos concretos do(s) tema(s)
    if inc:
        algo = True
        st.markdown("**🧠 Hipóteses do agente** — clique para abrir os casos:")
        for i, c in enumerate(inc):
            procs_c = _procs_no_texto(c)
            temas_h = [t for t in _tema_de_texto(c)
                       if len(df_inc) and (df_inc["tema"] == t).any()]
            with st.expander(f"🔎 {c}"):
                mostrou = False
                if procs_c:
                    mostrou = True
                    st.markdown("**📌 Processos citados nesta hipótese** "
                                "(clique para a análise):")
                    _btn_processos(pd.DataFrame({"Processo": procs_c}), f"hipp{i}")
                for t in temas_h:
                    g = df_inc[df_inc["tema"] == t].sort_values(
                        ["Severidade", "Data"])
                    if not len(g):
                        continue
                    mostrou = True
                    st.markdown(f"**{_TEMA_LABEL.get(t, t)}** — {len(g)} "
                                f"julgado(s) deste diretor (clique para a análise):")
                    _btn_processos(g, f"hip{i}_{t}")
                if not mostrou:
                    st.caption("Observação qualitativa — sem processo citado nem "
                               "tema mapeável automaticamente. Use as disparidades "
                               "abaixo e os cruzamentos acima para investigar.")
    # B) disparidades determinísticas: mesma tese, severidades opostas
    if clusters:
        algo = True
        st.markdown("**📊 Disparidades nos dados** — mesma tese julgada por este "
                    "diretor com severidades **opostas**:")
        for tema, g in clusters:
            alta = g[g["Severidade"] == "alta"]
            baixa = g[g["Severidade"] == "baixa"]
            faixa = g[g["Multa (R$)"] > 0]["Multa (R$)"]
            rng = (f" · R$ {faixa.min():,.0f} a R$ {faixa.max():,.0f}"
                   .replace(",", ".")) if len(faixa) else ""
            with st.expander(f"⚠️ {_TEMA_LABEL.get(tema, tema)} — "
                             f"{len(alta)} grave(s) × {len(baixa)} brando(s){rng}"):
                if st.button("📋 Ficha detalhada desta disparidade",
                             key=f"disp_{tema}", use_container_width=True):
                    dialog_disparidade(d, tema, g)
                st.caption("Ou compare os polos antagônicos abaixo — clique em "
                           "qualquer processo para a análise individual:")
                cG, cB = st.columns(2)
                with cG:
                    st.markdown("⬆️ **Tratou como mais GRAVE** (severidade alta)")
                    _btn_processos(alta, f"cl_{tema}_alta")
                with cB:
                    st.markdown("⬇️ **Tratou como mais BRANDO** (severidade baixa)")
                    _btn_processos(baixa, f"cl_{tema}_baixa")
    if not algo:
        if len(df_inc):
            st.success("Nenhuma divergência de severidade dentro de um mesmo tema "
                       "detectada (na cobertura atual 2022–2026).")
        else:
            st.info("Ainda não há julgados com análise IA para este diretor.")
    st.caption("A varredura da jurisprudência completa (1999–2025) aprofundará "
               "este relatório: mesma tese com dosimetria díspar ao longo dos anos "
               "e tratamento diferente entre classes de réu no mesmo caso.")


def render_colegiado():
    st.subheader("🏛️ Colegiado — decisões, votos e pessoas")
    visao = st.segmented_control(
        "Seção", ["📜 Decisões, Atas e Votos", "🎯 Ficha do diretor",
                  "🧠 Agentes de tese", "🏢 Agentes de área",
                  "🗂️ Unidades e CTC", "📰 Informativos",
                  "👥 Quem é Quem", "📋 Atas do CGE"],
        key="col_nav", default="📜 Decisões, Atas e Votos",
        label_visibility="collapsed")
    st.divider()
    if visao == "📜 Decisões, Atas e Votos":
        render_decisoes()
    elif visao == "🎯 Ficha do diretor":
        render_ficha_diretor()
    elif visao == "🧠 Agentes de tese":
        render_agentes_tese()
    elif visao == "🏢 Agentes de área":
        render_agentes_area()
    elif visao == "🗂️ Unidades e CTC":
        render_agentes_unidade()
    elif visao == "📰 Informativos":
        render_informativos()
    elif visao == "👥 Quem é Quem":
        render_quem()
    elif visao == "📋 Atas do CGE":
        render_atas()


def render_bastidores():
    st.subheader("🕵️ Bastidores — quem circula na CVM")
    visao = st.segmented_control(
        "Seção", ["🏛️ Audiências particulares", "🏢 Servidores e viagens"],
        key="bas_nav", default="🏛️ Audiências particulares",
        label_visibility="collapsed")
    st.divider()
    if visao == "🏛️ Audiências particulares":
        render_audiencias()
    else:
        render_servidores()


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------
# Navegação por CASO DE USO (não por fonte). O acesso às fontes brutas segue
# disponível: Publicações no acervo do Radar; tudo o mais dentro das 5 abas.
SECOES = ["🏠 Radar", "🔎 Buscar", "⚖️ Enforcement", "🏛️ Colegiado",
          "🕵️ Bastidores"]


def main():
    if not autenticado():
        return
    _abrir_ata_por_query()   # ?ata=<link> abre o popup da ata dentro do app
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
        render_radar()
    elif sel == SECOES[1]:
        render_busca()
    elif sel == SECOES[2]:
        render_enforcement()
    elif sel == SECOES[3]:
        render_colegiado()
    elif sel == SECOES[4]:
        render_bastidores()


if __name__ == "__main__":
    main()
