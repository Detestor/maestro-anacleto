# -*- coding: utf-8 -*-
"""
MAESTRO ANACLETO — Telegram bot core (PTB v21.x)

✅ SINGLE SOURCE OF TRUTH for PDF_DIR (defined ONCE)
✅ Global INDEX used by /status and /ask
✅ Safe HTML escaping to avoid "Can't parse entities"
"""
from __future__ import annotations

import os
import re
import html
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Dict

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

# ----------------------------
# Logging
# ----------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("ANACLETO")

# ----------------------------
# Identity / env
# ----------------------------
BOT_DISPLAY = "MAESTRO ANACLETO"
BOT_USERNAME = os.getenv("BOT_USERNAME", "@MaestroAnacletoBot")  # cosmetic

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID", "").strip()
ALLOWED_GROUP_ID_INT: Optional[int] = None
if ALLOWED_GROUP_ID:
    try:
        ALLOWED_GROUP_ID_INT = int(ALLOWED_GROUP_ID)
    except Exception:
        logger.warning("ALLOWED_GROUP_ID non è un intero valido: %r", ALLOWED_GROUP_ID)
        ALLOWED_GROUP_ID_INT = None

# ----------------------------
# ✅ SINGLE PDF_DIR (ONLY HERE)
# ----------------------------
BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = Path(os.getenv("PDF_DIR", str(BASE_DIR / "data" / "pdfs"))).resolve()

# ----------------------------
# Optional dependency: pypdf
# ----------------------------
HAVE_PYPDF = False
try:
    from pypdf import PdfReader  # type: ignore
    HAVE_PYPDF = True
except Exception:
    PdfReader = None  # type: ignore
    HAVE_PYPDF = False

# ----------------------------
# Index structures
# ----------------------------
@dataclass
class PageChunk:
    book: str
    page: int          # 1-based
    text: str

@dataclass
class Cf77Index:
    books: int
    pages: int
    text_pages: int
    chars: int
    chunks: List[PageChunk]

# ✅ Global index (single source)
INDEX: Optional[Cf77Index] = None


# ----------------------------
# Helpers
# ----------------------------
def _clean_ws(s: str) -> str:
    s = s.replace("\u00ad", "")  # soft hyphen
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _escape_html(s: str) -> str:
    return html.escape(s, quote=False)

def list_pdfs(pdf_dir: Path) -> List[Path]:
    try:
        return sorted([p for p in pdf_dir.glob("*.pdf") if p.is_file()])
    except Exception:
        return []

def extract_pdf_text(path: Path) -> Tuple[int, int, int, List[str]]:
    """
    Returns: (pages_total, text_pages, chars, page_texts)
    """
    if not HAVE_PYPDF:
        raise RuntimeError("pypdf non disponibile")

    reader = PdfReader(str(path))
    page_texts: List[str] = []
    text_pages = 0
    chars = 0

    for i, page in enumerate(reader.pages, start=1):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        txt = _clean_ws(txt)
        if txt:
            text_pages += 1
            chars += len(txt)
        page_texts.append(txt)

    return len(reader.pages), text_pages, chars, page_texts

def build_index(pdf_dir: Path) -> Cf77Index:
    pdfs = list_pdfs(pdf_dir)
    logger.info("PDF_DIR=%s | trovati %s pdf: %s", pdf_dir, len(pdfs), [p.name for p in pdfs][:50])

    chunks: List[PageChunk] = []
    total_pages = 0
    total_text_pages = 0
    total_chars = 0

    if not pdfs:
        return Cf77Index(books=0, pages=0, text_pages=0, chars=0, chunks=[])

    if not HAVE_PYPDF:
        logger.warning("pypdf non disponibile: non posso estrarre testo dai PDF.")
        return Cf77Index(books=len(pdfs), pages=0, text_pages=0, chars=0, chunks=[])

    for pdf in pdfs:
        try:
            pages, text_pages, chars, page_texts = extract_pdf_text(pdf)
            total_pages += pages
            total_text_pages += text_pages
            total_chars += chars

            bookname = pdf.name
            for idx, txt in enumerate(page_texts, start=1):
                if txt:
                    chunks.append(PageChunk(book=bookname, page=idx, text=txt))

        except Exception as e:
            logger.exception("Errore estrazione testo da %s: %s", pdf.name, e)

    return Cf77Index(
        books=len(pdfs),
        pages=total_pages,
        text_pages=total_text_pages,
        chars=total_chars,
        chunks=chunks,
    )

def search_index(question: str, idx: Cf77Index, top_k: int = 3) -> List[Tuple[PageChunk, int]]:
    q = _clean_ws(question).lower()
    if not q or not idx.chunks:
        return []

    # keywords: words >= 4 chars
    terms = [t for t in re.findall(r"[a-zàèéìòù0-9']+", q, flags=re.IGNORECASE) if len(t) >= 4]
    if not terms:
        terms = [q]

    scored: List[Tuple[int, PageChunk]] = []
    for ch in idx.chunks:
        text_l = ch.text.lower()
        score = 0
        for t in terms:
            score += text_l.count(t)
        if score > 0:
            scored.append((score, ch))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [(ch, sc) for sc, ch in scored[:top_k]]

