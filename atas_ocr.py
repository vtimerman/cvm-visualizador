#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atas_ocr.py — OCR das Atas do CGE cujo texto extraido ficou ILEGIVEL
(PDF escaneado "Print To PDF" ou com codificacao de fonte corrompida).

Renderiza cada pagina com pdftoppm (poppler) e roda RapidOCR; se o resultado
for legivel, sobrescreve atas_txt/<base>.txt. Depois rode atas_seed.py para
reconstruir os metadados e (re)parametrizar por IA.

Uso:
  python atas_ocr.py            # OCR de todas as atas com txt ilegivel
  python atas_ocr.py <base...>  # forca OCR das bases dadas
"""
import os
import re
import sys
import glob
import subprocess
import tempfile

DIR = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(DIR, "atas_pdf")
TXT_DIR = os.path.join(DIR, "atas_txt")
DPI = int(os.environ.get("OCR_DPI", "200"))


def _pdftoppm():
    for p in os.environ.get("PATH", "").split(os.pathsep):
        exe = os.path.join(p, "pdftoppm.exe")
        if os.path.exists(exe):
            return exe
    pat = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"
                             r"\oschwartz10612.Poppler_*\poppler-*"
                             r"\Library\bin\pdftoppm.exe")
    hits = glob.glob(pat)
    return hits[0] if hits else "pdftoppm"


def legivel(txt):
    """True se o texto parece conteudo real (nao lixo/vazio)."""
    t = (txt or "").strip()
    if len(t) < 200:
        return False
    bons = sum(1 for c in t if c.isalnum() or c.isspace() or c in ",.;:()/%-–ºª")
    return bons / max(len(t), 1) > 0.75


def ocr_pdf(pdf_path, ocr):
    with tempfile.TemporaryDirectory() as tmp:
        pref = os.path.join(tmp, "pg")
        subprocess.run([_pdftoppm(), "-png", "-r", str(DPI), pdf_path, pref],
                       check=True, capture_output=True)
        partes = []
        for png in sorted(glob.glob(pref + "*.png")):
            res, _ = ocr(png)
            if res:
                partes.append(" ".join(linha[1] for linha in res))
        return "\n".join(partes).strip()


def main(bases=None):
    from rapidocr_onnxruntime import RapidOCR
    ocr = RapidOCR()
    alvos = []
    for pdf in sorted(glob.glob(os.path.join(PDF_DIR, "*.pdf"))):
        base = os.path.splitext(os.path.basename(pdf))[0]
        if bases:
            if base in bases:
                alvos.append((base, pdf))
            continue
        txtp = os.path.join(TXT_DIR, base + ".txt")
        atual = open(txtp, encoding="utf-8").read() if os.path.exists(txtp) else ""
        if not legivel(atual):
            alvos.append((base, pdf))
    print(f"[atas_ocr] {len(alvos)} ata(s) a processar por OCR.")
    ok = falhas = 0
    for base, pdf in alvos:
        try:
            texto = ocr_pdf(pdf, ocr)
        except Exception as e:
            print(f"  ! erro no OCR de {base}: {e}", file=sys.stderr)
            falhas += 1
            continue
        if legivel(texto):
            with open(os.path.join(TXT_DIR, base + ".txt"), "w",
                      encoding="utf-8") as f:
                f.write(texto)
            ok += 1
            print(f"  OK {base} ({len(texto)} chars)")
        else:
            falhas += 1
            print(f"  ! OCR ilegivel para {base} ({len(texto)} chars)")
    print(f"[atas_ocr] concluido: {ok} com texto, {falhas} falha(s).")


if __name__ == "__main__":
    main(sys.argv[1:] or None)
