import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from telegram import Update

# ✅ IMPORTA IL TUO BUILDER
from anacleto_bot import build_application, BOT_DISPLAY

from pathlib import Path

LOG = logging.getLogger("ANACLETO_WEB")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

# Normalizza WEBHOOK_PATH: deve iniziare con "/"
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram").strip()
if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = "/" + WEBHOOK_PATH

WEBHOOK_URL = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}" if PUBLIC_BASE_URL else ""

_application: Optional[object] = None


async def _set_webhook(app):
    if not WEBHOOK_URL:
        LOG.warning("PUBLIC_BASE_URL non settata: webhook NON impostato.")
        return
    try:
        # opzionale ma utile se Telegram resta "incollato" a un vecchio webhook
        try:
            await app.bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            pass

        ok = await app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        LOG.info("✅ %s webhook set: %s | ok=%s", BOT_DISPLAY, WEBHOOK_URL, ok)
    except Exception:
        LOG.exception("❌ Errore set_webhook")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _application
    LOG.info("startup…")

    _application = build_application()

    # Initialize + start (necessario per process_update)
    await _application.initialize()
    await _application.start()

    await _set_webhook(_application)

    yield

    LOG.info("🧯 shutdown…")
    try:
        if _application:
            await _application.stop()
            await _application.shutdown()
    except Exception:
        LOG.exception("Errore durante shutdown")


app = FastAPI(lifespan=lifespan)


# --- HEALTH / ROOT: rispondono anche a HEAD (niente 405) ---

@app.api_route("/", methods=["GET", "HEAD"])
async def root():
    # HEAD deve essere 200 senza body: FastAPI di solito lo gestisce,
    # ma per sicurezza lo facciamo esplicito.
    return {"ok": True, "service": BOT_DISPLAY}


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"ok": True}


# --- TELEGRAM WEBHOOK ---

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if _application is None:
        return JSONResponse({"ok": False, "error": "bot not ready"}, status_code=503)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

    update = Update.de_json(data, _application.bot)
    await _application.process_update(update)
    return {"ok": True}


# --- DEBUG PDFS ---

@app.get("/debug/pdfs")
async def debug_pdfs():
    base = Path(__file__).resolve().parent
    pdf_dir = base / "data" / "pdfs"

    files = []
    if pdf_dir.exists():
        for p in sorted(pdf_dir.glob("*.pdf")):
            try:
                head = p.read_bytes()[:120].decode("utf-8", errors="ignore")
            except Exception:
                head = ""
            files.append(
                {
                    "name": p.name,
                    "size": p.stat().st_size,
                    "head": head[:120],
                }
            )

    return {
        "cwd": str(Path().resolve()),
        "base": str(base),
        "pdf_dir": str(pdf_dir),
        "pdf_dir_exists": pdf_dir.exists(),
        "count": len(files),
        "files": files[:30],
    }