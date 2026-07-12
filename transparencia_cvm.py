#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transparencia_cvm.py -- "Raio-X da CVM" (Frente A) e Pessoas (Frente B) via
API do Portal da Transparencia: orcamento, contratos, licitacoes, cartoes
corporativos e PEP dos diretores. Reusa o cliente de transparencia_api.py.

Grava em transparencia.db. CVM = orgao SIAFI 25203 / SIAPE 45203.

Uso:
  python transparencia_cvm.py orcamento [ano_ini ano_fim]
  python transparencia_cvm.py contratos [ano_ini ano_fim]
  python transparencia_cvm.py licitacoes [ano_ini ano_fim]
  python transparencia_cvm.py cartoes [ano_ini ano_fim]
  python transparencia_cvm.py peps         # PEP dos diretores (por nome)
  python transparencia_cvm.py status
"""
import os
import sys
import time
import sqlite3
import unicodedata

from transparencia_api import _get, _campo, _num, DB, DIR, PAUSA

COD_SIAFI = "25203"
PESSOAL = os.path.join(DIR, "pessoal.db")


def _na(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _con():
    c = sqlite3.connect(DB, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("""CREATE TABLE IF NOT EXISTS orcamento_gov(
        ano INTEGER PRIMARY KEY, empenhado REAL, liquidado REAL, pago REAL,
        coletado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS contratos_gov(
        id TEXT PRIMARY KEY, numero TEXT, objeto TEXT, modalidade TEXT,
        fornecedor TEXT, fornecedor_doc TEXT, valor_inicial REAL,
        valor_final REAL, data_assinatura TEXT, vig_inicio TEXT, vig_fim TEXT,
        ano INTEGER, coletado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS licitacoes_gov(
        id TEXT PRIMARY KEY, numero TEXT, objeto TEXT, modalidade TEXT,
        situacao TEXT, data_abertura TEXT, valor REAL, ano INTEGER,
        coletado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cartoes_gov(
        id TEXT PRIMARY KEY, mes TEXT, data TEXT, valor REAL, portador TEXT,
        portador_cpf TEXT, estabelecimento TEXT, estab_doc TEXT, ano INTEGER,
        coletado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS peps_cvm(
        nome TEXT, cpf TEXT, funcao TEXT, orgao TEXT, inicio TEXT, fim TEXT,
        casou_diretor TEXT, coletado_em TEXT)""")
    return c


# ------------------------------------------------------------------ Frente A
def orcamento(ai=2011, af=2026):
    c = _con()
    n = 0
    for ano in range(int(ai), int(af) + 1):
        try:
            dados = _get("/despesas/por-orgao", {"orgao": COD_SIAFI,
                                                 "ano": ano, "pagina": 1})
        except RuntimeError as e:
            print(f"  ! {ano}: {e}", file=sys.stderr)
            continue
        for d in dados:
            c.execute("INSERT OR REPLACE INTO orcamento_gov(ano,empenhado,"
                      "liquidado,pago,coletado_em) VALUES(?,?,?,?,datetime('now'))",
                      (ano, _num(_campo(d, "empenhado")),
                       _num(_campo(d, "liquidado")), _num(_campo(d, "pago"))))
            n += 1
        c.commit()
        time.sleep(PAUSA)
    c.close()
    print(f"[orcamento] {n} anos gravados.")


def _pagina_ano(path, base_params, ano, store, c):
    pagina, tot = 1, 0
    while True:
        p = dict(base_params)
        p["pagina"] = pagina
        try:
            dados = _get(path, p)
        except RuntimeError as e:
            print(f"  ! {ano} p{pagina}: {e}", file=sys.stderr)
            break
        if not dados:
            break
        for d in dados:
            store(c, d, ano)
            tot += 1
        c.commit()
        pagina += 1
        time.sleep(PAUSA)
    return tot


def contratos(ai=2011, af=2026):
    c = _con()
    def store(c, d, ano):
        c.execute(
            "INSERT OR REPLACE INTO contratos_gov(id,numero,objeto,modalidade,"
            "fornecedor,fornecedor_doc,valor_inicial,valor_final,"
            "data_assinatura,vig_inicio,vig_fim,ano,coletado_em) VALUES("
            "?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
            (str(_campo(d, "id")), _campo(d, "numero"),
             _campo(d, "objeto")[:400], _campo(d, "modalidadeCompra"),
             _campo(d, "fornecedor.nome", "fornecedor.razaoSocialReceita"),
             _campo(d, "fornecedor.cnpjFormatado", "fornecedor.cpfFormatado"),
             _num(_campo(d, "valorInicialCompra")),
             _num(_campo(d, "valorFinalCompra")),
             _campo(d, "dataAssinatura"), _campo(d, "dataInicioVigencia"),
             _campo(d, "dataFimVigencia"), ano))
    tot = 0
    for ano in range(int(ai), int(af) + 1):
        base = {"codigoOrgao": COD_SIAFI, "dataInicial": f"01/01/{ano}",
                "dataFinal": f"31/12/{ano}"}
        t = _pagina_ano("/contratos", base, ano, store, c)
        if t:
            print(f"  contratos {ano}: {t}")
        tot += t
    c.close()
    print(f"[contratos] {tot} contratos gravados.")


def licitacoes(ai=2011, af=2026):
    c = _con()
    def store(c, d, ano):
        c.execute(
            "INSERT OR REPLACE INTO licitacoes_gov(id,numero,objeto,modalidade,"
            "situacao,data_abertura,valor,ano,coletado_em) VALUES("
            "?,?,?,?,?,?,?,?,datetime('now'))",
            (str(_campo(d, "id")), _campo(d, "licitacao.numero"),
             _campo(d, "licitacao.objeto")[:400],
             _campo(d, "modalidadeLicitacao"), _campo(d, "situacaoCompra"),
             _campo(d, "dataAbertura"), _num(_campo(d, "valor")), ano))
    import calendar as _cal
    tot = 0
    for ano in range(int(ai), int(af) + 1):
        ta = 0
        for mes in range(1, 13):
            ult = _cal.monthrange(ano, mes)[1]
            base = {"codigoOrgao": COD_SIAFI,
                    "dataInicial": f"01/{mes:02d}/{ano}",
                    "dataFinal": f"{ult:02d}/{mes:02d}/{ano}"}
            ta += _pagina_ano("/licitacoes", base, ano, store, c)
        if ta:
            print(f"  licitacoes {ano}: {ta}")
        tot += ta
    c.close()
    print(f"[licitacoes] {tot} licitacoes gravadas.")


def cartoes(ai=2011, af=2026):
    c = _con()
    def store(c, d, ano):
        c.execute(
            "INSERT OR REPLACE INTO cartoes_gov(id,mes,data,valor,portador,"
            "portador_cpf,estabelecimento,estab_doc,ano,coletado_em) VALUES("
            "?,?,?,?,?,?,?,?,?,datetime('now'))",
            (str(_campo(d, "id")), _campo(d, "mesExtrato"),
             _campo(d, "dataTransacao"), _num(_campo(d, "valorTransacao")),
             _campo(d, "portador.nome"), _campo(d, "portador.cpfFormatado"),
             _campo(d, "estabelecimento.nome",
                    "estabelecimento.razaoSocialReceita"),
             _campo(d, "estabelecimento.cnpjFormatado"), ano))
    tot = 0
    for ano in range(int(ai), int(af) + 1):
        base = {"codigoOrgao": COD_SIAFI, "mesExtratoInicio": f"01/{ano}",
                "mesExtratoFim": f"12/{ano}"}
        t = _pagina_ano("/cartoes", base, ano, store, c)
        if t:
            print(f"  cartoes {ano}: {t}")
        tot += t
    c.close()
    print(f"[cartoes] {tot} transacoes gravadas.")


# ------------------------------------------------------------------ Frente B
def _diretores():
    """nomes estatutarios (diretores/presidentes) do Boletim (pessoal.db)."""
    if not os.path.exists(PESSOAL):
        return []
    p = sqlite3.connect(PESSOAL)
    try:
        rows = [r[0] for r in p.execute(
            "SELECT DISTINCT servidor_nome FROM movimentos WHERE funcao "
            "LIKE '%Diretor%' OR funcao LIKE '%Presidente%'")]
    except Exception:
        rows = []
    p.close()
    return sorted({n.strip() for n in rows if n and len(n.split()) >= 2})


def peps():
    """Consulta o cadastro de PEP por NOME para cada diretor da CVM."""
    c = _con()
    c.execute("DELETE FROM peps_cvm")
    dirs = _diretores()
    print(f"[peps] consultando {len(dirs)} diretores...")
    achados = 0
    for nome in dirs:
        alvo = _na(nome)
        try:
            dados = _get("/peps", {"nome": nome, "pagina": 1})
        except RuntimeError as e:
            print(f"  ! {nome}: {e}", file=sys.stderr)
            time.sleep(PAUSA)
            continue
        for d in dados:
            if _na(d.get("nome")) == alvo:      # match exato de nome normalizado
                c.execute("INSERT INTO peps_cvm(nome,cpf,funcao,orgao,inicio,"
                          "fim,casou_diretor,coletado_em) VALUES(?,?,?,?,?,?,?,"
                          "datetime('now'))",
                          (d.get("nome"), d.get("cpf"),
                           d.get("descricao_funcao"), d.get("nome_orgao"),
                           d.get("dt_inicio_exercicio"),
                           d.get("dt_fim_exercicio"), nome))
                achados += 1
        c.commit()
        time.sleep(PAUSA)
    c.close()
    print(f"[peps] {achados} vinculos PEP casados a diretores.")


def status():
    if not os.path.exists(DB):
        print("transparencia.db ausente."); return
    c = _con()
    for t, lbl in [("orcamento_gov", "orcamento(anos)"),
                   ("contratos_gov", "contratos"),
                   ("licitacoes_gov", "licitacoes"),
                   ("cartoes_gov", "cartoes"), ("peps_cvm", "PEP diretores")]:
        n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {lbl:18}: {n}")
    r = c.execute("SELECT MIN(ano),MAX(ano),SUM(pago) FROM orcamento_gov").fetchone()
    if r and r[0]:
        print(f"  orcamento {r[0]}-{r[1]} | pago acumulado R$ {r[2]:,.2f}")
    c.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    args = [int(x) for x in sys.argv[2:4]] if len(sys.argv) > 3 else []
    fn = {"orcamento": orcamento, "contratos": contratos,
          "licitacoes": licitacoes, "cartoes": cartoes}.get(cmd)
    if fn:
        fn(*args) if args else fn()
    elif cmd == "peps":
        peps()
    else:
        status()
