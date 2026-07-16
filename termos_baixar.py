#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
termos_baixar.py — coleta a base consolidada de Termos de Compromisso da CVM,
juntando os ACEITOS e os REJEITADOS, em termos.db (tabela `termos`).

Fontes:
  - Aceitos:    https://conteudo.cvm.gov.br/termos_compromisso/index.html
                (tabela paginada por AJAX: POST com searchPage/itensPagina)
  - Rejeitados: https://conteudo.cvm.gov.br/termos_compromisso_rejeitados/index.html
                (tabela HTML estatica, uma pagina)

Cada linha: numero do processo, situacao (Aceito/Rejeitado), datas, partes
(compromitentes/proponentes) e o link da Decisao/Parecer do Colegiado.

O full refresh so' e' aplicado se o snapshot novo passar no _conferir (fonte nao
vazia e sem encolhimento alem de ENCOLHIMENTO_MAX). Snapshot suspeito levanta
erro e preserva o banco anterior — a coleta e' idempotente, a proxima rodada
tenta de novo.

Uso:
  python termos_baixar.py            # (re)coleta e reconstroi termos.db (full refresh)
  python termos_baixar.py docs [N]   # baixa ate N documentos linkados que faltam
  TERMOS_FORCAR=1 python termos_baixar.py   # aplica mesmo se encolheu (queda real)
"""
import os
import re
import sys
import html
import time
import sqlite3
import datetime as dt

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "termos.db")
DOCS_DIR = os.path.join(DIR, "termos_docs")
BASE = "https://conteudo.cvm.gov.br"
URL_ACEITOS = BASE + "/termos_compromisso/index.html"
URL_REJEIT = BASE + "/termos_compromisso_rejeitados/index.html"
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def norm_proc(p):
    if not p:
        return ""
    m = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}|"
                  r"SP\s?\d{4}/\d{3,6}|\d{1,4}\.?\d{0,4}/\d{4}", str(p))
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def limpar_cel(celula):
    txt = re.sub(r"<[^>]+>", " ", celula)
    txt = html.unescape(txt)  # &ndash; &amp; &nbsp; ...
    txt = txt.replace("\xa0", " ")
    return re.sub(r"\s+", " ", txt).strip()


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS termos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        processo TEXT, proc_norm TEXT, situacao TEXT,
        data_decisao TEXT, data_decisao_iso TEXT,
        data_assinatura TEXT, data_publicacao TEXT, data_arquivamento TEXT,
        partes TEXT, link TEXT, coletado_em TEXT)""")
    con.commit()
    return con


def _iso(d):
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", d or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def linhas_de(html):
    """Extrai as <tr> de dados (com celulas) de um HTML de tabela."""
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if not tds:
            continue
        link = ""
        ml = re.search(r'href=["\']([^"\']+)["\']', tr)
        if ml:
            link = ml.group(1)
            if link.startswith("/"):
                link = BASE + link
        cels = [limpar_cel(td) for td in tds]
        out.append((cels, link))
    return out


INSERT_SQL = """INSERT INTO termos(processo,proc_norm,situacao,
    data_decisao,data_decisao_iso,data_assinatura,data_publicacao,
    data_arquivamento,partes,link,coletado_em)
    VALUES(?,?,?,?,?,?,?,?,?,?,?)"""

TENTATIVAS = 3          # a CVM devolve 200 com pagina de erro; insista antes de desistir
ENCOLHIMENTO_MAX = 0.10  # snapshot novo nao pode ser >10% menor que o anterior


def _linhas_pagina_aceitos(page):
    """Uma pagina dos aceitos, com retry. [] = fim da lista OU falha persistente
    (quem decide e' o _conferir, comparando o total com o snapshot anterior)."""
    data = {"searchPage": str(page), "itensPagina": "10",
            "ordenar": "recentes", "buscado": "false", "lastName": "",
            "filtro": "", "dataInicio": "", "dataFim": "", "tipos": ""}
    for tent in range(1, TENTATIVAS + 1):
        try:
            r = requests.post(URL_ACEITOS, headers=H, data=data, timeout=60)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            uteis = [(cels, link) for cels, link in linhas_de(r.text)
                     if len(cels) >= 7 and re.search(r"\d{4}", cels[0] or "")
                     and "Número do processo" not in cels[0]]
            if uteis:
                return uteis
        except Exception as e:
            print(f"  ! aceitos p.{page} (tentativa {tent}/{TENTATIVAS}): {e}")
        if tent < TENTATIVAS:
            time.sleep(2 * tent)
    return []


