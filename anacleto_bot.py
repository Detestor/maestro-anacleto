# =========================
# MAESTRO ANACLETO - BOT
# anacleto_bot.py
# =========================

from __future__ import annotations

import os
import re
import time
import random
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from pathlib import Path
PDF_DIR = Path(__file__).resolve().parent / "data" / "pdfs"
pdf_paths = sorted(PDF_DIR.glob("*.pdf"))  # prende anche *_ocr.pdf
from datetime import datetime, timedelta

from dotenv import load_dotenv

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Optional: pypdf per estrazione testo
try:
    from pypdf import PdfReader  # type: ignore
    HAVE_PYPDF = True
except Exception:
    HAVE_PYPDF = False


# -------------------------
# Config / Constants
# -------------------------

load_dotenv()

BOT_DISPLAY = "MAESTRO ANACLETO"
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()

# Allow group restriction (supergroup id). If empty -> allow all.
ALLOWED_GROUP_ID = os.getenv("ALLOWED_GROUP_ID", "").strip()

# Directory PDF:
BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = Path(os.getenv("PDF_DIR", str(BASE_DIR / "data" / "pdfs")))

# Schedules (Europe/Rome)
TZ_NAME = os.getenv("TZ_NAME", "Europe/Rome")
MORNING_HHMM = os.getenv("MORNING_HHMM", "08:20")
NIGHT_HHMM = os.getenv("NIGHT_HHMM", "00:24")

# Search tuning
MAX_SNIPPET_CHARS = 360
TOP_K = 4

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger = logging.getLogger("ANACLETO")
logger.setLevel(LOG_LEVEL)

if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(LOG_LEVEL)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    ch.setFormatter(fmt)
    logger.addHandler(ch)


# -------------------------
# Utilities
# -------------------------

def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == "private")


def _chat_id(update: Update) -> Optional[int]:
    if update.effective_chat:
        return update.effective_chat.id
    return None


def _user_mention(update: Update) -> str:
    u = update.effective_user
    if not u:
        return "amico"
    if u.username:
        return f"@{u.username}"
    name = (u.first_name or "amico").strip()
    return name


def _allowed_here(update: Update) -> bool:
    """Se ALLOWED_GROUP_ID è settato: consenti DM + quel gruppo, blocca altri gruppi."""
    if _is_private(update):
        return True
    if not ALLOWED_GROUP_ID:
        return True
    try:
        allowed = int(ALLOWED_GROUP_ID)
    except Exception:
        return True
    cid = _chat_id(update)
    return cid == allowed


def _safe_text(text: str) -> str:
    """Evita problemi di entities su Telegram. Usiamo HTML minimal e escape."""
    # Escape HTML basic
    text = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )
    return text


