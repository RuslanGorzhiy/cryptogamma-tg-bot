"""
Клиент публичных (без ключей) API Binance:
    - Spot klines (свечи) — для расчёта RSI и EMA
    - Futures premiumIndex — funding rate по перпетуалам
    - Futures openInterest — открытый интерес по перпетуалам

Все функции возвращают None на отдельных полях при сбое сети/парсинга,
а не бросают исключение наружу — эти данные дополняют сигнал
cryptogamma.io, и их временная недоступность не должна ронять весь бот.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
USER_AGENT = "cryptogamma-tg-bot/1.0"
TIMEOUT = 10

SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}


def _symbol(asset: str) -> str:
    sym = SYMBOLS.get(asset.upper())
    if not sym:
        raise ValueError(f"Неподдерживаемый актив для Binance: {asset}")
    return sym


def compute_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """RSI по Уайлдеру. Возвращает None, если данных недостаточно."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_ema(closes: List[float], period: int) -> Optional[float]:
    """EMA с сидированием простой средней по первым `period` значениям."""
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


@dataclass
class TechnicalSnapshot:
    price: Optional[float] = None
    rsi14: Optional[float] = None
    ema20: Optional[float] = None
    ema50: Optional[float] = None


def fetch_technicals(asset: str, interval: str = "1h", limit: int = 200) -> TechnicalSnapshot:
    """Тянет часовые свечи с Binance Spot и считает RSI(14)/EMA(20)/EMA(50).

    При любой ошибке возвращает TechnicalSnapshot с пустыми полями и
    пишет предупреждение в лог — вызывающий код должен относиться к
    этому как к «данных нет», а не как к фатальной ошибке.
    """
    try:
        symbol = _symbol(asset)
        resp = requests.get(
            f"{SPOT_BASE}/api/v3/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        raw = resp.json()
        closes = [float(candle[4]) for candle in raw]
        if not closes:
            return TechnicalSnapshot()
        return TechnicalSnapshot(
            price=closes[-1],
            rsi14=compute_rsi(closes, 14),
            ema20=compute_ema(closes, 20),
            ema50=compute_ema(closes, 50),
        )
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        logger.warning("Не удалось получить технические данные Binance для %s: %s", asset, exc)
        return TechnicalSnapshot()


def fetch_funding_rate(asset: str) -> Optional[float]:
    """Funding rate по бессрочному фьючерсу, в процентах (например 0.01 = 0.01%)."""
    try:
        symbol = _symbol(asset)
        resp = requests.get(
            f"{FUTURES_BASE}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        rate = data.get("lastFundingRate")
        return float(rate) * 100 if rate is not None else None
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Не удалось получить funding rate Binance для %s: %s", asset, exc)
        return None


def fetch_open_interest(asset: str) -> Optional[float]:
    """Текущий открытый интерес по бессрочному фьючерсу (в контрактах базового актива)."""
    try:
        symbol = _symbol(asset)
        resp = requests.get(
            f"{FUTURES_BASE}/fapi/v1/openInterest",
            params={"symbol": symbol},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        oi = data.get("openInterest")
        return float(oi) if oi is not None else None
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Не удалось получить open interest Binance для %s: %s", asset, exc)
        return None