def snippet(text: str, terms: List[str], max_len: int = 420) -> str:
    t = text
    low = t.lower()
    pos = None
    for term in terms:
        p = low.find(term.lower())
        if p != -1:
            pos = p
            break
    if pos is None:
        return (t[:max_len] + "…") if len(t) > max_len else t

    start = max(0, pos - 120)
    end = min(len(t), start + max_len)
    s = t[start:end]
    if start > 0:
        s = "…" + s
    if end < len(t):
        s = s + "…"
    return s


# ----------------------------
# Access control
# ----------------------------
def is_allowed_chat(update: Update) -> bool:
    # Always allow private
    if update.effective_chat and update.effective_chat.type == "private":
        return True

    # If group restriction is set, enforce it
    if ALLOWED_GROUP_ID_INT is not None and update.effective_chat:
        return update.effective_chat.id == ALLOWED_GROUP_ID_INT

    # Otherwise allow
    return True


# ----------------------------
# Commands
# ----------------------------
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return

    msg = (
        f"<b>{_escape_html(BOT_DISPLAY)}</b> 📚\n\n"
        "Comandi:\n"
        "• /status — stato bot + PDF\n"
        "• /sources — lista PDF\n"
        "• /ask &lt;domanda&gt; — cerca nei testi\n"
        "• /quote — citazione (placeholder)\n"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return

    global INDEX
    idx = INDEX

    pdf_ok = idx is not None and idx.books > 0 and idx.pages > 0
    pdf_line = f"{'✅' if pdf_ok else '❌'} {idx.books if idx else 0} libri / {idx.pages if idx else 0} pagine / testo:{idx.text_pages if idx else 0} / chars:{idx.chars if idx else 0}"

    msg = (
        "<b>📌 Status</b>\n"
        f"• Username: {_escape_html(BOT_USERNAME)}\n"
        f"• 🔒 ALLOWED_GROUP_ID={_escape_html(ALLOWED_GROUP_ID) if ALLOWED_GROUP_ID else '—'}\n"
        f"• JobQueue: ✅\n"
        f"• PDF: {pdf_line}\n"
        f"• pypdf: {'✅' if HAVE_PYPDF else '❌'}\n"
        f"• PDF_DIR: {_escape_html(str(PDF_DIR))}\n"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return

    pdfs = list_pdfs(PDF_DIR)
    if not pdfs:
        await update.effective_message.reply_text("📚 Nessun PDF trovato in data/pdfs.")
        return

    lines = ["📚 Libri CF77 caricati"]
    for p in pdfs:
        lines.append(f"• {p.name}")
    await update.effective_message.reply_text("\n".join(lines))

async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return
    # Placeholder: keep simple & safe
    await update.effective_message.reply_text("📜 “Conosci te stesso.” — (placeholder)")

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed_chat(update):
        return

    global INDEX
    idx = INDEX

    q = " ".join(context.args).strip() if context.args else ""
    if not q:
        await update.effective_message.reply_text("Usa: /ask <domanda>")
        return

    if idx is None or idx.books == 0 or idx.pages == 0 or not idx.chunks:
        await update.effective_message.reply_text(
            "😤 Non ho un indice pronto o i PDF non hanno testo estraibile.\n"
            "Controlla /status e /sources."
        )
        return

    terms = [t for t in re.findall(r"[a-zàèéìòù0-9']+", q, flags=re.IGNORECASE) if len(t) >= 4]
    results = search_index(q, idx, top_k=3)

    if not results:
        await update.effective_message.reply_text(
            "😤 Non ho trovato un passaggio chiaro nei PDF indicizzati.\n"
            "Prova con parole chiave più specifiche (es: “piano astrale”, “trapasso”, “corpo astrale”)."
        )
        return

    blocks = []
    for ch, sc in results:
        sn = snippet(ch.text, terms, max_len=420)
        blocks.append(
            f"<b>📖 { _escape_html(ch.book) }</b> — pag. <b>{ch.page}</b>\n"
            f"{_escape_html(sn)}"
        )

    header = (
        f"Salve, <b>@{_escape_html(update.effective_user.username or 'utente')}</b>. Hai chiamato il { _escape_html(BOT_DISPLAY) } 📚\n"
        f"📌 Domanda ricevuta:\n{_escape_html(q)}\n\n"
        "🧠 Passaggi trovati:\n\n"
    )
    msg = header + "\n\n— — —\n\n".join(blocks)
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Optional: ignore plain text in groups; allow in private with hint
    if not is_allowed_chat(update):
        return
    if update.effective_chat and update.effective_chat.type == "private":
        await update.effective_message.reply_text("Scrivi /help oppure usa /ask <domanda> 🙂")


# ----------------------------
# Application builder
# ----------------------------
def _warmup_index() -> None:
    global INDEX
    INDEX = build_index(PDF_DIR)
    logger.info(
        "CF77 RAG pronto. books=%s pages=%s text_pages=%s chars=%s dir=%s pdfreader=%s",
        INDEX.books, INDEX.pages, INDEX.text_pages, INDEX.chars, PDF_DIR, HAVE_PYPDF
    )

async def post_init(app: Application) -> None:
    # Runs in PTB event loop at startup
    _warmup_index()

def build_application() -> Application:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN mancante nelle env vars")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("ask", cmd_ask))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


# ----------------------------
# Local polling entrypoint
# ----------------------------
def main() -> None:
    logger.info("%s è in ascolto…", BOT_DISPLAY)
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
