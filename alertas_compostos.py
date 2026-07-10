#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
alertas_compostos.py — alertas de CORRELACAO entre as bases (a inteligencia que
nenhuma fonte sozinha da). Roda apos as coletas (GitHub Actions) e envia UM
Telegram consolidado com o que for novo. Dedup em alertas.db.

Correlacoes:
  1. Audiencia nova cujo solicitante/empresa e ACUSADO em processo nao julgado.
  2. Processo pautado com sessao de julgamento nos proximos 7 dias.
  3. Retirada de pauta recem-publicada no Diario.
  4. TC rejeitado nos ultimos 7 dias (processo tende a ir a julgamento).
"""
import os
import re
import sys
import sqlite3
import unicodedata
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))


def _db(nome):
    return os.path.join(DIR, nome)


def _key(nome):
    s = unicodedata.normalize("NFKD", str(nome or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def _estado():
    con = sqlite3.connect(_db("alertas.db"))
    con.execute("CREATE TABLE IF NOT EXISTS enviados("
                "chave TEXT PRIMARY KEY, enviado_em TEXT)")
    con.commit()
    return con

def _novo(est, chave):
    if est.execute("SELECT 1 FROM enviados WHERE chave=?", (chave,)).fetchone():
        return False
    est.execute("INSERT INTO enviados(chave, enviado_em) VALUES(?,?)",
                (chave, dt.date.today().isoformat()))
    return True


def correlacoes():
    hoje = dt.date.today()
    hoje_iso = hoje.isoformat()
    sem7 = (hoje + dt.timedelta(days=7)).isoformat()
    atras7 = (hoje - dt.timedelta(days=7)).isoformat()
    est = _estado()
    msgs = []

    # universo: acusados de processos NAO julgados
    julg = set()
    if os.path.exists(_db("julgar.db")):
        cj = sqlite3.connect(_db("julgar.db"))
        julg = {r[0] for r in cj.execute(
            "SELECT proc_norm FROM julgados WHERE proc_norm<>''")}
        cj.close()
    acusados = {}
    if os.path.exists(_db("processos.db")):
        cp = sqlite3.connect(_db("processos.db"))
        pn_por_id = {}
        for idp, num in cp.execute("SELECT idproc, numero FROM processos"):
            m = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}", str(num or ""))
            if m:
                pn_por_id[idp] = (num, m.group(0))
        for idp, nome in cp.execute("SELECT idproc, nome FROM acusados"):
            num, pn = pn_por_id.get(idp, ("", ""))
            if pn and pn not in julg and len(_key(nome)) > 6:
                acusados.setdefault(_key(nome), []).append(num)
        cp.close()

    # 1) audiencia nova de acusado com processo em curso
    if os.path.exists(_db("audiencias.db")):
        ca = sqlite3.connect(_db("audiencias.db"))
        # audiencia RECENTE/FUTURA (data_iso), coletada ha pouco — nao o acervo
        for aid, data, comp, sol, emp in ca.execute(
                "SELECT id, data, componente, solicitante_nome, "
                "solicitante_empresa FROM audiencias WHERE estado='valido' "
                "AND coletado_em>=? AND data_iso>=?", (atras7, atras7)):
            for nome in (sol, emp):
                k = _key(nome)
                if k in acusados and _novo(est, f"aud_acusado:{aid}:{k}"):
                    procs = ", ".join(acusados[k][:3])
                    msgs.append(f"🚨 {nome} (acusado em {procs}) pediu "
                                f"audiência: {data} com {comp}")
        ca.close()

    # 2) sessao de julgamento nos proximos 7 dias / 3) retirada recente
    if os.path.exists(_db("pautas.db")):
        cpa = sqlite3.connect(_db("pautas.db"))
        try:
            for proc, rel, ds, iso, sit in cpa.execute(
                    "SELECT processo, relator, data_sessao, data_sessao_iso, "
                    "situacao FROM pauta_sei"):
                pn = proc
                if (sit or "").startswith("retirado"):
                    if _novo(est, f"retirado:{proc}:{iso}"):
                        msgs.append(f"🚫 TIRADO de pauta: {proc} "
                                    f"(sessão {ds}, {rel}) — {sit}")
                elif iso and hoje_iso <= iso <= sem7 and pn not in julg:
                    if _novo(est, f"sessao7d:{proc}:{iso}"):
                        msgs.append(f"🗓️ Julgamento em breve: {proc} — sessão "
                                    f"{ds} ({rel})")
        except sqlite3.OperationalError:
            pass
        cpa.close()

    # 4) TC rejeitado recente
    if os.path.exists(_db("termos.db")):
        ct = sqlite3.connect(_db("termos.db"))
        for proc, data in ct.execute(
                "SELECT processo, data_decisao FROM termos WHERE "
                "situacao='Rejeitado' AND data_decisao_iso>=?", (atras7,)):
            if _novo(est, f"tc_rej:{proc}"):
                msgs.append(f"🤝❌ TC rejeitado em {data}: {proc} "
                            "(processo tende a ir a julgamento)")
        ct.close()

    est.commit()
    est.close()
    return msgs


def main():
    msgs = correlacoes()
    if not msgs:
        print("[alertas] nada novo.")
        return
    corpo = "🔗 Alertas cruzados Motumbo CVM:\n" + "\n".join(f"• {m}" for m in msgs[:15])
    if len(msgs) > 15:
        corpo += f"\n(+{len(msgs) - 15} outros)"
    print(corpo)
    try:
        from notificar import enviar
        enviar(corpo)
    except Exception as e:
        print(f"[alertas] falha ao notificar: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
