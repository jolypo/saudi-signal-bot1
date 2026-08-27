from __future__ import annotations

from dataclasses import dataclass, field

from app.indicators.technical import latest_features


@dataclass
class StrategyAssessment:
    trade_type: str
    strategy: str
    score: float
    reasons: list[str]
    invalidation_reasons: list[str]
    features: dict
    hard_rejects: list[str] = field(default_factory=list)
    grade: str = "B"


def _base(f: dict) -> bool:
    required = (
        "close", "ema9", "ema20", "rsi14", "macd", "macd_signal",
        "atr14", "adx14", "relative_volume", "momentum5_pct",
    )
    return bool(f) and all(k in f for k in required)


def _cap(score: float) -> float:
    return max(0.0, min(100.0, score))


def _grade(score: float, hard_rejects: list[str]) -> str:
    if hard_rejects:
        return "REJECT"
    if score >= 90:
        return "A+"
    if score >= 82:
        return "A"
    return "B"


def _hard_rejects(f: dict, market_context: dict | None, *, swing: bool = False) -> list[str]:
    """Saudi-market anti-fake-momentum gates."""
    r: list[str] = []
    regime = (market_context or {}).get("regime", "UNKNOWN")

    # Intraday uses time-adjusted RVOL when available; daily falls back to normal RVOL.
    ta_rvol = f.get("time_adjusted_rvol")
    rv = ta_rvol if (not swing and ta_rvol is not None) else f.get("relative_volume", 0.0)
    di_spread = f.get("di_spread", f.get("plus_di14", 0) - f.get("minus_di14", 0))
    vwap_ext = f.get("vwap_distance_atr", 0.0)
    ema20_ext = f.get("ema20_distance_atr", 0.0)
    upper_wick = f.get("upper_wick_pct", 0.0)
    close_pos = f.get("close_position", 0.5)
    failed_breakout = f.get("failed_breakout", 0.0)
    breakout = f.get("is_breakout", 0.0)
    breakout_buffer = f.get("breakout_buffer_atr", 0.0)
    resistance_distance = f.get("resistance_distance_atr")
    adx = f.get("adx14", 0.0)

    min_rvol = 1.10 if swing else 1.30
    if rv < min_rvol:
        label = "Time-adjusted RVOL" if (not swing and ta_rvol is not None) else "Relative Volume"
        r.append(f"{label} ضعيف ({rv:.2f}×)")

    if regime == "UNKNOWN":
        r.append("بيانات سياق TASI غير متاحة؛ لا يتم إنشاء شراء جديد")
    elif regime == "BEARISH":
        r.append("اتساع/اتجاه TASI هابط؛ لا شراء جديد")

    if di_spread < 3.0:
        r.append("تفوق +DI غير كافٍ لتأكيد السيطرة الشرائية")
    if adx < 20:
        r.append(f"ADX ضعيف ({adx:.1f})")

    max_vwap_ext = 1.8 if swing else 1.5
    max_ema_ext = 2.2 if swing else 2.0
    if vwap_ext > max_vwap_ext:
        r.append(f"السعر ممتد عن VWAP بمقدار {vwap_ext:.2f} ATR")
    if ema20_ext > max_ema_ext:
        r.append(f"السعر ممتد عن EMA20 بمقدار {ema20_ext:.2f} ATR")

    if failed_breakout >= 0.5:
        r.append("اختراق فاشل: تجاوز المقاومة ثم أغلق تحتها")

    # Buying directly under resistance is a common Saudi momentum trap.
    if breakout < 0.5 and resistance_distance is not None and 0.0 < resistance_distance < 0.35:
        r.append("الدخول مباشرة أسفل مقاومة قريبة؛ مساحة الحركة غير كافية")

    if breakout >= 0.5:
        if breakout_buffer < 0.10:
            r.append("الاختراق ضعيف ولم يبتعد بما يكفي عن المقاومة")
        if close_pos < 0.65:
            r.append("إغلاق شمعة الاختراق ليس قريبًا من القمة")
        if upper_wick > 0.35:
            r.append("فتيل علوي كبير على شمعة الاختراق؛ احتمال تصريف")

    if rv >= 2.5 and (close_pos < 0.55 or upper_wick > 0.40):
        r.append("حجم انفجاري بدون إغلاق قوي؛ احتمال Volume Climax/تصريف")

    if f.get("price_volume_divergence", 0.0) >= 0.5:
        r.append("السعر صاعد بينما OBV وA/D لا يؤكدان؛ زخم محتمل أن يكون وهميًا")

    return r


