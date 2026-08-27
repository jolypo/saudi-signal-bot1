from __future__ import annotations
from dataclasses import dataclass
from config.universe import TASI_25
from config.settings import settings
from data.yahoo_history import YahooHistory
from scanner.scorer import score_stock
from signal_engine.engine import build_signal

@dataclass
class ScanResult:
    symbol: str
    name: str
    score: float
    signal: object | None
    reason: str

class UniverseScanner:
    def __init__(self, min_score: float, min_probability: float, min_rr: float):
        self.history = YahooHistory(settings.history_period, settings.history_interval)
        self.min_score = min_score
        self.min_probability = min_probability
        self.min_rr = min_rr

    def scan(self, market_bullish: bool = True):
        results = []
        for item in TASI_25:
            try:
                df = self.history.history(item["symbol"])
                if len(df) < 60:
                    results.append(ScanResult(item["symbol"], item["name"], 0, None, "بيانات تاريخية غير كافية"))
                    continue
                scored = score_stock(df, market_bullish)
                if not scored:
                    results.append(ScanResult(item["symbol"], item["name"], 0, None, "لا توجد نتيجة"))
                    continue
                signal = build_signal(item["symbol"], item["name"], df, market_bullish, self.min_rr)
                if signal and signal.score >= self.min_score and signal.probability >= self.min_probability:
                    results.append(ScanResult(item["symbol"], item["name"], signal.score, signal, "اجتاز الفلاتر"))
                else:
                    results.append(ScanResult(item["symbol"], item["name"], scored["score"], None, "لم يتجاوز العتبة"))
            except Exception as exc:
                results.append(ScanResult(item["symbol"], item["name"], 0, None, f"خطأ: {exc}"))
        return sorted(results, key=lambda x: x.score, reverse=True)
