import asyncio
import time
from datetime import datetime, timezone

import httpx

from app.data.providers.base import DataProvider, Quote


class TasilabProvider(DataProvider):
    """
    Tasilab market-data provider.

    Used as the secondary provider behind ProviderRouter.

    Supported:
    - /v1/auth/me
    - /v1/market/quote/{symbol}
    - /v1/market/quotes
    - /v1/market/status

    Historical strategy data remains on Yahoo.
    """

    def __init__(self, settings):
        self.s = settings

        self.base_url = str(
            settings.tasilab_base_url
        ).strip().rstrip("/")

        self.api_key = str(
            settings.tasilab_api_key
        ).strip()

        self.timeout = float(
            settings.tasilab_timeout_seconds
        )

        self.min_interval = max(
            0.1,
            float(
                settings.tasilab_min_request_interval
            ),
        )

        self.client = httpx.AsyncClient(
            timeout=self.timeout,
            headers={
                "X-API-Key": self.api_key,
                "Accept": "application/json",
                "User-Agent": "TASI-KSA-Trading-Bot/1.0",
            },
        )

        self._last_request_at = 0.0
        self._request_lock = asyncio.Lock()

        self._request_count = 0
        self._successful_requests = 0
        self._rate_limit_count = 0
        self._request_errors = 0
        self._universe_symbols = []

        # 429 cooldown applies to the whole provider.
        self._cooldown_until = 0.0

        # Bulk endpoint health is tracked separately. A broken bulk endpoint
        # must not prevent the documented single-quote endpoint from working.
        self._bulk_cooldown_until = 0.0
        self._bulk_5xx_streak = 0

        # Provider-wide circuit opens only when SINGLE quotes repeatedly fail.
        self._circuit_until = 0.0
        self._single_5xx_streak = 0
        self._single_scan_cursor = 0

        self.max_retries = 1
        self.bulk_chunk_size = max(
            1,
            min(
                int(getattr(settings, "tasilab_bulk_chunk_size", 20)),
                50,
            ),
        )
        self.single_fallback_scan_limit = max(
            1,
            min(
                int(getattr(settings, "tasilab_single_fallback_scan_limit", 60)),
                100,
            ),
        )
        self.bulk_cooldown_seconds = max(
            30,
            int(getattr(settings, "tasilab_bulk_cooldown_seconds", 300)),
        )
        self.circuit_failure_threshold = max(
            2,
            int(getattr(settings, "tasilab_circuit_failure_threshold", 3)),
        )
        self.circuit_cooldown_seconds = max(
            30,
            int(getattr(settings, "tasilab_circuit_cooldown_seconds", 300)),
        )

    def set_universe(self, symbols):
        self._universe_symbols = list(dict.fromkeys(
            str(s).strip() for s in symbols if str(s).strip()
        ))

    # =========================================================
    # CLOSE
    # =========================================================

    async def close(self):
        await self.client.aclose()

    # =========================================================
    # THROTTLE
    # =========================================================

    async def _throttle(self):
        async with self._request_lock:
            now = time.monotonic()

            if now < self._cooldown_until:
                remaining = max(1, int(self._cooldown_until - now))
                raise RuntimeError(
                    f"Tasilab temporary rate limit active; retry in {remaining}s"
                )

            if now < self._circuit_until:
                remaining = max(1, int(self._circuit_until - now))
                raise RuntimeError(
                    f"Tasilab circuit open; retry in {remaining}s"
                )

            elapsed = now - self._last_request_at

            if elapsed < self.min_interval:
                await asyncio.sleep(
                    self.min_interval - elapsed
                )

            self._last_request_at = time.monotonic()

    # =========================================================
    # HTTP
    # =========================================================

    async def _get(
        self,
        path,
        params=None,
    ):
        last_exc = None

        for attempt in range(self.max_retries + 1):
            await self._throttle()

            try:
                response = await self.client.get(
                    f"{self.base_url}{path}",
                    params=params,
                )
                self._request_count += 1

            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.ReadError,
            ) as exc:
                self._request_errors += 1
                last_exc = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2 ** (attempt + 1), 5))
                    continue
                raise RuntimeError(
                    f"Tasilab network error: {exc}"
                ) from exc

            if response.status_code == 429:
                self._rate_limit_count += 1
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = 60.0
                wait = max(5.0, min(wait, 3600.0))
                self._cooldown_until = max(
                    self._cooldown_until,
                    time.monotonic() + wait,
                )
                raise httpx.HTTPStatusError(
                    f"Tasilab rate limit exceeded (HTTP 429); retry in {wait:.0f}s",
                    request=response.request,
                    response=response,
                )

            if response.status_code in (500, 502, 503, 504):
                self._request_errors += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(min(2 ** (attempt + 1), 5))
                    continue

            if response.status_code >= 400:
                self._request_errors += 1
                raise httpx.HTTPStatusError(
                    (
                        f"Tasilab HTTP {response.status_code}: "
                        f"{response.text[:300]}"
                    ),
                    request=response.request,
                    response=response,
                )

            self._successful_requests += 1
            self._cooldown_until = 0.0

            try:
                return response.json()
            except ValueError as exc:
                self._request_errors += 1
                raise RuntimeError(
                    "Tasilab returned invalid JSON"
                ) from exc

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Tasilab request failed")


    # =========================================================
    # FAILURE / CIRCUIT HELPERS
    # =========================================================

    @staticmethod
    def _http_status(exc):
        if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
            return exc.response.status_code
        return None

    @classmethod
    def _is_server_error(cls, exc):
        return cls._http_status(exc) in {500, 502, 503, 504}

    def _bulk_available(self):
        return time.monotonic() >= self._bulk_cooldown_until

    def _record_bulk_success(self):
        self._bulk_5xx_streak = 0

    def _record_bulk_5xx(self):
        self._bulk_5xx_streak += 1
        # One final _get retry has already happened before this is called.
        # Cool down bulk immediately so subsequent chunks degrade to singles.
        self._bulk_cooldown_until = max(
            self._bulk_cooldown_until,
            time.monotonic() + self.bulk_cooldown_seconds,
        )
        print(
            "[Tasilab] bulk endpoint cooldown "
            f"started for {self.bulk_cooldown_seconds}s "
            f"after 5xx (streak={self._bulk_5xx_streak})"
        )

    def _record_single_success(self):
        self._single_5xx_streak = 0
        self._circuit_until = 0.0

    def _record_single_5xx(self):
        self._single_5xx_streak += 1
        if self._single_5xx_streak >= self.circuit_failure_threshold:
            self._circuit_until = max(
                self._circuit_until,
                time.monotonic() + self.circuit_cooldown_seconds,
            )
            print(
                "[Tasilab] provider circuit opened for "
                f"{self.circuit_cooldown_seconds}s after "
                f"{self._single_5xx_streak} consecutive single-quote 5xx errors"
            )

    async def _single_quotes_bounded(self, symbols, limit=None):
        normalized = list(dict.fromkeys(
            str(symbol).strip() for symbol in symbols if str(symbol).strip()
        ))
        if limit is not None:
            normalized = normalized[: max(0, int(limit))]

        result = {}
        for symbol in normalized:
            try:
                result[symbol] = await self.quote(symbol)
            except Exception as exc:
                text = str(exc).lower()
                if self._is_server_error(exc):
                    self._record_single_5xx()
                    print(f"[Tasilab] single quote {symbol} server error: {exc}")
                    if time.monotonic() < self._circuit_until:
                        break
                elif "429" in text or "rate limit" in text or "circuit open" in text:
                    print(f"[Tasilab] single fallback stopped: {exc}")
                    break
                else:
                    print(f"[Tasilab] single quote {symbol} failed: {exc}")
        return result

    # =========================================================
    # HELPERS
    # =========================================================

    @staticmethod
    def _float(
        value,
        default=0.0,
    ):
        try:
            return float(value)

        except (
            TypeError,
            ValueError,
        ):
            return default

    @staticmethod
    def _parse_datetime(value):
        if value in (None, ""):
            return None
        try:
            # Unix timestamp support (seconds or milliseconds).
            if isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
                num = float(value)
                if num > 10_000_000_000:
                    num /= 1000.0
                return datetime.fromtimestamp(num, tz=timezone.utc)

            text = str(value).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError, OSError):
            return None

    @staticmethod
    def _extract_dict(payload):
        if not isinstance(payload, dict):
            return {}
        for key in ("data", "result", "quote"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _extract_rows(payload):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []

        for key in ("data", "results", "quotes", "stocks", "items"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return rows
            if isinstance(rows, dict):
                out = []
                for symbol, value in rows.items():
                    if isinstance(value, dict):
                        row = dict(value)
                        row.setdefault("symbol", str(symbol))
                        out.append(row)
                if out:
                    return out

        # Some APIs return a top-level symbol -> quote mapping.
        mapping_rows = []
        for symbol, value in payload.items():
            if isinstance(value, dict) and str(symbol).strip().isdigit():
                row = dict(value)
                row.setdefault("symbol", str(symbol))
                mapping_rows.append(row)
        return mapping_rows

    @classmethod
    def _find_timestamp(cls, data, fallback=None):
        if isinstance(data, dict):
            for key in (
                "updated_at", "last_updated", "last_update", "as_of", "asof",
                "timestamp", "datetime", "time", "quote_time", "market_time",
            ):
                if key in data:
                    parsed = cls._parse_datetime(data.get(key))
                    if parsed is not None:
                        return parsed, key
            for key in ("meta", "metadata"):
                nested = data.get(key)
                if isinstance(nested, dict):
                    parsed, source = cls._find_timestamp(nested)
                    if parsed is not None:
                        return parsed, f"{key}.{source}"
        parsed = cls._parse_datetime(fallback)
        return (parsed, "response") if parsed is not None else (None, "missing")

    # =========================================================
    # QUOTE NORMALIZATION
    # =========================================================

    def quote_from_payload(self, data, fallback_symbol=None, fallback_timestamp=None):
        if not isinstance(data, dict):
            return None

        symbol = str(data.get("symbol", fallback_symbol or "")).strip()
        if not symbol:
            return None

        price = self._float(
            data.get("price", data.get("last", data.get("last_price", data.get("close", data.get("current_price", 0)))))
        )
        if price <= 0:
            return None

        change_percent = self._float(
            data.get("change_percent", data.get("change_pct", data.get("percent_change", data.get("pct_change", 0))))
        )
        volume = self._float(data.get("volume", data.get("trading_volume", data.get("total_volume", 0))))
        value = self._float(data.get("value", data.get("trading_value", data.get("value_traded", data.get("turnover", 0)))))
        bid = self._float(data.get("bid", data.get("bid_price")), None) if data.get("bid", data.get("bid_price")) is not None else None
        ask = self._float(data.get("ask", data.get("ask_price")), None) if data.get("ask", data.get("ask_price")) is not None else None

        updated_at, timestamp_source = self._find_timestamp(data, fallback_timestamp)
        raw = dict(data)
        raw["_timestamp_source"] = timestamp_source

        # Tasilab Free is a 15-minute delayed snapshot. If the endpoint omits a
        # market timestamp, the HTTP fetch time is a safe freshness bound for
        # transport health; raw metadata records that this is fetch-time based.
        if updated_at is None:
            updated_at = datetime.now(timezone.utc)
            raw["_timestamp_source"] = "fetch_time"

        return Quote(
            symbol=symbol,
            name=data.get("name", "") or data.get("company_name", "") or data.get("name_ar", "") or "",
            name_en=data.get("name_en", "") or data.get("english_name", "") or "",
            price=price,
            change_percent=change_percent,
            volume=volume,
            value=value,
            bid=bid,
            ask=ask,
            updated_at=updated_at,
            is_delayed=bool(data.get("is_delayed", True)),
            raw=raw,
        )

    # =========================================================
    # AUTH
    # =========================================================

    async def me(self):
        return await self._get(
            "/v1/auth/me"
        )

    # =========================================================
    # DIAGNOSTICS
    # =========================================================

    async def _diagnostic_get(self, path, params=None):
        """Perform one isolated diagnostic request.

        This intentionally bypasses provider circuit/cooldown state so an
        operator can determine whether a failure is auth-, endpoint-,
        Cloudflare-, or upstream-related. It never logs or returns the API key.
        """
        started = time.monotonic()
        try:
            response = await self.client.get(
                f"{self.base_url}{path}",
                params=params,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            body_preview = response.text[:240].replace("\n", " ").strip()
            server = response.headers.get("server", "")
            cf_ray = response.headers.get("cf-ray", "")
            retry_after = response.headers.get("retry-after", "")
            ok = 200 <= response.status_code < 400
            return {
                "ok": ok,
                "status": response.status_code,
                "latency_ms": elapsed_ms,
                "server": server,
                "cloudflare": bool(cf_ray or "cloudflare" in server.lower()),
                "cf_ray": cf_ray,
                "retry_after": retry_after,
                "body_preview": body_preview if not ok else "",
            }
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            return {
                "ok": False,
                "status": None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "server": "",
                "cloudflare": False,
                "cf_ray": "",
                "retry_after": "",
                "body_preview": f"network error: {type(exc).__name__}: {exc}",
            }

    @staticmethod
    def _diagnostic_classification(results):
        auth = results.get("auth", {})
        status = results.get("market_status", {})
        single = results.get("single_quote", {})
        bulk = results.get("bulk_quotes", {})

        if auth.get("status") in {401, 403}:
            return "AUTH_ERROR"
        if any(item.get("status") == 429 for item in results.values()):
            return "RATE_LIMIT"

        endpoint_results = [status, single, bulk]
        server_failures = [
            item for item in endpoint_results
            if item.get("status") in {500, 502, 503, 504}
        ]
        if len(server_failures) >= 2:
            return "PROVIDER_OR_UPSTREAM_5XX"
        if bulk.get("status") in {500, 502, 503, 504} and single.get("ok"):
            return "BULK_ENDPOINT_5XX_ONLY"
        if single.get("status") in {500, 502, 503, 504} and status.get("ok"):
            return "QUOTE_ENDPOINT_5XX"
        required = [auth, status, single]
        if bulk:
            required.append(bulk)
        if all(item.get("ok") for item in required):
            return "HEALTHY"
        if any(item.get("status") in {400, 404, 422} for item in endpoint_results):
            return "ENDPOINT_OR_PARAMETER_ERROR"
        if any(item.get("status") is None for item in results.values()):
            return "NETWORK_OR_TIMEOUT"
        return "DEGRADED_UNKNOWN"

    async def diagnose(self, symbol="1120", include_bulk=True):
        """Run a small Tasilab health matrix for Render/operator diagnosis.

        include_bulk=False is intended for the private Telegram command so the
        health check stays lightweight (auth + market status + one quote).
        """
        symbol = str(symbol).strip() or "1120"
        secondary = "2222" if symbol != "2222" else "1120"

        results = {}
        results["auth"] = await self._diagnostic_get("/v1/auth/me")

        # Invalid credentials make the remaining calls less useful and would
        # only create extra traffic.
        if results["auth"].get("status") in {401, 403}:
            return {
                "classification": self._diagnostic_classification(results),
                "base_url": self.base_url,
                "symbol": symbol,
                "checks": results,
            }

        results["market_status"] = await self._diagnostic_get("/v1/market/status")
        results["single_quote"] = await self._diagnostic_get(
            f"/v1/market/quote/{symbol}"
        )
        if include_bulk:
            results["bulk_quotes"] = await self._diagnostic_get(
                "/v1/market/quotes",
                params={"symbols": f"{symbol},{secondary}"},
            )

        return {
            "classification": self._diagnostic_classification(results),
            "base_url": self.base_url,
            "symbol": symbol,
            "checks": results,
        }

    # =========================================================
    # COMPANIES
    # =========================================================

    async def companies(
        self,
        market="TASI",
    ):
        """
        Universe remains sourced from SAHMK.

        ProviderRouter prevents this method from being
        used in normal operation.
        """

        raise RuntimeError(
            "Tasilab companies endpoint not configured"
        )

    # =========================================================
    # SINGLE QUOTE
    # =========================================================

    async def quote(
        self,
        symbol,
    ):
        symbol = str(symbol).strip()

        if not symbol:
            raise ValueError(
                "Empty symbol"
            )

        payload = await self._get(
            f"/v1/market/quote/{symbol}"
        )

        data = self._extract_dict(
            payload
        )

        quote = self.quote_from_payload(
            data,
            symbol,
        )

        if quote is None:
            raise ValueError(
                f"Invalid or empty Tasilab quote for {symbol}"
            )

        self._record_single_success()
        return quote

    async def _bulk_quotes(self, symbols):
        normalized = list(dict.fromkeys(
            str(symbol).strip() for symbol in symbols if str(symbol).strip()
        ))
        if not normalized:
            return {}

        fetched_at = datetime.now(timezone.utc)

        # Documented endpoint requires `symbols`. Comma-separated form is the
        # primary request; repeated params are retried only when response
        # coverage is suspiciously low.
        payload = await self._get(
            "/v1/market/quotes",
            params={"symbols": ",".join(normalized)},
        )
        rows = self._extract_rows(payload)

        def normalize_rows(rows_):
            result_ = {}
            for row in rows_:
                if not isinstance(row, dict):
                    continue
                symbol = str(row.get("symbol", "")).strip()
                if not symbol:
                    continue
                quote = self.quote_from_payload(row, symbol, fetched_at)
                if quote is not None:
                    result_[symbol] = quote
            return result_

        result = normalize_rows(rows)

        # A one-row response for a large symbol batch is not useful for market
        # screening. Try the standard repeated-query representation once.
        if len(normalized) > 3 and len(result) < max(2, int(len(normalized) * 0.20)):
            try:
                payload2 = await self._get(
                    "/v1/market/quotes",
                    params=[("symbols", symbol) for symbol in normalized],
                )
                result2 = normalize_rows(self._extract_rows(payload2))
                if len(result2) > len(result):
                    result = result2
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status not in {400, 404, 422}:
                    raise

        return result

    async def _bulk_quotes_resilient(self, symbols):
        """Fetch bulk quotes while isolating stale/invalid bootstrap symbols.

        A future delisting or symbol change should not make an entire TASI chunk
        unusable. Validation-like failures are bisected until the bad symbol is
        isolated. Rate limits and network failures are still surfaced normally.
        """
        normalized = list(dict.fromkeys(
            str(symbol).strip() for symbol in symbols if str(symbol).strip()
        ))
        if not normalized:
            return {}

        try:
            return await self._bulk_quotes(normalized)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in {400, 404, 422} or len(normalized) <= 1:
                if len(normalized) == 1 and status in {400, 404, 422}:
                    print(
                        f"[Tasilab] skipping invalid/stale symbol {normalized[0]} "
                        f"after HTTP {status}"
                    )
                    return {}
                raise

            middle = len(normalized) // 2
            left = await self._bulk_quotes_resilient(normalized[:middle])
            right = await self._bulk_quotes_resilient(normalized[middle:])
            left.update(right)
            return left

    # =========================================================
    # MULTIPLE QUOTES
    # =========================================================

    async def quotes(
        self,
        symbols,
    ):
        """Fetch finalist quotes with graceful bulk degradation.

        Policy:
        - bulk first while the bulk endpoint is healthy;
        - one retry is already handled by _get();
        - 5xx from bulk -> bulk cooldown + single-quote fallback;
        - provider-wide circuit opens only if single quotes also return 5xx.
        """
        normalized = list(dict.fromkeys(
            str(symbol).strip() for symbol in symbols if str(symbol).strip()
        ))
        if not normalized:
            return {}

        result = {}

        if self._bulk_available():
            try:
                result = await self._bulk_quotes_resilient(normalized)
                self._record_bulk_success()
            except Exception as exc:
                text = str(exc).lower()
                if self._is_server_error(exc):
                    self._record_bulk_5xx()
                elif "429" in text or "rate limit" in text:
                    print(f"[Tasilab] bulk quotes stopped by rate limit: {exc}")
                    return result
                else:
                    print(f"[Tasilab] bulk quotes failed: {exc}")

        # Fill any missing finalists with the single endpoint. For small
        # detailed requests this gives the strategy the best possible coverage.
        missing = [symbol for symbol in normalized if symbol not in result]
        if missing:
            singles = await self._single_quotes_bounded(missing)
            result.update(singles)

        return result

    # =========================================================
    # MARKET STATUS
    # =========================================================

    async def market_summary(self):
        payload = await self._get(
            "/v1/market/status"
        )

        data = self._extract_dict(
            payload
        )

        return {
            "value": (
                data.get("index_value")
                or data.get("value")
                or data.get("index")
                or data.get("tasi")
                or 0
            ),

            "change_percent": (
                data.get("change_percent")
                or data.get("change_pct")
                or 0
            ),

            "advancers": (
                data.get("advancers")
                or data.get("advancing")
                or 0
            ),

            "decliners": (
                data.get("decliners")
                or data.get("declining")
                or 0
            ),

            "trading_value": (
                data.get("trading_value")
                or data.get("value_traded")
                or 0
            ),

            "mood": (
                data.get("mood")
                or data.get("market_mood")
                or ""
            ),

            "raw": data,
        }

    # =========================================================
    # TOP VOLUME
    # =========================================================

    async def top_volume_quotes(
        self,
        limit=50,
        market="TASI",
    ):
        """Build a local volume ranking with resilient degradation.

        Normal path: Tasilab bulk quotes in small chunks.
        If bulk returns 5xx after its one retry, bulk is cooled down and this
        scan continues through a bounded number of single quote calls.
        If single quotes also fail repeatedly, a provider-wide circuit opens.
        """
        limit = max(1, min(int(limit), 100))
        symbols = list(self._universe_symbols)
        if not symbols:
            print("[Tasilab] top-volume unavailable: no cached TASI universe")
            return []

        collected = {}
        chunk_size = self.bulk_chunk_size
        single_budget = self.single_fallback_scan_limit
        single_used = 0
        bulk_degraded = not self._bulk_available()

        # When bulk is already cooling down, rotate the single-quote window so
        # repeated manual scans eventually cover the entire bootstrap universe
        # instead of always checking the same first symbols.
        if bulk_degraded and symbols:
            start = self._single_scan_cursor % len(symbols)
            symbols = symbols[start:] + symbols[:start]
            self._single_scan_cursor = (start + single_budget) % len(symbols)

        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i + chunk_size]

            if bulk_degraded:
                remaining_budget = single_budget - single_used
                if remaining_budget <= 0:
                    break
                sample = chunk[:remaining_budget]
                result = await self._single_quotes_bounded(sample)
                single_used += len(sample)
                collected.update(result)
                if time.monotonic() < self._circuit_until:
                    break
                continue

            try:
                result = await self._bulk_quotes_resilient(chunk)
                self._record_bulk_success()
                collected.update(result)
            except Exception as exc:
                text = str(exc).lower()
                if "429" in text or "rate limit" in text:
                    print(f"[Tasilab] bulk scan stopped by rate limit: {exc}")
                    break

                if self._is_server_error(exc):
                    print(f"[Tasilab] bulk chunk server error: {exc}")
                    self._record_bulk_5xx()
                    bulk_degraded = True

                    remaining_budget = single_budget - single_used
                    if remaining_budget <= 0:
                        break
                    sample = chunk[:remaining_budget]
                    fallback = await self._single_quotes_bounded(sample)
                    single_used += len(sample)
                    collected.update(fallback)
                    if time.monotonic() < self._circuit_until:
                        break
                    continue

                print(f"[Tasilab] bulk chunk failed: {exc}")
                continue

        quotes = list(collected.values())
        quotes.sort(key=lambda q: (q.volume, q.value), reverse=True)

        if bulk_degraded:
            print(
                "[Tasilab] degraded scan completed: "
                f"single_requests={single_used} quotes={len(quotes)}"
            )

        return quotes[:limit]

    # =========================================================
    # HISTORICAL
    # =========================================================

    async def historical(
        self,
        symbol,
        days=250,
    ):
        """
        Required by DataProvider.

        TradingService currently uses YahooHistoricalProvider
        for historical analysis.
        """

        raise RuntimeError(
            "Tasilab historical is disabled in this project; "
            "Yahoo is the configured historical provider"
        )

    # =========================================================
    # STATS
    # =========================================================

    def stats(self):
        return {
            "provider": "tasilab",

            "requests": (
                self._request_count
            ),

            "daily_requests": (
                self._successful_requests
            ),

            "daily_limit": "—",

            "remaining": "—",

            "rate_limits": (
                self._rate_limit_count
            ),

            "errors": (
                self._request_errors
            ),

            "cooldown_remaining": max(
                0,
                int(self._cooldown_until - time.monotonic()),
            ),

            "bulk_cooldown_remaining": max(
                0,
                int(self._bulk_cooldown_until - time.monotonic()),
            ),

            "circuit_open": time.monotonic() < self._circuit_until,

            "circuit_remaining": max(
                0,
                int(self._circuit_until - time.monotonic()),
            ),

            "single_5xx_streak": self._single_5xx_streak,
        }
