import asyncio
from types import SimpleNamespace

from app.data.tasilab import TasilabProvider


def make_settings():
    return SimpleNamespace(
        tasilab_base_url="https://api.tasilab.com",
        tasilab_api_key="test",
        tasilab_timeout_seconds=15,
        tasilab_min_request_interval=0.1,
    )


def test_top_volume_always_supplies_symbols():
    async def run():
        provider = TasilabProvider(make_settings())
        provider.set_universe(["1120", "2222"])
        seen = []

        async def fake_get(path, params=None):
            seen.append((path, params))
            symbols = (params or {}).get("symbols", "").split(",")
            return {
                "data": [
                    {"symbol": s, "price": 10, "volume": 100 if s == "1120" else 200}
                    for s in symbols if s
                ]
            }

        provider._get = fake_get
        rows = await provider.top_volume_quotes(2, "TASI")
        assert [q.symbol for q in rows] == ["2222", "1120"]
        assert seen
        assert all(call[1].get("symbols") for call in seen)
        await provider.close()

    asyncio.run(run())


def test_resilient_bulk_isolates_invalid_symbol():
    async def run():
        provider = TasilabProvider(make_settings())
        calls = []

        async def fake_bulk(symbols):
            calls.append(list(symbols))
            if "9999" in symbols:
                request = __import__("httpx").Request("GET", "https://api.tasilab.com/v1/market/quotes")
                response = __import__("httpx").Response(422, request=request)
                raise __import__("httpx").HTTPStatusError(
                    "invalid symbol", request=request, response=response
                )
            from app.data.providers.base import Quote
            return {s: Quote(symbol=s, name="", name_en="", price=10) for s in symbols}

        provider._bulk_quotes = fake_bulk
        result = await provider._bulk_quotes_resilient(["1120", "9999", "2222"])
        assert set(result) == {"1120", "2222"}
        assert len(calls) > 1
        await provider.close()

    asyncio.run(run())


def test_top_volume_chunks_large_bootstrap_universe():
    async def run():
        provider = TasilabProvider(make_settings())
        provider.set_universe([str(1000 + i) for i in range(250)])
        seen = []

        async def fake_get(path, params=None):
            symbols = (params or {}).get("symbols", "").split(",")
            seen.append([s for s in symbols if s])
            return {
                "data": [
                    {"symbol": s, "price": 10, "volume": int(s)}
                    for s in symbols if s
                ]
            }

        provider._get = fake_get
        rows = await provider.top_volume_quotes(50, "TASI")
        assert len(seen) == 13
        assert all(len(chunk) <= 20 for chunk in seen)
        assert len(rows) == 50
        await provider.close()

    asyncio.run(run())


def test_top_volume_stops_after_tasilab_rate_limit():
    async def run():
        import httpx

        provider = TasilabProvider(make_settings())
        provider.set_universe([str(1000 + i) for i in range(120)])
        calls = 0

        async def fake_resilient(symbols):
            nonlocal calls
            calls += 1
            request = httpx.Request("GET", "https://api.tasilab.com/v1/market/quotes")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("rate limit", request=request, response=response)

        provider._bulk_quotes_resilient = fake_resilient
        rows = await provider.top_volume_quotes(50, "TASI")
        assert rows == []
        assert calls == 1
        await provider.close()

    asyncio.run(run())


def test_quote_accepts_symbol_mapping_and_missing_timestamp_uses_fetch_time():
    async def run():
        provider = TasilabProvider(make_settings())

        async def fake_get(path, params=None):
            return {
                "quotes": {
                    "1120": {"price": 30.5, "volume": 1000},
                    "2222": {"price": 25.0, "volume": 2000},
                }
            }

        provider._get = fake_get
        rows = await provider._bulk_quotes(["1120", "2222"])
        assert set(rows) == {"1120", "2222"}
        assert rows["1120"].updated_at is not None
        assert rows["1120"].raw["_timestamp_source"] in {"response", "fetch_time"}
        await provider.close()

    asyncio.run(run())


def test_parse_unix_millisecond_timestamp():
    provider = TasilabProvider(make_settings())
    dt = provider._parse_datetime(1787745600000)
    assert dt is not None
    assert dt.tzinfo is not None
    asyncio.run(provider.close())



