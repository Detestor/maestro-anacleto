# -*- coding: utf-8 -*-
"""
MAESTRO ANACLETO — Telegram bot core (python-telegram-bot v21.x)

Obiettivo: indicizzare PDF (già OCR) in data/pdfs e rispondere a /ask citando libro+pagina.

NOTE IMPORTANTI (Render + webhook):
- PDF_DIR è definita UNA SOLA VOLTA qui ed è la fonte di verità.
- In modalità webhook (FastAPI), l'indice viene costruito a startup dal wrapper web
  (vedi anacleto_web.py). Qui lasciamo comunque post_init per sicurezza.
"""
from __future__ import annotations

import os
import re
import html
import logging
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────
# Logging
# ─────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ANACLETO")

# ─────────────────────────────────────────
# Identity / env
# ─────────────────────────────────────────
BOT_DISPLAY = "MAESTRO ANACLETO"
BOT_USERNAME = os.getenv("BOT_USERNAME", "@MaestroAnacletoBot")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID", "").strip()
ALLOWED_GROUP_ID_INT: Optional[int] = None
if ALLOWED_GROUP_ID:
    try:
        ALLOWED_GROUP_ID_INT = int(ALLOWED_GROUP_ID)
    except Exception:
        logger.warning("ALLOWED_GROUP_ID non è un intero valido: %r", ALLOWED_GROUP_ID)

# ─────────────────────────────────────────
# ✅ UNICA DEFINIZIONE DI PDF_DIR — usata ovunque
# ─────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = Path(os.getenv("PDF_DIR", str(BASE_DIR / "data" / "pdfs"))).resolve()

logger.info("▶ BASE_DIR=%s", BASE_DIR)
logger.info("▶ PDF_DIR=%s (env override: %s)", PDF_DIR, bool(os.getenv("PDF_DIR")))

# ─────────────────────────────────────────
# pypdf
# ─────────────────────────────────────────
HAVE_PYPDF = False
PdfReader = None  # type: ignore
try:
    from pypdf import PdfReader as _PdfReader  # type: ignore
    PdfReader = _PdfReader
    HAVE_PYPDF = True
    logger.info("✅ pypdf disponibile")
except ImportError as e:
    logger.error("❌ pypdf NON disponibile: %s — i PDF non potranno essere letti!", e)

# ─────────────────────────────────────────
# Index structures
# ─────────────────────────────────────────
@dataclass
class PageChunk:
    book: str
    page: int   # 1-based
    text: str

@dataclass
class Cf77Index:
    books: int
    pages: int
    text_pages: int
    chars: int
    chunks: List[PageChunk]

# ✅ Global index — scritto da build_and_store_index(), letto da handler
INDEX: Optional[Cf77Index] = None


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────
def _clean_ws(s: str) -> str:
    s = s.replace("\u00ad", "")  # soft hyphen
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _escape_html(s: str) -> str:
    return html.escape(str(s), quote=False)

def list_pdfs(pdf_dir: Path) -> List[Path]:
    try:
        return sorted([p for p in pdf_dir.glob("*.pdf") if p.is_file()])
    except Exception:
        return []

def _extract_one_pdf(path: Path) -> Tuple[int, int, int, List[str]]:
    if not HAVE_PYPDF or PdfReader is None:
        raise RuntimeError("pypdf non disponibile")

    reader = PdfReader(str(path))
    page_texts: List[str] = []
    text_pages = 0
    chars = 0
    empty_pages = 0

    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception as e:
            logger.debug("Errore extract_text pagina: %s", e)
            txt = ""
        txt = _clean_ws(txt)
        if txt:
            text_pages += 1
            chars += len(txt)
        else:
            empty_pages += 1
        page_texts.append(txt)

    logger.info(
        "  📄 %s: %d pag totali / %d con testo / %d vuote / %d chars",
        path.name, len(reader.pages), text_pages, empty_pages, chars,
    )
    return len(reader.pages), text_pages, chars, page_texts


