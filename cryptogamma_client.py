"""
Клиент для публичного JSON snapshot API cryptogamma.io

Публичный эндпоинт (без токена, без авторизации):
    https://cryptogamma.io/api/public/snapshot?asset=BTC
    https://cryptogamma.io/api/public/snapshot?asset=ETH

Данные обновляются на стороне cryptogamma.io примерно раз в 15 минут
(источник — публичное Deribit options API).

Модуль написан защитно: структура ответа может немного отличаться /
меняться со временем, поэтому используется поиск значений по
нескольким возможным именам ключей (см. _pick).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://cryptogamma.io/api/public/snapshot"
DEFAULT_TIMEOUT = 15
USER_AGENT = "cryptogamma-tg-bot/1.0 (+https://github.com/)"

SUPPORTED_ASSETS = ("BTC", "ETH")


class CryptoGammaError(RuntimeError):
    """Ошибка при получении или разборе данных cryptogamma.io."""


def _pick(data: dict, *keys: str, default: Any = None) -> Any:
    """Достаёт первое найденное значение по списку возможных ключей.

    Поддерживает вложенные пути через точку, например "gex.net".
    """
    for key in keys:
        cur: Any = data
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur is not None:
            return cur
    return default


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


@dataclass
class GammaSnapshot:
    """Нормализованный снимок метрик для одного актива."""

    asset: str
    price: Optional[float] = None
    net_gamma: Optional[float] = None
    call_gamma: Optional[float] = None
    put_gamma: Optional[float] = None
    dealer_bias: Optional[str] = None
    call_weighted_pct: Optional[float] = None
    support: Optional[float] = None
    resistance: Optional[float] = None
    breakout: Optional[float] = None
    iv_atm: Optional[float] = None
    realized_vol: Optional[float] = None
    vol_premium: Optional[float] = None
    call_flow_24h: Optional[float] = None
    put_flow_24h: Optional[float] = None
    put_call_ratio: Optional[float] = None
    delta_hedging: Optional[str] = None
    squeeze_risk: Optional[str] = None
    pin_risk: Optional[str] = None
    updated_at: Optional[str] = None
    raw: dict = None

    @classmethod
    def from_api(cls, asset: str, data: dict) -> "GammaSnapshot":
        # Реальные данные могут лежать прямо в корне ответа или во
        # вложенном объекте data/snapshot/result.
        root = data
        for wrapper in ("data", "snapshot", "result"):
            if isinstance(data.get(wrapper), dict):
                root = data[wrapper]
                break

        return cls(
            asset=asset,
            price=_to_float(_pick(root, "price", "spot", "underlyingPrice", "spotPrice")),
            net_gamma=_to_float(_pick(root, "netGamma", "net_gex", "netGex", "gex.net")),
            call_gamma=_to_float(_pick(root, "callGamma", "call_gex", "gex.call")),
            put_gamma=_to_float(_pick(root, "putGamma", "put_gex", "gex.put")),
            dealer_bias=_pick(root, "dealerBias", "bias", "dealer_bias"),
            call_weighted_pct=_to_float(
                _pick(root, "callWeightedPct", "callWeighted", "call_weighted_pct")
            ),
            support=_to_float(_pick(root, "support", "squeeze.support", "squeezeLevels.support")),
            resistance=_to_float(
                _pick(root, "resistance", "squeeze.resistance", "squeezeLevels.resistance")
            ),
            breakout=_to_float(_pick(root, "breakout", "squeeze.breakout", "squeezeLevels.breakout")),
            iv_atm=_to_float(_pick(root, "ivAtm", "impliedVol", "iv_atm", "vol.iv")),
            realized_vol=_to_float(_pick(root, "realizedVol", "rv", "vol.realized")),
            vol_premium=_to_float(_pick(root, "volPremium", "premium", "vol.premium")),
            call_flow_24h=_to_float(_pick(root, "callFlow24h", "callFlow", "flow.call")),
            put_flow_24h=_to_float(_pick(root, "putFlow24h", "putFlow", "flow.put")),
            put_call_ratio=_to_float(_pick(root, "putCallRatio", "cpRatio", "pcRatio")),
            delta_hedging=_pick(root, "deltaHedging", "risk.deltaHedging"),
            squeeze_risk=_pick(root, "squeezeRisk", "risk.squeezeRisk"),
            pin_risk=_pick(root, "pinRisk", "risk.pinRisk"),
            updated_at=_pick(root, "updatedAt", "timestamp", "asOf", "lastUpdated"),
            raw=data,
        )


def fetch_snapshot(asset: str, timeout: int = DEFAULT_TIMEOUT) -> GammaSnapshot:
    """Запрашивает снимок метрик для BTC или ETH.

    Поднимает CryptoGammaError при сетевой ошибке, HTTP-ошибке или
    некорректном JSON.
    """
    asset = asset.upper().strip()
    if asset not in SUPPORTED_ASSETS:
        raise CryptoGammaError(f"Неподдерживаемый актив: {asset}. Используйте BTC или ETH.")

    try:
        resp = requests.get(
            BASE_URL,
            params={"asset": asset},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        logger.exception("Сетевая ошибка при запросе cryptogamma.io для %s", asset)
        raise CryptoGammaError(f"Не удалось получить данные для {asset}: {exc}") from exc
    except ValueError as exc:  # JSON decode error
        logger.exception("Некорректный JSON от cryptogamma.io для %s", asset)
        raise CryptoGammaError(f"Некорректный ответ API для {asset}") from exc

    if not isinstance(data, dict):
        raise CryptoGammaError(f"Неожиданный формат ответа API для {asset}")

    return GammaSnapshot.from_api(asset, data)
