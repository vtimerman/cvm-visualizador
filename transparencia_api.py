#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transparencia_api.py -- cliente da API do Portal da Transparencia para trazer
as VIAGENS (com custo: diarias + passagens) e os SERVIDORES da CVM, cruzando com
o Boletim de Pessoal (pessoal.db) para o acompanhamento de viagens dos diretores.

A API exige CHAVE GRATUITA (header 'chave-api-dados'). O USUARIO gera em
http://www.portaldatransparencia.gov.br/api-de-dados/cadastrar-email e guarda
como SEGREDO (nunca no git). A chave e' lida, nesta ordem, de:
  1. env TRANSPARENCIA_API_KEY
  2. transparencia.local.json  ->  {"chave": "..."}   (gitignored)
  3. .streamlit/secrets.toml    ->  [transparencia] chave="..."

Uso:
  python transparencia_api.py orgao [descricao]     # acha o codigo SIAFI da CVM
  python transparencia_api.py viagens <cod> <ano_ini> <ano_fim>
  python transparencia_api.py servidores <cod>
  python transparencia_api.py cruzar                # custo por diretor (x Boletim)
  python transparencia_api.py status
"""
import os
import re
import sys
import json
import time
import sqlite3
import calendar
import unicodedata

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(DIR, "transparencia.db")
PESSOAL = os.path.join(DIR, "pessoal.db")
BASE = "https://api.portaldatransparencia.gov.br/api-de-dados"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MotumboCVM/1.0"
PAUSA = float(os.environ.get("PAUSA", "1.0"))   # respeitar rate limit da API


# --------------------------------------------------------------- chave/segredo
def chave():
    k = os.environ.get("TRANSPARENCIA_API_KEY", "").strip()
    if k:
        return k
    p = os.path.join(DIR, "transparencia.local.json")
    if os.path.exists(p):
        try:
            return (json.load(open(p, encoding="utf-8")).get("chave") or "").strip()
        except Exception:
            pass
    sp = os.path.join(DIR, ".streamlit", "secrets.toml")
    if os.path.exists(sp):
        m = re.search(r'chave\s*=\s*["\']([^"\']+)["\']', open(sp, encoding="utf-8").read())
        if m:
            return m.group(1).strip()
    return ""


def _headers():
    k = chave()
    if not k:
        print("ERRO: chave da API ausente. Gere em\n  http://www.portaldata"
              "ransparencia.gov.br/api-de-dados/cadastrar-email\ne salve em "
              "transparencia.local.json {\"chave\": \"...\"} ou na env "
              "TRANSPARENCIA_API_KEY.", file=sys.stderr)
        sys.exit(2)
    return {"chave-api-dados": k, "Accept": "application/json", "User-Agent": UA}


def _get(path, params, tentativas=4):
    url = f"{BASE}{path}"
    for t in range(tentativas):
        r = requests.get(url, headers=_headers(), params=params, timeout=60)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:          # rate limit -> espera e repete
            time.sleep(2 + 3 * t)
            continue
        if r.status_code == 204:
            return []
        raise RuntimeError(f"HTTP {r.status_code} em {path}: {r.text[:200]}")
    raise RuntimeError(f"esgotou tentativas (rate limit) em {path}")


# --------------------------------------------------------------- normalizacao
def _na(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


# --------------------------------------------------------------- orgao
def orgao(descricao="valores mobiliarios"):
    """Procura o codigo SIAFI do orgao (CVM)."""
    dados = _get("/orgaos-siafi", {"descricao": descricao, "pagina": 1})
    if not dados:
        print("(nada encontrado; tente outra descricao)")
        return
    for o in dados:
        print(f"  codigo={o.get('codigo')}  {o.get('descricao')}")


# --------------------------------------------------------------- storage
def _con():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS viagens_gov(
        pcdp TEXT PRIMARY KEY, beneficiario TEXT, benef_key TEXT, cpf TEXT,
        cargo TEXT, funcao TEXT, orgao TEXT, ug TEXT,
        data_inicio TEXT, data_fim TEXT, destino TEXT, motivo TEXT,
        valor_diarias REAL, valor_passagem REAL, valor_total REAL,
        valor_devolucao REAL, ano INTEGER, coletado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS servidores_gov(
        id TEXT PRIMARY KEY, nome TEXT, nome_key TEXT, cpf TEXT,
        cargo TEXT, funcao TEXT, orgao_lotacao TEXT, situacao TEXT,
        coletado_em TEXT)""")
    return c


