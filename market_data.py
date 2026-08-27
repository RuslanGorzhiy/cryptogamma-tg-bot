"""
Объединяет дополнительные (не cryptogamma.io) источники в один контекст:
    - технический анализ по Binance (RSI, EMA20/50)
    - funding rate и open interest по бессрочным фьючерсам Binance
    - Crypto Fear & Greed Index

Каждый источник независим: если один недоступен, остальные всё равно
используются — MarketContext просто имеет пустые поля там, где данных
нет, и signals.py аккуратно это учитывает.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from binance_client import fetch_funding_rate, fetch_open_interest, fetch_technicals
from feargreed_client import fetch_fear_greed


@dataclass
class MarketContext:
    binance_price: Optional[float] = None
    rsi14: Optional[float] = None
    ema20: Optional[float] = None
    ema50: Optional[float] = None
    funding_rate_pct: Optional[float] = None
    open_interest: Optional[float] = None
    fear_greed_value: Optional[int] = None
    fear_greed_class: Optional[str] = None


def fetch_market_context(
    asset: str, fear_greed: Optional[Tuple[Optional[int], Optional[str]]] = None
) -> MarketContext:
    """Собирает контекст по одному активу.

    `fear_greed` можно передать заранее полученным (значение, значение,
    классификация) кортежем, чтобы не дёргать API общего индекса
    отдельно для BTC и ETH в одном прогоне — см. alert.py.
    """
    tech = fetch_technicals(asset)
    funding = fetch_funding_rate(asset)
    oi = fetch_open_interest(asset)

    if fear_greed is None:
        fear_greed = fetch_fear_greed()
    fg_value, fg_class = fear_greed

    return MarketContext(
        binance_price=tech.price,
        rsi14=tech.rsi14,
        ema20=tech.ema20,
        ema50=tech.ema50,
        funding_rate_pct=funding,
        open_interest=oi,
        fear_greed_value=fg_value,
        fear_greed_class=fg_class,
    )
