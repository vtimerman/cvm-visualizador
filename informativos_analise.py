#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Analise exploratoria: quantas deliberacoes ha no total e quais os tipos."""
import os, re, glob
from collections import Counter

DIR = os.path.dirname(os.path.abspath(__file__))
TXT_DIR = os.path.join(DIR, "informativos_txt")

RODAPE = re.compile(r"(caráter meramente informativo|COMISSÃO DE VALORES|www\.cvm|"
                    r"Rua Sete de Setembro|Rua Cincinato|SCN Q\.02)", re.I)


def limpar(texto):
    return "\n".join(l for l in texto.splitlines() if not RODAPE.search(l))


def itens_do(texto):
    """Divide em deliberacoes pelo marcador 'N.' no inicio de linha apos DELIBERACOES."""
    t = limpar(texto)
    m = re.search(r"DELIBERA[ÇC][ÕO]ES\s*:?", t, re.I)
    corpo = t[m.end():] if m else t
    # marcadores: linha que e' so 'N.' ou comeca com 'N. '
    partes = re.split(r"\n\s*(\d{1,2})\.\s*\n?", corpo)
    itens = []
    # partes = [pre, num1, texto1, num2, texto2, ...]
    for i in range(1, len(partes) - 1, 2):
        num = partes[i]
        corpo_item = partes[i + 1]
        itens.append((num, corpo_item))
    return itens


def titulo_e_tipo(corpo_item):
    # o titulo sao as primeiras linhas em MAIUSCULAS ate achar 'Reg.'/'Relator'/'Proc'
    linhas = [l.strip() for l in corpo_item.splitlines() if l.strip()]
    titulo_ls = []
    for l in linhas:
        if re.match(r"(Reg\.|Relator|Relatora|Proponente|Impedimento|Suspei|Por unanimidade|Por maioria|O Colegiado|Trata-se)", l, re.I):
            break
        titulo_ls.append(l)
        if len(" ".join(titulo_ls)) > 200:
            break
    titulo = re.sub(r"\s+", " ", " ".join(titulo_ls)).strip(" -–")
    # tipo = primeiras palavras significativas
    tipo = titulo.split(" – ")[0].split(" - ")[0][:60]
    return titulo, tipo


tot_itens = 0
tot_arq = 0
tipos = Counter()
exemplos = []
for txt in sorted(glob.glob(os.path.join(TXT_DIR, "*.txt"))):
    tot_arq += 1
    texto = open(txt, encoding="utf-8").read()
    itens = itens_do(texto)
    tot_itens += len(itens)
    for num, corpo in itens:
        titulo, tipo = titulo_e_tipo(corpo)
        # normaliza tipo pela primeira palavra-chave
        chave = "OUTROS"
        up = titulo.upper()
        for k in ["RECURSO", "TERMO DE COMPROMISSO", "PEDIDO DE ANULA", "CONSULTA",
                  "JULGAMENTO", "PAS ", "PROCESSO ADMINISTRATIVO SANCIONADOR",
                  "MINUTA", "EDITAL", "AUDIÊNCIA PÚBLICA", "PLANO ANUAL", "PAINT",
                  "PROPOSTA", "REGISTRO", "OFERTA PÚBLICA", "RECLAMA"]:
            if k in up:
                chave = k
                break
        tipos[chave] += 1

print(f"arquivos: {tot_arq} | deliberacoes totais: {tot_itens} | media/arq: {tot_itens/tot_arq:.1f}")
print("--- tipos (heuristica bruta) ---")
for k, v in tipos.most_common(20):
    print(f"{v:5d}  {k}")
