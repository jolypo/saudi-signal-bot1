import numpy as np
import pandas as pd

from app.indicators.technical import add_indicators, latest_features, resample_ohlcv
from app.market.regime import classify_tasi
from app.risk.levels import build_long_levels
from app.strategy.analyzer import assess_intraday, assess_swing


def _trend_df(n=300, freq="15min"):
    t = np.arange(n, dtype=float)
    close = 80 + 0.08 * t + 0.35 * np.sin(t / 4.0)
    volume = np.full(n, 1_000_000.0)
    volume[-25:] = np.linspace(1_000_000, 1_600_000, 25)
    return pd.DataFrame({
        "datetime": pd.date_range("2026-01-01", periods=n, freq=freq, tz="UTC"),
        "open": close - 0.10,
        "high": close + 0.30,
        "low": close - 0.30,
        "close": close,
        "volume": volume,
    })


def test_required_professional_indicators_are_present_and_finite():
    f = latest_features(_trend_df())
    for key in (
        "ema9", "ema20", "ema50", "ema200", "rsi14", "macd",
        "macd_signal", "macd_hist", "atr14", "adx14", "plus_di14",
        "minus_di14", "momentum5_pct", "relative_volume", "vwap20",
        "support20", "resistance20", "upper_wick_pct", "close_position",
        "vwap_distance_atr", "obv_slope5", "ad_slope5", "di_spread",
    ):
        assert key in f
        assert np.isfinite(f[key])


def test_strategy_uses_adx_momentum_and_requires_mtf():
    df = _trend_df(500)
    h1 = resample_ohlcv(df, "60min")
    daily = _trend_df(300, "1D")
    ctx = {"regime": "BULLISH", "breadth_available": True, "breadth": 0.4}
    intraday = assess_intraday(df, "BULLISH", higher_tf_df=h1, daily_df=daily, market_context=ctx)
    swing = assess_swing(daily, "BULLISH", market_context=ctx)
    assert intraday is not None
    assert swing is not None
    assert "adx14" in intraday.features
    assert "momentum5_pct" in intraday.features
    assert "h1_ema20" in intraday.features
    assert "d1_ema20" in intraday.features
    assert 0 <= intraday.score <= 100
    assert 0 <= swing.score <= 100


def test_live_intraday_without_mtf_is_rejected():
    a = assess_intraday(_trend_df(400), "BULLISH")
    assert a is not None
    assert any("60 دقيقة" in x for x in a.hard_rejects)
    assert a.grade == "REJECT"


def test_regime_uses_breadth_not_only_index_change():
    assert classify_tasi({"change_percent": 0.2, "advancers": 160, "decliners": 50}) == "BULLISH"
    assert classify_tasi({"change_percent": -0.2, "advancers": 40, "decliners": 170}) == "BEARISH"
    assert classify_tasi({"change_percent": 0.0, "advancers": 100, "decliners": 100}) == "NEUTRAL"


def test_stop_is_below_support_with_buffer_when_structure_is_used():
    levels = build_long_levels(99.8, 100.2, atr=1.0, support=99.0, rr_min=1.5, trade_type="مضاربة يومية")
    assert levels is not None
    assert levels["sl"] < 99.0
    assert levels["rr_tp1"] >= 1.5


def test_fake_breakout_gets_hard_rejected():
    df = _trend_df()
    # Force last candle to pierce prior resistance but close weak with a large upper wick.
    prior_high = float(df["high"].iloc[-21:-1].max())
    df.loc[df.index[-1], "open"] = prior_high - 0.05
    df.loc[df.index[-1], "high"] = prior_high + 2.0
    df.loc[df.index[-1], "low"] = prior_high - 0.20
    df.loc[df.index[-1], "close"] = prior_high - 0.02
    df.loc[df.index[-1], "volume"] = 4_000_000.0
    a = assess_intraday(df, "NEUTRAL")
    assert a is not None
    assert a.hard_rejects
    assert a.grade == "REJECT"


def test_signal_quality_labels_are_a_or_reject_not_probability():
    a = assess_intraday(_trend_df(), "BULLISH")
    assert a is not None
    assert a.grade in {"A+", "A", "B", "REJECT"}
    assert 0 <= a.score <= 100

def test_hourly_resample_uses_bar_start_alignment_and_drops_partial_hour():
    close = np.arange(1, 7, dtype=float) + 100
    df = pd.DataFrame({
        "datetime": pd.to_datetime([
            "2026-08-27T07:00:00Z", "2026-08-27T07:15:00Z",
            "2026-08-27T07:30:00Z", "2026-08-27T07:45:00Z",
            "2026-08-27T08:00:00Z", "2026-08-27T08:15:00Z",
        ]),
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": np.full(6, 1000.0),
    })
    h1 = resample_ohlcv(df, "60min")
    assert len(h1) == 1
    assert h1.iloc[0]["open"] == df.iloc[0]["open"]
    assert h1.iloc[0]["close"] == df.iloc[3]["close"]
    assert h1.iloc[0]["volume"] == 4000.0
