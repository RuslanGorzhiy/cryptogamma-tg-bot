"""
Клиент публичного (без ключа) Crypto Fear & Greed Index —
https://alternative.me/crypto/fear-and-greed-index/

Индекс общий для всего крипторынка (не по конкретному активу), поэтому
его достаточно запросить один раз за прогон и переиспользовать для
BTC и ETH.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

API_URL = "https://api.alternative.me/fng/"
USER_AGENT = "cryptogamma-tg-bot/1.0"
TIMEOUT = 10


def fetch_fear_greed() -> Tuple[Optional[int], Optional[str]]:
    """Возвращает (значение 0-100, классификация) или (None, None) при сбое."""
    try:
        resp = requests.get(
            API_URL,
            params={"limit": 1, "format": "json"},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        entry = payload.get("data", [{}])[0]
        value = int(entry["value"])
        classification = entry.get("value_classification")
        return value, classification
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        logger.warning("Не удалось получить Fear & Greed Index: %s", exc)
        return None, None