def test_bulk_502_degrades_to_single_quotes():
    async def run():
        import httpx
        from app.data.providers.base import Quote

        provider = TasilabProvider(make_settings())
        provider.set_universe(["1120", "2222", "2010"])
        provider.single_fallback_scan_limit = 3
        single_calls = []

        async def fake_bulk(symbols):
            request = httpx.Request("GET", "https://api.tasilab.com/v1/market/quotes")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("bad gateway", request=request, response=response)

        async def fake_quote(symbol):
            single_calls.append(symbol)
            provider._record_single_success()
            return Quote(symbol=symbol, name="", name_en="", price=10, volume=float(symbol))

        provider._bulk_quotes_resilient = fake_bulk
        provider.quote = fake_quote
        rows = await provider.top_volume_quotes(3, "TASI")
        assert len(rows) == 3
        assert set(single_calls) == {"1120", "2222", "2010"}
        assert provider.stats()["bulk_cooldown_remaining"] > 0
        assert provider.stats()["circuit_open"] is False
        await provider.close()

    asyncio.run(run())


def test_single_quote_5xx_opens_provider_circuit():
    async def run():
        import httpx

        provider = TasilabProvider(make_settings())
        provider.circuit_failure_threshold = 3
        provider.circuit_cooldown_seconds = 300

        async def fake_quote(symbol):
            request = httpx.Request("GET", f"https://api.tasilab.com/v1/market/quote/{symbol}")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("bad gateway", request=request, response=response)

        provider.quote = fake_quote
        result = await provider._single_quotes_bounded(["1120", "2222", "2010", "1211"])
        assert result == {}
        stats = provider.stats()
        assert stats["circuit_open"] is True
        assert stats["circuit_remaining"] > 0
        await provider.close()

    asyncio.run(run())


def test_diagnose_classifies_provider_wide_502():
    async def run():
        provider = TasilabProvider(make_settings())
        calls = []

        async def fake_diag(path, params=None):
            calls.append((path, params))
            if path == "/v1/auth/me":
                return {
                    "ok": True,
                    "status": 200,
                    "latency_ms": 10,
                    "server": "cloudflare",
                    "cloudflare": True,
                    "cf_ray": "abc",
                    "retry_after": "",
                    "body_preview": "",
                }
            return {
                "ok": False,
                "status": 502,
                "latency_ms": 20,
                "server": "cloudflare",
                "cloudflare": True,
                "cf_ray": "xyz",
                "retry_after": "",
                "body_preview": "bad gateway",
            }

        provider._diagnostic_get = fake_diag
        report = await provider.diagnose("1120")
        assert report["classification"] == "PROVIDER_OR_UPSTREAM_5XX"
        assert len(calls) == 4
        await provider.close()

    asyncio.run(run())


def test_diagnose_stops_after_auth_error():
    async def run():
        provider = TasilabProvider(make_settings())
        calls = []

        async def fake_diag(path, params=None):
            calls.append((path, params))
            return {
                "ok": False,
                "status": 401,
                "latency_ms": 5,
                "server": "",
                "cloudflare": False,
                "cf_ray": "",
                "retry_after": "",
                "body_preview": "unauthorized",
            }

        provider._diagnostic_get = fake_diag
        report = await provider.diagnose("1120")
        assert report["classification"] == "AUTH_ERROR"
        assert len(calls) == 1
        await provider.close()

    asyncio.run(run())


def test_diagnose_classifies_bulk_only_failure():
    async def run():
        provider = TasilabProvider(make_settings())

        async def fake_diag(path, params=None):
            if path == "/v1/market/quotes":
                status = 502
                ok = False
            else:
                status = 200
                ok = True
            return {
                "ok": ok,
                "status": status,
                "latency_ms": 5,
                "server": "cloudflare",
                "cloudflare": True,
                "cf_ray": "test",
                "retry_after": "",
                "body_preview": "bad gateway" if not ok else "",
            }

        provider._diagnostic_get = fake_diag
        report = await provider.diagnose("1120")
        assert report["classification"] == "BULK_ENDPOINT_5XX_ONLY"
        await provider.close()

    asyncio.run(run())


def test_diagnose_light_mode_skips_bulk():
    async def run():
        provider = TasilabProvider(make_settings())
        calls = []

        async def fake_diag(path, params=None):
            calls.append((path, params))
            return {
                "ok": True,
                "status": 200,
                "latency_ms": 5,
                "server": "cloudflare",
                "cloudflare": True,
                "cf_ray": "test",
                "retry_after": "",
                "body_preview": "",
            }

        provider._diagnostic_get = fake_diag
        report = await provider.diagnose("1120", include_bulk=False)
        assert report["classification"] == "HEALTHY"
        assert [path for path, _ in calls] == [
            "/v1/auth/me",
            "/v1/market/status",
            "/v1/market/quote/1120",
        ]
        await provider.close()

    asyncio.run(run())
