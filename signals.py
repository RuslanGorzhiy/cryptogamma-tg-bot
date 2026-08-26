"""
Формирование читаемого текста и торгового сигнала из GammaSnapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from cryptogamma_client import GammaSnapshot


def _fmt_num(value, suffix: str = "") -> str:
    if value is None:
        return "н/д"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M{suffix}"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.2f}K{suffix}"
    return f"{value:.2f}{suffix}"


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
}


def trackable_fields(snap: GammaSnapshot) -> dict:
    """Небольшой словарь ключевых метрик снимка для сохранения между запусками.

    Используется state_store.py, чтобы на следующем запуске можно было
    посчитать дельту/разворот метрик, а не оценивать сигнал по одной
    изолированной точке.
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
    }


def derive_overall_signal(snap: GammaSnapshot, previous: Optional[dict] = None) -> OverallSignal:
    """Считает составной бычий/медвежий/нейтральный сигнал.

    Комбинирует dealer bias, знак Net GEX, положение цены относительно
    squeeze-уровней, флоу коллов/путов за 24ч, put/call ratio, а также,
    если передан предыдущий снимок (см. state_store.py), динамику
    между снимками: разворот dealer bias и направление изменения Net GEX
    и C/P ratio. Разворот метрики часто значимее её текущего абсолютного
    значения.

    Это упрощённая эвристика поверх метрик cryptogamma.io, а не
    самостоятельный количественный сигнал — она не учитывает контекст
    рынка вне опционных данных и не является финансовым советом.
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


def format_snapshot_message(snap: GammaSnapshot, previous: Optional[dict] = None) -> str:
    overall = derive_overall_signal(snap, previous=previous)
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
        f"Net GEX: <b>{_fmt_num(snap.net_gamma)}</b> "
        f"(Call {_fmt_num(snap.call_gamma)} / Put {_fmt_num(snap.put_gamma)})",
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

    if snap.updated_at:
        lines += ["", f"<i>Обновлено: {snap.updated_at}</i>"]

    lines += ["", "<i>Источник: cryptogamma.io (данные Deribit). Не является финансовым советом.</i>"]

    return "\n".join(lines)
