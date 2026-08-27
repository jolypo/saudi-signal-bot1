from __future__ import annotations

import math


def saudi_tick_size(price: float) -> float:
    """Current Saudi Exchange equity tick-size bands."""
    p = float(price)
    if p < 25.0:
        return 0.01
    if p < 50.0:
        return 0.02
    if p < 100.0:
        return 0.05
    if p < 250.0:
        return 0.10
    if p < 500.0:
        return 0.20
    return 0.50


def round_saudi_price(price: float, mode: str = "nearest") -> float:
    tick = saudi_tick_size(price)
    units = float(price) / tick
    if mode == "floor":
        units = math.floor(units + 1e-12)
    elif mode == "ceil":
        units = math.ceil(units - 1e-12)
    else:
        units = round(units)
    value = units * tick
    decimals = 2 if tick < 1 else 1
    return round(value, decimals)


def build_long_levels(low, high, atr, support, rr_min, trade_type="سوينغ"):
    entry = (float(low) + float(high)) / 2.0
    atr = float(atr)
    if entry <= 0 or atr <= 0:
        return None

    support = float(support) if support else None

    trade_type_text = str(trade_type)
    if trade_type_text == "مضاربة يومية":
        atr_stop = entry - 1.05 * atr
        min_risk = 0.65 * atr
        max_risk = min(2.30 * atr, entry * 0.04)
        multipliers = (
            max(float(rr_min), 1.8),
            max(float(rr_min) + 0.6, 2.4),
            max(float(rr_min) + 1.4, 3.2),
        )
    elif "قصير" in trade_type_text:
        # 2-5 session Saudi swing: allow normal daily noise instead of using
        # a day-trade stop that is too tight for a multi-session holding period.
        atr_stop = entry - 1.25 * atr
        min_risk = 0.80 * atr
        max_risk = min(2.60 * atr, entry * 0.05)
        multipliers = (
            max(float(rr_min), 1.8),
            max(float(rr_min) + 0.7, 2.5),
            max(float(rr_min) + 1.6, 3.4),
        )
    else:
        atr_stop = entry - 1.45 * atr
        min_risk = 0.95 * atr
        max_risk = min(3.00 * atr, entry * 0.06)
        multipliers = (
            max(float(rr_min), 1.8),
            max(float(rr_min) + 0.9, 2.7),
            max(float(rr_min) + 1.9, 3.7),
        )

    sl = atr_stop
    if support and support < entry:
        structural_stop = support - 0.15 * atr
        structural_risk = entry - structural_stop
        if min_risk <= structural_risk <= max_risk:
            sl = min(atr_stop, structural_stop)

    risk = entry - sl
    if risk <= 0:
        return None
    if risk < min_risk:
        sl = entry - min_risk
        risk = min_risk
    if risk > max_risk or risk / entry > 0.06:
        return None

    raw_tp1 = entry + risk * multipliers[0]
    raw_tp2 = entry + risk * multipliers[1]
    raw_tp3 = entry + risk * multipliers[2]

    # Saudi Exchange prices must land on valid ticks. Long stops are rounded
    # down (slightly more conservative) and targets up.
    entry_low = round_saudi_price(float(low), "floor")
    entry_high = round_saudi_price(float(high), "ceil")
    entry_px = round_saudi_price(entry, "nearest")
    sl_px = round_saudi_price(sl, "floor")
    tp1 = round_saudi_price(raw_tp1, "ceil")
    tp2 = round_saudi_price(raw_tp2, "ceil")
    tp3 = round_saudi_price(raw_tp3, "ceil")

    actual_risk = entry_px - sl_px
    if actual_risk <= 0:
        return None
    rr_tp1 = (tp1 - entry_px) / actual_risk
    if rr_tp1 + 1e-9 < float(rr_min):
        return None

    return {
        "entry_low": entry_low,
        "entry_high": entry_high,
        "entry": entry_px,
        "sl": sl_px,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr_tp1": round(rr_tp1, 2),
    }


def build_quote_long_levels(price, day_low=None, day_high=None, rr_min=1.8):
    price = float(price)
    if price <= 0:
        return None
    risk = price * 0.012
    if day_low:
        candidate = price - float(day_low)
        if candidate > 0:
            risk = max(risk, min(candidate + price * 0.0015, price * 0.02))
    if risk <= 0 or risk / price > 0.04:
        return None
    return build_long_levels(price * 0.9975, price * 1.0025, max(risk / 1.05, price * 0.002), day_low, rr_min, "سوينغ قصير")
