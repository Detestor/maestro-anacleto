import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telegram import Update
from anacleto_bot import build_application

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("ANACLETO_WEB")

_application = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _application
    LOG.info("🚀 startup MAESTRO ANACLETO")
    _application = build_application()
    await _application.initialize()
    await _application.start()

    webhook_url = os.getenv("PUBLIC_BASE_URL", "") + "/telegram"
    if webhook_url:
        await _application.bot.set_webhook(url=webhook_url)

    yield

    await _application.stop()
    await _application.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, _application.bot)
    await _application.process_update(update)
    return JSONResponse({"ok": True})