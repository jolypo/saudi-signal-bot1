from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.database.json_store import JsonStore
from app.trades.manager import TradeManager


def _settings():
    return SimpleNamespace(
        max_open_trades=5, fee_bps=0, slippage_bps=0,
        tp1_percent=30, tp2_percent=30, tp3_percent=40,
    )

def _trade():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "trade_id":"T1", "symbol":"2222", "name":"أرامكو", "name_en":"Aramco",
        "direction":"BUY", "entry_low":30.2, "entry_high":30.7, "entry":30.5,
        "sl":28.9, "tp1":31.2, "tp2":32.1, "tp3":33.2, "rr_tp1":1.8,
        "score":90.0, "probability":0, "probability_status":"UNVALIDATED",
        "probability_samples":0, "probability_bucket":"", "strategy":"test",
        "trade_type":"سوينغ قصير", "market_regime":"BULL", "sector":"الطاقة",
        "risk_level":"منخفضة", "grade":"A+", "discovered_at":now,
        "expected_tp1":"1–2 جلسة", "expected_tp2":"2–4 جلسات", "expected_tp3":"3–5 جلسات",
        "reasons":[], "target_reasons":[], "invalidation_reasons":[], "indicators":{},
        "quote_updated_at":now, "historical_updated_at":now,
    }

def test_trade_manager_accepts_pending_dict(tmp_path):
    store = JsonStore(tmp_path)
    manager = TradeManager(store, _settings())
    assert manager.add(_trade()) is True
    assert store.state()["open_trades"][0]["symbol"] == "2222"

def test_confirmation_buttons_and_pending_storage_present():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    service = (root / "app/service.py").read_text(encoding="utf-8")
    settings = (root / "app/config/settings.py").read_text(encoding="utf-8")
    for text in ("✅ إرسال الصفقة", "❌ إلغاء الصفقة", "_confirm_signal_menu"):
        assert text in bots
    assert "_stage_pending_signal" in service
    assert "confirm_pending_signal" in service
    assert "signal_confirmation_expiry_minutes: int = 5" in settings
    # Discovery stages a setup; only the confirmation method contains the publish marker.
    assert "[signal] staged for admin confirmation" in service
    assert "[signal] confirmed/sent" in service
    scan_block = service[service.index("async def scan_once"):service.index("# PRICE UPDATE")]
    assert "await self.b.send_signal(" not in scan_block