def coletar_aceitos(hoje):
    """Devolve as linhas em memoria — nao escreve no banco."""
    # itensPagina=10 e' o unico valor com paginacao confiavel (50 quebra na pag. 18)
    out = []
    for page in range(1, 400):
        uteis = _linhas_pagina_aceitos(page)
        if not uteis:
            break
        for cels, link in uteis:
            proc = cels[0]
            out.append((proc, norm_proc(proc), "Aceito", cels[1], _iso(cels[1]),
                        cels[2], cels[3], cels[6], cels[4], link, hoje))
        time.sleep(0.3)
    return out


def coletar_rejeitados(hoje):
    """Devolve as linhas em memoria — nao escreve no banco."""
    texto = ""
    for tent in range(1, TENTATIVAS + 1):
        try:
            r = requests.get(URL_REJEIT, headers=H, timeout=120)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            texto = r.text
            break
        except Exception as e:
            print(f"  ! rejeitados (tentativa {tent}/{TENTATIVAS}): {e}")
            if tent < TENTATIVAS:
                time.sleep(2 * tent)
    out = []
    for cels, link in linhas_de(texto):
        if len(cels) < 3 or not re.search(r"\d{4}", cels[0] or ""):
            continue
        if "Número do processo" in cels[0]:
            continue
        proc = cels[0]
        out.append((proc, norm_proc(proc), "Rejeitado", cels[1], _iso(cels[1]),
                    "", "", "", cels[2], link, hoje))
    return out


def _conferir(novos, antes, rotulo):
    """Barra snapshot mutilado. Sem isto, uma pagina de erro da CVM zera a fonte
    e o banco truncado vai para o git por cima do bom (ja aconteceu: em
    14/07/2026 os 804 aceitos sumiram e sobraram so os 215 rejeitados, e o app
    passou a dizer 'TC rejeitado' em processo cujo TC foi aceito)."""
    if not novos:
        raise RuntimeError(
            f"[termos] {rotulo}: 0 linhas coletadas — a fonte falhou. "
            f"Snapshot anterior preservado.")
    if antes and len(novos) < antes * (1 - ENCOLHIMENTO_MAX):
        raise RuntimeError(
            f"[termos] {rotulo}: encolheu de {antes} para {len(novos)} "
            f"(tolerancia {ENCOLHIMENTO_MAX:.0%}) — recusado, snapshot anterior "
            f"preservado. Se a queda for real, rode com TERMOS_FORCAR=1.")


def construir():
    con = conectar()
    hoje = dt.date.today().isoformat()
    antes_a = con.execute(
        "SELECT COUNT(*) FROM termos WHERE situacao='Aceito'").fetchone()[0]
    antes_r = con.execute(
        "SELECT COUNT(*) FROM termos WHERE situacao='Rejeitado'").fetchone()[0]

    aceitos = coletar_aceitos(hoje)
    rejeitados = coletar_rejeitados(hoje)

    if os.environ.get("TERMOS_FORCAR") != "1":
        _conferir(aceitos, antes_a, "aceitos")
        _conferir(rejeitados, antes_r, "rejeitados")

    with con:  # troca atomica: ou o snapshot inteiro entra, ou o banco nao muda
        con.execute("DELETE FROM termos")
        con.executemany(INSERT_SQL, aceitos + rejeitados)

    tot = con.execute("SELECT COUNT(*) FROM termos").fetchone()[0]
    comproc = con.execute("SELECT COUNT(*) FROM termos WHERE proc_norm!=''").fetchone()[0]
    con.close()
    print(f"[termos] aceitos: {len(aceitos)} | rejeitados: {len(rejeitados)} | "
          f"total {tot} | com nº de processo normalizado: {comproc}")


def baixar_docs(limite=99999):
    """Baixa os documentos linkados (decisao HTML / PDF do termo) que faltam."""
    os.makedirs(DOCS_DIR, exist_ok=True)
    con = conectar()
    linked = con.execute("SELECT DISTINCT link FROM termos WHERE link!=''").fetchall()
    con.close()
    n = 0
    for (link,) in linked:
        if n >= limite:
            break
        nome = re.sub(r"[^A-Za-z0-9._-]", "_", link.split("/")[-1]) or "doc.html"
        if "." not in nome:
            nome += ".html"
        dest = os.path.join(DOCS_DIR, nome)
        if os.path.exists(dest) and os.path.getsize(dest) > 200:
            continue
        try:
            r = requests.get(link, headers=H, timeout=60)
            if r.status_code == 200 and len(r.content) > 200:
                with open(dest, "wb") as f:
                    f.write(r.content)
                n += 1
                time.sleep(0.2)
        except Exception as e:
            print(f"  ! {link}: {e}")
    print(f"[termos] documentos novos baixados: {n}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "docs":
        baixar_docs(int(sys.argv[2]) if len(sys.argv) > 2 else 99999)
    else:
        construir()
