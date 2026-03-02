import os
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, Response
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
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")  # usata dentro anacleto_bot, qui serve per sanity-check


def _webhook_url() -> str:
    if not PUBLIC_BASE_URL:
        return ""
    return f"{PUBLIC_BASE_URL}/telegram"


# ----------------------------
# Lifespan (startup/shutdown)
# ----------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    In startup:
    - costruisce l'application PTB
    - initialize + start
    - setWebhook -> /telegram
    In shutdown:
    - stop + shutdown
    """
    application = None

    try:
        # Sanity checks
        if not TELEGRAM_TOKEN:
            LOG.warning("TELEGRAM_TOKEN non impostato nelle env. Il bot potrebbe non avviarsi.")
        if not PUBLIC_BASE_URL:
            LOG.warning("PUBLIC_BASE_URL non impostato: webhook non verrà configurato automaticamente.")

        # Build PTB Application (dal tuo anacleto_bot.py)
        application = build_application()
        app.state.tg_app = application

        # Start PTB application (senza polling!)
        await application.initialize()
        await application.start()

        # Webhook
        if PUBLIC_BASE_URL:
            url = _webhook_url()
            try:
                r = await application.bot.set_webhook(
                    url=url,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,  # ripulisce eventuali update vecchi
                )
                LOG.info(f"✅ MAESTRO ANACLETO webhook set: {url} | ok={getattr(r, 'to_dict', lambda: r)() if r else r}")
            except Exception as e:
                LOG.exception(f"❌ set_webhook fallita: {e}")

        yield

    finally:
        # Shutdown PTB application
        try:
            if application is not None:
                LOG.info("🧯 shutdown…")
                await application.stop()
                await application.shutdown()
        except Exception as e:
            LOG.exception(f"Errore in shutdown: {e}")


app = FastAPI(title="Maestro Anacleto", lifespan=lifespan)


# ----------------------------
# Routes base
# ----------------------------
@app.get("/")
async def root():
    return {"ok": True, "service": BOT_DISPLAY}


@app.get("/health")
async def health():
    """
    Endpoint per UptimeRobot / ping. Se risponde, Render è sveglio.
    """
    info = {
        "ok": True,
        "service": BOT_DISPLAY,
        "health": "ok",
        "public_base_url": PUBLIC_BASE_URL or None,
    }

    # Se l'app telegram non è attaccata, segnaliamolo
    tg_app = getattr(app.state, "tg_app", None)
    info["telegram_app"] = bool(tg_app)

    return info


@app.get("/ping")
async def ping():
    return {"ok": True}


# ----------------------------
# Telegram Webhook endpoint
# ----------------------------
@app.post("/telegram")
async def telegram_webhook(request: Request):
    """
    Telegram manda update JSON qui.
    Noi lo trasformiamo in Update e lo passiamo a PTB.
    """
    tg_app = getattr(app.state, "tg_app", None)
    if tg_app is None:
        # se arriva un update durante cold start / init non completata
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
        # Telegram vuole 200 spesso comunque, ma mettiamo ok=True per non ritentare all'infinito
        return JSONResponse({"ok": True, "warning": "update processing failed"})