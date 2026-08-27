from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from telegram import Update

from config.settings import settings
from health_server import get_state
from telegram_bot.bot import build_application

telegram_app = build_application()
secret = hashlib.sha256(settings.telegram_bot_token.encode("utf-8")).hexdigest()[:48]
webhook_path = f"/telegram/{secret}"
public_base = os.getenv("RENDER_EXTERNAL_URL", "https://saudi-signal-bot1.onrender.com").rstrip("/")
webhook_url = public_base + webhook_path

@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.bot.set_webhook(
        url=webhook_url,
        secret_token=secret,
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )
    print(f"Telegram webhook active: {public_base}/telegram/<secret>", flush=True)
    try:
        yield
    finally:
        await telegram_app.stop()
        await telegram_app.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/")
@app.get("/health")
@app.get("/healthz")
async def health():
    return {"status": "ok", "service": "saudi-signal-bot", "telegram_mode": "webhook", **get_state()}

@app.get("/status")
async def status():
    return get_state()

@app.post(webhook_path)
async def telegram_webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != secret:
        return Response(status_code=403)
    payload = await request.json()
    update = Update.de_json(payload, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

def run():
    port = int(os.getenv("PORT", str(settings.port)))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
