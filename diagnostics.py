from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

INSTANCE_ID = os.getenv("RENDER_INSTANCE_ID") or uuid.uuid4().hex[:10]
_events: deque[dict[str, Any]] = deque(maxlen=300)
_lock = threading.Lock()
log = logging.getLogger("audit")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(value: str | None) -> str:
    if not value:
        return "missing"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def caller_chain(limit: int = 7) -> list[str]:
    frames = inspect.stack()[2:2 + limit]
    out: list[str] = []
    for f in frames:
        filename = os.path.basename(f.filename)
        if filename == "diagnostics.py":
            continue
        out.append(f"{filename}:{f.lineno}:{f.function}")
    return out[:limit]


def event(kind: str, **fields: Any) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "ts": utc_now(),
        "instance": INSTANCE_ID,
        "event": kind,
        **fields,
    }
    with _lock:
        _events.append(rec)
    # JSON one-line logs are easy to search in Render. Never pass secrets in fields.
    print("AUDIT " + json.dumps(rec, ensure_ascii=False, default=str, separators=(",", ":")), flush=True)
    return rec


def recent(limit: int = 25) -> list[dict[str, Any]]:
    with _lock:
        return list(_events)[-max(1, min(limit, 100)):]
