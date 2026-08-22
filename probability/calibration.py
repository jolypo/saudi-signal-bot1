from __future__ import annotations
import numpy as np
from indicators.technical import enrich


def empirical_probability(df, horizon=12, tp_r=1.5, sl_r=1.0, lookback=500, min_samples=25):
    """Walk-forward-style empirical probability.

    For each historical bar, the setup is evaluated using information available at that
    bar, then future bars label TP-first vs SL-first. The current bar is excluded from
    the training sample. If there are too few comparable observations, return None.
    """
    x = enrich(df).dropna().copy()
    if len(x) < 80:
        return None
    candidates = x.iloc[:-1].tail(lookback)
    wins = 0; samples = 0
    current = x.iloc[-1]
    current_score = _setup_score(current)
    current_bucket = round(current_score / 10) * 10
    start_idx = max(20, len(x) - len(candidates) - horizon - 1)
    for i in range(start_idx, len(x) - horizon - 1):
        row = x.iloc[i]
        score = _setup_score(row)
        bucket = round(score / 10) * 10
        if abs(bucket - current_bucket) > 10:
            continue
        atr = float(row.atr)
        if not np.isfinite(atr) or atr <= 0:
            continue
        entry = float(row.close)
        tp = entry + tp_r * atr
        sl = entry - sl_r * atr
        future = x.iloc[i+1:i+1+horizon]
        outcome = None
        for _, f in future.iterrows():
            # Conservative tie rule: if both levels are touched in the same candle,
            # count as SL first because intrabar order is unknown.
            if float(f.low) <= sl and float(f.high) >= tp:
                outcome = 0; break
            if float(f.high) >= tp:
                outcome = 1; break
            if float(f.low) <= sl:
                outcome = 0; break
        if outcome is not None:
            samples += 1; wins += outcome
    if samples < min_samples:
        return None
    # Jeffreys smoothing avoids extreme 0/100% with small samples.
    return float((wins + 0.5) / (samples + 1.0) * 100)


def _setup_score(row):
    score = 0
    if row.close > row.ema20 > row.ema50: score += 20
    if row.close > row.vwap: score += 10
    if 50 <= row.rsi <= 68: score += 10
    if row.macd_hist > 0: score += 10
    if row.rel_volume >= 1.5: score += 15
    if row.momentum_5 > 1: score += 10
    if row.close >= row.resistance * 0.995: score += 15
    if row.close > row.ema200: score += 10
    return min(score, 100)
