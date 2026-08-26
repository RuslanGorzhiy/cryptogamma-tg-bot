"""
Формирование читаемого текста и торгового сигнала из GammaSnapshot.
"""

from __future__ import annotations

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


def format_snapshot_message(snap: GammaSnapshot) -> str:
    bias_emoji = derive_bias_emoji(snap)
    lines = [
        f"{bias_emoji} <b>{snap.asset} — Gamma Exposure (cryptogamma.io)</b>",
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
