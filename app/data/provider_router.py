from __future__ import annotations

import json
import httpx
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.data.providers.sahmk import SahmkRateLimitError


class ProviderRouter:
    """Route market data between SAHMK and Tasilab safely.

    Policy:
    - SAHMK is primary while its daily budget is available.
    - A temporary SAHMK 429 never switches provider. The request fails fast
      and the caller can try again after Retry-After.
    - A daily/account/IP daily 429 switches to Tasilab for the rest of the
      Saudi calendar day.
    - Reaching SAHMK_DAILY_SWITCH_LIMIT also switches to Tasilab.
    - A bundled TASI equity universe is always available, so a fresh Render
      deploy can still use Tasilab even when SAHMK is already daily-limited.
    - Live SAHMK company metadata replaces the bundled bootstrap universe
      whenever it is available.
    """

    def __init__(self, settings, sahmk_provider, tasilab_provider):
        self.s = settings
        self.sahmk = sahmk_provider
        self.tasilab = tasilab_provider
        self.tz = ZoneInfo(getattr(settings, "timezone", "Asia/Riyadh"))
        self._last_day = self._today_key()
        self._forced_daily_switch = False

        self._runtime_cache_path = (
            Path(getattr(settings, "state_dir", "data")) / "universe_cache.json"
        )
        self._bootstrap_path = self._resolve_bootstrap_path()
        self._universe_symbols, self._universe_source = self._load_best_universe()
        self._sync_tasilab_universe()

        print(
            f"[router] universe ready: {len(self._universe_symbols)} symbols "
            f"source={self._universe_source}"
        )

    # =========================================================
    # TIME
    # =========================================================

    def _today_key(self):
        return datetime.now(self.tz).date().isoformat()

    def _reset_day_if_needed(self):
        current = self._today_key()
        if current != self._last_day:
            self._last_day = current
            self._forced_daily_switch = False
            print("[router] new Saudi day; SAHMK restored as primary provider")

    # =========================================================
    # UNIVERSE
    # =========================================================

    def _resolve_bootstrap_path(self):
        configured = getattr(self.s, "bootstrap_universe_file", "app/data/tasi_universe.json")
        path = Path(str(configured))
        if path.is_absolute():
            return path

        # Render/Docker starts with the repository at /app. Local tests may
        # start from another cwd, so also resolve relative to this module.
        cwd_path = Path.cwd() / path
        if cwd_path.exists():
            return cwd_path

        app_dir = Path(__file__).resolve().parents[2]
        if str(path).startswith("app/"):
            return app_dir.parent / path
        return app_dir / "data" / path.name

    @staticmethod
    def _extract_symbols(payload):
        if not isinstance(payload, dict):
            return []
        symbols = payload.get("symbols")
        if not isinstance(symbols, list):
            return []
        return list(
            dict.fromkeys(
                str(symbol).strip()
                for symbol in symbols
                if str(symbol).strip()
            )
        )

    def _load_json_symbols(self, path):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return self._extract_symbols(payload)
        except Exception:
            return []

    def _load_best_universe(self):
        runtime = self._load_json_symbols(self._runtime_cache_path)
        if runtime:
            return runtime, "runtime_cache"

        bundled = self._load_json_symbols(self._bootstrap_path)
        if bundled:
            return bundled, "bundled_bootstrap"

        return [], "none"

    def _save_runtime_universe(self):
        if not self._universe_symbols:
            return
        try:
            self._runtime_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._runtime_cache_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "market": "TASI",
                        "source": "sahmk_runtime",
                        "updated_at": datetime.now(self.tz).isoformat(),
                        "symbols": self._universe_symbols,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._runtime_cache_path)
        except Exception as exc:
            print(f"[router] universe cache save failed: {exc}")

    def _sync_tasilab_universe(self):
        if hasattr(self.tasilab, "set_universe"):
            self.tasilab.set_universe(self._universe_symbols)

    def cached_companies(self):
        """Return the local runtime/bundled universe without any API request."""
        return self._bootstrap_companies()

    def _bootstrap_companies(self):
        return [
            {
                "symbol": symbol,
                "name": "",
                "name_en": "",
                "sector": "",
                "security_type": "equity",
                "metadata_source": self._universe_source,
            }
            for symbol in self._universe_symbols
        ]

    # =========================================================
    # SAHMK DAILY STATE
    # =========================================================

    def _sahmk_stats(self):
        try:
            stats = self.sahmk.stats() if hasattr(self.sahmk, "stats") else {}
            return stats if isinstance(stats, dict) else {}
        except Exception as exc:
            print(f"[router] unable to read SAHMK stats: {exc}")
            return {}

    def _sahmk_switch_limit(self):
        return max(1, int(getattr(self.s, "sahmk_daily_switch_limit", 90)))

    def _sahmk_daily_limit_reached(self):
        self._reset_day_if_needed()

        if self._forced_daily_switch:
            return True

        if not getattr(self.s, "provider_switch_on_daily_limit", True):
            return False

        stats = self._sahmk_stats()
        if bool(stats.get("daily_exhausted", False)):
            return True

        try:
            used = int(stats.get("daily_requests", 0) or 0)
        except (TypeError, ValueError):
            used = 0

        return used >= self._sahmk_switch_limit()

    def _activate_daily_switch(self, reason):
        if not self._forced_daily_switch:
            print(f"[router] switching to Tasilab for rest of Saudi day: {reason}")
        self._forced_daily_switch = True
        self._sync_tasilab_universe()

    def active_provider(self):
        return "tasilab" if self._sahmk_daily_limit_reached() else "sahmk"

    def _http_fallback_allowed(self, exc):
        if not getattr(self.s, "provider_fallback_enabled", True):
            return False
        if not isinstance(exc, httpx.HTTPStatusError):
            return False
        status = getattr(getattr(exc, "response", None), "status_code", 0) or 0
        # 403 can mean an invalid/unauthorized SAHMK key or endpoint permission;
        # 5xx is a provider outage. Both are safe reasons for a one-call fallback.
        return status == 403 or status in (500, 502, 503, 504)

    # =========================================================
    # GENERIC CALL
    # =========================================================

    async def _call(self, method_name, *args, **kwargs):
        if self._sahmk_daily_limit_reached():
            method = getattr(self.tasilab, method_name)
            return await method(*args, **kwargs)

        try:
            method = getattr(self.sahmk, method_name)
            result = await method(*args, **kwargs)
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                method = getattr(self.tasilab, method_name)
                return await method(*args, **kwargs)

            print(
                "[router] temporary SAHMK throttle; provider unchanged; "
                f"retry_after={exc.retry_after:.0f}s"
            )
            raise
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc):
                status = exc.response.status_code
                print(f"[router] SAHMK HTTP {status}; one-call fallback to Tasilab for {method_name}")
                method = getattr(self.tasilab, method_name)
                return await method(*args, **kwargs)
            raise

        return result

    # =========================================================
    # COMPANIES / UNIVERSE
    # =========================================================

    async def companies(self, market="TASI"):
        # Once daily-limited, serve bundled/runtime symbols instead of making
        # another SAHMK request. This is what makes fresh Render deploys robust.
        if self._sahmk_daily_limit_reached():
            if self._universe_symbols:
                return self._bootstrap_companies()
            raise RuntimeError(
                "SAHMK daily limit reached and no TASI fallback universe is available"
            )

        try:
            companies = await self.sahmk.companies(market)
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                if self._universe_symbols:
                    return self._bootstrap_companies()
            else:
                print(
                    "[router] temporary SAHMK throttle while refreshing universe; "
                    f"using {self._universe_source} symbols; "
                    f"retry_after={exc.retry_after:.0f}s"
                )
                if self._universe_symbols:
                    return self._bootstrap_companies()
            raise
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc) and self._universe_symbols:
                print(f"[router] SAHMK HTTP {exc.response.status_code}; using bundled/runtime universe")
                return self._bootstrap_companies()
            raise

        symbols = []
        for item in companies:
            if isinstance(item, dict):
                symbol = str(item.get("symbol", "")).strip()
                security_type = str(item.get("security_type", "")).lower()
                if not symbol:
                    continue
                if security_type and not any(
                    token in security_type for token in ("equity", "stock", "share")
                ):
                    continue
                symbols.append(symbol)

        if symbols:
            self._universe_symbols = list(dict.fromkeys(symbols))
            self._universe_source = "sahmk_runtime"
            self._save_runtime_universe()
            self._sync_tasilab_universe()

        return companies

    # =========================================================
    # MARKET DATA METHODS
    # =========================================================

    async def market_summary(self):
        return await self._call("market_summary")

    async def quote(self, symbol):
        return await self._call("quote", symbol)

    async def quotes(self, symbols):
        if self._sahmk_daily_limit_reached():
            return await self.tasilab.quotes(symbols)

        try:
            return await self.sahmk.quotes(symbols)
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                return await self.tasilab.quotes(symbols)

            print(
                "[router] temporary SAHMK throttle in quotes; "
                f"Tasilab NOT activated; retry_after={exc.retry_after:.0f}s"
            )
            return {}
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc):
                print(f"[router] SAHMK HTTP {exc.response.status_code}; one-call fallback to Tasilab quotes")
                return await self.tasilab.quotes(symbols)
            raise

    async def top_volume_quotes(self, limit=50, market="TASI"):
        if self._sahmk_daily_limit_reached():
            self._sync_tasilab_universe()
            return await self.tasilab.top_volume_quotes(limit, market)

        try:
            return await self.sahmk.top_volume_quotes(limit, market)
        except SahmkRateLimitError as exc:
            if exc.daily_exhausted:
                self._activate_daily_switch(str(exc))
                self._sync_tasilab_universe()
                return await self.tasilab.top_volume_quotes(limit, market)

            print(
                "[router] temporary SAHMK throttle in top-volume; "
                f"Tasilab NOT activated; retry_after={exc.retry_after:.0f}s"
            )
            return []
        except httpx.HTTPStatusError as exc:
            if self._http_fallback_allowed(exc):
                self._sync_tasilab_universe()
                print(f"[router] SAHMK HTTP {exc.response.status_code}; one-call fallback to Tasilab top-volume")
                return await self.tasilab.top_volume_quotes(limit, market)
            raise

    # =========================================================
    # STATS
    # =========================================================

    def stats(self):
        self._reset_day_if_needed()
        sahmk_stats = self._sahmk_stats()

        try:
            tasilab_stats = self.tasilab.stats() if hasattr(self.tasilab, "stats") else {}
        except Exception:
            tasilab_stats = {}

        active = self.active_provider()

        return {
            # Compatibility with existing health endpoints/messages.
            "daily_requests": sahmk_stats.get("daily_requests", 0),
            "daily_limit": sahmk_stats.get(
                "daily_limit", getattr(self.s, "sahmk_local_daily_limit", 100)
            ),
            "remaining": sahmk_stats.get("remaining", "—"),
            "rate_limits": sahmk_stats.get("rate_limits", 0),
            "errors": sahmk_stats.get("errors", 0),

            # Router details.
            "active_provider": active,
            "sahmk_available": active == "sahmk",
            "sahmk_daily_requests": sahmk_stats.get("daily_requests", 0),
            "sahmk_switch_limit": self._sahmk_switch_limit(),
            "sahmk_daily_switched": active == "tasilab",
            "sahmk_daily_exhausted": bool(sahmk_stats.get("daily_exhausted", False)),
            "sahmk_cooldown": int(sahmk_stats.get("cooldown_remaining", 0) or 0) > 0,
            "sahmk_cooldown_remaining": sahmk_stats.get("cooldown_remaining", 0),

            # Tasilab details.
            "tasilab_requests": tasilab_stats.get("daily_requests", 0),
            "tasilab_rate_limits": tasilab_stats.get("rate_limits", 0),
            "tasilab_errors": tasilab_stats.get("errors", 0),
            "tasilab_bulk_cooldown_remaining": tasilab_stats.get(
                "bulk_cooldown_remaining", 0
            ),
            "tasilab_circuit_open": tasilab_stats.get("circuit_open", False),
            "tasilab_circuit_remaining": tasilab_stats.get("circuit_remaining", 0),

            # Universe diagnostics.
            "universe_cache_size": len(self._universe_symbols),
            "universe_source": self._universe_source,
        }
