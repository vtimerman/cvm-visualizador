#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scraper_pas.py — Processos Sancionadores da CVM (+ acusados) para SQLite.

Páginas (sequenciais por idProc, ESPARSAS — muitos ids vazios):
  Processo: .../inqueritos/DetPasAndamentoSSI.asp?idProc=N
  Acusado : .../inqueritos/SitIncdoSSI.asp?idProc=N&idIndcdo=M   (histórico dele)

Comandos:
  python scraper_pas.py backfill [ini] [fim]
  python scraper_pas.py atualizar
  python scraper_pas.py um <idProc>       # imprime o que extraiu (debug)
  python scraper_pas.py stats
"""
import sys
import os
import re
import time
import html
import sqlite3
import datetime as dt

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
BASE = "https://sistemas.cvm.gov.br/asp/cvmwww/inqueritos/"
URL_PROC = BASE + "DetPasAndamentoSSI.asp?idProc="
URL_ACUS = BASE + "SitIncdoSSI.asp?idProc={p}&idIndcdo={a}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
PAUSA = float(os.environ.get("PAUSA", "0.2"))
WORKERS = int(os.environ.get("WORKERS", "1"))
DB_PATH = os.environ.get("PAS_DB_PATH", os.path.join(DIR, "processos.db"))
LIMITE = int(os.environ.get("PAS_LIMITE", "13000"))
# Disjuntor de rede: apos N erros de rede seguidos, aborta a rodada (CVM
# indisponivel do runner). Sem isto, cmd_atualizar girava id a id ate o limite
# de 6h do GitHub. Ids em erro nao sao gravados: a proxima rodada os reprocessa
# a partir de max_valido+1. Mesmo padrao de scraper.py.
MAX_ERROS_SEGUIDOS = int(os.environ.get("MAX_ERROS_SEGUIDOS", "10"))

COLS_P = ["idproc", "estado", "numero", "objeto", "ementa", "data_abertura",
          "data_iso", "encarregado", "fase", "subfase", "data_fase",
          "local_atual", "data_local", "acusados", "n_acusados", "coletado_em"]
COLS_A = ["idproc", "idindcdo", "numero", "nome", "situacao", "data",
          "historico", "coletado_em"]


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS processos(
        idproc INTEGER PRIMARY KEY, estado TEXT, numero TEXT, objeto TEXT,
        ementa TEXT, data_abertura TEXT, data_iso TEXT, encarregado TEXT,
        fase TEXT, subfase TEXT, data_fase TEXT, local_atual TEXT, data_local TEXT,
        acusados TEXT, n_acusados INTEGER, coletado_em TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS acusados(
        idproc INTEGER, idindcdo INTEGER, numero TEXT, nome TEXT, situacao TEXT,
        data TEXT, historico TEXT, coletado_em TEXT,
        PRIMARY KEY(idproc, idindcdo))""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_ac_proc ON acusados(idproc)")
    con.commit()
    return con


def _upsert(con, tabela, cols, reg):
    campos = ",".join(cols)
    marc = ",".join("?" for _ in cols)
    chave = "idproc" if tabela == "processos" else "idproc,idindcdo"
    upd = ",".join(f"{c}=excluded.{c}" for c in cols if c not in chave.split(","))
    con.execute(f"INSERT INTO {tabela}({campos}) VALUES({marc}) "
                f"ON CONFLICT({chave}) DO UPDATE SET {upd}",
                [reg.get(c, "") for c in cols])


def ids_existentes(con):
    return {r[0] for r in con.execute("SELECT idproc FROM processos")}


def max_valido(con):
    return con.execute("SELECT MAX(idproc) FROM processos WHERE estado='valido'").fetchone()[0]


# ---------------------------------------------------------------------------
# Rede + parsing
# ---------------------------------------------------------------------------
def baixar(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.encoding = "windows-1252"
    return r.text


def eh_valido(texto):
    up = texto.upper()
    return len(texto) > 2000 and "INFORMA" in up and "DO PROCESSO" in up


def _celulas(texto):
    t = re.sub(r"(?is)<script.*?</script>", " ", texto)
    out = []
    for c in re.findall(r"(?is)<td[^>]*>(.*?)</td>", t):
        c = re.sub(r"(?is)<[^>]*>", " ", c)
        c = html.unescape(c)
        c = re.sub(r"\s+", " ", c).strip()
        out.append(c)
    return out


def _iso(d):
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", (d or "").strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


def _prox(cels, i, k=1):
    return cels[i + k] if 0 <= i + k < len(cels) else ""


def registro_vazio(idproc):
    reg = {c: "" for c in COLS_P}
    reg["idproc"] = idproc
    reg["estado"] = "vazio"
    reg["n_acusados"] = 0
    reg["coletado_em"] = dt.date.today().isoformat()
    return reg


def parse_processo(idproc, texto):
    cels = _celulas(texto)

    def idx_eq(label):
        for i, c in enumerate(cels):
            if c.strip().rstrip(":").upper() == label.upper():
                return i
        return -1

    def idx_has(*terms):
        for i, c in enumerate(cels):
            u = c.upper()
            if all(t.upper() in u for t in terms):
                return i
        return -1

    reg = {c: "" for c in COLS_P}
    reg["idproc"] = idproc
    reg["estado"] = "valido"

    m = re.search(r"([0-9]{5}\.[0-9]{6}/[0-9]{4}-[0-9]{2})", texto)
    reg["numero"] = m.group(1) if m else ""

    i = idx_eq("Objeto")
    if i >= 0:
        reg["objeto"] = _prox(cels, i)
    i = idx_eq("Ementa")
    if i >= 0:
        v = _prox(cels, i)
        reg["ementa"] = "" if v.lower().startswith("data de abertura") else v
    i = idx_eq("Data de abertura")
    if i >= 0:
        reg["data_abertura"] = _prox(cels, i)
    i = idx_has("Encarregado da Instru")
    if i >= 0:
        reg["encarregado"] = _prox(cels, i)

    i = idx_has("MUDAN", "FASE")
    if i >= 0:
        reg["fase"] = _prox(cels, i, 1)
        reg["subfase"] = _prox(cels, i, 2)
        reg["data_fase"] = _prox(cels, i, 3)
    i = idx_has("MOVIMENTA", "DE LOCAL")
    if i >= 0:
        reg["local_atual"] = _prox(cels, i, 1)
        reg["data_local"] = _prox(cels, i, 2)

    reg["data_iso"] = _iso(reg["data_abertura"])
    reg["coletado_em"] = dt.date.today().isoformat()

    # acusados (na ordem em que aparecem os links idIndcdo)
    ids_ind = re.findall(r"idIndcdo(?:%3D|=)(\d+)", texto)
    acus = []
    ia = idx_has("ACUSADOS NO PROCESSO")
    if ia >= 0:
        j = ia + 1
        while j < len(cels) and cels[j].strip().upper() != "DATA":
            j += 1
        j += 1
        k = 0
        while j + 2 < len(cels):
            nome = cels[j]
            if (not nome) or nome.lower().startswith("total de acusado") \
                    or nome.upper().startswith("OBS"):
                break
            acus.append({
                "idproc": idproc, "numero": reg["numero"], "nome": nome,
                "situacao": cels[j + 1], "data": cels[j + 2],
                "idindcdo": int(ids_ind[k]) if k < len(ids_ind) else 0,
                "historico": "", "coletado_em": reg["coletado_em"],
            })
            k += 1
            j += 3

    reg["acusados"] = " | ".join(a["nome"] for a in acus)
    reg["n_acusados"] = len(acus)
    return reg, acus


def parse_acusado_hist(texto):
    cels = _celulas(texto)
    j = -1
    for i, c in enumerate(cels):
        if "DATA DA SITUA" in c.upper():
            j = i + 1
            break
    hist = []
    if j >= 0:
        while j + 1 < len(cels):
            s, d = cels[j], cels[j + 1]
            if not s:
                break
            hist.append(f"{s} ({d})")
            j += 2
    return hist


# Disjuntor do revarrer: uma vez aberto, as chamadas restantes voltam na hora
# sem tocar a rede. Necessario porque o ThreadPoolExecutor.map ja submeteu TODOS
# os ids: sair do laco nao cancela o resto (o with esperaria ~13k timeouts).
_circuito_aberto = False


def baixar_parse_full(idproc):
    """Baixa o processo e (se válido) o histórico de cada acusado. Sem tocar no banco."""
    if _circuito_aberto:                       # CVM caiu: nem tenta
        return None
    try:
        texto = baixar(URL_PROC + str(idproc))
    except Exception as e:
        print(f"  ! erro rede proc {idproc}: {e}", file=sys.stderr)
        return None
    if PAUSA:
        time.sleep(PAUSA)
    if not eh_valido(texto):
        return registro_vazio(idproc), []
    proc, acus = parse_processo(idproc, texto)
    for a in acus:
        try:
            th = baixar(URL_ACUS.format(p=idproc, a=a["idindcdo"]))
            if PAUSA:
                time.sleep(PAUSA)
            a["historico"] = " ; ".join(parse_acusado_hist(th))
        except Exception as e:
            print(f"  ! erro acusado {idproc}/{a['idindcdo']}: {e}", file=sys.stderr)
    return proc, acus


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def _gravar(con, proc, acus):
    _upsert(con, "processos", COLS_P, proc)
    for a in acus:
        _upsert(con, "acusados", COLS_A, a)


def cmd_backfill(ini=1, fim=None):
    fim = fim or LIMITE
    con = conectar()
    ja = ids_existentes(con)
    pend = [i for i in range(ini, fim + 1) if i not in ja]
    print(f"[pas backfill] {ini}..{fim}: {len(pend)} a coletar; workers={WORKERS}")
    feitos = 0
    if WORKERS > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            for res in ex.map(baixar_parse_full, pend):
                if res is None:
                    continue
                _gravar(con, res[0], res[1])
                feitos += 1
                if feitos % 200 == 0:
                    con.commit()
                    print(f"  ... {feitos}/{len(pend)}")
    else:
        for idp in pend:
            res = baixar_parse_full(idp)
            if res:
                _gravar(con, res[0], res[1])
                feitos += 1
            if feitos and feitos % 100 == 0:
                con.commit()
                print(f"  ... {feitos}")
    con.commit()
    con.close()
    print(f"[pas backfill] concluido ({feitos}).")


def cmd_atualizar():
    con = conectar()
    m = max_valido(con)
    if not m:
        print("[pas atualizar] base vazia; rode backfill.")
        return
    idp = m + 1
    vazios = 0
    erros = 0
    novos = 0
    print(f"[pas atualizar] a partir de {idp} (topo={m})")
    while vazios < 300:                    # esparso: tolerância alta de vazios
        res = baixar_parse_full(idp)
        if res is None:                    # erro de rede: nao grava, nao mexe em vazios
            erros += 1
            if erros >= MAX_ERROS_SEGUIDOS:
                print(f"[pas atualizar] {erros} erros de rede seguidos — CVM "
                      f"indisponivel, abortando rodada (retoma na proxima).",
                      file=sys.stderr)
                break
        else:
            erros = 0
            _gravar(con, res[0], res[1])
            if res[0]["estado"] == "valido":
                novos += 1
                vazios = 0
                print(f"  novo processo idProc={idp} ({res[0]['numero']})")
            else:
                vazios += 1
        con.commit()
        idp += 1
    con.close()
    print(f"[pas atualizar] concluido ({novos} novo(s)).")


def cmd_revarrer(ini=1, fim=None):
    """Re-varre os ids VAZIOS (e lacunas) de uma faixa, IGNORANDO o cache, para
    capturar processos que passaram a existir em ids antes vazios (a CVM pode
    'preencher' ids baixos) e estender o teto. Nao re-baixa os ja validos."""
    fim = fim or (LIMITE + 500)
    con = conectar()
    validos = {r[0] for r in con.execute(
        "SELECT idproc FROM processos WHERE estado='valido'")}
    alvo = [i for i in range(ini, fim + 1) if i not in validos]
    print(f"[pas revarrer] {ini}..{fim}: {len(alvo)} ids vazios/novos; workers={WORKERS}")
    global _circuito_aberto
    _circuito_aberto = False
    novos = feitos = erros = 0
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(WORKERS, 1)) as ex:
        for res in ex.map(baixar_parse_full, alvo):
            if res is None:                    # erro de rede (ou circuito aberto)
                erros += 1
                if erros >= MAX_ERROS_SEGUIDOS and not _circuito_aberto:
                    _circuito_aberto = True
                    print(f"[pas revarrer] {erros} erros de rede seguidos — CVM "
                          f"indisponivel, abortando (retoma na proxima).",
                          file=sys.stderr)
                continue
            erros = 0
            _gravar(con, res[0], res[1])
            feitos += 1
            if res[0]["estado"] == "valido":
                novos += 1
                print(f"  novo/atualizado idProc={res[0]['idproc']} ({res[0]['numero']})")
            if feitos % 300 == 0:
                con.commit()
                print(f"  ... {feitos}/{len(alvo)}")
    con.commit()
    con.close()
    print(f"[pas revarrer] concluido: {novos} processo(s) novo(s) em ids antes vazios.")


def cmd_um(idp):
    res = baixar_parse_full(idp)
    if not res:
        print("erro de rede")
        return
    proc, acus = res
    for k in COLS_P:
        print(f"  {k:14}: {proc[k]}")
    print(f"  --- {len(acus)} acusado(s) ---")
    for a in acus:
        print(f"   [{a['idindcdo']}] {a['nome']} | {a['situacao']} | {a['data']}")
        if a["historico"]:
            print(f"        hist: {a['historico']}")


def cmd_stats():
    con = conectar()
    tot = con.execute("SELECT COUNT(*) FROM processos").fetchone()[0]
    val = con.execute("SELECT COUNT(*) FROM processos WHERE estado='valido'").fetchone()[0]
    ac = con.execute("SELECT COUNT(*) FROM acusados").fetchone()[0]
    faixa = con.execute("SELECT MIN(data_iso), MAX(data_iso) FROM processos "
                        "WHERE data_iso!=''").fetchone()
    print(f"processos (ids): {tot}")
    print(f"validos........: {val}")
    print(f"acusados.......: {ac}")
    print(f"topo (idProc)..: {max_valido(con)}")
    print(f"abertura.......: {faixa[0]} a {faixa[1]}")
    con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "backfill":
        ini = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        fim = int(sys.argv[3]) if len(sys.argv) > 3 else None
        cmd_backfill(ini, fim)
    elif cmd == "atualizar":
        cmd_atualizar()
    elif cmd == "revarrer":
        ini = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        fim = int(sys.argv[3]) if len(sys.argv) > 3 else None
        cmd_revarrer(ini, fim)
    elif cmd == "um":
        cmd_um(int(sys.argv[2]))
    elif cmd == "stats":
        cmd_stats()
    else:
        print(__doc__)
        sys.exit(1)
