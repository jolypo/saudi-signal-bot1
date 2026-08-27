from pathlib import Path


def test_approved_visual_assets_are_bundled():
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "app/assets/telegram/trade_card.png",
        "app/assets/telegram/profit_update.png",
        "app/assets/telegram/daily_report.png",
        "app/assets/telegram/weekly_report.png",
    ):
        p = root / rel
        assert p.exists() and p.stat().st_size > 100_000


def test_profit_updates_reply_to_original_signal():
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    service = (root / "app/service.py").read_text(encoding="utf-8")
    manager = (root / "app/trades/manager.py").read_text(encoding="utf-8")
    assert "reply_to_message_id" in bots
    assert "signal_message_ids" in bots
    assert "set_signal_message_ids" in service
    assert "set_signal_message_ids" in manager
    assert "trade=updated" in service


def test_daily_weekly_reports_and_private_tests_exist():
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    service = (root / "app/service.py").read_text(encoding="utf-8")
    settings = (root / "app/config/settings.py").read_text(encoding="utf-8")
    for label in (
        "🧪 اختبار صفقة",
        "🧪 اختبار تحديث أرباح",
        "🧪 اختبار تقرير يومي",
        "🧪 اختبار تقرير أسبوعي",
    ):
        assert label in bots
    assert "async def daily_report" in service
    assert "async def weekly_report" in service
    assert "_scheduled_daily_report" in service
    assert "daily_report_enabled" in settings


def test_report_image_then_text_order():
    root = Path(__file__).resolve().parents[1]
    bots = (root / "app/telegram/bots.py").read_text(encoding="utf-8")
    block = bots[bots.index("async def send_report"):bots.index("# MARKET CLOSE")]
    assert block.index("_broadcast_photo") < block.index("_broadcast_text")
