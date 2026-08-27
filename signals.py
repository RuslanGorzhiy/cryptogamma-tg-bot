"""
Формирование читаемого текста и торгового сигнала из GammaSnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cryptogamma_client import GammaSnapshot
from market_data import MarketContext


def _fmt_num(value, suffix: str = "", show_sign: bool = False) -> str:
    if value is None:
        return "н/д"
    sign = "+" if (show_sign and value > 0) else ""
    if abs(value) >= 1_000_000:
        return f"{sign}{value / 1_000_000:.2f}M{suffix}"
    if abs(value) >= 1_000:
        return f"{sign}{value / 1_000:.2f}K{suffix}"
    return f"{sign}{value:.2f}{suffix}"


def _fmt_price(value) -> str:
    return "н/д" if value is None else f"${value:,.2f}"


def _fmt_pct(value) -> str:
    return "н/д" if value is None else f"{value:.1f}%"


def derive_bias_emoji(snap: GammaSnapshot) -> str:
    bias = (snap.dealer_bias or "").upper()
    if "BULL" in bias:
        return "🟢"
    if "BEAR" in bias:
        return "🔴"
    if snap.net_gamma is not None:
        return "🟢" if snap.net_gamma > 0 else "🔴"
    return "⚪️"


def derive_squeeze_note(snap: GammaSnapshot) -> str:
    """Простая эвристика: цена рядом с уровнем поддержки/сопротивления/пробоя."""
    if snap.price is None:
        return ""
    notes = []
    threshold = 0.003  # 0.3%
    for label, level in (
        ("поддержки", snap.support),
        ("сопротивления", snap.resistance),
        ("пробоя", snap.breakout),
    ):
        if level and abs(snap.price - level) / level <= threshold:
            notes.append(f"⚠️ цена рядом с уровнем {label} (${level:,.0f})")
    return "\n".join(notes)


@dataclass
class OverallSignal:
    """Итоговый составной сигнал: направление + сила + уверенность + обоснование."""

    label: str          # "БЫЧИЙ" / "МЕДВЕЖИЙ" / "НЕЙТРАЛЬНЫЙ"
    emoji: str           # 🟢 / 🔴 / ⚪️
    strength: str        # "сильный" / "умеренный" / "слабый"
    confidence: str       # "высокая" / "средняя" / "низкая" / "н/д"
    confidence_note: str  # короткое пояснение, откуда взялась уверенность
    score: float          # итоговый взвешенный балл, для отладки/сортировки
    reasons: List[str] = field(default_factory=list)


def derive_confidence(snap: GammaSnapshot) -> Tuple[str, str]:
    """Оценивает уверенность в направлении сигнала на основе IV/RV.

    Vol premium (IV минус RV) сам по себе не задаёт направление, но
    задаёт контекст: если рынок закладывает в цену опционов заметно
    больше волатильности, чем реализуется по факту, это означает, что
    участники ждут заметного движения/новостей — и любой направленный
    сигнал в такой момент менее надёжен, потому что дилерский хедж и
    squeeze-уровни легче ломаются резким импульсом.

    Если явного vol_premium нет, но есть IV и RV по отдельности — премия
    считается как их разница.
    """
    premium = snap.vol_premium
    if premium is None and snap.iv_atm is not None and snap.realized_vol is not None:
        premium = snap.iv_atm - snap.realized_vol

    if premium is None:
        return "н/д", "нет данных по IV/RV для оценки уверенности"

    abs_premium = abs(premium)
    if abs_premium <= 5:
        return "высокая", f"IV и RV близки (премия {premium:+.1f}%), рынок не закладывает сюрприз"
    if abs_premium <= 15:
        return "средняя", f"умеренная премия волатильности ({premium:+.1f}%)"
    return "низкая", f"высокая премия волатильности ({premium:+.1f}%) — рынок ждёт заметное движение"


# Веса компонентов итогового сигнала. Каждый компонент даёт вклад от -1
# до +1 (положительное = бычий фактор), итоговый score — взвешенная сумма.
_WEIGHTS = {
    "dealer_bias": 2.0,
    "net_gamma": 1.0,
    "level_position": 1.5,
    "flow": 1.5,
    "put_call_ratio": 1.0,
    "bias_flip": 2.0,        # разворот dealer bias между снимками — сильный сигнал
    "momentum_net_gamma": 1.0,
    "momentum_put_call": 1.0,
    "rsi": 1.0,
    "ema_trend": 1.5,
    "funding_rate": 1.0,
    "open_interest": 1.5,
    "fear_greed": 1.0,
}


def trackable_fields(snap: GammaSnapshot, market: Optional[MarketContext] = None) -> dict:
    """Небольшой словарь ключевых метрик снимка для сохранения между запусками.

    Используется state_store.py, чтобы на следующем запуске можно было
    посчитать дельту/разворот метрик, а не оценивать сигнал по одной
    изолированной точке. Если передан MarketContext, дополнительно
    сохраняется open interest — нужен для матрицы «цена × OI».
    """
    return {
        "price": snap.price,
        "net_gamma": snap.net_gamma,
        "dealer_bias": snap.dealer_bias,
        "put_call_ratio": snap.put_call_ratio,
        "iv_atm": snap.iv_atm,
        "realized_vol": snap.realized_vol,
        "vol_premium": snap.vol_premium,
        "updated_at": snap.updated_at,
        "open_interest": market.open_interest if market else None,
    }


def derive_overall_signal(
    snap: GammaSnapshot,
    previous: Optional[dict] = None,
    market: Optional[MarketContext] = None,
) -> OverallSignal:
    """Считает составной бычий/медвежий/нейтральный сигнал.

    Комбинирует dealer bias, знак Net GEX, положение цены относительно
    squeeze-уровней, флоу коллов/путов за 24ч, put/call ratio, динамику
    между снимками cryptogamma.io (если передан previous), а также,
    если передан MarketContext (см. market_data.py), независимые
    источники: RSI(14) и тренд по EMA20/EMA50 с Binance, funding rate и
    открытый интерес по бессрочным фьючерсам, и Crypto Fear & Greed
    Index. Это позволяет не полагаться только на опционные метрики
    cryptogamma.io — funding/OI/RSI берутся с другого рынка (спот и
    фьючерсы) и могут расходиться с ними, подсвечивая противоречия.

    Это упрощённая эвристика, а не самостоятельный количественный
    сигнал и не финансовый совет.
    """
    score = 0.0
    max_possible = 0.0
    reasons: List[str] = []

    # 1. Dealer bias — самый прямой индикатор с сайта.
    bias = (snap.dealer_bias or "").upper()
    if "BULL" in bias:
        score += _WEIGHTS["dealer_bias"]
        reasons.append("Dealer bias: BULLISH")
    elif "BEAR" in bias:
        score -= _WEIGHTS["dealer_bias"]
        reasons.append("Dealer bias: BEARISH")
    if bias:
        max_possible += _WEIGHTS["dealer_bias"]

    # 2. Знак Net GEX: положительный обычно ассоциируется с дилерами,
    # сглаживающими волатильность (бычий/стабилизирующий контекст),
    # отрицательный — с усилением движений (медвежий/рискованный контекст).
    if snap.net_gamma is not None:
        if snap.net_gamma > 0:
            score += _WEIGHTS["net_gamma"]
            reasons.append("Net GEX положительный (дилеры сглаживают волатильность)")
        else:
            score -= _WEIGHTS["net_gamma"]
            reasons.append("Net GEX отрицательный (дилеры усиливают движение)")
        max_possible += _WEIGHTS["net_gamma"]

    # 3. Положение цены относительно squeeze-уровней.
    if snap.price is not None and (snap.support or snap.resistance):
        if snap.support and snap.price <= snap.support * 1.003:
            score += _WEIGHTS["level_position"]
            reasons.append(f"Цена у поддержки ${snap.support:,.0f}")
        elif snap.resistance and snap.price >= snap.resistance * 0.997:
            score -= _WEIGHTS["level_position"]
            reasons.append(f"Цена у сопротивления ${snap.resistance:,.0f}")
        elif snap.support and snap.resistance and snap.support < snap.resistance:
            mid = (snap.support + snap.resistance) / 2
            if snap.price > mid:
                score += _WEIGHTS["level_position"] * 0.4
                reasons.append("Цена в верхней половине диапазона поддержка/сопротивление")
            else:
                score -= _WEIGHTS["level_position"] * 0.4
                reasons.append("Цена в нижней половине диапазона поддержка/сопротивление")
        max_possible += _WEIGHTS["level_position"]

    # 4. Флоу коллов/путов за 24ч.
    if snap.call_flow_24h is not None and snap.put_flow_24h is not None and (
        snap.call_flow_24h or snap.put_flow_24h
    ):
        diff = snap.call_flow_24h - snap.put_flow_24h
        total = abs(snap.call_flow_24h) + abs(snap.put_flow_24h)
        if total > 0:
            ratio = diff / total  # от -1 до +1
            score += _WEIGHTS["flow"] * ratio
            if ratio > 0.1:
                reasons.append("Флоу 24ч смещён в сторону коллов")
            elif ratio < -0.1:
                reasons.append("Флоу 24ч смещён в сторону путов")
            max_possible += _WEIGHTS["flow"]

    # 5. Put/Call ratio: >1 обычно медвежий перекос, <1 — бычий.
    if snap.put_call_ratio is not None and snap.put_call_ratio > 0:
        if snap.put_call_ratio > 1.15:
            score -= _WEIGHTS["put_call_ratio"]
            reasons.append(f"C/P ratio высокий ({snap.put_call_ratio:.2f})")
        elif snap.put_call_ratio < 0.85:
            score += _WEIGHTS["put_call_ratio"]
            reasons.append(f"C/P ratio низкий ({snap.put_call_ratio:.2f})")
        max_possible += _WEIGHTS["put_call_ratio"]

    # 6. Динамика между снимками (если есть предыдущий снимок).
    if previous:
        prev_bias = (previous.get("dealer_bias") or "").upper()
        cur_bias_norm = bias
        prev_is_directional = "BULL" in prev_bias or "BEAR" in prev_bias
        cur_is_directional = "BULL" in cur_bias_norm or "BEAR" in cur_bias_norm
        if prev_is_directional and cur_is_directional and prev_bias != cur_bias_norm:
            if "BULL" in cur_bias_norm:
                score += _WEIGHTS["bias_flip"]
                reasons.append(f"⚡ Dealer bias развернулся: {prev_bias} → {cur_bias_norm}")
            else:
                score -= _WEIGHTS["bias_flip"]
                reasons.append(f"⚡ Dealer bias развернулся: {prev_bias} → {cur_bias_norm}")
            max_possible += _WEIGHTS["bias_flip"]

        prev_net_gamma = previous.get("net_gamma")
        if prev_net_gamma is not None and snap.net_gamma is not None:
            delta = snap.net_gamma - prev_net_gamma
            # Порог, чтобы не реагировать на шум округления.
            if abs(delta) > abs(prev_net_gamma) * 0.02 + 1:
                if delta > 0:
                    score += _WEIGHTS["momentum_net_gamma"]
                    reasons.append("Net GEX растёт по сравнению с прошлым снимком")
                else:
                    score -= _WEIGHTS["momentum_net_gamma"]
                    reasons.append("Net GEX снижается по сравнению с прошлым снимком")
                max_possible += _WEIGHTS["momentum_net_gamma"]

        prev_ratio = previous.get("put_call_ratio")
        if prev_ratio is not None and snap.put_call_ratio is not None and prev_ratio > 0:
            delta_ratio = snap.put_call_ratio - prev_ratio
            if abs(delta_ratio) > 0.03:
                if delta_ratio < 0:
                    score += _WEIGHTS["momentum_put_call"]
                    reasons.append("C/P ratio снижается (сдвиг к коллам)")
                else:
                    score -= _WEIGHTS["momentum_put_call"]
                    reasons.append("C/P ratio растёт (сдвиг к путам)")
                max_possible += _WEIGHTS["momentum_put_call"]

    # 7. RSI(14) с Binance: контрарианская интерпретация на экстремумах,
    # трендовая — в средней зоне.
    if market and market.rsi14 is not None:
        rsi = market.rsi14
        if rsi >= 70:
            score -= _WEIGHTS["rsi"]
            reasons.append(f"RSI {rsi:.0f} — перекуплен (риск коррекции)")
        elif rsi <= 30:
            score += _WEIGHTS["rsi"]
            reasons.append(f"RSI {rsi:.0f} — перепродан (потенциал отскока)")
        else:
            contrib = (rsi - 50) / 50
            score += _WEIGHTS["rsi"] * contrib
            if rsi > 55:
                reasons.append(f"RSI {rsi:.0f} — бычий импульс")
            elif rsi < 45:
                reasons.append(f"RSI {rsi:.0f} — медвежий импульс")
        max_possible += _WEIGHTS["rsi"]

    # 8. Тренд по EMA20/EMA50 (Binance).
    if market and market.ema20 is not None and market.ema50 is not None:
        ref_price = market.binance_price if market.binance_price is not None else snap.price
        if ref_price is not None:
            if ref_price > market.ema20 > market.ema50:
                score += _WEIGHTS["ema_trend"]
                reasons.append("Цена выше EMA20 и EMA50 (растущий тренд)")
            elif ref_price < market.ema20 < market.ema50:
                score -= _WEIGHTS["ema_trend"]
                reasons.append("Цена ниже EMA20 и EMA50 (падающий тренд)")
            else:
                reasons.append("EMA без чёткого тренда (цена между скользящими)")
            max_possible += _WEIGHTS["ema_trend"]

    # 9. Funding rate по бессрочным фьючерсам: экстремальные значения —
    # контрарианский сигнал (рынок перегружен в одну сторону).
    if market and market.funding_rate_pct is not None:
        fr = market.funding_rate_pct
        if fr > 0.05:
            score -= _WEIGHTS["funding_rate"]
            reasons.append(f"Funding rate высокий ({fr:+.3f}%) — перегрев лонгов")
        elif fr < -0.02:
            score += _WEIGHTS["funding_rate"]
            reasons.append(f"Funding rate отрицательный ({fr:+.3f}%) — перегрев шортов")
        max_possible += _WEIGHTS["funding_rate"]

    # 10. Матрица «цена × открытый интерес» между прошлым и текущим снимком —
    # классическая интерпретация фьючерсного OI.
    if previous and market and market.open_interest is not None:
        prev_oi = previous.get("open_interest")
        prev_price = previous.get("price")
        if prev_oi is not None and prev_price and snap.price is not None:
            oi_rising = market.open_interest > prev_oi * 1.01
            oi_falling = market.open_interest < prev_oi * 0.99
            price_rising = snap.price > prev_price * 1.001
            price_falling = snap.price < prev_price * 0.999

            if price_rising and oi_rising:
                score += _WEIGHTS["open_interest"]
                reasons.append("Цена и OI растут вместе (новые лонги подтверждают тренд)")
                max_possible += _WEIGHTS["open_interest"]
            elif price_falling and oi_rising:
                score -= _WEIGHTS["open_interest"]
                reasons.append("Цена падает при росте OI (новые шорты подтверждают снижение)")
                max_possible += _WEIGHTS["open_interest"]
            elif price_rising and oi_falling:
                score += _WEIGHTS["open_interest"] * 0.3
                reasons.append("Цена растёт при падении OI (шорт-сквиз, тренд может быть слабее)")
                max_possible += _WEIGHTS["open_interest"]
            elif price_falling and oi_falling:
                score += _WEIGHTS["open_interest"] * 0.2
                reasons.append("Цена падает при падении OI (закрытие лонгов, возможное истощение)")
                max_possible += _WEIGHTS["open_interest"]

    # 11. Crypto Fear & Greed Index — общий по рынку, контрарианская трактовка.
    if market and market.fear_greed_value is not None:
        fg = market.fear_greed_value
        if fg >= 75:
            score -= _WEIGHTS["fear_greed"]
            reasons.append(f"Fear & Greed {fg} ({market.fear_greed_class}) — риск отката от жадности")
        elif fg <= 25:
            score += _WEIGHTS["fear_greed"]
            reasons.append(f"Fear & Greed {fg} ({market.fear_greed_class}) — потенциал разворота от страха")
        max_possible += _WEIGHTS["fear_greed"]

    if max_possible == 0:
        confidence, confidence_note = derive_confidence(snap)
        return OverallSignal(
            label="НЕЙТРАЛЬНЫЙ",
            emoji="⚪️",
            strength="н/д",
            confidence=confidence,
            confidence_note=confidence_note,
            score=0.0,
            reasons=["Недостаточно данных для расчёта сигнала"],
        )

    normalized = score / max_possible  # от -1 до +1

    if normalized >= 0.55:
        label, emoji, strength = "БЫЧИЙ", "🟢", "сильный"
    elif normalized >= 0.2:
        label, emoji, strength = "БЫЧИЙ", "🟢", "умеренный"
    elif normalized <= -0.55:
        label, emoji, strength = "МЕДВЕЖИЙ", "🔴", "сильный"
    elif normalized <= -0.2:
        label, emoji, strength = "МЕДВЕЖИЙ", "🔴", "умеренный"
    else:
        label, emoji, strength = "НЕЙТРАЛЬНЫЙ", "⚪️", "слабый"

    confidence, confidence_note = derive_confidence(snap)

    return OverallSignal(
        label=label,
        emoji=emoji,
        strength=strength,
        confidence=confidence,
        confidence_note=confidence_note,
        score=normalized,
        reasons=reasons,
    )


def format_snapshot_message(
    snap: GammaSnapshot,
    previous: Optional[dict] = None,
    market: Optional[MarketContext] = None,
) -> str:
    overall = derive_overall_signal(snap, previous=previous, market=market)
    bias_emoji = derive_bias_emoji(snap)

    lines = [
        f"{overall.emoji} <b>{snap.asset}: {overall.label} сигнал</b> "
        f"({overall.strength}, уверенность: {overall.confidence})",
    ]
    if overall.reasons:
        lines.append("<i>" + "; ".join(overall.reasons) + "</i>")
    if overall.confidence_note:
        lines.append(f"<i>Уверенность: {overall.confidence_note}</i>")
    lines += [
        "",
        f"{bias_emoji} <b>Gamma Exposure (cryptogamma.io)</b>",
        "",
        f"Цена: <b>{_fmt_price(snap.price)}</b>",
        f"Net GEX: <b>{_fmt_num(snap.net_gamma, show_sign=True)}</b> "
        f"(Call {_fmt_num(snap.call_gamma, show_sign=True)} / Put {_fmt_num(snap.put_gamma, show_sign=True)})",
        f"Dealer bias: <b>{snap.dealer_bias or 'н/д'}</b>"
        + (f" ({_fmt_pct(snap.call_weighted_pct)} call-weighted)" if snap.call_weighted_pct else ""),
        "",
        "<b>Squeeze levels:</b>",
        f"  Поддержка: {_fmt_price(snap.support)}",
        f"  Сопротивление: {_fmt_price(snap.resistance)}",
        f"  Пробой: {_fmt_price(snap.breakout)}",
        "",
        f"IV (ATM): {_fmt_pct(snap.iv_atm)} | RV (7д): {_fmt_pct(snap.realized_vol)} "
        f"| Премия: {_fmt_pct(snap.vol_premium)}",
        f"Flow 24ч: call {_fmt_num(snap.call_flow_24h)} / put {_fmt_num(snap.put_flow_24h)} "
        f"| C/P: {snap.put_call_ratio if snap.put_call_ratio is not None else 'н/д'}",
        "",
        f"Delta hedging: {snap.delta_hedging or 'н/д'} | "
        f"Squeeze risk: {snap.squeeze_risk or 'н/д'} | "
        f"Pin risk: {snap.pin_risk or 'н/д'}",
    ]

    squeeze_note = derive_squeeze_note(snap)
    if squeeze_note:
        lines += ["", squeeze_note]

    if market and any(
        v is not None
        for v in (market.rsi14, market.ema20, market.funding_rate_pct, market.open_interest, market.fear_greed_value)
    ):
        lines += ["", "<b>Доп. источники (Binance / Fear&Greed):</b>"]
        if market.rsi14 is not None:
            lines.append(f"  RSI(14): {market.rsi14:.0f}")
        if market.ema20 is not None and market.ema50 is not None:
            lines.append(f"  EMA20/EMA50: {market.ema20:,.0f} / {market.ema50:,.0f}")
        if market.funding_rate_pct is not None:
            lines.append(f"  Funding rate: {market.funding_rate_pct:+.4f}%")
        if market.open_interest is not None:
            lines.append(f"  Open Interest: {_fmt_num(market.open_interest)}")
        if market.fear_greed_value is not None:
            lines.append(f"  Fear & Greed: {market.fear_greed_value} ({market.fear_greed_class or 'н/д'})")

    if snap.updated_at:
        lines += ["", f"<i>Обновлено: {snap.updated_at}</i>"]

    lines += ["", "<i>Источники: cryptogamma.io (Deribit), Binance, alternative.me. Не является финансовым советом.</i>"]

    return "\n".join(lines)
