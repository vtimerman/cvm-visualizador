#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
informativos_seed.py — monta a base dos Informativos do Colegiado (CVM).

Camada 1 (regex): le informativos_txt/*.txt, fatia cada informativo nas suas
DELIBERACOES (uma linha por assunto) e extrai numero da reuniao, data, item,
tipo (categoria), assunto (titulo bruto), processo, Reg., relator e a decisao.

Camada 2 (IA, feita pelo Claude depois): resumo, palavras_chave, partes e
resultado por deliberacao — aplicada via aplicar_ia (ai_feito -> 1).

Uso:
  python informativos_seed.py                 # (re)constroi a base
  python informativos_seed.py pendentes [N]   # lista N deliberacoes sem IA (p/ enriquecer)
  python informativos_seed.py aplicar_ia x.json
"""
import os
import re
import sys
import glob
import json
import sqlite3
import datetime as dt

DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIR, "informativos.db")
TXT_DIR = os.path.join(DIR, "informativos_txt")
URLS = os.path.join(DIR, "informativos_urls.txt")

MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "março": 3, "abril": 4,
         "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
         "outubro": 10, "novembro": 11, "dezembro": 12}

RODAPE = re.compile(
    r"(caráter meramente informativo|não substituindo|substituindo\s*$|"
    r"COMISSÃO DE VALORES|www\.cvm|Rua Sete de Setembro|Rua Cincinato|"
    r"SCN Q\.02|CEP:\s*\d)", re.I)

# classificacao de tipo por palavra-chave no titulo (ordem importa)
TIPOS = [
    ("Termo de Compromisso", r"TERMO DE COMPROMISSO"),
    ("Recurso", r"\bRECURSO\b"),
    ("Consulta", r"\bCONSULTA\b"),
    ("Pedido de Anulação", r"ANULA[ÇC][ÃA]O|ANULATÓRI"),
    ("Julgamento/PAS", r"JULGAMENTO|SANCIONADOR|\bPAS\b|PROCESSO ADMINISTRATIVO"),
    ("Oferta Pública", r"OFERTA PÚBLICA|REGISTRO DE OFERTA|\bCRI\b|\bCRA\b|DEB[EÊ]NTURE"),
    ("Norma/Minuta", r"MINUTA|EDITAL DE AUDI|AUDI[ÊE]NCIA PÚBLICA|RESOLU[ÇC][ÃA]O|INSTRU[ÇC][ÃA]O"),
    ("Reclamação/MRP", r"RECLAMA[ÇC][ÃA]O|MECANISMO DE RESSARCIMENTO|\bMRP\b"),
    ("Multa Cominatória", r"MULTA COMINAT"),
    ("Plano/Gestão Interna", r"PLANO ANUAL|PAINT|PLANO DE|RELAT[ÓO]RIO DE|OR[ÇC]AMENT"),
    ("Proposta", r"\bPROPOSTA\b|APRECIA[ÇC][ÃA]O DE PROPOSTA"),
]

PARAR_TITULO = re.compile(
    r"^\s*(Reg\.|Relator|Relatora|Proponente|Impedimento|Suspei|Diretor Substituto|"
    r"Por unanimidade|Por maioria|O Colegiado|Trata-se|Acompanhando|Nos termos|"
    r"O Dir\.|A Dir\.|Foi |Em |Na |Após|O assunto)", re.I)

RE_PROC = re.compile(
    r"(19957\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}|SP\s?\d{4}/\d{3,6}|"
    r"\bPROC\.?\s*(?:SEI\s*)?[\d./-]{8,}|SEI\s*[\d./-]{10,})", re.I)
RE_REG = re.compile(r"Reg\.?\s*n?[ºo°]?\s*([\d./]+)", re.I)
RE_REL = re.compile(r"Relator[a]?:?\s*([^\n]{1,80})", re.I)

# tipos de deliberacao que sao, por natureza, de processo sancionador (PAS)
TIPOS_SANC = {"Julgamento/PAS", "Termo de Compromisso", "Pedido de Anulação"}
# sigla de relator = Presidente ou Diretor (relatam PAS)
RE_DIRETOR = re.compile(r"^(PTE|D[A-Z]{1,3})$")
# bloco de sorteio/redistribuicao: Reg. NNNN/YY - <processo> (..) - <SIGLA DIRETOR>
RE_SORTEIO = re.compile(
    r"Reg\.?\s*n?[ºo°]?\s*(\d{3,4}/\d{2})\s*[-–]\s*"
    r"(1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}|\d{2,4}/\d{4})"
    r"\s*(?:\([^)]*\)\s*)*[-–]\s*([A-Z]{2,4})\b")
# "Impedimento(s): DFP, DAR e DOL" / "Suspeição: PTE"
RE_IMPED = re.compile(
    r"(Impedimentos?|Suspei[çc][õoãa][eo]?s?)\s*:?\s*"
    r"([A-Z][A-Za-z,;/ eE]{0,70})")


def norm_proc(p):
    """Normaliza um numero de processo para chave de cruzamento."""
    if not p:
        return ""
    m = re.search(r"1\d{4}\.\d{6}/\d{4}-\d{2}|RJ\s?\d{4}/\d{3,6}|"
                  r"SP\s?\d{4}/\d{3,6}|\d{1,4}/\d{4}", p)
    return re.sub(r"\s+", "", m.group(0)).upper() if m else ""


def natureza_de(tipo, relator):
    sigla = (relator or "").split("/")[0].strip().upper()
    if tipo in TIPOS_SANC or RE_DIRETOR.match(sigla):
        return "Sancionador"
    return "Nao-sancionador"


def conectar():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS deliberacoes(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        arquivo TEXT, inf_numero TEXT, reuniao_tipo TEXT,
        data TEXT, data_iso TEXT,
        item TEXT, tipo TEXT, assunto TEXT, processo TEXT, reg TEXT, relator TEXT,
        decisao TEXT, texto TEXT, link TEXT, natureza TEXT, proc_norm TEXT,
        resumo TEXT, palavras_chave TEXT, partes TEXT, resultado TEXT,
        ai_feito INTEGER DEFAULT 0, coletado_em TEXT,
        UNIQUE(arquivo, item))""")
    # migracao: adiciona colunas novas se a base ja existia sem elas
    for col, tipo_sql in [("natureza", "TEXT"), ("proc_norm", "TEXT")]:
        try:
            con.execute(f"ALTER TABLE deliberacoes ADD COLUMN {col} {tipo_sql}")
        except sqlite3.OperationalError:
            pass
    # eventos de relatoria (sorteios/redistribuicoes + julgamentos por diretor)
    con.execute("""CREATE TABLE IF NOT EXISTS relatores(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proc_norm TEXT, processo TEXT, reg TEXT, relator TEXT, evento TEXT,
        arquivo TEXT, inf_numero TEXT, data TEXT, data_iso TEXT,
        UNIQUE(proc_norm, arquivo, relator, evento))""")
    # impedimentos/suspeicoes declarados por diretor em cada deliberacao
    con.execute("""CREATE TABLE IF NOT EXISTS impedimentos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proc_norm TEXT, processo TEXT, sigla TEXT, tipo TEXT,
        arquivo TEXT, inf_numero TEXT, assunto TEXT, data TEXT, data_iso TEXT,
        UNIQUE(proc_norm, sigla, tipo, arquivo))""")
    # mapa sigla <-> nome do diretor/presidente (mencoes 'Dir./Pres. Nome')
    cols_sig = [r[1] for r in con.execute("PRAGMA table_info(siglas)")]
    if cols_sig and "papel" not in cols_sig:
        con.execute("DROP TABLE siglas")  # migra esquema (dado 100% derivado)
    con.execute("""CREATE TABLE IF NOT EXISTS siglas(
        sigla TEXT, nome TEXT, papel TEXT, data_iso TEXT, arquivo TEXT,
        UNIQUE(sigla, nome, arquivo))""")
    con.commit()
    return con


def mapa_links():
    m = {}
    if os.path.exists(URLS):
        for lin in open(URLS, encoding="utf-8"):
            url = lin.split("\t")[0].strip()
            if url:
                base = os.path.splitext(os.path.basename(url))[0]
                m[base] = url
    return m


def limpar(texto):
    return "\n".join(l for l in texto.splitlines() if not RODAPE.search(l))


def meta_informativo(texto):
    cab = re.sub(r"\s+", " ", texto[:800])
    numero = ""
    m = re.search(r"COLEGIADO\s+N[ºo°]\s*(\d+)", cab, re.I)
    if m:
        numero = m.group(1).lstrip("0") or "0"
    tipo = "Extraordinária" if re.search(r"EXTRAORDIN", cab, re.I) else "Ordinária"
    data = data_iso = ""
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})", cab)
    if m:
        d, mm, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mm <= 12 and 1 <= d <= 31:
            data = f"{d:02d}/{mm:02d}/{a}"
            data_iso = f"{a}-{mm:02d}-{d:02d}"
    if not data:
        m = re.search(r"(\d{1,2})\s+DE\s+([A-Za-zçÇ]+)\s+DE\s+(\d{4})", cab, re.I)
        if m:
            d, mes, a = int(m.group(1)), m.group(2).lower(), int(m.group(3))
            k = MESES.get(mes.replace("ç", "c"), 0)
            if k:
                data = f"{d:02d}/{k:02d}/{a}"
                data_iso = f"{a}-{k:02d}-{d:02d}"
    return numero, tipo, data, data_iso


def fatiar(texto):
    """Retorna lista de (item_num, corpo_item) a partir da secao DELIBERACOES."""
    t = limpar(texto)
    m = re.search(r"DELIBERA[ÇC][ÕO]ES\s*:?", t, re.I)
    corpo = t[m.end():] if m else t
    # posicoes de marcadores 'N.' no inicio de linha
    marks = [(int(mo.group(1)), mo.start(), mo.end())
             for mo in re.finditer(r"(?m)^\s*(\d{1,2})\.(?:\s|$)", corpo)]
    # manter apenas a subsequencia crescente comecando em 1 (evita 'art. 3.' etc.)
    seq = []
    esperado = 1
    for num, ini, fim in marks:
        if num == esperado:
            seq.append((num, ini, fim))
            esperado += 1
        elif num == esperado - 0:  # tolera repeticao improvavel
            pass
    itens = []
    for i, (num, ini, fim) in enumerate(seq):
        prox = seq[i + 1][1] if i + 1 < len(seq) else len(corpo)
        corpo_item = corpo[fim:prox].strip()
        itens.append((str(num), corpo_item))
    return itens


def extrair_campos(corpo_item):
    linhas = [l.strip() for l in corpo_item.splitlines() if l.strip()]
    titulo_ls = []
    for l in linhas:
        if PARAR_TITULO.match(l):
            break
        titulo_ls.append(l)
        if len(" ".join(titulo_ls)) > 260:
            break
    assunto = re.sub(r"\s+", " ", " ".join(titulo_ls)).strip(" -–.")
    up = assunto.upper()
    tipo = "Outros"
    for nome, pat in TIPOS:
        if re.search(pat, up):
            tipo = nome
            break
    mp = RE_PROC.search(corpo_item)
    processo = re.sub(r"\s+", " ", mp.group(0)).strip() if mp else ""
    mr = RE_REG.search(corpo_item)
    reg = mr.group(1) if mr else ""
    ml = RE_REL.search(corpo_item)
    relator = re.sub(r"\s+", " ", ml.group(1)).strip(" .") if ml else ""
    # decisao: do primeiro "Por unanimidade/Por maioria/O Colegiado/O Dir." ate o fim
    md = re.search(r"(Por unanimidade|Por maioria|O Colegiado|Acompanhando|"
                   r"O Dir\.|A Dir\.|Nos termos|Em linha).*", corpo_item, re.S | re.I)
    decisao = re.sub(r"\s+", " ", md.group(0)).strip() if md else ""
    return tipo, assunto, processo, reg, relator, decisao


def eventos_sorteio(texto):
    """Extrai (reg, processo, sigla_diretor) dos blocos de sorteio/redistribuicao."""
    out = []
    for reg, proc, sigla in RE_SORTEIO.findall(texto):
        if RE_DIRETOR.match(sigla):
            out.append((reg, re.sub(r"\s+", "", proc), sigla))
    return out


# menções "Dir./Pres. Nome Sobrenome" — a sigla do diretor é D+inicial(nome)+inicial(sobrenome)
RE_MENCAO = re.compile(
    r"\b(Presidente|Pres\.|Diretora|Diretor|Dir\.)\s+"
    r"([A-ZÀ-Ú][a-zà-úâêôîûãõäëïöü]+"
    r"(?:\s+(?:d[aeo]s?\s+)?[A-ZÀ-Ú][a-zà-úâêôîûãõäëïöü]+){1,4})")


def _ini(tok):
    return unicodedata_ascii(tok[:1]).upper()


def unicodedata_ascii(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def pessoas_mencionadas(texto):
    """(sigla, nome_curto, papel) das mencoes 'Dir./Pres. Nome'.
    Diretor Otto Lobo -> ('DOL','Otto Lobo','diretor'); Presidente -> sigla 'PTE'.
    """
    out = []
    for papel, nome in RE_MENCAO.findall(texto):
        toks = [t for t in nome.split() if t[:1].isupper()]
        if len(toks) < 2:
            continue
        curto = f"{toks[0]} {toks[-1]}"
        if re.match(r"Pres", papel, re.I):
            out.append(("PTE", curto, "presidente"))
        else:
            sigla = "D" + _ini(toks[0]) + _ini(toks[-1])
            out.append((sigla, curto, "diretor"))
    return out


def construir():
    con = conectar()
    links = mapa_links()
    hoje = dt.date.today().isoformat()
    con.execute("DELETE FROM relatores")  # reconstroi o mapa de relatoria por completo
    con.execute("DELETE FROM siglas")
    con.execute("DELETE FROM impedimentos")
    n_arq = n_del = n_rel = 0
    for txt in sorted(glob.glob(os.path.join(TXT_DIR, "*.txt"))):
        base = os.path.splitext(os.path.basename(txt))[0]
        texto = open(txt, encoding="utf-8").read()
        inf_num, r_tipo, data, data_iso = meta_informativo(texto)
        link = links.get(base, "")
        itens = fatiar(texto)
        n_arq += 1
        # mapa sigla<->nome (para cruzar com "processos a julgar", ciente do Otto PTE/DOL)
        for sigla, nome, papel in pessoas_mencionadas(texto):
            con.execute("INSERT OR IGNORE INTO siglas(sigla,nome,papel,data_iso,arquivo)"
                        " VALUES(?,?,?,?,?)", (sigla, nome, papel, data_iso, base))
        # eventos de relatoria: sorteios/redistribuicoes (topo do informativo)
        for reg, proc, sigla in eventos_sorteio(texto):
            pn = norm_proc(proc)
            if not pn:
                continue
            con.execute("""INSERT OR IGNORE INTO relatores(
                proc_norm,processo,reg,relator,evento,arquivo,inf_numero,data,data_iso)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (pn, proc, reg, sigla, "sorteio/redistribuicao", base, inf_num,
                 data, data_iso))
            n_rel += con.total_changes and 1 or 0
        for item, corpo in itens:
            tipo, assunto, processo, reg, relator, decisao = extrair_campos(corpo)
            natureza = natureza_de(tipo, relator)
            pn = norm_proc(processo)
            # julgamento/anulacao relatado por diretor tambem e' evento de relatoria
            sigla = (relator or "").split("/")[0].strip().upper()
            if pn and RE_DIRETOR.match(sigla):
                con.execute("""INSERT OR IGNORE INTO relatores(
                    proc_norm,processo,reg,relator,evento,arquivo,inf_numero,
                    data,data_iso) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (pn, processo, reg, sigla, tipo, base, inf_num, data, data_iso))
            # impedimentos / suspeicoes declarados por diretor nesta deliberacao
            if pn:
                for rot, siglas_txt in RE_IMPED.findall(corpo):
                    kind = "Suspeição" if re.match(r"Suspei", rot, re.I) else "Impedimento"
                    for sg in re.findall(r"\b(PTE|D[A-Z]{2,3})\b", siglas_txt):
                        con.execute("""INSERT OR IGNORE INTO impedimentos(proc_norm,
                            processo,sigla,tipo,arquivo,inf_numero,assunto,data,data_iso)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                            (pn, processo, sg, kind, base, inf_num, assunto, data, data_iso))
            # preserva IA existente
            row = con.execute("SELECT resumo,palavras_chave,partes,resultado,ai_feito "
                              "FROM deliberacoes WHERE arquivo=? AND item=?",
                              (base, item)).fetchone()
            res, pc, pa, rs, ai = row if row else ("", "", "", "", 0)
            con.execute("""INSERT INTO deliberacoes(
                arquivo,inf_numero,reuniao_tipo,data,data_iso,item,tipo,assunto,
                processo,reg,relator,decisao,texto,link,natureza,proc_norm,
                resumo,palavras_chave,partes,resultado,ai_feito,coletado_em)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(arquivo,item) DO UPDATE SET
                inf_numero=excluded.inf_numero, reuniao_tipo=excluded.reuniao_tipo,
                data=excluded.data, data_iso=excluded.data_iso, tipo=excluded.tipo,
                assunto=excluded.assunto, processo=excluded.processo, reg=excluded.reg,
                relator=excluded.relator, decisao=excluded.decisao, texto=excluded.texto,
                link=excluded.link, natureza=excluded.natureza,
                proc_norm=excluded.proc_norm""",
                (base, inf_num, r_tipo, data, data_iso, item, tipo, assunto,
                 processo, reg, relator, decisao, corpo, link, natureza, pn,
                 res, pc, pa, rs, ai, hoje))
            n_del += 1
    con.commit()
    tot = con.execute("SELECT COUNT(*) FROM deliberacoes").fetchone()[0]
    comai = con.execute("SELECT COUNT(*) FROM deliberacoes WHERE ai_feito=1").fetchone()[0]
    n_rel_tot = con.execute("SELECT COUNT(*) FROM relatores").fetchone()[0]
    n_proc_rel = con.execute("SELECT COUNT(DISTINCT proc_norm) FROM relatores").fetchone()[0]
    print("[informativos_seed] natureza:")
    for nat, c in con.execute("SELECT natureza,COUNT(*) FROM deliberacoes "
                              "GROUP BY natureza ORDER BY 2 DESC"):
        print(f"  {c:5d}  {nat}")
    con.close()
    print(f"[informativos_seed] {n_arq} informativos | {n_del} deliberacoes | "
          f"total base {tot} | com IA {comai}")
    print(f"[informativos_seed] relatoria: {n_rel_tot} eventos | "
          f"{n_proc_rel} processos com relator identificado")


def pendentes(n=15):
    con = conectar()
    for r in con.execute("SELECT id,arquivo,item,data,tipo,substr(texto,1,120) "
                         "FROM deliberacoes WHERE ai_feito=0 "
                         "ORDER BY data_iso DESC, item LIMIT ?", (n,)):
        print(r)
    con.close()


def aplicar_ia(json_path):
    """JSON: { "<id>": {"resumo","palavras_chave","partes","resultado"} }"""
    dados = json.load(open(json_path, encoding="utf-8"))
    con = conectar()
    for _id, c in dados.items():
        con.execute("""UPDATE deliberacoes SET resumo=?,palavras_chave=?,partes=?,
                       resultado=?,ai_feito=1 WHERE id=?""",
                    (c.get("resumo", ""), c.get("palavras_chave", ""),
                     c.get("partes", ""), c.get("resultado", ""), int(_id)))
    con.commit()
    con.close()
    print(f"[informativos_seed] IA aplicada a {len(dados)} deliberacao(oes).")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "pendentes":
        pendentes(int(sys.argv[2]) if len(sys.argv) > 2 else 15)
    elif len(sys.argv) > 2 and sys.argv[1] == "aplicar_ia":
        aplicar_ia(sys.argv[2])
    else:
        construir()
