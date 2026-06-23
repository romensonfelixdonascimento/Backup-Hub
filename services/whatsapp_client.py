import logging
import os
import requests
from dotenv import load_dotenv

load_dotenv()

WAHA_ENABLED = os.getenv('WAHA_ENABLED', 'false').lower() == 'true'
WAHA_BASE_URL = os.getenv('WAHA_BASE_URL', '')
WAHA_SESSION = os.getenv('WAHA_SESSION', '')
WAHA_TARGET_NUMBER = os.getenv('WAHA_TARGET_NUMBER', '')
WAHA_HEADERS = {
    "Content-Type": "application/json",
    "X-Api-Key": os.getenv('WAHA_API_KEY', '')
}

def send_whatsapp_notification(message):
    if not WAHA_ENABLED:
        logging.info("Notificações de WhatsApp desativadas.")
        return

    num_limpo = WAHA_TARGET_NUMBER.strip()
    if not num_limpo:
        logging.warning("WAHA_TARGET_NUMBER não está configurado.")
        return

    if not num_limpo.startswith("55"):
        num_limpo = f"55{num_limpo}"

    check_url = f"{WAHA_BASE_URL}/contacts/check-exists"
    params_check = {"session": WAHA_SESSION, "phone": num_limpo}

    try:
        check_response = requests.get(check_url, params=params_check, headers=WAHA_HEADERS, timeout=15)
        if check_response.status_code == 200:
            dados = check_response.json()
            if dados.get("numberExists") is True and dados.get("chatId"):
                chat_id = dados.get("chatId")
                payload_send = {"session": WAHA_SESSION, "chatId": chat_id, "text": message}
                requests.post(f"{WAHA_BASE_URL}/sendText", json=payload_send, headers=WAHA_HEADERS, timeout=30)
    except Exception as e:
        logging.error(f"Erro no envio WAHA: {e}")