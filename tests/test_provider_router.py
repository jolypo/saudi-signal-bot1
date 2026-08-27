import asyncio
import httpx
from types import SimpleNamespace

from app.data.provider_router import ProviderRouter
from app.data.providers.base import Quote
from app.data.providers.sahmk import SahmkRateLimitError


class FakeSahmk:
    def __init__(self, used=0, remaining=None):
        self.used = used
        self.remaining = remaining
        self.daily_exhausted = False
        self.mode = "ok"

    def stats(self):
        return {
            "daily_requests": self.used,
            "daily_limit": 100,
            "remaining": self.remaining,
            "rate_limits": 0,
            "errors": 0,
            "daily_exhausted": self.daily_exhausted,
            "cooldown_remaining": 0,
        }

    async def companies(self, market="TASI"):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        return [
            {"symbol": "1120", "security_type": "equity"},
            {"symbol": "2222", "security_type": "equity"},
        ]

    async def quote(self, symbol):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        if self.mode == "403":
            request = httpx.Request("GET", "https://example.test/quote")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)
        return Quote(symbol=symbol, name="", name_en="", price=10)

    async def market_summary(self):
        return {"change_percent": 0}

    async def quotes(self, symbols):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        return {s: Quote(symbol=s, name="", name_en="", price=10) for s in symbols}

    async def top_volume_quotes(self, limit=50, market="TASI"):
        if self.mode == "temp429":
            raise SahmkRateLimitError("temporary", retry_after=600, daily_exhausted=False)
        if self.mode == "daily429":
            raise SahmkRateLimitError("daily", retry_after=0, daily_exhausted=True)
        return [Quote(symbol="1120", name="", name_en="", price=10, volume=1000)]


class FakeTasilab:
    def __init__(self):
        self.calls = 0
        self.universe = []

    def set_universe(self, symbols):
        self.universe = list(symbols)

    def stats(self):
        return {"daily_requests": self.calls, "rate_limits": 0, "errors": 0}

    async def quote(self, symbol):
        self.calls += 1
        return Quote(symbol=symbol, name="", name_en="", price=20)

    async def market_summary(self):
        self.calls += 1
        return {"change_percent": 1}

    async def quotes(self, symbols):
        self.calls += 1
        return {s: Quote(symbol=s, name="", name_en="", price=20) for s in symbols}

    async def top_volume_quotes(self, limit=50, market="TASI"):
        self.calls += 1
        return [Quote(symbol="2222", name="", name_en="", price=20, volume=2000)]


def settings(tmp_path):
    return SimpleNamespace(
        timezone="Asia/Riyadh",
        state_dir=str(tmp_path),
        sahmk_daily_switch_limit=90,
        sahmk_local_daily_limit=100,
        provider_switch_on_daily_limit=True,
        provider_fallback_enabled=True,
    )


def test_temporary_429_does_not_switch_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=5)
        sahmk.mode = "temp429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        result = await router.quotes(["1120"])
        assert result == {}
        assert tasi.calls == 0
        assert router.active_provider() == "sahmk"
    asyncio.run(run())


def test_daily_threshold_switches_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=90)
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        quote = await router.quote("1120")
        assert quote.price == 20
        assert tasi.calls == 1
        assert router.active_provider() == "tasilab"
    asyncio.run(run())


def test_daily_429_switches_same_request_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=40)
        sahmk.mode = "daily429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        quote = await router.quote("1120")
        assert quote.price == 20
        assert tasi.calls == 1
        assert router.active_provider() == "tasilab"
    asyncio.run(run())


def test_universe_cached_for_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=1)
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        await router.companies("TASI")
        assert tasi.universe == ["1120", "2222"]
        assert (tmp_path / "universe_cache.json").exists()
    asyncio.run(run())


def test_fresh_deploy_daily_429_uses_bundled_universe(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=10)
        sahmk.mode = "daily429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)

        companies = await router.companies("TASI")

        assert len(companies) >= 200
        assert len(tasi.universe) >= 200
        assert router.active_provider() == "tasilab"
        assert router.stats()["universe_source"] == "bundled_bootstrap"

    asyncio.run(run())


def test_temporary_429_universe_falls_back_without_switch(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=10)
        sahmk.mode = "temp429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)

        companies = await router.companies("TASI")

        assert len(companies) >= 200
        assert router.active_provider() == "sahmk"
        assert tasi.calls == 0

    asyncio.run(run())


def test_daily_ip_like_failure_can_continue_with_tasilab_top_volume(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=10)
        sahmk.mode = "daily429"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)

        companies = await router.companies("TASI")
        rows = await router.top_volume_quotes(50, "TASI")

        assert len(companies) >= 200
        assert tasi.universe
        assert rows and rows[0].symbol == "2222"
        assert router.active_provider() == "tasilab"

    asyncio.run(run())


def test_sahmk_403_one_call_falls_back_to_tasilab(tmp_path):
    async def run():
        sahmk = FakeSahmk(used=2)
        sahmk.mode = "403"
        tasi = FakeTasilab()
        router = ProviderRouter(settings(tmp_path), sahmk, tasi)
        quote = await router.quote("1120")
        assert quote.price == 20
        assert tasi.calls == 1
        # 403 fallback is per-call; it does not falsely mark the daily quota exhausted.
        assert router.active_provider() == "sahmk"
    asyncio.run(run())
