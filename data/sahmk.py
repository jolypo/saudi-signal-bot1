from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

import requests

log = logging.getLogger("sahmk.firewall")

# DEFAULT-DENY firewall. A SAHMK HTTP request is impossible unless the caller
# enters an explicit allow_scope(). ContextVars are copied by asyncio.to_thread.
_api_reason: ContextVar[str | None] = ContextVar("sahmk_api_reason", default=None)


class SahmkApiBlocked(RuntimeError):
    pass


class SahmkClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 20):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'X-API-Key': api_key, 'Accept': 'application/json'})
        self.timeout = timeout
        self.allowed_requests = 0
        self.blocked_requests = 0

    @contextmanager
    def allow_scope(self, reason: str) -> Iterator[None]:
        """Temporarily authorize SAHMK calls in this execution context only."""
        token = _api_reason.set(reason)
        try:
            yield
        finally:
            _api_reason.reset(token)

    def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        reason = _api_reason.get()
        if not reason:
            self.blocked_requests += 1
            log.warning("SAHMK BLOCKED path=%s blocked_total=%s", path, self.blocked_requests)
            raise SahmkApiBlocked(f"SAHMK API blocked by firewall: {path}")

        self.allowed_requests += 1
        log.info(
            "SAHMK ALLOWED reason=%s path=%s allowed_total=%s",
            reason, path, self.allowed_requests,
        )
        r = self.session.get(
            f'{self.base_url}/{path.lstrip("/")}', params=params, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def stats(self) -> dict[str, int]:
        return {"allowed": self.allowed_requests, "blocked": self.blocked_requests}

    def companies(self, limit=500, offset=0):
        return self.get('/companies/', {'market': 'TASI', 'limit': limit, 'offset': offset})

    def quote(self, symbol: str):
        return self.get(f'/quote/{symbol}/', {'data_mode': 'delayed'})

    def market_summary(self):
        return self.get('/market/summary/', {'index': 'TASI', 'data_mode': 'delayed'})

    def sectors(self):
        return self.get('/market/sectors/', {'index': 'TASI', 'data_mode': 'delayed'})

    def gainers(self, limit=20):
        return self.get('/market/gainers/', {'index': 'TASI', 'limit': limit, 'data_mode': 'delayed'})

    def losers(self, limit=20):
        return self.get('/market/losers/', {'index': 'TASI', 'limit': limit, 'data_mode': 'delayed'})

    def volume(self, limit=20):
        return self.get('/market/volume/', {'index': 'TASI', 'limit': limit, 'data_mode': 'delayed'})

    def value(self, limit=20):
        return self.get('/market/value/', {'index': 'TASI', 'limit': limit, 'data_mode': 'delayed'})
