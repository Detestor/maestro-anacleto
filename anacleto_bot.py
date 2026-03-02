# anacleto_bot.py
# Maestro Anacleto — Telegram bot core (python-telegram-bot v21.x)
# - Works both in polling (local) and webhook (via anacleto_web.py)
# - CF77 PDF index (pypdf) + /ask search with citations (book + page)
# - /sources, /status, /help, /quote (simple), basic guard for allowed group
#
# ENV VARS (Render -> Environment):
#   TELEGRAM_TOKEN            (required) bot token
#   ALLOWED_GROUP_ID          (optional) if set, only this group is allowed for group chats
#   PDF_DIR                   (optional) default: <repo>/data/pdfs
#   LOG_LEVEL                 (optional) default: INFO
#   TZ                        (optional) default: Europe/Rome
#   GOOD_MORNING_AT           (optional) HH:MM 24h, default 08:20
#   GOOD_NIGHT_AT             (optional) HH:MM 24h, default 00:24
#   DAILY_PLANNER_AT          (optional) HH:MM 24h, default 00:05
#
# Requirements (typical):
#   python-telegram-bot[job-queue]==21.6
#   pypdf>=4.0.0   (or compatible)
#   fastapi uvicorn (only for webhook wrapper in anacleto_web.py, not needed for polling-only)

from __future__ import annotations

import os
import re
import json
import time
import html
import math
import logging
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

# -----------------------
# Globals / config
# -----------------------

BOT_DISPLAY = "MAESTRO ANACLETO"

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger("ANACLETO")

BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = Path(os.getenv("PDF_DIR", str(BASE_DIR / "data" / "pdfs"))).resolve()

ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID", "").strip()
ALLOWED_GROUP_ID_INT: Optional[int] = int(ALLOWED_GROUP_ID) if ALLOWED_GROUP_ID else None

TZ = os.getenv("TZ", "Europe/Rome")
GOOD_MORNING_AT = os.getenv("GOOD_MORNING_AT", "08:20")
GOOD_NIGHT_AT = os.getenv("GOOD_NIGHT_AT", "00:24")
DAILY_PLANNER_AT = os.getenv("DAILY_PLANNER_AT", "00:05")

# -----------------------
# PDF extraction (pypdf)
# -----------------------
HAVE_PYPDF = False
try:
    from pypdf import PdfReader  # type: ignore
    HAVE_PYPDF = True
except Exception:
    HAVE_PYPDF = False


def _safe_int(s: str, default: int) -> int:
    try:
        return int(s)
    except Exception:
        return default