async def reply_safe(update: Update, text: str) -> None:
    """Invia testo in modo sicuro (HTML), spezzando se troppo lungo."""
    if not update.message:
        return
    text = text.strip()
    if not text:
        return

    # Telegram max message length ~4096
    chunks = []
    while len(text) > 3900:
        cut = text.rfind("\n", 0, 3900)
        if cut < 1000:
            cut = 3900
        chunks.append(text[:cut])
        text = text[cut:].lstrip()
    chunks.append(text)

    for c in chunks:
        await update.message.reply_text(
            _safe_text(c),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


def normalize_query(q: str) -> List[str]:
    q = q.lower().strip()
    # parole, togli roba corta
    tokens = re.findall(r"[a-zàèéìòù0-9']+", q, flags=re.IGNORECASE)
    tokens = [t for t in tokens if len(t) >= 3]
    return tokens


def compact_spaces(s: str) -> str:
    s = s.replace("\u00ad", "")  # soft hyphen
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def fix_hyphen_linebreaks(s: str) -> str:
    """
    Unisce parole spezzate tipo:
    'par-\nlassero' -> 'parlassero'
    """
    s = s.replace("\r\n", "\n")
    s = re.sub(r"(\w)-\n(\w)", r"\1\2", s)
    return s


# -------------------------
# Data structures
# -------------------------

@dataclass
class PageHit:
    book: str
    page: int
    score: float
    snippet: str


@dataclass
class PdfIndex:
    books: int = 0
    pages: int = 0
    text_pages: int = 0
    chars: int = 0
    entries: List[Tuple[str, int, str]] = None  # (book, page, text)

    def __post_init__(self):
        if self.entries is None:
            self.entries = []

# -------------------------
# PDF Indexing
# -------------------------

def list_pdfs(pdf_dir: Path) -> List[Path]:
    if not pdf_dir.exists():
        return []
    return sorted([p for p in pdf_dir.glob("*.pdf") if p.is_file()])


def extract_pdf_text(pdf_path: Path) -> List[str]:
    """
    Ritorna lista di testi per pagina (1-indexed concettualmente).
    Richiede pypdf.
    """
    if not HAVE_PYPDF:
        raise RuntimeError("pypdf non disponibile")

    reader = PdfReader(str(pdf_path))
    pages_text: List[str] = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        t = fix_hyphen_linebreaks(t)
        t = compact_spaces(t)
        pages_text.append(t)
    return pages_text


def build_index(pdf_dir: Path) -> PdfIndex:
    idx = PdfIndex()
    pdfs = list_pdfs(pdf_dir)
    idx.books = len(pdfs)

    if not pdfs:
        return idx

    if not HAVE_PYPDF:
        logger.warning("pypdf non disponibile: non posso estrarre testo dai PDF.")
        return idx

    for pdf in pdfs:
        book = pdf.name
        try:
            pages = extract_pdf_text(pdf)
        except Exception as e:
            logger.error(f"Errore lettura PDF {book}: {e}")
            continue

        for pi, text in enumerate(pages, start=1):
            idx.pages += 1
            if text:
                idx.text_pages += 1
                idx.chars += len(text)
            idx.entries.append((book, pi, text))

    return idx


def score_page(text: str, tokens: List[str]) -> float:
    if not text:
        return 0.0
    lt = text.lower()
    score = 0.0
    for t in tokens:
        # punteggio per occorrenze
        c = lt.count(t)
        if c:
            score += min(5, c) * 1.0
            # bonus se appare all'inizio
            if lt.find(t) < 120:
                score += 0.7
    # bonus se più token presenti
    present = sum(1 for t in set(tokens) if t in lt)
    score += present * 0.4
    return score


def make_snippet(text: str, tokens: List[str]) -> str:
    if not text:
        return ""
    lt = text.lower()

    # trova prima occorrenza di qualunque token
    pos = None
    for t in tokens:
        p = lt.find(t)
        if p != -1:
            pos = p if pos is None else min(pos, p)

    if pos is None:
        # fallback: inizio pagina
        snip = text[:MAX_SNIPPET_CHARS]
        return snip.strip()

    start = max(0, pos - 140)
    end = min(len(text), start + MAX_SNIPPET_CHARS)
    snip = text[start:end].strip()

    # aggiungi ellissi se tagliato
    if start > 0:
        snip = "… " + snip
    if end < len(text):
        snip = snip + " …"
    return snip


def search_index(idx: PdfIndex, query: str, top_k: int = TOP_K) -> List[PageHit]:
    tokens = normalize_query(query)
    if not tokens:
        return []

    hits: List[PageHit] = []
    for (book, page, text) in idx.entries:
        s = score_page(text, tokens)
        if s <= 0:
            continue
        snip = make_snippet(text, tokens)
        hits.append(PageHit(book=book, page=page, score=s, snippet=snip))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


# -------------------------
# Quotes (semplice ma decente)
# -------------------------

FALLBACK_QUOTES = [
    ("Cerchio Firenze 77 (idea guida)", "Conosci te stesso, e cambia il mondo."),
    ("Stoici (riassunto)", "Non controlli gli eventi, controlli la risposta."),
    ("Socrate (parafrasi)", "La vita non esaminata non vale d’essere vissuta."),
    ("Plotino (parafrasi)", "Rientra in te stesso: lì abita la verità."),
    ("Spiritualità (pratica)", "Il silenzio non è vuoto: è ascolto."),
]


def get_quote() -> Tuple[str, str]:
    return random.choice(FALLBACK_QUOTES)


# -------------------------
# Telegram Commands
# -------------------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed_here(update):
        return

    msg = (
        f"📚 <b>{BOT_DISPLAY}</b>\n\n"
        "Comandi:\n"
        "• <b>/ask &lt;domanda&gt;</b> — cerca nei libri CF77\n"
        "• <b>/sources</b> — lista PDF caricati\n"
        "• <b>/status</b> — stato bot + indicizzazione\n"
        "• <b>/quote</b> — citazione random (fallback)\n\n"
        "Nota: in DM funziona sempre. Nel gruppo, solo se autorizzato."
    )
    await reply_safe(update, msg)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed_here(update):
        return

    idx: PdfIndex = context.application.bot_data.get("pdf_index")  # type: ignore
    pdf_ok = "✅" if idx and idx.books > 0 else "❌"
    jq = "✅" if context.application.job_queue else "❌"

    msg = (
        "📌 <b>Status</b>\n"
        f"• Username: @{(context.bot.username or 'unknown')}\n"
        f"• 🔒 ALLOWED_GROUP_ID={ALLOWED_GROUP_ID or '(none)'}\n"
        f"• JobQueue: {jq}\n"
        f"• PDF: {pdf_ok} {idx.books if idx else 0} libri / {idx.pages if idx else 0} pagine / "
        f"testo:{idx.text_pages if idx else 0} / chars:{idx.chars if idx else 0}\n"
        f"• pypdf: {'✅' if HAVE_PYPDF else '❌'}\n"
        f"• PDF_DIR: {PDF_DIR}"
    )
    await reply_safe(update, msg)


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed_here(update):
        return

    pdfs = list_pdfs(PDF_DIR)
    if not pdfs:
        await reply_safe(update, "📚 Nessun PDF trovato in <b>data/pdfs</b>.")
        return

    lines = ["📚 <b>Libri CF77 caricati</b>"]
    for p in pdfs:
        lines.append(f"• {p.name}")
    await reply_safe(update, "\n".join(lines))


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed_here(update):
        return

    author, quote = get_quote()
    msg = f"📜 <b>{author}</b>\n“{quote}”"
    await reply_safe(update, msg)


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed_here(update):
        return

    if not update.message:
        return

    text = update.message.text or ""
    parts = text.split(" ", 1)
    if len(parts) < 2 or not parts[1].strip():
        await reply_safe(update, "Usa: <b>/ask &lt;domanda&gt;</b>")
        return

    question = parts[1].strip()
    user = _user_mention(update)

    idx: PdfIndex = context.application.bot_data.get("pdf_index")  # type: ignore
    if not idx or idx.books == 0 or idx.pages == 0:
        await reply_safe(update, "😤 Non ho PDF indicizzati al momento. Prova <b>/sources</b> e <b>/status</b>.")
        return

    hits = search_index(idx, question, top_k=TOP_K)

    header = (
        f"Salve, <b>{user}</b>. Hai chiamato il <b>{BOT_DISPLAY}</b>. 📚\n"
        f"📌 <b>Domanda</b>: {question}\n"
    )

    if not hits:
        tail = (
            "😤 Non ho trovato un passaggio chiaro nei PDF.\n"
            "Prova a riformulare con parole chiave più specifiche "
            "(es: “piano astrale”, “corpo astrale”, “trapasso”)."
        )
        await reply_safe(update, header + "\n" + tail)
        return

    lines = [header, "\n🧠 <b>Passaggi rilevanti</b>:"]
    for i, h in enumerate(hits, start=1):
        lines.append(
            f"\n<b>{i})</b> <i>{h.book}</i> — pag. <b>{h.page}</b>\n"
            f"{h.snippet}"
        )

    await reply_safe(update, "\n".join(lines))

# -------------------------
# Scheduler jobs (buongiorno / buonanotte / planner)
# -------------------------

def _parse_hhmm(hhmm: str) -> Tuple[int, int]:
    hhmm = (hhmm or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", hhmm)
    if not m:
        return (8, 20)
    h = int(m.group(1))
    mi = int(m.group(2))
    return (h, mi)


async def job_good_morning(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.application.bot_data.get("announce_chat_id")
    if not chat_id:
        return
    a, q = get_quote()
    msg = f"☀️ <b>Buongiorno</b>\n📜 <b>{a}</b>\n“{q}”"
    await context.bot.send_message(chat_id=chat_id, text=_safe_text(msg), parse_mode=ParseMode.HTML)


async def job_good_night(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.application.bot_data.get("announce_chat_id")
    if not chat_id:
        return
    a, q = get_quote()
    msg = f"🌙 <b>Buonanotte</b>\n📜 <b>{a}</b>\n“{q}”"
    await context.bot.send_message(chat_id=chat_id, text=_safe_text(msg), parse_mode=ParseMode.HTML)


async def job_daily_planner(context: ContextTypes.DEFAULT_TYPE) -> None:
    # placeholder: per ora non spamma nulla
    return


def schedule_jobs(app: Application) -> None:
    # decide dove annunciare: se c'è un allowed group id, usa quello, altrimenti nulla.
    if ALLOWED_GROUP_ID:
        try:
            app.bot_data["announce_chat_id"] = int(ALLOWED_GROUP_ID)
        except Exception:
            app.bot_data["announce_chat_id"] = None
    else:
        app.bot_data["announce_chat_id"] = None

    if not app.job_queue:
        logger.warning("JobQueue non disponibile.")
        return

    mh, mm = _parse_hhmm(MORNING_HHMM)
    nh, nm = _parse_hhmm(NIGHT_HHMM)

    # Nota: PTB JobQueue usa timezone interna dell'event loop; va bene per ora.
    now = datetime.now()
    morning = now.replace(hour=mh, minute=mm, second=0, microsecond=0)
    night = now.replace(hour=nh, minute=nm, second=0, microsecond=0)

    if morning <= now:
        morning += timedelta(days=1)
    if night <= now:
        night += timedelta(days=1)

    # one-shot + reschedule daily via repeating interval (semplice)
    app.job_queue.run_once(job_good_morning, when=(morning - now).total_seconds(), name="good_morning_once")
    app.job_queue.run_once(job_good_night, when=(night - now).total_seconds(), name="good_night_once")

    logger.info(f"Pianificato mattino: {morning.isoformat()} | notte: {night.isoformat()}")


# -------------------------
# Build application
# -------------------------

async def post_init(app: Application) -> None:
    # indicizza PDF all'avvio
    t0 = time.time()
    idx = build_index(PDF_DIR)
    app.bot_data["pdf_index"] = idx
    dt = time.time() - t0

    logger.info(
        f"CF77 RAG pronto. books={idx.books} pages={idx.pages} text_pages={idx.text_pages} "
        f"chars={idx.chars} dir={PDF_DIR} pdfreader={HAVE_PYPDF}"
    )
    logger.info(f"Indicizzazione completata in {dt:.2f}s")

    # schedula job
    schedule_jobs(app)


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN mancante nelle env vars")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Handlers
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("ask", cmd_ask))

    # fallback: se scrivi in privato senza /ask, ti risponde come guida
    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        if not _allowed_here(update):
            return
        if _is_private(update):
            await reply_safe(update, "Scrivimi con <b>/ask &lt;domanda&gt;</b> 🙂")

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    return app


# -------------------------
# Local run (solo per test)
# -------------------------
if __name__ == "__main__":
    logger.info(f"{BOT_DISPLAY} è in ascolto…")
    application = build_application()
    application.run_polling(allowed_updates=Update.ALL_TYPES)