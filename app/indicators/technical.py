from __future__ import annotations

import numpy as np
import pandas as pd


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def resample_ohlcv(df: pd.DataFrame, rule: str = "60min") -> pd.DataFrame:
    """Resample intraday OHLCV without look-ahead.

    Used to require higher-timeframe confirmation from the same delayed feed.
    """
    if df is None or df.empty or "datetime" not in df.columns:
        return pd.DataFrame()
    x = df.copy()
    x["datetime"] = pd.to_datetime(x["datetime"], utc=True, errors="coerce")
    x = x.dropna(subset=["datetime", "open", "high", "low", "close", "volume"])
    if x.empty:
        return pd.DataFrame()
    x = x.set_index("datetime").sort_index()

    # Yahoo intraday timestamps represent bar *start* times. Use left-closed
    # hourly buckets so 10:00/10:15/10:30/10:45 form the 10:00 hour, rather
    # than incorrectly attaching 11:00 to the prior hour. Keep only complete
    # 60-minute buckets (4 x 15m bars), which prevents partial higher-frame
    # candles from confirming an entry.
    grouped = x.resample(rule, label="left", closed="left")
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    counts = grouped["close"].count()
    if str(rule).lower() in {"60min", "60m", "1h"}:
        out = out[counts >= 4]
    out = out.dropna().reset_index()
    return out


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Technical, participation and anti-fake-momentum features.

    Intraday frames use *session VWAP* and time-of-day adjusted RVOL when a
    datetime column is available. Daily frames automatically fall back to
    rolling participation measures.
    """
    x = df.copy()
    required = ["open", "high", "low", "close", "volume"]
    if not set(required).issubset(x.columns):
        return x

    for c in required:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    if "datetime" in x.columns:
        x["datetime"] = pd.to_datetime(x["datetime"], utc=True, errors="coerce")
    x = x.replace([np.inf, -np.inf], np.nan)

    # Trend
    for span in (9, 20, 50, 200):
        x[f"ema{span}"] = x["close"].ewm(span=span, adjust=False, min_periods=span).mean()
    x["ema9_slope_pct"] = x["ema9"].pct_change(3) * 100.0
    x["ema20_slope_pct"] = x["ema20"].pct_change(3) * 100.0

    # RSI 14 - Wilder
    delta = x["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder(gain, 14)
    avg_loss = _wilder(loss, 14)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    x["rsi14"] = 100.0 - (100.0 / (1.0 + rs))
    x.loc[(avg_loss == 0) & (avg_gain > 0), "rsi14"] = 100.0
    x.loc[(avg_gain == 0) & (avg_loss > 0), "rsi14"] = 0.0
    x["rsi"] = x["rsi14"]

    # MACD 12/26/9
    ema12 = x["close"].ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = x["close"].ewm(span=26, adjust=False, min_periods=26).mean()
    x["macd"] = ema12 - ema26
    x["macd_signal"] = x["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    x["macd_hist"] = x["macd"] - x["macd_signal"]
    x["macd_hist_delta"] = x["macd_hist"].diff()
    x["macd_hist_rising2"] = (
        (x["macd_hist"] > x["macd_hist"].shift(1))
        & (x["macd_hist"].shift(1) > x["macd_hist"].shift(2))
    ).astype(float)

    # ATR14
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    x["tr"] = tr
    x["atr14"] = _wilder(tr, 14)
    x["atr_pct"] = x["atr14"] / x["close"].replace(0, np.nan) * 100.0

    # ADX14 / DI
    up_move = x["high"].diff()
    down_move = -x["low"].diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=x.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=x.index)
    atr_w = _wilder(tr, 14)
    plus_di = 100.0 * _wilder(plus_dm, 14) / atr_w.replace(0, np.nan)
    minus_di = 100.0 * _wilder(minus_dm, 14) / atr_w.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    x["plus_di14"] = plus_di
    x["minus_di14"] = minus_di
    x["di_spread"] = plus_di - minus_di
    x["adx14"] = _wilder(dx, 14)
    x["adx_delta"] = x["adx14"].diff()

    # Momentum
    x["momentum5"] = x["close"] - x["close"].shift(5)
    x["momentum5_pct"] = x["close"].pct_change(5) * 100.0
    x["return1_pct"] = x["close"].pct_change() * 100.0

    # Volume / value participation
    x["volume_avg20"] = x["volume"].rolling(20, min_periods=20).mean()
    x["relative_volume"] = x["volume"] / x["volume_avg20"].replace(0, np.nan)
    x["volume_avg5"] = x["volume"].rolling(5, min_periods=5).mean()
    x["volume_trend_ratio"] = x["volume_avg5"] / x["volume_avg20"].replace(0, np.nan)
    x["traded_value"] = x["close"] * x["volume"]
    x["traded_value_avg20"] = x["traded_value"].rolling(20, min_periods=20).mean()

    # Intraday time-adjusted RVOL: compare each bar to the same Saudi clock slot
    # on prior sessions, not to an all-day average.
    x["time_adjusted_rvol"] = np.nan
    if "datetime" in x.columns and x["datetime"].notna().sum() >= 40:
        local = x["datetime"].dt.tz_convert("Asia/Riyadh")
        # Only use this measure if multiple bars exist within a typical date.
        counts = local.dt.date.value_counts()
        intraday_like = (counts.median() if len(counts) else 0) >= 4
        if intraday_like:
            slot = local.dt.strftime("%H:%M")
            baseline = x.assign(_slot=slot).groupby("_slot")["volume"].transform(
                lambda s: s.shift(1).rolling(20, min_periods=5).mean()
            )
            x["time_adjusted_rvol"] = x["volume"] / baseline.replace(0, np.nan)

    # OBV and Accumulation/Distribution: confirmation only.
    direction = np.sign(x["close"].diff()).fillna(0.0)
    x["obv"] = (direction * x["volume"]).fillna(0.0).cumsum()
    x["obv_slope5"] = x["obv"].diff(5)
    hl_range = (x["high"] - x["low"]).replace(0, np.nan)
    mfm = ((x["close"] - x["low"]) - (x["high"] - x["close"])) / hl_range
    x["ad_line"] = (mfm.fillna(0.0) * x["volume"]).cumsum()
    x["ad_slope5"] = x["ad_line"].diff(5)
    x["price_volume_divergence"] = (
        (x["momentum5_pct"] > 0.5) & (x["obv_slope5"] <= 0) & (x["ad_slope5"] <= 0)
    ).astype(float)

    # Previous structure levels (no look-ahead)
    x["support20"] = x["low"].rolling(20, min_periods=20).min().shift(1)
    x["resistance20"] = x["high"].rolling(20, min_periods=20).max().shift(1)
    x["resistance50"] = x["high"].rolling(50, min_periods=50).max().shift(1)

    # Session VWAP for intraday; rolling VWAP remains as daily/fallback context.
    typical = (x["high"] + x["low"] + x["close"]) / 3.0
    vol_sum = x["volume"].rolling(20, min_periods=20).sum()
    x["vwap20"] = (typical * x["volume"]).rolling(20, min_periods=20).sum() / vol_sum.replace(0, np.nan)
    x["session_vwap"] = np.nan
    if "datetime" in x.columns and x["datetime"].notna().sum() > 0:
        local_date = x["datetime"].dt.tz_convert("Asia/Riyadh").dt.date
        pv = typical * x["volume"]
        cum_pv = pv.groupby(local_date).cumsum()
        cum_vol = x["volume"].groupby(local_date).cumsum()
        session_vwap = cum_pv / cum_vol.replace(0, np.nan)
        # For daily frames, each date has a single bar; session VWAP would just
        # equal typical price and adds no information, so keep rolling VWAP.
        if local_date.value_counts().median() >= 4:
            x["session_vwap"] = session_vwap

    x["active_vwap"] = x["session_vwap"].where(x["session_vwap"].notna(), x["vwap20"])
    x["vwap_distance_atr"] = (x["close"] - x["active_vwap"]) / x["atr14"].replace(0, np.nan)
    x["ema20_distance_atr"] = (x["close"] - x["ema20"]) / x["atr14"].replace(0, np.nan)
    x["resistance_distance_atr"] = (x["resistance20"] - x["close"]) / x["atr14"].replace(0, np.nan)

    # Candle quality / fake-breakout diagnostics
    candle_range = (x["high"] - x["low"]).replace(0, np.nan)
    body = (x["close"] - x["open"]).abs()
    upper_wick = x["high"] - x[["open", "close"]].max(axis=1)
    lower_wick = x[["open", "close"]].min(axis=1) - x["low"]
    x["candle_body_pct"] = body / candle_range
    x["upper_wick_pct"] = upper_wick / candle_range
    x["lower_wick_pct"] = lower_wick / candle_range
    x["close_position"] = (x["close"] - x["low"]) / candle_range
    x["bullish_candle"] = (x["close"] > x["open"]).astype(float)

    # Breakout acceptance quality
    x["breakout_buffer_atr"] = (x["close"] - x["resistance20"]) / x["atr14"].replace(0, np.nan)
    x["is_breakout"] = (x["close"] > x["resistance20"]).astype(float)
    x["failed_breakout"] = ((x["high"] > x["resistance20"]) & (x["close"] <= x["resistance20"])).astype(float)

    # Bollinger extension context
    mid = x["close"].rolling(20, min_periods=20).mean()
    std = x["close"].rolling(20, min_periods=20).std(ddof=0)
    x["bb_mid20"] = mid
    x["bb_upper20"] = mid + 2.0 * std
    x["bb_lower20"] = mid - 2.0 * std
    x["bb_position"] = (x["close"] - x["bb_lower20"]) / (x["bb_upper20"] - x["bb_lower20"]).replace(0, np.nan)

    return x


def latest_features(df: pd.DataFrame) -> dict:
    x = add_indicators(df)
    if x.empty:
        return {}

    keys = [
        "close", "open", "high", "low", "ema9", "ema20", "ema50", "ema200",
        "ema9_slope_pct", "ema20_slope_pct", "rsi14", "rsi", "macd", "macd_signal",
        "macd_hist", "macd_hist_delta", "macd_hist_rising2", "atr14", "atr_pct", "adx14",
        "adx_delta", "plus_di14", "minus_di14", "di_spread", "momentum5", "momentum5_pct",
        "return1_pct", "volume", "volume_avg20", "volume_avg5", "relative_volume",
        "time_adjusted_rvol", "volume_trend_ratio", "traded_value", "traded_value_avg20",
        "obv_slope5", "ad_slope5", "price_volume_divergence", "support20", "resistance20",
        "resistance50", "resistance_distance_atr", "vwap20", "session_vwap", "active_vwap",
        "vwap_distance_atr", "ema20_distance_atr", "candle_body_pct", "upper_wick_pct",
        "lower_wick_pct", "close_position", "bullish_candle", "breakout_buffer_atr",
        "is_breakout", "failed_breakout", "bb_mid20", "bb_upper20", "bb_lower20", "bb_position",
    ]

    core = ["close", "ema9", "ema20", "rsi14", "macd", "macd_signal", "atr14", "relative_volume"]
    valid = x.dropna(subset=[k for k in core if k in x.columns])
    if valid.empty:
        return {}

    r = valid.iloc[-1]
    out = {}
    for k in keys:
        if k not in valid.columns:
            continue
        value = r[k]
        if pd.notna(value) and np.isfinite(float(value)):
            out[k] = float(value)
    return out