def build_index(pdf_dir: Path) -> Cf77Index:
    pdfs = list_pdfs(pdf_dir)
    logger.info("═" * 60)
    logger.info("🔍 build_index START | PDF_DIR=%s | trovati %d PDF", pdf_dir, len(pdfs))
    for p in pdfs:
        try:
            logger.info("  • %s (%d bytes)", p.name, p.stat().st_size)
        except Exception:
            logger.info("  • %s", p.name)

    if not pdfs:
        logger.warning("⚠ Nessun PDF trovato in %s", pdf_dir)
        return Cf77Index(books=0, pages=0, text_pages=0, chars=0, chunks=[])

    if not HAVE_PYPDF:
        logger.error("❌ pypdf non disponibile — indice vuoto (books count-only)")
        return Cf77Index(books=len(pdfs), pages=0, text_pages=0, chars=0, chunks=[])

    chunks: List[PageChunk] = []
    total_pages = 0
    total_text_pages = 0
    total_chars = 0

    for pdf in pdfs:
        try:
            pages, text_pages, chars, page_texts = _extract_one_pdf(pdf)
            total_pages += pages
            total_text_pages += text_pages
            total_chars += chars
            bookname = pdf.name
            for idx, txt in enumerate(page_texts, start=1):
                if txt:
                    chunks.append(PageChunk(book=bookname, page=idx, text=txt))
        except Exception:
            logger.exception("❌ Errore estrazione testo da %s", pdf.name)

    result = Cf77Index(
        books=len(pdfs),
        pages=total_pages,
        text_pages=total_text_pages,
        chars=total_chars,
        chunks=chunks,
    )
    logger.info(
        "✅ build_index DONE | books=%d pages=%d text_pages=%d chars=%d chunks=%d",
        result.books, result.pages, result.text_pages, result.chars, len(result.chunks),
    )
    logger.info("═" * 60)
    return result


async def build_and_store_index() -> Cf77Index:
    global INDEX
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    try:
        INDEX = await loop.run_in_executor(None, build_index, PDF_DIR)
        return INDEX
    except Exception:
        logger.exception("❌ build_and_store_index fallita")
        INDEX = Cf77Index(books=0, pages=0, text_pages=0, chars=0, chunks=[])
        return INDEX


