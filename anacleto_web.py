import os
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from telegram import Update

from anacleto_bot import build_application, BOT_DISPLAY


# ----------------------------
# Logging
# ----------------------------
LOG = logging.getLogger("ANACLETO_WEB")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# ----------------------------
# Env / Config
# ----------------------------
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or "").rstrip("/")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")  # sanity check (di solito lo usa anacleto_bot)


def webhook_url() -> str:
    return f"{PUBLIC_BASE_URL}/telegram" if PUBLIC_BASE_URL else ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:
      - build PTB application
      - initialize + start
      - set webhook su /telegram
    Shutdown:
      - stop + shutdown
    """
    application = None
    try:
        if not TELEGRAM_TOKEN:
            LOG.warning("TELEGRAM_TOKEN mancante nelle env.")
        if not PUBLIC_BASE_URL:
            LOG.warning("PUBLIC_BASE_URL mancante: webhook non verrà configurato.")

        application = build_application()
        app.state.tg_app = application

        await application.initialize()
        await application.start()

        if PUBLIC_BASE_URL:
            try:
                r = await application.bot.set_webhook(
                    url=webhook_url(),
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )
                LOG.info(f"✅ MAESTRO ANACLETO webhook set: {webhook_url()} | ok={bool(r)}")
            except Exception as e:
                LOG.exception(f"❌ set_webhook fallita: {e}")

        yield

    finally:
        try:
            if application is not None:
                LOG.info("🧯 shutdown…")
                await application.stop()
                await application.shutdown()
        except Exception as e:
            LOG.exception(f"Errore shutdown: {e}")


app = FastAPI(title="Maestro Anacleto", lifespan=lifespan)


# ----------------------------
# Routes base (GET + HEAD)
# ----------------------------
@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {"ok": True, "service": BOT_DISPLAY}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    tg_app = getattr(app.state, "tg_app", None)
    return {
        "ok": True,
        "service": BOT_DISPLAY,
        "telegram_app": bool(tg_app),
        "public_base_url": PUBLIC_BASE_URL or None,
    }


@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping():
    return {"ok": True}


# ----------------------------
# Telegram Webhook
# ----------------------------
@app.post("/telegram")
async def telegram_webhook(request: Request):
    tg_app = getattr(app.state, "tg_app", None)
    if tg_app is None:
        return JSONResponse({"ok": False, "error": "telegram app not ready"}, status_code=503)

    try:
        payload: Dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

    try:
        update = Update.de_json(payload, tg_app.bot)
        await tg_app.process_update(update)
        return JSONResponse({"ok": True})
    except Exception as e:
        LOG.exception(f"Errore process_update: {e}")
        # meglio rispondere 200 per evitare retry aggressivi di Telegram
        return JSONResponse({"ok": True, "warning": "update processing failed"})