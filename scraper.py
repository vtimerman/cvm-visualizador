#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper.py — coleta as Audiencias Particulares da CVM para um banco SQLite.

O endpoint e sequencial por id:
    https://sistemas.cvm.gov.br/aplicacoes/cap/consulta/audiencia.asp?id=N

Pagina VALIDA contem o texto "PARTICULAR N"; pagina de ~874 bytes sem esse
texto = id pulado/inexistente ("buraco").

Comandos:
    python scraper.py seed_tsv <arquivo.tsv>   # importa a base local ja coletada
    python scraper.py backfill [ini] [fim]     # coleta historico (padrao 1..33000)
    python scraper.py atualizar                # pega ids novos acima do topo atual
    python scraper.py stats                    # resumo da base
"""
import sys
import os
import re
import time
import html
import sqlite3
import datetime as dt

import requests

BASE = "https://sistemas.cvm.gov.br/aplicacoes/cap/consulta/audiencia.asp?id="
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PAUSA = float(os.environ.get("PAUSA", "0.5"))
LIMITE_SUPERIOR = int(os.environ.get("LIMITE_SUPERIOR", "33000"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "audiencias.db"))

COLS = [
    "id", "estado", "data", "data_iso", "hora", "componente", "local",
    "assunto", "urgente", "status", "solicitante_nome", "solicitante_empresa",
    "solicitante_cargo", "acompanhantes", "observacoes", "coletado_em",
]

# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audiencias (
            id INTEGER PRIMARY KEY,
            estado TEXT,
            data TEXT,
            data_iso TEXT,
            hora TEXT,
            componente TEXT,
            local TEXT,
            assunto TEXT,
            urgente TEXT,
            status TEXT,
            solicitante_nome TEXT,
            solicitante_empresa TEXT,
            solicitante_cargo TEXT,
            acompanhantes TEXT,
            observacoes TEXT,
            coletado_em TEXT
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_data ON audiencias(data_iso)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_estado ON audiencias(estado)")
    con.commit()
    return con


def upsert(con, reg: dict):
    campos = ",".join(COLS)
    marc = ",".join("?" for _ in COLS)
    upd = ",".join(f"{c}=excluded.{c}" for c in COLS if c != "id")
    con.execute(
        f"INSERT INTO audiencias ({campos}) VALUES ({marc}) "
        f"ON CONFLICT(id) DO UPDATE SET {upd}",
        [reg.get(c, "") for c in COLS],
    )


def ids_existentes(con) -> set:
    return {r[0] for r in con.execute("SELECT id FROM audiencias")}


def max_valido(con):
    r = con.execute("SELECT MAX(id) FROM audiencias WHERE estado='valido'").fetchone()
    return r[0]


# ---------------------------------------------------------------------------
# Rede + parsing
# ---------------------------------------------------------------------------
def baixar(id_: int) -> str:
    r = requests.get(BASE + str(id_), headers={"User-Agent": UA}, timeout=30)
    r.encoding = "windows-1252"
    return r.text


def eh_valido(texto: str) -> bool:
    return "PARTICULAR N" in texto.upper()


def _celulas(texto: str):
    """Extrai o conteudo textual de cada <td>...</td>, em ordem."""
    brutos = re.findall(r"<td[^>]*>(.*?)</td>", texto, re.DOTALL | re.IGNORECASE)
    celulas = []
    for c in brutos:
        c = re.sub(r"<[^>]*>", "", c)          # remove tags internas
        c = html.unescape(c)
        c = re.sub(r"\s+", " ", c).strip()      # colapsa espacos
        celulas.append(c)
    return celulas


def _iso(data: str) -> str:
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", data or "")
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


LABELS_TOPO = {
    "COMPONENTE ORGANIZACIONAL": "componente",
    "DATA DE AUDIÊNCIA": "data",
    "HORA DE AUDIÊNCIA": "hora",
    "Local": "local",
    "Assunto": "assunto",
    "Urgente": "urgente",
    "Observações": "observacoes",
    "Status:": "status",
}


def parse(id_: int, texto: str) -> dict:
    cel = _celulas(texto)
    reg = {c: "" for c in COLS}
    reg["id"] = id_
    reg["estado"] = "valido"
    secao = "topo"
    acomp = []
    atual = None  # dict do acompanhante corrente
    i = 0
    while i < len(cel):
        c = cel[i]
        prox = cel[i + 1] if i + 1 < len(cel) else ""
        if c == "SOLICITANTE":
            secao = "sol"
        elif c == "ACOMPANHANTE":
            secao = "ac"
            atual = {"nome": "", "empresa": "", "cargo": ""}
            acomp.append(atual)
        elif secao == "topo" and c in LABELS_TOPO:
            reg[LABELS_TOPO[c]] = prox
        elif secao == "sol":
            if c == "Nome":
                reg["solicitante_nome"] = prox
            elif c == "Empresa":
                reg["solicitante_empresa"] = prox
            elif c == "Cargo":
                reg["solicitante_cargo"] = prox
        elif secao == "ac" and atual is not None:
            if c == "Nome":
                atual["nome"] = prox
            elif c == "Empresa":
                atual["empresa"] = prox
            elif c == "Cargo":
                atual["cargo"] = prox
        i += 1
    reg["acompanhantes"] = " | ".join(a["nome"] for a in acomp if a["nome"])
    reg["data_iso"] = _iso(reg["data"])
    reg["coletado_em"] = dt.date.today().isoformat()
    return reg


def registro_vazio(id_: int) -> dict:
    reg = {c: "" for c in COLS}
    reg["id"] = id_
    reg["estado"] = "vazio"
    reg["coletado_em"] = dt.date.today().isoformat()
    return reg


def coletar_um(con, id_: int) -> str:
    try:
        texto = baixar(id_)
    except Exception as e:                      # rede instavel: nao grava, tenta depois
        print(f"  ! erro de rede no id {id_}: {e}", file=sys.stderr)
        return "erro"
    if eh_valido(texto):
        upsert(con, parse(id_, texto))
        return "valido"
    upsert(con, registro_vazio(id_))
    return "vazio"


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def cmd_backfill(ini=1, fim=None):
    fim = fim or LIMITE_SUPERIOR
    con = conectar()
    ja = ids_existentes(con)
    print(f"[backfill] {ini}..{fim} (pausa {PAUSA}s); ja na base: {len(ja)}")
    feitos = 0
    for id_ in range(ini, fim + 1):
        if id_ in ja:
            continue
        estado = coletar_um(con, id_)
        if estado != "erro":
            feitos += 1
        if feitos % 100 == 0 and feitos:
            con.commit()
            print(f"  ... {feitos} coletados (id atual={id_})")
        time.sleep(PAUSA)
    con.commit()
    con.close()
    print(f"[backfill] concluido ({feitos} ids novos).")


def cmd_atualizar():
    con = conectar()
    m = max_valido(con)
    if not m:
        print("[atualizar] base vazia; rode backfill primeiro.")
        return
    id_ = m + 1
    vazios = 0
    novos = 0
    print(f"[atualizar] a partir de {id_} (topo atual={m})")
    while vazios < 100:
        estado = coletar_um(con, id_)
        if estado == "valido":
            novos += 1
            vazios = 0
            print(f"  novo registro id={id_}")
        elif estado == "vazio":
            vazios += 1
        con.commit()
        id_ += 1
        time.sleep(PAUSA)
    con.close()
    print(f"[atualizar] concluido ({novos} novo(s)).")


def cmd_seed_tsv(caminho):
    con = conectar()
    n = 0
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            p = linha.rstrip("\n").split("\t")
            if len(p) < 15:
                continue
            reg = {
                "id": int(p[0]), "estado": p[1], "data": p[2], "data_iso": _iso(p[2]),
                "hora": p[3], "componente": p[4], "local": p[5], "assunto": p[6],
                "urgente": p[7], "status": p[8], "solicitante_nome": p[9],
                "solicitante_empresa": p[10], "solicitante_cargo": p[11],
                "acompanhantes": p[12], "observacoes": p[13], "coletado_em": p[14],
            }
            upsert(con, reg)
            n += 1
    con.commit()
    con.close()
    print(f"[seed_tsv] {n} linhas importadas de {caminho}")


def cmd_stats():
    con = conectar()
    tot = con.execute("SELECT COUNT(*) FROM audiencias").fetchone()[0]
    val = con.execute("SELECT COUNT(*) FROM audiencias WHERE estado='valido'").fetchone()[0]
    vaz = con.execute("SELECT COUNT(*) FROM audiencias WHERE estado='vazio'").fetchone()[0]
    faixa = con.execute("SELECT MIN(data_iso), MAX(data_iso) FROM audiencias WHERE data_iso!=''").fetchone()
    print(f"total ids...: {tot}")
    print(f"validos.....: {val}")
    print(f"vazios......: {vaz}")
    print(f"topo (id)...: {max_valido(con)}")
    print(f"periodo.....: {faixa[0]} a {faixa[1]}")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "backfill":
        ini = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        fim = int(sys.argv[3]) if len(sys.argv) > 3 else None
        cmd_backfill(ini, fim)
    elif cmd == "atualizar":
        cmd_atualizar()
    elif cmd == "seed_tsv":
        cmd_seed_tsv(sys.argv[2])
    elif cmd == "stats":
        cmd_stats()
    else:
        print(__doc__)
        sys.exit(1)
