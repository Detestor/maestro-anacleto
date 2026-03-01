import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from telegram import Update

from anacleto_bot import build_application, BOT_DISPLAY

log = logging.getLogger("ANACLETO_WEB")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")


# --- Helper: URL pubblico del servizio Render ---
def get_public_base_url() -> str:
    """
    Render spesso espone RENDER_EXTERNAL_URL (dipende dal contesto).
    In alternativa, impostalo tu come env: PUBLIC_BASE_URL
    """
    url = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "Manca URL pubblico. Imposta env PUBLIC_BASE_URL = https://<tuo-servizio>.onrender.com"
        )
    return url.rstrip("/")


WEBHOOK_PATH = "/telegram"  # endpoint webhook
application = None  # PTB Application


@asynccontextmanager
async def lifespan(app: FastAPI):
    global application

    # 1) build PTB application
    application = build_application()

    # 2) initialize + start PTB (NO polling)
    await application.initialize()
    await application.start()

    # 3) set webhook
    base_url = get_public_base_url()
    webhook_url = f"{base_url}{WEBHOOK_PATH}"

    # Importantissimo: reset webhook e droppa backlog
    await application.bot.delete_webhook(drop_pending_updates=True)
    ok = await application.bot.set_webhook(url=webhook_url, drop_pending_updates=True)

    log.info("✅ %s webhook set: %s | ok=%s", BOT_DISPLAY, webhook_url, ok)

    yield

    # shutdown PTB
    log.info("🧯 shutdown…")
    await application.stop()
    await application.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def health():
    return {"ok": True, "service": BOT_DISPLAY}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(req: Request):
    """
    Riceve update Telegram e lo passa al dispatcher PTB
    """
    global application
    if application is None:
        return Response(status_code=503, content="Bot not ready")

    data = await req.json()
    update = Update.de_json(data, application.bot)

    # process_update è async: lo awaitiamo
    await application.process_update(update)
    return {"ok": True}