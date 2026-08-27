from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator
from urllib.parse import urlsplit

import requests

from diagnostics import caller_chain, event, fingerprint

log = logging.getLogger("sahmk.firewall")
_api_reason: ContextVar[str | None] = ContextVar("sahmk_api_reason", default=None)


class SahmkApiBlocked(RuntimeError):
    pass


class SahmkClient:
    """SAHMK client with a hard, central, DEFAULT-DENY circuit breaker.

    A request can leave the process only if BOTH conditions are true:
    1) the client was explicitly enabled with enable(); and
    2) the current execution context is inside allow_scope(reason).

    This prevents forgotten callers/jobs from leaking quota while SHUTDOWN is active.
    """

    def __init__(self, base_url: str, api_key: str, timeout: int = 20):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({'X-API-Key': api_key, 'Accept': 'application/json'})
        self.timeout = timeout
        self.allowed_requests = 0
        self.blocked_requests = 0
        self.failed_requests = 0
        self._enabled = False
        self._state_lock = threading.Lock()
        self.api_key_fingerprint = fingerprint(api_key)
        event(
            "sahmk_client_init",
            base_host=urlsplit(self.base_url).netloc,
            key_fp=self.api_key_fingerprint,
            enabled=False,
        )

    def enable(self, reason: str) -> None:
        with self._state_lock:
            self._enabled = True
        event("sahmk_firewall_enabled", reason=reason, key_fp=self.api_key_fingerprint)

    def disable(self, reason: str) -> None:
        with self._state_lock:
            self._enabled = False
        event("sahmk_firewall_disabled", reason=reason, key_fp=self.api_key_fingerprint)

    @property
    def enabled(self) -> bool:
        with self._state_lock:
            return self._enabled

    @contextmanager
    def allow_scope(self, reason: str) -> Iterator[None]:
        event("sahmk_scope_enter", reason=reason, enabled=self.enabled)
        token = _api_reason.set(reason)
        try:
            yield
        finally:
            _api_reason.reset(token)
            event("sahmk_scope_exit", reason=reason, enabled=self.enabled)

    def get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        reason = _api_reason.get()
        enabled = self.enabled
        callers = caller_chain()

        if not enabled or not reason:
            self.blocked_requests += 1
            event(
                "sahmk_http_blocked",
                method="GET",
                path=path,
                reason=reason or "NO_SCOPE",
                firewall_enabled=enabled,
                blocked_total=self.blocked_requests,
                callers=callers,
            )
            raise SahmkApiBlocked(
                f"SAHMK API blocked by firewall: path={path} enabled={enabled} reason={reason}"
            )

        self.allowed_requests += 1
        req_no = self.allowed_requests
        started = time.monotonic()
        # Log BEFORE network I/O. If the dashboard increments, this line identifies the caller.
        event(
            "sahmk_http_outbound",
            request_no=req_no,
            method="GET",
            host=urlsplit(self.base_url).netloc,
            path=path,
            reason=reason,
            params_keys=sorted((params or {}).keys()),
            callers=callers,
        )
        try:
            r = self.session.get(
                f'{self.base_url}/{path.lstrip("/")}', params=params, timeout=self.timeout
            )
            elapsed_ms = round((time.monotonic() - started) * 1000)
            event(
                "sahmk_http_response",
                request_no=req_no,
                path=path,
                reason=reason,
                status=r.status_code,
                elapsed_ms=elapsed_ms,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            self.failed_requests += 1
            event(
                "sahmk_http_error",
                request_no=req_no,
                path=path,
                reason=reason,
                error=type(exc).__name__,
                message=str(exc)[:240],
            )
            raise

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed": self.allowed_requests,
            "blocked": self.blocked_requests,
            "failed": self.failed_requests,
            "key_fp": self.api_key_fingerprint,
        }

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
