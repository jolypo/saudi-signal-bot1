import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import numpy as np
from zoneinfo import ZoneInfo

from app.data.providers.base import Quote
from app.service import TradingService


class FakeBots:
    def __init__(self):
        self.sent = []
        self.service = None
        self.signal = SimpleNamespace(get_me=self._get_me)

    def attach_service(self, service):
        self.service = service

    async def _get_me(self):
        return {"ok": True}

    async def send_signal(self, text, image_path=None, trade=None):
        self.sent.append(text)
        return {"-1": 123}

    async def send_profit(self, text):
        pass

    async def send_loss_for_trade(self, trade, price):
        pass

    async def send_near_sl(self, trade, price):
        pass

    async def send_market_close(self, text):
        pass

    async def send_report(self, text=None, image_path=None):
        pass


class FakeProvider:
    def __init__(self, now):
        self.now = now
        self.historical_called = False
        self.detail_requested = []

    async def companies(self, market="TASI"):
        return [
            {
                "symbol": str(2000 + i),
                "name": f"شركة {i}",
                "name_en": f"Company {i}",
                "sector": "Test",
                "security_type": "equity",
            }
            for i in range(60)
        ]

    async def market_summary(self):
        return {"change_percent": 1.0, "index": 12000}

    async def top_volume_quotes(self, limit=50, index="TASI"):
        return [
            Quote(
                symbol=str(2000 + i),
                name=f"شركة {i}",
                name_en=f"Company {i}",
                price=100.0,
                change_percent=1.5,
                volume=2_000_000 - i,
                value=0,
                updated_at=self.now,
                is_delayed=True,
                raw={"updated_at": self.now.isoformat()},
            )
            for i in range(limit)
        ]

    async def quotes(self, symbols):
        self.detail_requested = list(symbols)
        return {
            symbol: Quote(
                symbol=symbol,
                name=f"شركة {symbol}",
                name_en=f"Company {symbol}",
                price=100.0,
                change_percent=1.5,
                volume=2_000_000,
                value=100_000_000,
                bid=99.90,
                ask=100.10,
                updated_at=self.now,
                is_delayed=True,
                raw={
                    "open": 99.0,
                    "high": 101.0,
                    "low": 98.0,
                    "previous_close": 98.5,
                    "value": 100_000_000,
                    "liquidity": {"net_value": 10_000_000},
                    "updated_at": self.now.isoformat(),
                },
            )
            for symbol in symbols
        }

    async def quote(self, symbol):
        return (await self.quotes([symbol]))[symbol]

    async def historical(self, symbol, days=250):
        self.historical_called = True
        raise AssertionError("Free scan must not call historical")

    def stats(self):
        return {"daily_requests": 5, "daily_limit": 95, "rate_limits": 0, "errors": 0}


class FakeHistorical:
    def __init__(self, now):
        self.now = now

    def _df(self, n, freq):
        # Smooth uptrend with a mild acceleration and healthy last-bar volume.
        base = np.linspace(80.0, 100.0, n)
        zig = np.where(np.arange(n) % 4 < 3, 0.2, -0.4)
        close = base + zig
        volume = np.full(n, 1_000_000.0)
        volume[-1] = 1_500_000.0
        dt = pd.date_range(end=self.now, periods=n, freq=freq, tz="UTC")
        return pd.DataFrame({
            "datetime": dt,
            "open": close - 0.15,
            "high": close + 0.35,
            "low": close - 0.35,
            "close": close,
            "volume": volume,
        })

    async def datasets(self, symbol):
        return {"intraday": self._df(120, "15min"), "daily": self._df(260, "1D")}

    def validate_against_quote(self, df, price, max_gap_pct=15):
        return True

    def last_stamp(self, df):
        return df.iloc[-1]["datetime"].to_pydatetime()


def make_settings(tmp_path):
    return SimpleNamespace(
        state_dir=str(tmp_path),
        timezone="Asia/Riyadh",
        market_open="10:00",
        market_close="15:00",
        allow_off_hours_scan=False,
        universe_refresh_seconds=21600,
        market_cache_seconds=600,
        manual_quotes_per_signal=50,
        detail_quotes_per_signal=5,
        min_score=75,
        min_probability=65,
        max_daily_signals=3,
        max_open_trades=5,
        max_risk_per_trade=0.01,
        data_max_delay_minutes=30,
        min_rr=1.5,
        allow_long=True,
        paper_mode=True,
        sahmk_plan="free",
        trade_monitor_quotes_per_cycle=1,
        trailing_stop_enabled=False,
        trailing_after_tp1_to_entry=True,
        trailing_after_tp2_atr=1.0,
        profit_alert_thresholds="2,5,10,15,20",
        near_sl_warning_pct=0.5,
        weekly_report_enabled=True,
        weekly_report_weekday=3,
        weekly_report_hour=15,
        weekly_report_minute=5,
        scan_interval_seconds=900,
        historical_max_price_gap_pct=15.0,
        intraday_min_bars=60,
        swing_min_bars=120,
    )


def test_free_scan_uses_active_50_and_no_historical(tmp_path):
    async def run():
        local = datetime(2026, 8, 25, 11, 0, tzinfo=ZoneInfo("Asia/Riyadh"))
        now_utc = local.astimezone(timezone.utc)
        provider = FakeProvider(now_utc)
        bots = FakeBots()
        service = TradingService(make_settings(tmp_path), provider, bots, historical_provider=FakeHistorical(now_utc))
        service._local_now = lambda: local
        service._utc_now = lambda: now_utc

        result = await service.scan_once()

        # A scan may find no trade. If it finds one, it must be staged privately
        # and must NOT publish/register until explicit admin confirmation.
        assert ("بانتظار تأكيدك" in result) or ("لم توجد صفقة" in result)
        assert len(provider.detail_requested) <= 5
        assert provider.historical_called is False
        assert bots.sent == []
        if "بانتظار تأكيدك" in result:
            assert service.pending_signal() is not None
            assert service.store.state()["open_trades"] == []
            ok, confirmation = await service.confirm_pending_signal()
            assert ok is True
            assert "تم تأكيد ونشر" in confirmation
            assert len(bots.sent) == 1
            assert len(service.store.state()["open_trades"]) == 1

    asyncio.run(run())


def test_closed_market_does_not_consume_scan_api(tmp_path):
    async def run():
        local = datetime(2026, 8, 25, 4, 0, tzinfo=ZoneInfo("Asia/Riyadh"))
        now_utc = local.astimezone(timezone.utc)
        provider = FakeProvider(now_utc)
        bots = FakeBots()
        service = TradingService(make_settings(tmp_path), provider, bots, historical_provider=FakeHistorical(now_utc))
        service._local_now = lambda: local
        service._utc_now = lambda: now_utc

        async def fail(*args, **kwargs):
            raise AssertionError("API must not be called while market is closed")

        provider.companies = fail
        provider.market_summary = fail
        provider.top_volume_quotes = fail

        result = await service.scan_once()
        assert "السوق السعودي مغلق" in result

    asyncio.run(run())
