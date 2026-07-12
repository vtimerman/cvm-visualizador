#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""transparencia_cruzamento.py -- Frente C: "passaporte federal" de cada parte
dos processos sancionadores da CVM. Para cada ACUSADO (processos.db::acusados)
consulta os cadastros federais de sancao/exposicao e guarda os vinculos:
  - PF  -> CEAF (agentes expulsos), PEP (politicamente exposto), CEIS/CNEP
  - PJ  -> CEIS (inidoneas), CNEP (anticorrupcao), CEPIM, acordos de leniencia

Como o filtro nomeSancionado e' "contem" e as partes so tem NOME (sem CPF/CNPJ),
cada retorno e' VERIFICADO por similaridade de tokens; guardamos so os vinculos
que casam, com um nivel de confianca. Resumivel (nao repete nome/registro ja
consultado). Roda como maratona em background.

Uso:
  python transparencia_cruzamento.py rodar [limite]
  python transparencia_cruzamento.py status
  python transparencia_cruzamento.py achados
"""
import os
import re
import sys
import time
import sqlite3
import unicodedata

from transparencia_api import _get, _campo, DB, DIR, PAUSA

PROCESSOS = os.path.join(DIR, "processos.db")
STOP = {"ltda", "sa", "s", "a", "do", "da", "de", "e", "dos", "das", "me",
        "epp", "eireli", "cia", "ltda.", "s.a", "s/a", "and", "the", "ctvm",
        "dtvm", "cvm"}
PJ_RE = re.compile(r"\b(ltda|s\.?a\.?|s/a|eireli|banco|corretora|dtvm|ctvm|"
                   r"distribuidora|fundo|participa|holding|securit|"
                   r"incorpora|imobiliaria|asset|capital|investimbentos|"
                   r"investimentos|gestao|administradora|cia|companhia|"
                   r"associa|instituto|consultoria)\b")


def _na(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower().strip()


def _toks(s):
    return {t for t in re.split(r"[^a-z0-9]+", _na(s)) if t and t not in STOP
            and len(t) > 1}


def _eh_pj(nome):
    return bool(PJ_RE.search(_na(nome)))


def _match(alvo, achado):
    """confianca de que 'achado' (nome retornado) e a mesma parte que 'alvo'."""
    a, f = _toks(alvo), _toks(achado)
    if not a or not f:
        return 0.0
    inter = a & f
    if not inter:
        return 0.0
    # todos os tokens do alvo presentes -> forte
    cob = len(inter) / len(a)
    if a.issubset(f) and len(a) >= 2:
        return 0.95 if len(a) >= 3 else 0.8
    if cob >= 0.8 and len(inter) >= 2:
        return 0.7
    return 0.0


def _con():
    c = sqlite3.connect(DB, timeout=60)
    c.execute("PRAGMA busy_timeout=60000")
    c.execute("""CREATE TABLE IF NOT EXISTS cruzamento_gov(
        nome TEXT, nome_key TEXT, registro TEXT, achado_nome TEXT,
        confianca REAL, detalhe TEXT, orgao TEXT, data TEXT, processo TEXT,
        coletado_em TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS cruz_feito(
        nome_key TEXT, registro TEXT, PRIMARY KEY(nome_key, registro))""")
    return c


# registro -> (endpoint, param do nome, extrator de (detalhe, orgao, data, achado))
def _reg_ceis(d):
    return (_campo(d, "tipoSancao.descricaoResumida", "tipoSancao"),
            _campo(d, "orgaoSancionador.nome"), _campo(d, "dataInicioSancao"),
            _campo(d, "sancionado.nome", "pessoa.nome"))


def _reg_cnep(d):
    return (_campo(d, "tipoSancao.descricaoResumida", "tipoSancao"),
            _campo(d, "orgaoSancionador.nome"), _campo(d, "dataInicioSancao"),
            _campo(d, "sancionado.nome", "pessoa.nome"))


def _reg_cepim(d):
    return (_campo(d, "motivo", "tipo"), _campo(d, "orgaoSuperior.nome",
            "orgao.nome"), _campo(d, "dataReferencia"),
            _campo(d, "pessoaJuridica.nome", "nome"))


def _reg_leniencia(d):
    return (_campo(d, "situacaoAcordo", "situacao"),
            _campo(d, "orgaoResponsavel.nome"), _campo(d, "dataInicioAcordo"),
            _campo(d, "nomeEmpresa", "empresa.nome"))


def _reg_ceaf(d):
    return (_campo(d, "punicao", "tipoPunicao"),
            _campo(d, "orgaoLotacao.nome", "orgaoLotacao"),
            _campo(d, "dataPublicacao"),
            _campo(d, "servidor.nome", "nome", "pessoa.nome"))


def _reg_pep(d):
    return (_campo(d, "descricao_funcao"), _campo(d, "nome_orgao"),
            _campo(d, "dt_inicio_exercicio"), _campo(d, "nome"))


REGS = {
    "CEIS": ("/ceis", "nomeSancionado", _reg_ceis),
    "CNEP": ("/cnep", "nomeSancionado", _reg_cnep),
    "CEPIM": ("/cepim", "nomeSancionado", _reg_cepim),
    "LENIENCIA": ("/acordos-leniencia", "nomeSancionado", _reg_leniencia),
    "CEAF": ("/ceaf", "nomeSancionado", _reg_ceaf),
    "PEP": ("/peps", "nome", _reg_pep),
}
REGS_PJ = ["CEIS", "CNEP", "CEPIM", "LENIENCIA"]
REGS_PF = ["CEAF", "PEP", "CEIS", "CNEP"]


def _acusados():
    """(nome, processo) distintos dos acusados da CVM."""
    if not os.path.exists(PROCESSOS):
        return []
    p = sqlite3.connect(PROCESSOS)
    seen, out = set(), []
    for nome, proc in p.execute("SELECT nome, numero FROM acusados WHERE nome "
                                "IS NOT NULL AND LENGTH(nome)>4"):
        k = _na(nome)
        if k in seen:
            continue
        seen.add(k)
        out.append((nome.strip(), proc or ""))
    p.close()
    return out


def _consulta(c, nome, proc, registro):
    ep, pnome, extr = REGS[registro]
    key = _na(nome)
    if c.execute("SELECT 1 FROM cruz_feito WHERE nome_key=? AND registro=?",
                 (key, registro)).fetchone():
        return 0
    achados = 0
    try:
        dados = _get(ep, {pnome: nome[:60], "pagina": 1})
    except RuntimeError as e:
        print(f"  ! {registro} '{nome[:30]}': {e}", file=sys.stderr)
        return -1
    for d in dados:
        det, org, data, achn = extr(d)
        conf = _match(nome, achn)
        if conf >= 0.7:
            c.execute("INSERT INTO cruzamento_gov(nome,nome_key,registro,"
                      "achado_nome,confianca,detalhe,orgao,data,processo,"
                      "coletado_em) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
                      (nome, key, registro, achn, conf, str(det)[:200],
                       str(org)[:120], str(data)[:20], proc))
            achados += 1
    c.execute("INSERT OR IGNORE INTO cruz_feito(nome_key,registro) VALUES(?,?)",
              (key, registro))
    c.commit()
    return achados


def rodar(limite=None):
    c = _con()
    alvos = _acusados()
    print(f"[cruzamento] {len(alvos)} acusados distintos.")
    consultas = hits = 0
    for nome, proc in alvos:
        regs = REGS_PJ if _eh_pj(nome) else REGS_PF
        for registro in regs:
            r = _consulta(c, nome, proc, registro)
            if r >= 0:
                consultas += 1
                hits += r
                if r > 0:
                    print(f"  + {registro}: {nome[:40]} ({r})")
            time.sleep(PAUSA)
            if limite and consultas >= int(limite):
                c.close()
                print(f"[cruzamento] parcial: {consultas} consultas, {hits} "
                      "vinculos.")
                return
    c.close()
    print(f"[cruzamento] concluido: {consultas} consultas novas, {hits} "
          "vinculos encontrados.")


def status():
    if not os.path.exists(DB):
        print("transparencia.db ausente."); return
    c = _con()
    tot = len(_acusados())
    feito = c.execute("SELECT COUNT(DISTINCT nome_key) FROM cruz_feito").fetchone()[0]
    hits = c.execute("SELECT COUNT(*) FROM cruzamento_gov").fetchone()[0]
    print(f"acusados: {tot} | ja consultados: {feito} | vinculos: {hits}")
    for reg, n in c.execute("SELECT registro,COUNT(*) FROM cruzamento_gov "
                            "GROUP BY registro ORDER BY 2 DESC"):
        print(f"  {reg}: {n}")
    c.close()


def achados():
    c = _con()
    print(f"{'parte':38} {'registro':10} {'conf':>4}  detalhe")
    for nome, reg, conf, det, org in c.execute(
            "SELECT nome,registro,confianca,detalhe,orgao FROM cruzamento_gov "
            "ORDER BY confianca DESC, registro"):
        print(f"{nome[:38]:38} {reg:10} {conf:>4.2f}  {(det or '')[:40]} | {(org or '')[:30]}")
    c.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "rodar":
        rodar(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "achados":
        achados()
    else:
        status()
