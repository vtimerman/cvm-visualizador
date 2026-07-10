#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notificar.py — canal unico de notificacao do Motumbo CVM.

Backend atual: Telegram (API oficial, confiavel; aceita UTF-8, sem gambiarra de
ASCII). Todas as tarefas agendadas devem chamar este modulo, para trocar de canal
num lugar so no futuro.

Credenciais (NUNCA no git): lidas de variaveis de ambiente
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
ou, se ausentes, do arquivo local `notificar.local.json` ao lado deste script:
  {"telegram_token": "...", "telegram_chat_id": "..."}
(esse arquivo esta no .gitignore).

Uso:
  python notificar.py "sua mensagem"        # envia
  python notificar.py --teste               # envia um ping de teste
  from notificar import enviar; enviar("texto")
"""
import os
import sys
import json

import requests

DIR = os.path.dirname(os.path.abspath(__file__))
CONF_LOCAL = os.path.join(DIR, "notificar.local.json")


def _cred():
    tok = os.environ.get("TELEGRAM_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (tok and chat) and os.path.exists(CONF_LOCAL):
        try:
            d = json.load(open(CONF_LOCAL, encoding="utf-8"))
            tok = tok or d.get("telegram_token", "")
            chat = chat or d.get("telegram_chat_id", "")
        except (ValueError, OSError):
            pass
    return tok, chat


def enviar(texto: str) -> bool:
    """Envia uma notificacao. Retorna True se entregue; False se sem credenciais
    ou erro (nunca levanta excecao, para nao derrubar as tarefas)."""
    tok, chat = _cred()
    if not (tok and chat):
        print("[notificar] sem credenciais (TELEGRAM_TOKEN/CHAT_ID) — ignorado.",
              file=sys.stderr)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{tok}/sendMessage",
            json={"chat_id": chat, "text": texto,
                  "disable_web_page_preview": True},
            timeout=30)
        if r.status_code != 200:
            print(f"[notificar] falha Telegram {r.status_code}: {r.text[:200]}",
                  file=sys.stderr)
            return False
        return True
    except requests.RequestException as e:
        print(f"[notificar] erro de rede: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    msg = ("Motumbo CVM: teste de notificacao (Telegram) OK."
           if sys.argv[1] == "--teste" else " ".join(sys.argv[1:]))
    ok = enviar(msg)
    print("enviado" if ok else "nao enviado")
    sys.exit(0 if ok else 2)
