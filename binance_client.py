"""
Клиент публичных (без ключей) API Binance:
    - Spot klines (свечи) — для расчёта RSI и EMA
    - Futures premiumIndex — funding rate по перпетуалам
    - Futures openInterest — открытый интерес по перпетуалам
    - Futures aggTrades — детекция крупных («китовых») сделок
    - Futures 24hr ticker — объём торгов за 24ч (для OI/Volume ratio)

Все функции возвращают None (или пустой dataclass) на отдельных полях
при сбое сети/парсинга, а не бросают исключение наружу — эти данные
дополняют сигнал cryptogamma.io, и их временная недоступность не должна
ронять весь бот.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"
USER_AGENT = "cryptogamma-tg-bot/1.0"
TIMEOUT = 10

SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}

# Порог notional (в USD) одной сделки, чтобы считать её «китовой».
# Публичный API Binance не сообщает тип счёта контрагента — это чисто
# эвристический порог по размеру сделки, а не подтверждённая
# принадлежность институциональному игроку. Можно переопределить через
# переменные окружения WHALE_THRESHOLD_BTC_USD / WHALE_THRESHOLD_ETH_USD.
_DEFAULT_WHALE_THRESHOLD_USD = {"BTC": 500_000.0, "ETH": 200_000.0}


def _whale_threshold(asset: str) -> float:
    asset = asset.upper()
    env_key = f"WHALE_THRESHOLD_{asset}_USD"
    override = os.environ.get(env_key)
    if override:
        try:
            return float(override)
        except ValueError:
            logger.warning("Некорректное значение %s=%s, использую значение по умолчанию", env_key, override)
    return _DEFAULT_WHALE_THRESHOLD_USD.get(asset, 250_000.0)


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


def fetch_futures_volume_24h(asset: str) -> Optional[float]:
    """Объём торгов по бессрочному фьючерсу за 24ч, в USDT (quoteVolume)."""
    try:
        symbol = _symbol(asset)
        resp = requests.get(
            f"{FUTURES_BASE}/fapi/v1/ticker/24hr",
            params={"symbol": symbol},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        vol = data.get("quoteVolume")
        return float(vol) if vol is not None else None
    except (requests.RequestException, ValueError, KeyError) as exc:
        logger.warning("Не удалось получить объём Binance Futures для %s: %s", asset, exc)
        return None


@dataclass
class WhaleActivity:
    """Сводка по крупным сделкам за выборку последних aggTrades.

    Это эвристический прокси-показатель «институционального» интереса:
    публичный Binance API не сообщает тип контрагента (розница/фонд/
    маркет-мейкер), только размер сделки. Крупная сделка — признак
    крупного капитала, но не подтверждённый факт институционального
    происхождения.
    """

    count: int = 0
    buy_notional: float = 0.0
    sell_notional: float = 0.0
    sample_size: int = 0
    threshold_usd: float = 0.0


def fetch_whale_trades(asset: str, limit: int = 1000) -> WhaleActivity:
    """Сканирует последние `limit` сделок по бессрочному фьючерсу и находит
    те, чей notional (цена × количество) превышает порог для актива.

    `limit=1000` — это количество последних сделок, а не фиксированный
    временной интервал: при высокой активности рынка окно может
    покрывать всего несколько минут, при низкой — куда больше.
    """
    try:
        symbol = _symbol(asset)
        threshold = _whale_threshold(asset)
        resp = requests.get(
            f"{FUTURES_BASE}/fapi/v1/aggTrades",
            params={"symbol": symbol, "limit": limit},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        trades = resp.json()

        buy_notional = 0.0
        sell_notional = 0.0
        count = 0
        for t in trades:
            notional = float(t["p"]) * float(t["q"])
            if notional < threshold:
                continue
            count += 1
            # m=True: покупатель был мейкером => сделку инициировал
            # продавец (агрессивная продажа). m=False: инициатор — покупатель.
            if t.get("m"):
                sell_notional += notional
            else:
                buy_notional += notional

        return WhaleActivity(
            count=count,
            buy_notional=buy_notional,
            sell_notional=sell_notional,
            sample_size=len(trades),
            threshold_usd=threshold,
        )
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        logger.warning("Не удалось получить крупные сделки Binance для %s: %s", asset, exc)
        return WhaleActivity()
