"""
Разовый скрипт для запуска по расписанию (GitHub Actions cron).

В отличие от bot.py (постоянный long-polling процесс, требующий
всегда работающего сервера), этот скрипт:
    1. один раз запрашивает снимки по BTC и ETH,
    2. отправляет сообщение в заданный Telegram chat_id через
       Bot API sendMessage,
    3. завершает работу.

Нужные переменные окружения (задаются как GitHub Secrets):
    TELEGRAM_BOT_TOKEN — токен бота от @BotFather
    TELEGRAM_CHAT_ID   — id чата/канала, куда слать алерты
                         (узнать: написать боту и открыть
                         https://api.telegram.org/bot<token>/getUpdates)

Опционально:
    ALERT_ASSETS — список активов через запятую, по умолчанию "BTC,ETH"

Между запусками скрипт хранит короткий снимок ключевых метрик в
state/last_snapshot.json (см. state_store.py) — это нужно, чтобы
считать динамику (разворот dealer bias, рост/падение Net GEX и
C/P ratio), а не оценивать сигнал по одной изолированной точке. В
GitHub Actions файл нужно закоммитить обратно в репозиторий — это
делает последний шаг в .github/workflows/alert.yml.
"""

from __future__ import annotations

import logging
import os
import sys

import requests

from cryptogamma_client import CryptoGammaError, fetch_snapshot
from signals import format_snapshot_message, trackable_fields
from state_store import load_previous, save_previous

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("cryptogamma-alert")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    resp = requests.post(
        TELEGRAM_API.format(token=token),
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if not resp.ok:
        logger.error("Telegram API вернул ошибку: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.error("Заданы не все обязательные переменные: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
        return 1

    assets = [a.strip().upper() for a in os.environ.get("ALERT_ASSETS", "BTC,ETH").split(",") if a.strip()]

    exit_code = 0
    for asset in assets:
        try:
            snap = fetch_snapshot(asset)
            previous = load_previous(asset)
            text = format_snapshot_message(snap, previous=previous)
            # Сохраняем состояние сразу после успешного получения данных —
            # даже если отправка в Telegram ниже не удастся, динамика
            # между снимками не потеряется на следующем запуске.
            save_previous(asset, trackable_fields(snap))
            send_telegram_message(token, chat_id, text)
            logger.info("Отправлен алерт по %s", asset)
        except CryptoGammaError as exc:
            logger.error("Ошибка получения данных по %s: %s", asset, exc)
            exit_code = 1
        except requests.RequestException as exc:
            logger.error("Ошибка отправки в Telegram для %s: %s", asset, exc)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