# ─────────────────────────────────────────
# Search helpers
# ─────────────────────────────────────────
def search_index(question: str, idx: Cf77Index, top_k: int = 3) -> List[Tuple[PageChunk, int]]:
    q = _clean_ws(question).lower()
    if not q or not idx.chunks:
        return []
    terms = [t for t in re.findall(r"[a-zàèéìòù0-9']+", q, flags=re.IGNORECASE) if len(t) >= 4]
    if not terms:
        terms = [q]
    scored = []
    for ch in idx.chunks:
        text_l = ch.text.lower()
        score = sum(text_l.count(t) for t in terms)
        if score > 0:
            scored.append((score, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(ch, sc) for sc, ch in scored[:top_k]]


def snippet(text: str, terms: List[str], max_len: int = 420) -> str:
    if not text:
        return ""
    low = text.lower()
    pos = None
    for t in terms:
        i = low.find(t.lower())
        if i != -1:
            pos = i
            break
    if pos is None:
        return (text[:max_len] + "…") if len(text) > max_len else text
    start = max(0, pos - 120)
    end = min(len(text), start + max_len)
    s = ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
    return s


# ─────────────────────────────────────────
# Access control
# ─────────────────────────────────────────
def is_allowed_chat(update: Update) -> bool:
    if update.effective_chat and update.effective_chat.type == "private":
        return True
    if ALLOWED_GROUP_ID_INT is not None and update.effective_chat:
        return update.effective_chat.id == ALLOWED_GROUP_ID_INT
    return True


# ─────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    msg = (
        f"<b>{_escape_html(BOT_DISPLAY)}</b> 📚\n\n"
        "Comandi:\n"
        "• /status — stato bot + PDF\n"
        "• /sources — lista PDF\n"
        "• /ask &lt;domanda&gt; — cerca nei testi\n"
        "• /quote — citazione casuale dai testi\n"
        "• /reindex — ricostruisce l'indice (se hai cambiato PDF)\n"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    idx = INDEX
    pdf_ok = idx is not None and idx.books > 0 and idx.pages > 0 and idx.text_pages > 0
    pdf_line = (
        f"{'✅' if pdf_ok else '❌'} "
        f"{idx.books if idx else 0} libri / "
        f"{idx.pages if idx else 0} pagine / "
        f"testo:{idx.text_pages if idx else 0} / "
        f"chars:{idx.chars if idx else 0}"
    )
    msg = (
        "<b>📌 Status</b>\n"
        f"• Username: {_escape_html(BOT_USERNAME)}\n"
        f"• 🔒 ALLOWED_GROUP_ID={_escape_html(ALLOWED_GROUP_ID) if ALLOWED_GROUP_ID else '—'}\n"
        f"• PDF: {pdf_line}\n"
        f"• pypdf: {'✅' if HAVE_PYPDF else '❌'}\n"
        f"• PDF_DIR: <code>{_escape_html(str(PDF_DIR))}</code>\n"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    pdfs = list_pdfs(PDF_DIR)
    if not pdfs:
        await update.effective_message.reply_text("📚 Nessun PDF trovato in data/pdfs.")
        return
    lines = ["📚 Libri caricati:"] + [f"• {p.name}" for p in pdfs]
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_reindex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    await update.effective_message.reply_text("⏳ Ricostruisco l'indice…")
    idx = await build_and_store_index()
    await update.effective_message.reply_text(
        f"✅ Indice pronto: {idx.books} libri / {idx.pages} pagine / testo:{idx.text_pages} / chars:{idx.chars}"
    )


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    idx = INDEX
    if idx and idx.chunks:
        import random
        ch = random.choice(idx.chunks)
        q = snippet(ch.text, [], max_len=320)
        msg = f"📜 <i>{_escape_html(q)}</i>\n\n— {_escape_html(ch.book)}, pag. {ch.page}"
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)
    else:
        await update.effective_message.reply_text('📜 "Conosci te stesso." — (indice non pronto)')


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    idx = INDEX
    q = " ".join(context.args).strip() if context.args else ""
    if not q:
        await update.effective_message.reply_text("Usa: /ask <domanda>")
        return
    if not idx or not idx.chunks:
        await update.effective_message.reply_text(
            "😤 Indice non pronto o PDF senza testo estraibile.\nControlla /status oppure /reindex."
        )
        return

    terms = [t for t in re.findall(r"[a-zàèéìòù0-9']+", q, flags=re.IGNORECASE) if len(t) >= 4]
    results = search_index(q, idx, top_k=3)
    if not results:
        await update.effective_message.reply_text(
            "😤 Non ho trovato un passaggio chiaro.\n"
            "Prova con parole chiave più specifiche (es: “piano astrale”, “corpo astrale”, “trapasso”)."
        )
        return

    blocks = []
    for ch, sc in results:
        sn = snippet(ch.text, terms, max_len=420)
        blocks.append(
            f"<b>📖 {_escape_html(ch.book)}</b> — pag. <b>{ch.page}</b>\n"
            f"{_escape_html(sn)}"
        )
    header = (
        f"Salve, <b>@{_escape_html(update.effective_user.username or 'utente')}</b>. "
        f"Hai chiamato il {_escape_html(BOT_DISPLAY)} 📚\n"
        f"📌 <i>{_escape_html(q)}</i>\n\n"
        "🧠 Passaggi trovati:\n\n"
    )
    await update.effective_message.reply_text(
        header + "\n\n— — —\n\n".join(blocks),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    if update.effective_chat and update.effective_chat.type == "private":
        await update.effective_message.reply_text("Scrivi /help oppure usa /ask <domanda> 🙂")


# ─────────────────────────────────────────
# post_init (PTB v21)
# ─────────────────────────────────────────
async def post_init(app: Application) -> None:
    logger.info("post_init: avvio build_and_store_index…")
    idx = await build_and_store_index()
    logger.info("post_init: indice pronto. books=%d pages=%d text_pages=%d", idx.books, idx.pages, idx.text_pages)


# ─────────────────────────────────────────
# Application factory
# ─────────────────────────────────────────
def build_application() -> Application:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN mancante nelle env vars")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("reindex", cmd_reindex))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


# ─────────────────────────────────────────
# Local polling entrypoint (dev only)
# ─────────────────────────────────────────
def main() -> None:
    logger.info("%s avvio polling locale…", BOT_DISPLAY)
    build_application().run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