def _mtf_rejects(higher: dict | None, daily: dict | None) -> tuple[list[str], list[str]]:
    rejects: list[str] = []
    reasons: list[str] = []

    if not higher or not _base(higher):
        rejects.append("تأكيد إطار 60 دقيقة غير متاح")
    else:
        if not (higher.get("ema9", 0) > higher.get("ema20", 0)):
            rejects.append("إطار 60 دقيقة لا يؤكد اتجاهًا صاعدًا")
        if higher.get("ema20_slope_pct", 0) <= 0:
            rejects.append("ميل EMA20 على 60 دقيقة غير صاعد")
        if higher.get("close", 0) < higher.get("ema20", 0):
            rejects.append("سعر 60 دقيقة أسفل EMA20")
        if higher.get("macd_hist", 0) > 0:
            reasons.append("إطار 60 دقيقة يؤكد MACD إيجابي")
        if higher.get("ema9", 0) > higher.get("ema20", 0) and higher.get("ema20_slope_pct", 0) > 0:
            reasons.append("اتجاه 60 دقيقة متوافق مع الدخول")

    if not daily or not _base(daily):
        rejects.append("السياق اليومي غير متاح")
    else:
        # For a 2-5 session swing, daily trend must not be actively falling.
        if daily.get("ema20_slope_pct", 0) < 0:
            rejects.append("EMA20 اليومي هابط؛ لا ندخل سوينغ شراء ضد الاتجاه")
        if daily.get("close", 0) < daily.get("ema20", 0):
            rejects.append("الإغلاق اليومي أسفل EMA20")
        if daily.get("ema20_slope_pct", 0) >= 0 and daily.get("close", 0) >= daily.get("ema20", 0):
            reasons.append("السياق اليومي داعم للسوينغ القصير")

    return rejects, reasons


def _score_common(f: dict, market_context: dict | None, *, swing: bool = False) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    regime = (market_context or {}).get("regime", "UNKNOWN")

    # Trend 15
    if f.get("ema9", 0) > f.get("ema20", 0):
        score += 5; reasons.append("EMA9 أعلى من EMA20")
    if f.get("ema50") is not None and f.get("ema20", 0) > f.get("ema50", 0):
        score += 5; reasons.append("EMA20 أعلى من EMA50")
    if f.get("ema20_slope_pct", 0) > 0:
        score += 5; reasons.append("ميل EMA20 صاعد")

    # Momentum 10
    rsi = f.get("rsi14", 0)
    healthy = (48 <= rsi <= 68) if swing else (52 <= rsi <= 68)
    if healthy:
        score += 3; reasons.append(f"RSI14 صحي عند {rsi:.0f}")
    if f.get("macd_hist", 0) > 0:
        score += 3; reasons.append("MACD histogram موجب")
    if f.get("macd_hist_rising2", 0) >= 0.5:
        score += 2; reasons.append("زخم MACD يتسارع")
    if f.get("momentum5_pct", 0) > 0:
        score += 2; reasons.append(f"زخم 5 شمعات موجب {f.get('momentum5_pct', 0):.2f}%")

    # Participation 20
    ta_rvol = f.get("time_adjusted_rvol")
    rv = ta_rvol if (not swing and ta_rvol is not None) else f.get("relative_volume", 0)
    if rv >= 1.8:
        score += 10; reasons.append(f"RVOL زمني قوي {rv:.2f}×" if ta_rvol is not None and not swing else f"RVOL قوي {rv:.2f}×")
    elif rv >= 1.5:
        score += 8; reasons.append(f"RVOL جيد {rv:.2f}×")
    elif rv >= (1.10 if swing else 1.30):
        score += 5
    if f.get("volume_trend_ratio", 0) >= 1.05:
        score += 4; reasons.append("متوسط الحجم القصير أعلى من متوسط 20")
    if f.get("obv_slope5", 0) > 0:
        score += 3; reasons.append("OBV يؤكد التجميع")
    if f.get("ad_slope5", 0) > 0:
        score += 3; reasons.append("Accumulation/Distribution إيجابي")

    # Structure 20
    resistance = f.get("resistance20")
    support = f.get("support20")
    if resistance and f.get("close", 0) > resistance:
        score += 8; reasons.append("إغلاق أعلى مقاومة 20 شمعة")
    elif resistance and f.get("close", 0) < resistance and f.get("resistance_distance_atr", 99) >= 0.35:
        score += 3; reasons.append("توجد مساحة قبل المقاومة التالية")
    if support and f.get("close", 0) > support * 1.01:
        score += 4
    if f.get("close_position", 0) >= 0.70:
        score += 4; reasons.append("الإغلاق في أعلى نطاق الشمعة")
    if f.get("candle_body_pct", 0) >= 0.55 and f.get("bullish_candle", 0) >= 0.5:
        score += 4; reasons.append("شمعة شرائية بجسم قوي")

    # Entry quality 15
    active_vwap = f.get("active_vwap", f.get("vwap20"))
    if active_vwap is not None and f.get("close", 0) >= active_vwap:
        score += 5; reasons.append("السعر فوق VWAP الفعّال")
    if 0 <= f.get("vwap_distance_atr", 0) <= 1.0:
        score += 4; reasons.append("الدخول غير مطارد وبعيد عن التمدد")
    if f.get("upper_wick_pct", 1) <= 0.25:
        score += 3; reasons.append("لا يوجد رفض سعري علوي كبير")
    if f.get("adx_delta", 0) >= 0 and f.get("di_spread", 0) >= 5:
        score += 3; reasons.append("ADX/DI يؤكدان تحسن قوة الاتجاه")

    # Market context 15. Missing breadth gets no fabricated breadth bonus.
    if regime == "BULLISH":
        if (market_context or {}).get("breadth_available"):
            score += 15; reasons.append("TASI والاتساع الداخلي داعمان")
        else:
            score += 10; reasons.append("TASI صاعد؛ بيانات الاتساع غير متاحة")
    elif regime == "NEUTRAL":
        score += 5; reasons.append("TASI محايد")
    elif regime == "HIGH_VOL":
        score += 1

    return _cap(score), reasons


