#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boletim_pessoal_extrair.py — extrai dados estruturados do texto dos boletins
(pessoal.db / tabela boletins) para tabelas consultaveis.

Fase B do projeto de servidores. Nesta primeira parte foca nas VIAGENS, que
aparecem em duas secoes do Boletim de Pessoal:
  - "AFASTAMENTO DO PAIS": viagens internacionais (texto narrativo).
  - "CONCESSAO DE DIARIAS": viagens nacionais (tabela do SCDP: PCDP, proposto,
    motivo, trechos com datas, valor das diarias).
Cada viagem e ligada ao servidor pelo NOME (para depois cruzar com o Portal da
Transparencia, cujo CPF vem mascarado).

Tambem preenche a data dos boletins antigos cujo titulo/arquivo nao a traziam,
usando o cabecalho do texto ("Edicao N, de DD de mes de AAAA").

Uso:
  python boletim_pessoal_extrair.py datas      # preenche datas faltantes
  python boletim_pessoal_extrair.py viagens    # extrai viagens -> tabela viagens
  python boletim_pessoal_extrair.py stats
"""
import os
import re
import sys
import sqlite3
import unicodedata
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "pessoal.db")
MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
         "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
         "outubro": 10, "novembro": 11, "dezembro": 12}
# rotulos de secao conhecidos (para delimitar blocos)
SECOES = (r"NOMEA[CÇ][AÃ]O|EXONERA[CÇ][AÃ]O|DESIGNA[CÇ][AÃ]O|DISPENSA|REMO[CÇ][AÃ]O|"
          r"SUBSTITUI[CÇ][AÃ]O|APOSENTADORIA|VAC[AÂ]NCIA|LICEN[CÇ]A[A-Z ]*|"
          r"F[EÉ]RIAS[A-Z ]*|FRUI[CÇ][AÃ]O|AFASTAMENTO DO PA[IÍ]S|"
          r"CONCESS[AÃ]O DE DI[AÁ]RIAS|CONCESS[AÃ]O DE INDENIZA[CÇ][AÃ]O[A-Z ]*|"
          r"ERRATA|INFORMATIVO|ANEXO|VOTO")


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS viagens(
        id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT, servidor_nome TEXT,
        servidor_key TEXT, cargo TEXT, origem TEXT, destino TEXT,
        periodo_ini TEXT, periodo_fim TEXT, motivo TEXT, descricao TEXT,
        valor_diarias TEXT, pcdp TEXT, processo TEXT, boletim_numero TEXT,
        boletim_data_iso TEXT, link_boletim TEXT, coletado_em TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_vg_key ON viagens(servidor_key)")
    con.commit()
    return con


def _key(nome):
    """Normaliza nome para casar (sem acento, maiusculo, espacos colapsados)."""
    s = unicodedata.normalize("NFKD", str(nome or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def _secao(texto, cab):
    m = re.search(r"^[ \t]*" + cab + r"[ \t]*$(.*?)(?=^[ \t]*(?:" + SECOES +
                  r")[ \t]*$|\Z)", texto, re.M | re.S)
    return m.group(1).strip() if m else ""


def preencher_datas(con):
    """Data faltante <- cabecalho 'Edicao N, de DD de mes de AAAA'."""
    rows = con.execute("SELECT pdf_url, texto FROM boletins WHERE "
                       "(data_iso IS NULL OR data_iso='') AND texto<>''").fetchall()
    n = 0
    for url, texto in rows:
        m = re.search(r"(?:Edi[cç][aã]o|Boletim de Pessoal)[^\n]*?,\s*de\s+"
                      r"(\d{1,2})[ºo]?\s+de\s+([a-zA-Zçã]+)\s+de\s+(\d{4})",
                      texto[:600], re.I)
        if not m or not MESES.get(m.group(2).lower()):
            continue
        d, mo, a = int(m.group(1)), MESES[m.group(2).lower()], int(m.group(3))
        try:
            iso = dt.date(a, mo, d).isoformat()
        except ValueError:
            continue
        con.execute("UPDATE boletins SET data=?, data_iso=?, ano=? WHERE pdf_url=?",
                    (f"{d:02d}/{mo:02d}/{a}", iso, str(a), url))
        n += 1
    con.commit()
    print(f"[extrair] datas preenchidas: {n}")


def _viagens_pais(sec):
    out = []
    # cada autorizacao comeca em 'afastamento do Pais de NOME,'
    for m in re.finditer(r"afastamento do Pa[ií]s de\s+([A-ZÀ-Ú][^,]+?),\s*(.*?)"
                         r"(?=afastamento do Pa[ií]s de|\Z)", sec, re.S):
        nome = re.sub(r"\s+", " ", m.group(1)).strip()
        corpo = re.sub(r"\s+", " ", m.group(2)).strip()
        per = re.search(r"no per[ií]odo de\s+(.*?)(?:,?\s*inclusive|,\s*com [oô]nus|"
                        r",\s*a fim)", corpo, re.I)
        motivo = re.search(r"a fim de\s+(.*?)(?:\.\s|\(Processo|$)", corpo, re.I)
        dest = re.search(r"(?:realizad[ao]|ocorrer[aá]?)\s+em\s+([^.(]+)", corpo, re.I)
        proc = re.search(r"Processo\s+CVM\s+n[ºo\.]?\s*([\d./-]+)", corpo, re.I)
        out.append({"tipo": "afastamento_pais", "servidor_nome": nome,
                    "cargo": "", "origem": "", "destino": (dest.group(1).strip()
                    if dest else ""), "periodo_ini": (per.group(1).strip()
                    if per else ""), "periodo_fim": "",
                    "motivo": (motivo.group(1).strip() if motivo else ""),
                    "descricao": "", "valor_diarias": "", "pcdp": "",
                    "processo": (proc.group(1).strip() if proc else "")})
    return out


def _viagens_diarias(sec):
    out = []
    blocos = re.split(r"\bPCDP\b", sec)
    for b in blocos[1:]:
        pcdp = re.match(r"\s*([0-9]{3,}/\d{2,4})", b)
        nome = re.search(r"Nome do Proposto:\s*(.+)", b)
        motivo = re.search(r"Motivo da Viagem:\s*(.+)", b)
        desc = re.search(r"Descri[cç][aã]o Motivo:\s*(.+)", b)
        valor = re.search(r"Valor das Di[aá]rias:\s*([\d.,]+)", b)
        trechos = re.findall(r"([A-Za-zÀ-ú][A-Za-zÀ-ú .'-]+?)\s*\((\d{2}/\d{2}/\d{4})\)", b)
        if not nome:
            continue
        origem = trechos[0][0].strip() if trechos else ""
        destino = trechos[1][0].strip() if len(trechos) > 1 else ""
        ini = trechos[0][1] if trechos else ""
        fim = trechos[-1][1] if trechos else ""
        out.append({"tipo": "diaria", "servidor_nome": nome.group(1).strip(),
                    "cargo": "", "origem": origem, "destino": destino,
                    "periodo_ini": ini, "periodo_fim": fim,
                    "motivo": motivo.group(1).strip() if motivo else "",
                    "descricao": desc.group(1).strip() if desc else "",
                    "valor_diarias": valor.group(1).strip() if valor else "",
                    "pcdp": pcdp.group(1) if pcdp else "", "processo": ""})
    return out


def extrair_viagens(con):
    con.execute("DELETE FROM viagens")
    hoje = dt.date.today().isoformat()
    rows = con.execute("SELECT numero, data_iso, pdf_url, texto FROM boletins "
                       "WHERE texto IS NOT NULL AND texto<>''").fetchall()
    total = 0
    for numero, data_iso, url, texto in rows:
        itens = []
        s1 = _secao(texto, r"AFASTAMENTO DO PA[IÍ]S")
        if s1:
            itens += _viagens_pais(s1)
        s2 = _secao(texto, r"CONCESS[AÃ]O DE DI[AÁ]RIAS")
        if s2:
            itens += _viagens_diarias(s2)
        for it in itens:
            con.execute(
                "INSERT INTO viagens(tipo,servidor_nome,servidor_key,cargo,origem,"
                "destino,periodo_ini,periodo_fim,motivo,descricao,valor_diarias,"
                "pcdp,processo,boletim_numero,boletim_data_iso,link_boletim,"
                "coletado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (it["tipo"], it["servidor_nome"], _key(it["servidor_nome"]),
                 it["cargo"], it["origem"], it["destino"], it["periodo_ini"],
                 it["periodo_fim"], it["motivo"], it["descricao"],
                 it["valor_diarias"], it["pcdp"], it["processo"], numero,
                 data_iso, url, hoje))
            total += 1
    con.commit()
    print(f"[extrair] viagens: {total}")


def stats(con=None):
    own = con is None
    con = con or conectar()
    for t in ("afastamento_pais", "diaria"):
        n = con.execute("SELECT COUNT(*) FROM viagens WHERE tipo=?", (t,)).fetchone()[0]
        print(f"  viagens {t}: {n}")
    ns = con.execute("SELECT COUNT(DISTINCT servidor_key) FROM viagens").fetchone()[0]
    print(f"  servidores distintos em viagens: {ns}")
    if own:
        con.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "stats"
    c = conectar()
    if cmd == "datas":
        preencher_datas(c)
    elif cmd == "viagens":
        preencher_datas(c)
        extrair_viagens(c)
        stats(c)
    else:
        stats(c)
    c.close()
