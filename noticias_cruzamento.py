#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
noticias_cruzamento.py — cruza as Notícias da CVM com os processos, por número E por nome.

Lê noticias.db (tabela `noticias`: url, categoria, titulo, data, data_iso, resumo,
tags, corpo). O `corpo` (texto completo da notícia, baixado só para os últimos ~2 anos)
é onde os números de processo (19957.NNNNNN/AAAA-DD, "RJ AAAA/NNNN", "SP AAAA/NNNN") e
os nomes dos envolvidos de fato aparecem. Duas formas de ligar uma notícia a um processo:

  - por NÚMERO: o corpo/título cita o nº do processo (match tolerante ao dígito verificador);
  - por NOME: o corpo/título menciona a razão social/nome de uma parte do processo
    (ex.: acusados de um sancionador, proponentes de um TC). Isso pega notícias que
    tratam do caso sem citar o número — ex.: grupo de trabalho Master/REAG.

Só stdlib. Consulta sob demanda; degrada para vazio se noticias.db não existir.

Uso:
  python noticias_cruzamento.py     # imprime estatísticas de cobertura
"""
import os
import re
import sqlite3
import unicodedata

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "noticias.db")

# Mesmo padrão usado pelos demais scrapers (decisoes_baixar.norm_proc).
RE_PROC = re.compile(r"1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}|SP\s?\d{4}/\d{3,6}")
# nomes muito curtos/genéricos geram falso-positivo no match por nome
MIN_NOME = 8


def norm_proc(texto):
    """Primeiro nº de processo no texto, normalizado (sem espaços, upper). '' se não achar."""
    m = RE_PROC.search(str(texto or ""))
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def extrair_procs(texto):
    """Lista de proc_norm distintos citados no texto, preservando a ordem de aparição."""
    vistos = []
    for m in RE_PROC.findall(str(texto or "")):
        pn = re.sub(r"\s+", "", m).upper()
        if pn and pn not in vistos:
            vistos.append(pn)
    return vistos


def _chave(pn):
    """Chave de comparação de processo: sem espaços/caixa e sem o dígito verificador."""
    return re.sub(r"-\d{1,2}$", "", re.sub(r"\s+", "", str(pn or "")).upper())


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFKD", str(s or ""))
                   if not unicodedata.combining(c))


def _norm(s):
    """minúsculas, sem acento, espaços colapsados — para o match por nome."""
    return re.sub(r"\s+", " ", _sem_acento(s).lower()).strip()


def _dict_noticia(url, categoria, titulo, data, data_iso, resumo):
    return {
        "url": url or "", "link": url or "",   # a coluna `url` é o link da notícia
        "categoria": categoria or "", "titulo": titulo or "",
        "data": data or "", "data_iso": data_iso or "", "resumo": resumo or "",
    }


def _conectar():
    if not os.path.exists(DB_PATH):
        return None
    return sqlite3.connect(DB_PATH)


def carregar_noticias():
    """Todas as notícias com corpo, já com o texto normalizado e os processos citados.

    Devolve lista de (noticia_dict, texto_norm, set(chaves_de_processo)). Pesada; o app
    deve embrulhar com @st.cache_data. [] se noticias.db não existir.
    """
    con = _conectar()
    if con is None:
        return []
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(noticias)")}
        if "corpo" in cols:
            # só as notícias com corpo (= janela de ~2 anos); ignora o ruído antigo
            rows = con.execute(
                "SELECT url,categoria,titulo,data,data_iso,resumo,tags,corpo "
                "FROM noticias WHERE corpo IS NOT NULL AND corpo != ''").fetchall()
        else:
            rows = con.execute(
                "SELECT url,categoria,titulo,data,data_iso,resumo,tags,resumo "
                "FROM noticias").fetchall()
    finally:
        con.close()
    base = []
    for url, cat, tit, data, diso, resumo, tags, corpo in rows:
        texto = " ".join(x or "" for x in (tit, resumo, tags, corpo))
        chaves = {_chave(p) for p in extrair_procs(texto)}
        base.append((_dict_noticia(url, cat, tit, data, diso, resumo),
                     _norm(texto), chaves))
    return base


def noticias_relacionadas(pn=None, nomes=None, base=None, limite=40):
    """Notícias ligadas a um processo, por número (pn) e/ou por nome (lista `nomes`).

    Cada notícia retornada ganha um campo `motivo` ("nº do processo" e/ou "nome").
    `base` pode ser o resultado de carregar_noticias() (para reaproveitar cache); se
    None, carrega na hora. Retorna [] se não houver base. Ordena por data desc.
    """
    if base is None:
        base = carregar_noticias()
    if not base:
        return []
    chave_alvo = _chave(pn) if pn else ""
    alvos_nome = [_norm(n) for n in (nomes or []) if len(_norm(n)) >= MIN_NOME]
    achadas = {}
    for noticia, texto_norm, chaves in base:
        motivos = []
        if chave_alvo and chave_alvo in chaves:
            motivos.append("nº do processo")
        if alvos_nome and any(nm in texto_norm for nm in alvos_nome):
            motivos.append("nome")
        if motivos:
            item = dict(noticia, motivo=" + ".join(motivos))
            achadas[noticia["url"]] = item
    out = sorted(achadas.values(), key=lambda n: n["data_iso"], reverse=True)
    return out[:limite]


def noticias_do_processo(pn, base=None):
    """Compat.: notícias que citam o processo `pn` pelo número. Ver noticias_relacionadas."""
    return noticias_relacionadas(pn=pn, base=base)


def _estatisticas():
    base = carregar_noticias()
    if not base:
        print(f"[cruzamento] noticias.db não encontrado ou vazio em {DB_PATH}")
        return
    com_proc = sum(1 for _, _, chaves in base if chaves)
    contagem = {}
    for _, _, chaves in base:
        for k in chaves:
            contagem[k] = contagem.get(k, 0) + 1
    top = sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    print(f"[cruzamento] notícias com corpo: {len(base)}")
    print(f"[cruzamento] notícias que citam processo: {com_proc}")
    print(f"[cruzamento] processos distintos citados: {len(contagem)}")
    print("[cruzamento] 10 processos mais citados:")
    for pn, n in top:
        print(f"    {pn}  ({n})")


if __name__ == "__main__":
    _estatisticas()
