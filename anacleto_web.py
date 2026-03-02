import os
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from telegram import Update

from anacleto_bot import (
    build_application,
    BOT_DISPLAY,
    get_index_state,
    force_reindex,
)

LOG = logging.getLogger("ANACLETO_WEB")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram")
WEBHOOK_URL = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}" if PUBLIC_BASE_URL else ""

_application: Optional[object] = None


async def _set_webhook(app):
    if not WEBHOOK_URL:
        LOG.warning("PUBLIC_BASE_URL non settata: webhook NON impostato.")
        return
    try:
        ok = await app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        LOG.info("✅ %s webhook set: %s | ok=%s", BOT_DISPLAY, WEBHOOK_URL, ok)
    except Exception:
        LOG.exception("❌ Errore set_webhook")


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _application
    LOG.info("startup…")

    _application = build_application()

    # initialize/start -> fa partire PTB e (se usi post_init) costruisce anche l'indice
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


@app.get("/")
async def root():
    return {"ok": True, "service": BOT_DISPLAY}


@app.get("/health")
async def health_get():
    return {"ok": True}


@app.head("/health")
async def health_head():
    return Response(status_code=200)


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if _application is None:
        return JSONResponse({"ok": False, "error": "bot not ready"}, status_code=503)

    data = await request.json()
    update = Update.de_json(data, _application.bot)

    await _application.process_update(update)
    return {"ok": True}


# --- DEBUG ENDPOINTS ---

from pathlib import Path

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
            files.append({
                "name": p.name,
                "size": p.stat().st_size,
                "head": head[:120],
            })

    return {
        "cwd": str(Path().resolve()),
        "base": str(base),
        "pdf_dir": str(pdf_dir),
        "pdf_dir_exists": pdf_dir.exists(),
        "count": len(files),
        "files": files[:30],
    }


@app.get("/debug/index")
async def debug_index():
    # stato indice lato bot
    return get_index_state()


@app.post("/debug/reindex")
async def debug_reindex():
    # forza rebuild indice (utile quando cambi pdf)
    return force_reindex()