from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from app.probability.engine import ProbabilityEngine
from app.risk.levels import build_long_levels


@dataclass
class Signal:
    trade_id: str; symbol: str; name: str; name_en: str; direction: str
    entry_low: float; entry_high: float; entry: float; sl: float; tp1: float; tp2: float; tp3: float; rr_tp1: float
    score: float; probability: float; probability_status: str; probability_samples: int; probability_bucket: str
    strategy: str; trade_type: str; market_regime: str; sector: str; risk_level: str; grade: str
    discovered_at: str; expected_tp1: str; expected_tp2: str; expected_tp3: str
    reasons: list[str] = field(default_factory=list)
    target_reasons: list[str] = field(default_factory=list)
    invalidation_reasons: list[str] = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    quote_updated_at: str = ""
    historical_updated_at: str = ""

    def to_dict(self): return asdict(self)


class SignalEngine:
    def __init__(self, settings, history):
        self.s=settings; self.p=ProbabilityEngine(history)

    def build_assessment(self, candidate, regime, sector, assessment, quote_updated_at="", historical_updated_at=""):
        if not assessment or assessment.score < self.s.min_score or not self.s.allow_long:
            return None
        if getattr(assessment, "hard_rejects", None):
            return None
        if getattr(assessment, "grade", "B") not in {"A+", "A"}:
            return None
        f=assessment.features
        price=float(candidate.quote.price)

        # Saudi liquidity gate: a technically strong setup is not enough if
        # the live market feed cannot prove adequate traded value. This helps
        # avoid fragile momentum in thin names.
        traded_value=float(getattr(candidate.quote, "value", 0) or 0)
        min_value=float(getattr(self.s, "min_daily_traded_value", 2_000_000) or 0)
        if min_value > 0 and traded_value < min_value:
            return None

        atr=float(f.get("atr14") or 0)
        support=f.get("support20")
        if price<=0 or atr<=0:return None
        # Entry zone scales with ATR but remains tight around current SAHMK price.
        zone=min(max(atr*0.12, price*0.002), price*0.006)
        levels=build_long_levels(price-zone, price+zone, atr, support, self.s.min_rr, assessment.trade_type)
        if not levels:return None
        prob,samples,status,bucket=self.p.estimate(assessment.strategy,regime,assessment.score,levels["rr_tp1"])
        if status=="VALIDATED" and prob<self.s.min_probability:return None
        risk_pct=(levels["entry"]-levels["sl"])/levels["entry"]*100
        risk_level="منخفضة" if risk_pct<=1.5 else "متوسطة" if risk_pct<=3 else "مرتفعة"
        if risk_level=="مرتفعة": return None
        if "2–5" in assessment.trade_type or "قصير" in assessment.trade_type:
            expected=("1–2 جلسة","2–4 جلسات","3–5 جلسات")
        else:
            expected=("2–4 جلسات","3–7 جلسات","5–10 جلسات")
        target_reasons=["الهدف الأول مبني على مسافة الوقف وبحد أدنى للعائد مقابل المخاطرة", "الهدف الثاني امتداد محسوب بوحدات R بعد الهدف الأول", "الهدف الثالث امتداد أكبر بوحدات R إذا استمر الاتجاه"]
        now=datetime.now(timezone.utc)
        return Signal(
            trade_id=f"TASI-{now.strftime('%Y%m%d-%H%M%S')}-{candidate.quote.symbol}",
            symbol=candidate.quote.symbol,name=candidate.quote.name,name_en=candidate.quote.name_en,direction="BUY",
            **levels,score=round(assessment.score,2),probability=prob,probability_status=status,probability_samples=samples,
            probability_bucket=bucket,strategy=assessment.strategy,trade_type=assessment.trade_type,market_regime=regime,
            sector=sector or "غير متاح",risk_level=risk_level,grade=getattr(assessment, "grade", "A"),discovered_at=now.isoformat(),
            expected_tp1=expected[0],expected_tp2=expected[1],expected_tp3=expected[2],reasons=assessment.reasons,
            target_reasons=target_reasons,invalidation_reasons=assessment.invalidation_reasons,
            indicators={k:round(float(v),3) for k,v in f.items() if isinstance(v,(int,float))},
            quote_updated_at=quote_updated_at or "",historical_updated_at=historical_updated_at or "",
        )