def _num(v):
    if v in (None, ""):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(".", "").replace(",", ".")
    try:
        return float(re.sub(r"[^0-9.\-]", "", s) or 0)
    except ValueError:
        return 0.0


def _campo(d, *ks):
    for k in ks:
        cur = d
        ok = True
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return ""


def viagens(cod, ano_ini, ano_fim):
    """Baixa as viagens da CVM (codigoOrgao SIAFI) mes a mes, ano_ini..ano_fim."""
    c = _con()
    total = 0
    for ano in range(int(ano_ini), int(ano_fim) + 1):
        for mes in range(1, 13):
            ult = calendar.monthrange(ano, mes)[1]
            di = f"01/{mes:02d}/{ano}"
            dfim = f"{ult:02d}/{mes:02d}/{ano}"
            pagina = 1
            while True:
                params = {"codigoOrgao": cod, "dataIdaDe": di,
                          "dataIdaAte": dfim, "pagina": pagina}
                try:
                    dados = _get("/viagens", params)
                except RuntimeError as e:
                    print(f"  ! {ano}-{mes:02d} p{pagina}: {e}", file=sys.stderr)
                    break
                if not dados:
                    break
                for v in dados:
                    pcdp = str(_campo(v, "pcdp", "identificadorPcdp", "id") or
                               f"{ano}{mes:02d}-{total}")
                    nome = _campo(v, "nomeBeneficiario", "beneficiario.nome",
                                  "servidor.nome", "nome")
                    c.execute(
                        "INSERT OR REPLACE INTO viagens_gov(pcdp,beneficiario,"
                        "benef_key,cpf,cargo,funcao,orgao,ug,data_inicio,"
                        "data_fim,destino,motivo,valor_diarias,valor_passagem,"
                        "valor_total,valor_devolucao,ano,coletado_em) VALUES("
                        "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                        (pcdp, nome, _na(nome),
                         _campo(v, "cpfFormatado", "beneficiario.cpfFormatado"),
                         _campo(v, "cargo.descricao", "cargo", "descricaoCargo"),
                         _campo(v, "funcao.descricao", "funcao"),
                         _campo(v, "orgao.nome", "orgao", "nomeOrgao"),
                         _campo(v, "unidadeGestoraResponsavel.nome", "ug"),
                         _campo(v, "dataInicioAfastamento", "dataIda"),
                         _campo(v, "dataFimAfastamento", "dataRetorno"),
                         _campo(v, "destinos", "destino"),
                         _campo(v, "motivo", "justificativa"),
                         _num(_campo(v, "valorTotalDiarias", "valorDiarias")),
                         _num(_campo(v, "valorTotalPassagem", "valorPassagens")),
                         _num(_campo(v, "valorTotalViagem", "valorTotal")),
                         _num(_campo(v, "valorTotalDevolucao", "valorDevolucao")),
                         ano))
                    total += 1
                c.commit()
                print(f"  {ano}-{mes:02d} p{pagina}: +{len(dados)} (acum {total})")
                pagina += 1
                time.sleep(PAUSA)
    c.close()
    print(f"[viagens] {total} viagens da CVM gravadas em transparencia.db")