def _parse_hhmm(s: str, default: str) -> Tuple[int, int]:
    s = (s or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not m:
        m = re.match(r"^(\d{1,2})\.(\d{2})$", default)
    if not m:
        m = re.match(r"^(\d{1,2}):(\d{2})$", default)
    hh = _safe_int(m.group(1), 0)
    mm = _safe_int(m.group(2), 0)
    hh = max(0, min(23, hh))
    mm = max(0, min(59, mm))
    return hh, mm


def _collapse_ws(text: str) -> str:
    # Fix hyphenation at line breaks: "par-\nlassero" -> "parlassero"
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    # Newlines -> space
    text = re.sub(r"[ \t]*\n+[ \t]*", " ", text)
    # Multiple spaces
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def _escape_md(s: str) -> str:
    # For Telegram MarkdownV2 would be annoying. We use HTML mode instead,
    # so escape HTML and do minimal cleaning.
    return html.escape(s)


@dataclass
class PageChunk:
    book: str
    page_1based: int
    text: str


@dataclass
class PdfIndex:
    books: int
    pages: int
    text_pages: int
    chars: int
    chunks: List[PageChunk]
    by_book: Dict[str, List[PageChunk]]


def list_pdfs(pdf_dir: Path) -> List[Path]:
    if not pdf_dir.exists():
        logger.warning("PDF_DIR non trovata: %s — crea la cartella e inserisci i PDF.", pdf_dir)
        return []
    # case-insensitive: raccoglie sia *.pdf che *.PDF
    found = [p for p in pdf_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    if not found:
        logger.warning("Nessun PDF trovato in: %s", pdf_dir)
    return sorted(found)


def extract_pdf_text(path: Path) -> List[str]:
    """Return list of page texts (raw)."""
    if not HAVE_PYPDF:
        raise RuntimeError("pypdf non disponibile")
    reader = PdfReader(str(path))
    out: List[str] = []
    for p in reader.pages:
        try:
            txt = p.extract_text() or ""
        except Exception:
            txt = ""
        out.append(txt)
    return out


def build_index(pdf_dir: Path) -> PdfIndex:
    pdfs = list_pdfs(pdf_dir)
    chunks: List[PageChunk] = []
    by_book: Dict[str, List[PageChunk]] = {}

    pages_total = 0
    text_pages = 0
    chars = 0

    if not pdfs:
        return PdfIndex(books=0, pages=0, text_pages=0, chars=0, chunks=[], by_book={})

    if not HAVE_PYPDF:
        logger.warning("pypdf non disponibile: non posso estrarre testo dai PDF.")
        # Still report books count for /sources, but no text.
        return PdfIndex(books=len(pdfs), pages=0, text_pages=0, chars=0, chunks=[], by_book={p.name: [] for p in pdfs})

    for pdf in pdfs:
        book_name = pdf.name
        try:
            page_texts = extract_pdf_text(pdf)
        except Exception as e:
            logger.exception("Errore lettura PDF %s: %s", pdf.name, e)
            continue

        pages_total += len(page_texts)
        book_chunks: List[PageChunk] = []

        for i, raw in enumerate(page_texts):
            cleaned = _collapse_ws(raw) if raw else ""
            if cleaned:
                text_pages += 1
                chars += len(cleaned)
                chunk = PageChunk(book=book_name, page_1based=i + 1, text=cleaned)
                chunks.append(chunk)
                book_chunks.append(chunk)

        by_book[book_name] = book_chunks

    return PdfIndex(
        books=len(pdfs),
        pages=pages_total,
        text_pages=text_pages,
        chars=chars,
        chunks=chunks,
        by_book=by_book,
    )


# Cache in-memory (ok for Render single instance)
_INDEX: Optional[PdfIndex] = None
_INDEX_BUILT_AT: float = 0.0
_INDEX_TTL_SECONDS = int(os.getenv("INDEX_TTL_SECONDS", "600"))  # 10 minutes


def get_index(force: bool = False) -> PdfIndex:
    global _INDEX, _INDEX_BUILT_AT
    now = time.time()
    if force or (_INDEX is None) or (now - _INDEX_BUILT_AT > _INDEX_TTL_SECONDS):
        pdfs = list_pdfs(PDF_DIR)
        logger.info("PDF_DIR=%s | found_pdfs=%s", PDF_DIR, len(pdfs))
        idx = build_index(PDF_DIR)
        _INDEX = idx
        _INDEX_BUILT_AT = now
        logger.info(
            "CF77 RAG pronto. books=%s pages=%s text_pages=%s chars=%s dir=%s pdfreader=%s",
            idx.books, idx.pages, idx.text_pages, idx.chars, PDF_DIR, HAVE_PYPDF
        )
    return _INDEX


# -----------------------
# Search / Ask
# -----------------------

def _score(text: str, keywords: List[str]) -> int:
    t = text.lower()
    score = 0
    for kw in keywords:
        if not kw:
            continue
        # simple count occurrences
        score += t.count(kw.lower())
    return score


def _keywords(question: str) -> List[str]:
    # keep words >= 4 chars, remove stopwords-ish
    q = question.lower()
    q = re.sub(r"[^a-zàèéìòù0-9\s']", " ", q, flags=re.I)
    words = [w.strip("'") for w in q.split()]
    stop = {"dopo", "cosa", "succede", "quando", "come", "perché", "perche", "che", "della", "delle", "degli", "dello",
            "dell", "alla", "alle", "agli", "allo", "del", "dei", "di", "da", "in", "su", "per", "con", "tra", "fra",
            "un", "una", "uno", "il", "lo", "la", "i", "gli", "le", "e", "o", "ma", "non", "più", "piu", "vero"}
    kws = [w for w in words if len(w) >= 4 and w not in stop]
    # add some common anchors if user asks about death/afterlife and didn't mention specifics
    if any(x in q for x in ["morte", "morire", "trapasso", "defunto", "aldilà", "aldila"]) and not any(
        x in kws for x in ["astrale", "astral", "spirituale", "spiriti", "piano", "corpo", "trapasso"]
    ):
        kws += ["trapasso", "astrale", "piano", "spirito"]
    # de-dupe
    seen = set()
    out = []
    for k in kws:
        if k not in seen:
            out.append(k)
            seen.add(k)
    return out[:12]


def search_passages(question: str, top_k: int = 5) -> List[Tuple[PageChunk, int]]:
    idx = get_index()
    if idx.text_pages == 0 or not idx.chunks:
        return []
    kws = _keywords(question)
    if not kws:
        return []

    scored: List[Tuple[PageChunk, int]] = []
    for ch in idx.chunks:
        s = _score(ch.text, kws)
        if s > 0:
            scored.append((ch, s))

    scored.sort(key=lambda x: x[1], reverse=True)
    # keep diverse: avoid too many from same book+near pages
    out: List[Tuple[PageChunk, int]] = []
    used = set()
    for ch, s in scored:
        key = (ch.book, ch.page_1based // 3)  # bucket pages
        if key in used:
            continue
        out.append((ch, s))
        used.add(key)
        if len(out) >= top_k:
            break
    return out


def format_answer(question: str) -> str:
    hits = search_passages(question, top_k=4)
    if not hits:
        return (
            "😤 <b>Non ho trovato un passaggio chiaro</b> nei PDF indicizzati.\n"
            "Prova a riformulare con parole chiave più specifiche (es: “piano astrale”, “corpo astrale”, “trapasso”)."
        )

    # Build response
    lines = []
    lines.append("📚 <b>Risposta dai testi CF77 (estratti)</b>")
    lines.append(f"📌 <b>Domanda:</b> {_escape_md(question)}")
    lines.append("")

    for ch, s in hits:
        excerpt = ch.text
        # keep excerpt short-ish
        if len(excerpt) > 800:
            excerpt = excerpt[:800].rsplit(" ", 1)[0] + "…"
        lines.append(f"🔎 <b>{_escape_md(ch.book)}</b> — pag. <b>{ch.page_1based}</b>")
        lines.append(f"“{_escape_md(excerpt)}”")
        lines.append("")

    lines.append("🧾 <i>Cita sempre libro+pagina se ricondividi.</i>")
    return "\n".join(lines).strip()


# -----------------------
# Quotes (simple)
# -----------------------

FALLBACK_QUOTES = [
    ("Cerchio Firenze 77", "Conosci te stesso.", "motto"),
    ("Kardec", "Il vero spirito si riconosce dalla sua trasformazione morale.", "parafrasi"),
    ("Anacleto", "Se non trovi la risposta, cambia la domanda.", "bot-saggezza"),
]


def pick_quote() -> Tuple[str, str, str]:
    i = int(time.time()) % len(FALLBACK_QUOTES)
    return FALLBACK_QUOTES[i]


# -----------------------
# Chat guard
# -----------------------

def is_allowed_chat(update: Update) -> bool:
    if update.effective_chat is None:
        return False
    chat = update.effective_chat
    if chat.type in ("private",):
        return True
    if ALLOWED_GROUP_ID_INT is None:
        return True
    return chat.id == ALLOWED_GROUP_ID_INT


async def guard_or_warn(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if is_allowed_chat(update):
        return True
    try:
        await update.effective_message.reply_text(
            "⛔ Questo bot è configurato per funzionare solo nel gruppo autorizzato.",
            disable_web_page_preview=True,
        )
    except Exception:
        pass
    return False


# -----------------------
# Commands
# -----------------------

HELP_TEXT = (
    "📚 <b>MAESTRO ANACLETO</b>\n"
    "\n"
    "Comandi:\n"
    "• /ask &lt;domanda&gt; — cerca nei PDF CF77 e cita libro+pagina\n"
    "• /sources — lista PDF caricati\n"
    "• /status — stato bot + indicizzazione\n"
    "• /quote — una citazione (fallback)\n"
    "• /reindex — ricostruisce indice (admin)\n"
)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_or_warn(update, context):
        return
    await update.effective_message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_or_warn(update, context):
        return
    pdfs = list_pdfs(PDF_DIR)
    if not pdfs:
        await update.effective_message.reply_text("📚 Nessun PDF trovato in data/pdfs.", parse_mode=ParseMode.HTML)
        return
    lines = ["📚 <b>Libri CF77 caricati</b>"]
    for p in pdfs:
        lines.append(f"• {_escape_md(p.name)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_or_warn(update, context):
        return
    idx = get_index(force=False)
    me = await context.bot.get_me()
    jobq = "✅" if context.application.job_queue else "❌"
    lines = [
        "📌 <b>Status</b>",
        f"• Username: @{_escape_md(me.username or '')}",
        f"• 🔒 ALLOWED_GROUP_ID={_escape_md(ALLOWED_GROUP_ID) if ALLOWED_GROUP_ID else '(none)'}",
        f"• JobQueue: {jobq}",
        f"• PDF: ✅ {idx.books} libri / {idx.pages} pagine / testo:{idx.text_pages} / chars:{idx.chars}",
        f"• PDF_DIR: {_escape_md(str(PDF_DIR))}",
        f"• pypdf: {'✅' if HAVE_PYPDF else '❌'}",
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_or_warn(update, context):
        return
    a, q, src = pick_quote()
    msg = (
        f"📜 <b>{_escape_md(a)}</b>\n"
        f"“{_escape_md(q)}”\n"
        f"<i>Fonte:</i> {_escape_md(src)}"
    )
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_or_warn(update, context):
        return

    text = update.effective_message.text or ""
    parts = text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        await update.effective_message.reply_text("Usa: /ask <domanda>", parse_mode=ParseMode.HTML)
        return
    question = parts[1].strip()

    # quick ack
    header = (
        f"Salve, <b>@{_escape_md(update.effective_user.username or 'utente')}</b>. "
        f"Hai chiamato il <b>{BOT_DISPLAY}</b>. 📚\n"
    )
    body = format_answer(question)
    await update.effective_message.reply_text(header + body, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_reindex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await guard_or_warn(update, context):
        return
    # simple admin: allow only private for now
    if update.effective_chat and update.effective_chat.type != "private":
        await update.effective_message.reply_text("🔧 /reindex è disponibile solo in privato.", parse_mode=ParseMode.HTML)
        return
    get_index(force=True)
    idx = get_index(force=False)
    await update.effective_message.reply_text(
        f"✅ Reindex completo: {idx.books} libri, {idx.pages} pagine, testo:{idx.text_pages}, chars:{idx.chars}",
        parse_mode=ParseMode.HTML,
    )


# -----------------------
# Scheduled messages
# -----------------------

def _job_time(hhmm: str, default: str) -> dtime:
    hh, mm = _parse_hhmm(hhmm, default)
    return dtime(hour=hh, minute=mm)


async def good_morning_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # Send only if allowed group is set; otherwise do nothing (avoids spamming).
    if ALLOWED_GROUP_ID_INT is None:
        return
    a, q, src = pick_quote()
    msg = f"☀️ <b>Buongiorno</b>.\n📜 <b>{_escape_md(a)}</b> — “{_escape_md(q)}”\n<i>{_escape_md(src)}</i>"
    try:
        await context.bot.send_message(chat_id=ALLOWED_GROUP_ID_INT, text=msg, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Errore invio buongiorno")


async def good_night_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    if ALLOWED_GROUP_ID_INT is None:
        return
    msg = "🌙 <b>Buonanotte</b>."
    try:
        await context.bot.send_message(chat_id=ALLOWED_GROUP_ID_INT, text=msg, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Errore invio buonanotte")


async def daily_planner_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    # Placeholder (you can expand later)
    if ALLOWED_GROUP_ID_INT is None:
        return
    msg = "🧭 <b>Daily planner</b>: (placeholder)"
    try:
        await context.bot.send_message(chat_id=ALLOWED_GROUP_ID_INT, text=msg, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Errore invio daily planner")


async def post_init(app: Application) -> None:
    # Warm up index on startup
    try:
        get_index(force=True)
    except Exception:
        logger.exception("Errore warm-up index")

    # Schedule jobs
    if app.job_queue:
        gm = _job_time(GOOD_MORNING_AT, "08:20")
        gn = _job_time(GOOD_NIGHT_AT, "00:24")
        dp = _job_time(DAILY_PLANNER_AT, "00:05")

        app.job_queue.run_daily(good_morning_job, time=gm, name="good_morning")
        app.job_queue.run_daily(good_night_job, time=gn, name="good_night")
        app.job_queue.run_daily(daily_planner_job, time=dp, name="daily_planner")

        logger.info("Pianificato mattino: %s | notte: %s", gm, gn)
        logger.info("Daily planner schedulato per: %s", dp)


# -----------------------
# Builder (used by anacleto_web.py)
# -----------------------

def build_application() -> Application:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN mancante nelle env vars")

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("reindex", cmd_reindex))

    # Optional: if user writes plain text in private, treat as /ask
    async def _fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await guard_or_warn(update, context):
            return
        if update.effective_chat and update.effective_chat.type == "private":
            question = (update.effective_message.text or "").strip()
            if question:
                header = (
                    f"Salve, <b>@{_escape_md(update.effective_user.username or 'utente')}</b>. "
                    f"Hai chiamato il <b>{BOT_DISPLAY}</b>. 📚\n"
                )
                body = format_answer(question)
                await update.effective_message.reply_text(
                    header + body, parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _fallback_text))

    # Global error handler
    async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.exception("Errore non gestito: %s", context.error)

    app.add_error_handler(_on_error)

    return app


# -----------------------
# Polling entrypoint (local)
# -----------------------

def main_polling() -> None:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN mancante nelle env vars")
    app = build_application()
    print(f"{BOT_DISPLAY} è in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main_polling()
