from __future__ import annotations

import hashlib
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from telegram import Update

from config.settings import settings
from diagnostics import INSTANCE_ID, event
from health_server import get_state
from telegram_bot.bot import build_application, client

telegram_app = build_application()
# Webhook URL does not contain the secret. Telegram sends this secret in a header.
secret = hashlib.sha256(settings.telegram_bot_token.encode("utf-8")).hexdigest()[:48]
webhook_path = "/telegram/webhook"
public_base = os.getenv("RENDER_EXTERNAL_URL", "https://saudi-signal-bot1.onrender.com").rstrip("/")
webhook_url = public_base + webhook_path


@asynccontextmanager
async def lifespan(app: FastAPI):
    event(
        "app_startup_begin",
        public_base=public_base,
        webhook_path=webhook_path,
        port=os.getenv("PORT", str(settings.port)),
        sahmk=client.stats(),
    )
    await telegram_app.initialize()
    await telegram_app.start()
    # Register the official Telegram Menu commands with Arabic descriptions.
    from telegram_bot.bot import BOT_COMMANDS
    await telegram_app.bot.set_my_commands(BOT_COMMANDS)
    event("telegram_commands_registered", count=len(BOT_COMMANDS), sahmk=client.stats())
    event("telegram_set_webhook_begin", url=webhook_url)
    await telegram_app.bot.set_webhook(
        url=webhook_url,
        secret_token=secret,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    event("telegram_webhook_active", url=webhook_url, sahmk=client.stats())
    try:
        yield
    finally:
        event("app_shutdown_begin", sahmk=client.stats())
        await telegram_app.stop()
        await telegram_app.shutdown()
        event("app_shutdown_complete", sahmk=client.stats())


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def audit_http(request: Request, call_next):
    started = time.monotonic()
    # Never log query strings, headers, body, tokens, or webhook secrets.
    safe_path = request.url.path
    event(
        "http_inbound",
        method=request.method,
        path=safe_path,
        client_host=request.client.host if request.client else None,
        sahmk=client.stats(),
    )
    try:
        response = await call_next(request)
        event(
            "http_complete",
            method=request.method,
            path=safe_path,
            status=response.status_code,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            sahmk=client.stats(),
        )
        return response
    except Exception as exc:
        event(
            "http_error",
            method=request.method,
            path=safe_path,
            error=type(exc).__name__,
            message=str(exc)[:240],
            sahmk=client.stats(),
        )
        raise


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/health", methods=["GET", "HEAD"])
@app.api_route("/healthz", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "service": "saudi-signal-bot",
        "telegram_mode": "webhook",
        "instance": INSTANCE_ID,
        "sahmk": client.stats(),
        **get_state(),
    }


@app.get("/status")
async def status():
    return {"instance": INSTANCE_ID, "sahmk": client.stats(), **get_state()}


@app.post(webhook_path)
async def telegram_webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        event("telegram_webhook_rejected", reason="bad_secret_header")
        return Response(status_code=403)
    payload = await request.json()
    message = payload.get("message") or payload.get("edited_message") or {}
    text = message.get("text")
    event(
        "telegram_webhook_update",
        update_id=payload.get("update_id"),
        update_keys=sorted(payload.keys()),
        command=(text.split()[0] if isinstance(text, str) and text.startswith("/") else None),
        sahmk=client.stats(),
    )
    update = Update.de_json(payload, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


def run():
    port = int(os.getenv("PORT", str(settings.port)))
    event("uvicorn_run", host="0.0.0.0", port=port)
    # Disable default access log because it can expose sensitive URL paths. Our middleware logs sanitized requests.
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info", access_log=False)