def servidores(cod):
    c = _con()
    pagina, total = 1, 0
    while True:
        try:
            dados = _get("/servidores", {"orgaoServidorLotacao": cod,
                                         "pagina": pagina})
        except RuntimeError as e:
            print("  !", e, file=sys.stderr)
            break
        if not dados:
            break
        for s in dados:
            nome = _campo(s, "servidor.pessoa.nome", "nome")
            sid = str(_campo(s, "id", "servidor.id") or f"{pagina}-{total}")
            c.execute("INSERT OR REPLACE INTO servidores_gov(id,nome,nome_key,"
                      "cpf,cargo,funcao,orgao_lotacao,situacao,coletado_em) "
                      "VALUES(?,?,?,?,?,?,?,?,datetime('now'))",
                      (sid, nome, _na(nome),
                       _campo(s, "servidor.pessoa.cpfFormatado", "cpf"),
                       _campo(s, "cargo.descricao", "cargo"),
                       _campo(s, "funcao.descricao", "funcao"),
                       _campo(s, "orgaoLotacao.nome", "orgaoServidorLotacao"),
                       _campo(s, "situacao", "situacaoFuncional")))
            total += 1
        c.commit()
        print(f"  servidores p{pagina}: +{len(dados)} (acum {total})")
        pagina += 1
        time.sleep(PAUSA)
    c.close()
    print(f"[servidores] {total} servidores da CVM gravados.")


def _pk(nome):
    """chave por primeiro+ultimo nome (igual ao app), p/ casar variantes."""
    toks = [t for t in _na(nome).split() if len(t) > 2]
    if not toks:
        return ""
    return toks[0] + " " + toks[-1] if len(toks) > 1 else toks[0]


def cruzar():
    """Custo de viagens (Transparencia) por diretor do Boletim (pessoal.db)."""
    if not os.path.exists(DB):
        print("transparencia.db ainda vazio (rode 'viagens' primeiro)."); return
    c = _con()
    dirs = set()
    if os.path.exists(PESSOAL):
        p = sqlite3.connect(PESSOAL)
        try:
            for (nome,) in p.execute(
                    "SELECT DISTINCT servidor_nome FROM movimentos WHERE "
                    "funcao LIKE '%Diretor%' OR funcao LIKE '%Presidente%'"):
                dirs.add(_pk(nome))
        except Exception:
            pass
        p.close()
    agg = {}
    for nome, di, va, vp, vt in c.execute(
            "SELECT beneficiario, valor_diarias, valor_diarias, valor_passagem,"
            " valor_total FROM viagens_gov"):
        k = _pk(nome)
        if dirs and k not in dirs:
            continue
        a = agg.setdefault(k or nome, {"nome": nome, "n": 0, "d": 0.0,
                                       "p": 0.0, "t": 0.0})
        a["n"] += 1
        a["d"] += _num(va)
        a["p"] += _num(vp)
        a["t"] += _num(vt)
    print(f"{'Diretor':32} {'viagens':>7} {'diarias':>12} {'passagens':>12} "
          f"{'total':>12}")
    for k, a in sorted(agg.items(), key=lambda kv: -kv[1]["t"]):
        print(f"{a['nome'][:32]:32} {a['n']:>7} {a['d']:>12,.2f} "
              f"{a['p']:>12,.2f} {a['t']:>12,.2f}")
    c.close()


def status():
    if not os.path.exists(DB):
        print("transparencia.db ainda nao criado."); return
    c = _con()
    nv = c.execute("SELECT COUNT(*) FROM viagens_gov").fetchone()[0]
    ns = c.execute("SELECT COUNT(*) FROM servidores_gov").fetchone()[0]
    tot = c.execute("SELECT COALESCE(SUM(valor_total),0) FROM viagens_gov").fetchone()[0]
    anos = c.execute("SELECT MIN(ano), MAX(ano) FROM viagens_gov").fetchone()
    print(f"viagens_gov: {nv} (anos {anos[0]}-{anos[1]}, custo total "
          f"R$ {tot:,.2f}) | servidores_gov: {ns}")
    print("chave configurada:", "sim" if chave() else "NAO")
    c.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "orgao":
        orgao(sys.argv[2] if len(sys.argv) > 2 else "valores mobiliarios")
    elif cmd == "viagens":
        viagens(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "servidores":
        servidores(sys.argv[2])
    elif cmd == "cruzar":
        cruzar()
    else:
        status()