def assess_intraday(df, market_regime="NEUTRAL", higher_tf_df=None, daily_df=None, market_context=None):
    # Backward compatibility: callers may still pass just a regime string.
    ctx = dict(market_context or {})
    ctx.setdefault("regime", market_regime)

    f = latest_features(df)
    if not _base(f):
        return None
    higher = latest_features(higher_tf_df) if higher_tf_df is not None else None
    daily = latest_features(daily_df) if daily_df is not None else None

    rejects = _hard_rejects(f, ctx, swing=False)
    mtf_rejects, mtf_reasons = _mtf_rejects(higher, daily)
    rejects.extend(mtf_rejects)
    score, reasons = _score_common(f, ctx, swing=False)
    reasons.extend(mtf_reasons)
    grade = _grade(score, rejects)

    # Store MTF diagnostics with prefixes so Telegram/reporting can inspect them.
    for prefix, source in (("h1", higher), ("d1", daily)):
        if source:
            for key in ("close", "ema9", "ema20", "ema50", "ema20_slope_pct", "macd_hist", "rsi14"):
                if key in source:
                    f[f"{prefix}_{key}"] = source[key]

    invalid = [
        "كسر وقف الخسارة يلغي السيناريو",
        "عودة السعر أسفل Session VWAP مع ضعف الحجم تلغي أفضلية الدخول",
        "اختراق المقاومة ثم الإغلاق تحتها يلغي جودة الحركة",
        "تراجع RVOL الزمني/OBV مع استمرار صعود السعر يحذر من زخم وهمي",
        "فقدان توافق 60 دقيقة أو تحول TASI إلى ضغط بيعي قوي يضعف الصفقة",
    ]
    return StrategyAssessment(
        "سوينغ قصير 2–5 جلسات", "SAUDI_MTF_ANTI_FAKE", score,
        reasons, invalid, f, rejects, grade,
    )


def assess_swing(df, market_regime="NEUTRAL", market_context=None):
    """Kept for research/backward compatibility; live signals use MTF intraday gate."""
    ctx = dict(market_context or {})
    ctx.setdefault("regime", market_regime)
    f = latest_features(df)
    if not _base(f) or "ema50" not in f:
        return None
    rejects = _hard_rejects(f, ctx, swing=True)
    score, reasons = _score_common(f, ctx, swing=True)
    if f.get("ema200") is not None and f.get("ema50", 0) > f.get("ema200", 0):
        score = _cap(score + 4); reasons.append("EMA50 أعلى من EMA200")
    grade = _grade(score, rejects)
    return StrategyAssessment(
        "سوينغ قصير 2–5 جلسات", "SAUDI_SWING_QUALITY", score, reasons,
        ["إغلاق واضح تحت وقف الخسارة يلغي فكرة الصفقة"], f, rejects, grade,
    )
