import asyncio
import json
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .base import DataProvider, Quote


class SahmkRateLimitError(RuntimeError):
    """Raised for SAHMK HTTP 429 responses without blocking the caller."""

    def __init__(self, message, *, retry_after=0.0, daily_exhausted=False):
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after or 0.0))
        self.daily_exhausted = bool(daily_exhausted)


class SahmkProvider(DataProvider):
    """SAHMK provider optimized for the Free plan.

    Key behavior:
    - Keeps requests below the 10/min free-plan burst limit.
    - Never sleeps for a long Retry-After inside a Telegram command.
    - Distinguishes temporary 429 throttles from daily-quota exhaustion.
    - Exposes server rate-limit headers so ProviderRouter can switch at the
      configured daily threshold.
    """

    def __init__(
        self,
        api_key,
        base_url,
        min_request_interval=6.5,
        local_daily_request_limit=100,
        timezone_name="Asia/Riyadh",
    ):
        self.base_url = str(base_url).rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=30,
            headers={
                "X-API-Key": api_key,
                "Accept": "application/json",
                "User-Agent": "TASI-KSA-Trading-Bot/1.0",
            },
        )

        self.max_retries = 2
        self.min_request_interval = max(6.1, float(min_request_interval))
        self.local_daily_request_limit = max(1, min(int(local_daily_request_limit), 100))
        self.tz = ZoneInfo(timezone_name)

        self._last_request_time = 0.0
        self._request_lock = asyncio.Lock()
        self._cooldown_until = 0.0

        self._request_count = 0
        self._daily_request_count = 0
        self._daily_request_date = self._today()
        self._rate_limit_count = 0
        self._request_errors = 0
        self._rate_limit_remaining = None
        self._rate_limit_reset = None
        self._daily_exhausted = False

        self.quote_cache = {}
        self.quote_cache_ttl = 600

    def _today(self):
        return datetime.now(self.tz).date()

    async def close(self):
        await self.client.aclose()

    def _reset_daily_counter_if_needed(self):
        today = self._today()
        if today != self._daily_request_date:
            self._daily_request_date = today
            self._daily_request_count = 0
            self._rate_limit_remaining = None
            self._rate_limit_reset = None
            self._daily_exhausted = False
            self._cooldown_until = 0.0
            print("[SAHMK] new Saudi day; counters reset")

    def _can_make_request(self):
        self._reset_daily_counter_if_needed()
        if self._daily_exhausted:
            return False
        return self._daily_request_count < self.local_daily_request_limit

    def cooldown_remaining(self):
        return max(0, int(self._cooldown_until - time.monotonic()))

    async def _rate_limit(self):
        async with self._request_lock:
            remaining = self.cooldown_remaining()
            if remaining > 0:
                raise SahmkRateLimitError(
                    f"SAHMK temporary throttle active; retry in {remaining}s",
                    retry_after=remaining,
                    daily_exhausted=False,
                )

            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self.min_request_interval:
                await asyncio.sleep(self.min_request_interval - elapsed)
            self._last_request_time = time.monotonic()

    def _capture_rate_headers(self, response):
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        try:
            if remaining is not None:
                self._rate_limit_remaining = int(remaining)
        except (TypeError, ValueError):
            pass
        try:
            if reset is not None:
                self._rate_limit_reset = int(reset)
        except (TypeError, ValueError):
            pass

    def _record_success(self, response):
        if 200 <= response.status_code < 400:
            self._daily_request_count += 1

    @staticmethod
    def _response_detail(response):
        try:
            payload = response.json()
        except Exception:
            return response.text or ""
        if isinstance(payload, dict):
            detail = payload.get("detail")
            if detail:
                return str(detail)
            error = payload.get("error")
            if isinstance(error, dict):
                return " ".join(str(error.get(k, "")) for k in ("code", "message")).strip()
        return json.dumps(payload, ensure_ascii=False)[:500]

    def _classify_429(self, response):
        detail = self._response_detail(response)
        text = detail.lower()
        retry_after_raw = response.headers.get("Retry-After")
        try:
            retry_after = float(retry_after_raw)
        except (TypeError, ValueError):
            retry_after = 65.0

        daily_tokens = (
            "daily",
            "daily limit",
            "daily quota",
            "quota exhausted",
            "quota exceeded",
            "الحصة اليومية",
            "الحد اليومي",
        )
        daily_exhausted = any(token in text for token in daily_tokens)

        # If SAHMK has explicitly told us zero daily requests remain,
        # treat the 429 as daily exhaustion even if detail text is terse.
        if self._rate_limit_remaining == 0:
            daily_exhausted = True

        if daily_exhausted:
            self._daily_exhausted = True
            self._cooldown_until = 0.0
        else:
            retry_after = max(10.0, min(retry_after, 3600.0))
            self._cooldown_until = max(
                self._cooldown_until,
                time.monotonic() + retry_after,
            )

        return detail, retry_after, daily_exhausted

    async def _get(self, path, params=None):
        normal_attempt = 0

        while True:
            if not self._can_make_request():
                raise SahmkRateLimitError(
                    "SAHMK daily request limit reached",
                    retry_after=0,
                    daily_exhausted=True,
                )

            await self._rate_limit()

            try:
                response = await self.client.get(self.base_url + path, params=params)
                self._request_count += 1
                self._capture_rate_headers(response)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                self._request_errors += 1
                if normal_attempt < self.max_retries:
                    normal_attempt += 1
                    await asyncio.sleep(min(2 ** normal_attempt, 10))
                    continue
                raise

            if response.status_code == 429:
                self._rate_limit_count += 1
                detail, retry_after, daily_exhausted = self._classify_429(response)
                kind = "daily" if daily_exhausted else "temporary"
                print(
                    f"[SAHMK] 429 {path}; type={kind}; "
                    f"retry-after={retry_after:.0f}s; detail={detail[:180]}"
                )
                raise SahmkRateLimitError(
                    f"SAHMK 429 ({kind}): {detail or 'rate limit'}",
                    retry_after=retry_after,
                    daily_exhausted=daily_exhausted,
                )

            if response.status_code == 403:
                self._request_errors += 1
                raise httpx.HTTPStatusError(
                    "SAHMK endpoint returned 403 Forbidden",
                    request=response.request,
                    response=response,
                )

            if response.status_code in (500, 502, 503, 504):
                self._request_errors += 1
                if normal_attempt < self.max_retries:
                    normal_attempt += 1
                    await asyncio.sleep(min(2 ** normal_attempt, 15))
                    continue

            if response.status_code >= 400:
                self._request_errors += 1
                response.raise_for_status()

            self._record_success(response)
            return response.json()

    def stats(self):
        self._reset_daily_counter_if_needed()
        server_used = None
        if self._rate_limit_remaining is not None:
            try:
                server_used = max(0, self.local_daily_request_limit - int(self._rate_limit_remaining))
            except (TypeError, ValueError):
                server_used = None

        effective_used = self._daily_request_count
        if server_used is not None:
            effective_used = max(effective_used, server_used)

        return {
            "provider": "sahmk",
            "requests": self._request_count,
            "daily_requests": effective_used,
            "local_successful_requests": self._daily_request_count,
            "daily_limit": self.local_daily_request_limit,
            "rate_limits": self._rate_limit_count,
            "errors": self._request_errors,
            "remaining": self._rate_limit_remaining,
            "reset": self._rate_limit_reset,
            "cooldown_remaining": self.cooldown_remaining(),
            "daily_exhausted": self._daily_exhausted,
        }

    async def companies(self, market="TASI"):
        out = []
        offset = 0
        while True:
            payload = await self._get(
                "/companies/",
                {"market": market, "limit": 100, "offset": offset},
            )
            if isinstance(payload, list):
                batch = payload
            elif isinstance(payload, dict):
                batch = payload.get("results", payload.get("companies", payload.get("data", [])))
            else:
                batch = []
            if not isinstance(batch, list):
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            offset += 100
            if offset > 2000:
                break
        return out

    def _parse_updated_at(self, value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def quote_from_payload(self, data, fallback_symbol=None):
        if not isinstance(data, dict):
            return None
        symbol = str(data.get("symbol", fallback_symbol or "")).strip()
        if not symbol:
            return None
        price = self._float(data.get("price"))
        if price <= 0:
            return None
        value = self._float(data.get("value", data.get("trading_value", data.get("net_liquidity", 0))))
        bid = self._float(data.get("bid"), None) if data.get("bid") is not None else None
        ask = self._float(data.get("ask"), None) if data.get("ask") is not None else None
        return Quote(
            symbol=symbol,
            name=data.get("name", "") or "",
            name_en=data.get("name_en", "") or "",
            price=price,
            change_percent=self._float(data.get("change_percent", data.get("change_pct", 0))),
            volume=self._float(data.get("volume")),
            value=value,
            bid=bid,
            ask=ask,
            updated_at=self._parse_updated_at(data.get("updated_at")),
            is_delayed=bool(data.get("is_delayed", True)),
            raw=data,
        )

    async def quote(self, symbol):
        symbol = str(symbol).strip()
        if not symbol:
            raise ValueError("Empty symbol")
        cached = self.quote_cache.get(symbol)
        if cached and time.monotonic() - cached[0] < self.quote_cache_ttl:
            return cached[1]
        data = await self._get(f"/quote/{symbol}/", {"data_mode": "delayed"})
        quote = self.quote_from_payload(data, symbol)
        if quote is None:
            raise ValueError(f"Invalid or empty quote response for {symbol}")
        self.quote_cache[symbol] = (time.monotonic(), quote)
        return quote

    async def quotes(self, symbols):
        normalized = list(dict.fromkeys(str(s).strip() for s in symbols if str(s).strip()))
        results = {}
        for symbol in normalized:
            try:
                results[symbol] = await self.quote(symbol)
            except SahmkRateLimitError:
                raise
            except Exception as exc:
                print(f"[SAHMK] quote {symbol} failed: {exc}")
        return results

    async def top_volume(self, limit=50, index="TASI"):
        limit = max(1, min(int(limit), 100))
        payload = await self._get(
            "/market/volume/",
            {"limit": limit, "index": index, "data_mode": "delayed"},
        )
        if isinstance(payload, dict):
            rows = payload.get("stocks", payload.get("results", payload.get("data", [])))
        elif isinstance(payload, list):
            rows = payload
        else:
            rows = []
        return rows if isinstance(rows, list) else []

    async def top_volume_quotes(self, limit=50, index="TASI"):
        rows = await self.top_volume(limit=limit, index=index)
        quotes = []
        for row in rows:
            quote = self.quote_from_payload(row)
            if quote is not None:
                quotes.append(quote)
        return quotes

    async def market_summary(self):
        return await self._get("/market/summary/")

    async def historical(self, symbol, days=250):
        symbol = str(symbol).strip()
        end = date.today()
        start = end - timedelta(days=max(days * 2, 365))
        return await self._get(
            f"/historical/{symbol}/",
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "interval": "1d",
                "limit": 2000,
            },
        )
