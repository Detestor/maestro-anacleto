# -*- coding: utf-8 -*-
"""
MAESTRO ANACLETO — FastAPI webhook wrapper (Render Web Service)

Routes:
  GET  /             -> 200 ok
  GET  /health       -> 200 ok  (Render + UptimeRobot)
  HEAD /health       -> 200 ok
  POST /telegram     -> Telegram webhook
  GET  /debug/pdfs   -> lista PDF su disco (usa PDF_DIR da anacleto_bot)
  GET  /debug/index  -> stato indice
  GET/POST /debug/reindex -> forza rebuild indice
"""
from __future__ import annotations

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response as StarletteResponse

from telegram import Update

from anacleto_bot import (
    build_application,
    build_and_store_index,
    list_pdfs,
    BOT_DISPLAY,
    PDF_DIR,
    HAVE_PYPDF,
)
import anacleto_bot as _bot

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
LOG = logging.getLogger("ANACLETO_WEB")

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/telegram")
WEBHOOK_URL = f"{PUBLIC_BASE_URL}{WEBHOOK_PATH}" if PUBLIC_BASE_URL else ""

_application = None


async def _set_webhook(app) -> bool:
    if not WEBHOOK_URL:
        LOG.warning("PUBLIC_BASE_URL non settata: webhook NON impostato.")
        return False
    try:
        ok = await app.bot.set_webhook(url=WEBHOOK_URL, allowed_updates=Update.ALL_TYPES)
        LOG.info("✅ webhook impostato: %s | ok=%s", WEBHOOK_URL, ok)
        return bool(ok)
    except Exception:
        LOG.exception("❌ Errore set_webhook")
        return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _application

    LOG.info("═" * 60)
    LOG.info("🚀 startup %s", BOT_DISPLAY)
    LOG.info("  PUBLIC_BASE_URL=%s", PUBLIC_BASE_URL or "(non impostata)")
    LOG.info("  WEBHOOK_URL=%s", WEBHOOK_URL or "(non impostata)")
    LOG.info("  PDF_DIR=%s", PDF_DIR)
    LOG.info("═" * 60)

    _application = build_application()
    await _application.initialize()
    await _application.start()

    # Safety net: se post_init non ha costruito INDEX, lo facciamo qui.
    if _bot.INDEX is None or _bot.INDEX.books == 0:
        LOG.info("🔁 INDEX non pronto (o vuoto) — forzo build_and_store_index()…")
        await build_and_store_index()
        LOG.info("🔁 build_and_store_index() completato: books=%s pages=%s",
                 _bot.INDEX.books if _bot.INDEX else None,
                 _bot.INDEX.pages if _bot.INDEX else None)

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
    return StarletteResponse(status_code=200)


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if _application is None:
        return JSONResponse({"ok": False, "error": "bot not ready"}, status_code=503)
    try:
        data = await request.json()
        update = Update.de_json(data, _application.bot)
        await _application.process_update(update)
        return {"ok": True}
    except Exception:
        LOG.exception("Errore process_update")
        return JSONResponse({"ok": False, "error": "internal error"}, status_code=500)


@app.get("/debug/pdfs")
async def debug_pdfs():
    files = []
    if PDF_DIR.exists():
        for p in list_pdfs(PDF_DIR):
            try:
                head = p.read_bytes()[:4].hex()
            except Exception:
                head = ""
            files.append({
                "name": p.name,
                "size_bytes": p.stat().st_size,
                "magic": head,
                "is_pdf": head.startswith("25504446"),
            })
    return {
        "pdf_dir": str(PDF_DIR),
        "pdf_dir_exists": PDF_DIR.exists(),
        "count": len(files),
        "files": files,
    }


@app.get("/debug/index")
async def debug_index():
    idx = _bot.INDEX
    return {
        "PDF_DIR": str(_bot.PDF_DIR),
        "have_pypdf": HAVE_PYPDF,
        "index_built": idx is not None,
        "books": idx.books if idx else 0,
        "pages": idx.pages if idx else 0,
        "text_pages": idx.text_pages if idx else 0,
        "chars": idx.chars if idx else 0,
        "chunks": len(idx.chunks) if idx else 0,
    }


@app.post("/debug/reindex")
async def reindex_post():
    idx = await build_and_store_index()
    return {"ok": True, "books": idx.books, "pages": idx.pages, "text_pages": idx.text_pages, "chars": idx.chars, "chunks": len(idx.chunks)}


@app.get("/debug/reindex")
async def reindex_get():
    idx = await build_and_store_index()
    return {"ok": True, "books": idx.books, "pages": idx.pages, "text_pages": idx.text_pages, "chars": idx.chars, "chunks": len(idx.chunks)}
