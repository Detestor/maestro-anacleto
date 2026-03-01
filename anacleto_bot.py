from __future__ import annotations

import os
import re
import datetime as dt
import logging
import random
import html
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import httpx

from pypdf import PdfReader

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import Conflict


# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)
log = logging.getLogger("ANACLETO")


# ===================== ENV =====================
load_dotenv()

BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
BOT_USERNAME = (os.getenv("BOT_USERNAME", "MaestroAnacletoBot") or "MaestroAnacletoBot").lstrip("@").strip()
ALLOWED_GROUP_ID_RAW = (os.getenv("ALLOWED_GROUP_ID") or "").strip()
TZ_NAME = (os.getenv("TZ", "Europe/Rome") or "Europe/Rome").strip()
BOT_DISPLAY = (os.getenv("BOT_DISPLAY", "MAESTRO ANACLETO") or "MAESTRO ANACLETO").strip()

PDF_DIR_ENV = (os.getenv("PDF_DIR") or "data/pdfs").strip()

ALLOWED_GROUP_ID: Optional[int] = None
if ALLOWED_GROUP_ID_RAW:
    try:
        ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID_RAW)
    except ValueError:
        raise RuntimeError("ALLOWED_GROUP_ID deve essere un intero (es: -1001234567890)")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN mancante. Mettilo nelle env vars di Render o nel .env locale.")


# ===================== UTIL TIME / STYLE =====================
def mention_token() -> str:
    return f"@{BOT_USERNAME}"


def now_local() -> dt.datetime:
    return dt.datetime.now()


def is_night_now() -> bool:
    h = now_local().hour
    return h >= 23 or h < 7


def night_intro(user_first: str) -> str:
    intros = [
        f"@{user_first}, soffri di insonnia stanotte? 😈\n",
        f"Eh… spero sia importante, @{user_first}. A quest’ora. 🌙\n",
        f"@{user_first} evocazioni notturne? Va bene… parla. 🕯️\n",
    ]
    return random.choice(intros)


def day_intro(user_first: str) -> str:
    return f"Salve, @{user_first}. Hai chiamato il {BOT_DISPLAY}. 📚\n"


def random_time_today_window(start_hm: Tuple[int, int], end_hm: Tuple[int, int]) -> dt.datetime:
    now = now_local()
    start = now.replace(hour=start_hm[0], minute=start_hm[1], second=0, microsecond=0)
    end = now.replace(hour=end_hm[0], minute=end_hm[1], second=0, microsecond=0)
    delta = int((end - start).total_seconds())
    if delta <= 0:
        return start
    return start + dt.timedelta(seconds=random.randint(0, delta))


def random_time_night_window() -> dt.datetime:
    """
    Finestra: 23:00–00:45 (attraversa mezzanotte)
    """
    now = now_local()
    today_2300 = now.replace(hour=23, minute=0, second=0, microsecond=0)
    today_235959 = now.replace(hour=23, minute=59, second=59, microsecond=0)

    tomorrow = now + dt.timedelta(days=1)
    tomorrow_0000 = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_0045 = tomorrow.replace(hour=0, minute=45, second=0, microsecond=0)

    if random.random() < 0.65:
        delta = int((today_235959 - today_2300).total_seconds())
        return today_2300 + dt.timedelta(seconds=random.randint(0, max(delta, 1)))
    else:
        delta = int((tomorrow_0045 - tomorrow_0000).total_seconds())
        return tomorrow_0000 + dt.timedelta(seconds=random.randint(0, max(delta, 1)))


# ===================== QUOTES =====================
@dataclass
class Quote:
    author: str
    text: str
    source: str
    ref: str


QUOTE_AUTHORS = [
    "Allan Kardec",
    "Rudolf Steiner",
    "Helena Blavatsky",
    "Georges Ivanovich Gurdjieff",
    "Hermes Trismegistus",
    "Pythagoras",
    "Plato",
    "Socrates",
    "Aristotle",
    "Plotinus",
    "Marcus Aurelius",
    "Epictetus",
]

LOCAL_QUOTES: List[Quote] = [
    Quote("Eraclito", "Tutto scorre.", "Local", "Frammenti (attrib.)"),
    Quote("Marco Aurelio", "La vita di un uomo è ciò che i suoi pensieri ne fanno.", "Local", "Meditazioni (attrib.)"),
    Quote("Epitteto", "Non sono le cose a turbarci, ma i giudizi che diamo alle cose.", "Local", "Enchiridion (attrib.)"),
    Quote("Platone", "La conoscenza è il nutrimento dell’anima.", "Local", "attrib."),
]


async def fetch_wikiquote_quote(author: str) -> Optional[Quote]:
    api = "https://it.wikiquote.org/w/api.php"
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "MaestroAncletoBot/1.0"}) as client:
            r = await client.get(
                api,
                params={
                    "format": "json",
                    "action": "query",
                    "list": "search",
                    "srsearch": author,
                    "srlimit": 1,
                },
                timeout=10,
            )
            r.raise_for_status()
            hits = r.json().get("query", {}).get("search", [])
            if not hits:
                return None
            title = hits[0].get("title")
            if not title:
                return None

            r2 = await client.get(
                api,
                params={
                    "format": "json",
                    "action": "query",
                    "prop": "extracts",
                    "explaintext": 1,
                    "exsectionformat": "plain",
                    "titles": title,
                },
                timeout=10,
            )
            r2.raise_for_status()
            pages = r2.json().get("query", {}).get("pages", {})
            if not pages:
                return None
            page = next(iter(pages.values()))
            extract = (page.get("extract") or "").strip()
            if not extract:
                return None

            lines = [ln.strip("•- \t") for ln in extract.splitlines()]
            candidates = []
            for ln in lines:
                if len(ln) < 35:
                    continue
                low = ln.lower()
                if low.startswith("citazioni") or low.startswith("bibliografia"):
                    continue
                if ln.endswith(":"):
                    continue
                candidates.append(ln)

            if not candidates:
                return None

            text = random.choice(candidates)
            url = f"https://it.wikiquote.org/wiki/{title.replace(' ', '_')}"
            return Quote(author=title, text=text, source="Wikiquote", ref=url)

    except Exception:
        return None


async def get_random_quote() -> Quote:
    author = random.choice(QUOTE_AUTHORS)
    q = await fetch_wikiquote_quote(author)
    if q:
        return q
    return random.choice(LOCAL_QUOTES)


def fmt_quote_html(q: Quote) -> str:
    a = html.escape(q.author)
    t = html.escape(q.text)
    s = html.escape(q.source)
    r = html.escape(q.ref)
    return f"📜 <b>{a}</b>\n“{t}”\n\nFonte: {s}\n{r}"


# ===================== GROUP LOCK / FILTERS =====================
def in_allowed_context(update: Update) -> bool:
    if not update.effective_chat:
        return False

    chat = update.effective_chat

    if chat.type == ChatType.PRIVATE:
        return True

    if ALLOWED_GROUP_ID is None:
        return True

    return chat.id == ALLOWED_GROUP_ID


def is_reply_to_bot(msg) -> bool:
    try:
        if msg.reply_to_message and msg.reply_to_message.from_user:
            u = msg.reply_to_message.from_user
            return (u.username or "").lower() == BOT_USERNAME.lower()
    except Exception:
        pass
    return False


def is_bot_mentioned(msg) -> bool:
    if not msg:
        return False

    if is_reply_to_bot(msg):
        return True

    text = msg.text or ""
    target = mention_token().lower()

    if target in text.lower():
        return True

    entities = msg.entities or []
    for ent in entities:
        if ent.type == "mention":
            part = text[ent.offset : ent.offset + ent.length].lower()
            if part == target:
                return True
        if ent.type == "text_mention" and ent.user:
            if (ent.user.username or "").lower() == BOT_USERNAME.lower():
                return True

    return False


# ===================== PDF RAG (lite, offline) =====================
@dataclass
class PageChunk:
    book: str
    page: int  # 1-based
    text: str


PDF_INDEX: List[PageChunk] = []
PDF_BOOKS: List[str] = []


def _normalize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_pdfs(pdf_dir: str) -> Tuple[List[PageChunk], List[str]]:
    root = Path(__file__).resolve().parent
    d = (root / pdf_dir).resolve()

    if not d.exists():
        log.warning("PDF dir NON trovato: %s", d)
        return [], []

    pdfs = sorted([p for p in d.glob("*.pdf") if p.is_file()])
    if not pdfs:
        log.warning("PDF dir trovato ma vuoto: %s", d)
        return [], []

    pages: List[PageChunk] = []
    books: List[str] = []

    log.info("PDF scan: dir=%s | files=%s", d, len(pdfs))
    for p in pdfs:
        try:
            reader = PdfReader(str(p))
            books.append(p.name)
            for i, page in enumerate(reader.pages, start=1):
                txt = page.extract_text() or ""
                txt = txt.strip()
                if not txt:
                    continue
                pages.append(PageChunk(book=p.name, page=i, text=txt))
        except Exception as e:
            log.exception("PDF parse fail: %s | err=%s", p.name, e)

    return pages, books


def search_pdf(question: str, k: int = 3) -> List[PageChunk]:
    q = _normalize(question)
    if not q or not PDF_INDEX:
        return []

    tokens = [t for t in re.split(r"[^a-zàèéìòù0-9]+", q) if len(t) >= 3]
    if not tokens:
        return []

    scored: List[Tuple[int, PageChunk]] = []
    for ch in PDF_INDEX:
        t = _normalize(ch.text)
        score = 0
        for tok in tokens:
            score += t.count(tok)
        if score > 0:
            scored.append((score, ch))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:k]]


def clip(text: str, max_chars: int = 600) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


# ===================== SCHEDULED MESSAGES =====================
async def send_good_morning(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID is None:
        return
    q = await get_random_quote()
    text = (
        "☀️ Buongiorno, Metavoice.\n"
        "Lo so… è mattina. Anche per me è un trauma.\n\n"
        + fmt_quote_html(q)
    )
    await context.bot.send_message(
        chat_id=ALLOWED_GROUP_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def send_good_night(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID is None:
        return
    q = await get_random_quote()
    text = (
        "🌙 Buonanotte, Metavoice.\n"
        "Se è fatta na certa… io mi ritiro nel mio piano dimensionale.\n\n"
        + fmt_quote_html(q)
    )
    await context.bot.send_message(
        chat_id=ALLOWED_GROUP_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


def plan_today_jobs(application):
    if ALLOWED_GROUP_ID is None:
        log.warning("ALLOWED_GROUP_ID non impostato: non pianifico buongiorno/buonanotte.")
        return

    if application.job_queue is None:
        log.error("JobQueue non disponibile (application.job_queue=None).")
        return

    now = now_local()
    gm_time = random_time_today_window((8, 0), (9, 0))
    gn_time = random_time_night_window()

    if gm_time <= now:
        gm_time = now + dt.timedelta(minutes=2)
    if gn_time <= now:
        gn_time = now + dt.timedelta(minutes=3)

    for name in ("good_morning", "good_night"):
        old = application.job_queue.get_jobs_by_name(name)
        for j in old:
            j.schedule_removal()

    application.job_queue.run_once(send_good_morning, when=gm_time, name="good_morning")
    application.job_queue.run_once(send_good_night, when=gn_time, name="good_night")
    log.info("Pianificato buongiorno: %s | buonanotte: %s", gm_time, gn_time)


async def daily_planner(context: ContextTypes.DEFAULT_TYPE):
    plan_today_jobs(context.application)


# ===================== COMMANDS =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    await update.message.reply_text(
        f"Sono <b>{html.escape(BOT_DISPLAY)}</b> 🕯️\n\n"
        "✅ In gruppo rispondo solo se mi menzioni o mi fai reply.\n"
        "✅ Buongiorno random 08:00–09:00 e buonanotte random 23:00–00:45.\n\n"
        "Comandi:\n"
        "• /ping\n"
        "• /status\n"
        "• /sources\n"
        "• /ask &lt;domanda&gt;\n"
        "• /quote\n"
        "• /test_gm\n"
        "• /test_gn\n",
        parse_mode="HTML",
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    await update.message.reply_text("🏓 Pong. Io quasi dormivo.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return

    lock = f"🔒 ALLOWED_GROUP_ID={ALLOWED_GROUP_ID}" if ALLOWED_GROUP_ID is not None else "🔓 ALLOWED_GROUP_ID non impostato"
    jq = "✅" if (context.application.job_queue is not None) else "❌"
    pdfs = f"✅ {len(PDF_BOOKS)} libri / {len(PDF_INDEX)} pagine" if PDF_BOOKS else "❌ (nessun PDF caricato)"

    await update.message.reply_text(
        "📌 <b>Status</b>\n"
        f"• Username: {html.escape(mention_token())}\n"
        f"• {html.escape(lock)}\n"
        f"• JobQueue: {jq}\n"
        f"• PDF: {html.escape(pdfs)}\n"
        "• Quote: ✅ (Wikiquote + fallback)\n",
        parse_mode="HTML",
    )


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    if not PDF_BOOKS:
        await update.message.reply_text(
            "📚 <b>Libri CF77</b>\nNessun PDF trovato in <code>data/pdfs</code>.\n"
            "Mettili lì e fai push su GitHub, poi Render redeploya.",
            parse_mode="HTML",
        )
        return

    items = "\n".join([f"• {html.escape(name)}" for name in PDF_BOOKS])
    await update.message.reply_text(
        "📚 <b>Libri CF77 caricati</b>\n" + items,
        parse_mode="HTML",
    )


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    q = await get_random_quote()
    await update.message.reply_text(
        fmt_quote_html(q),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    user_first = update.message.from_user.first_name if update.message.from_user else "umano"
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text("Usa: /ask <domanda>")
        return

    intro = night_intro(user_first) if is_night_now() else day_intro(user_first)

    hits = search_pdf(question, k=3)
    if not hits:
        await update.message.reply_text(
            intro
            + "📌 Domanda ricevuta:\n"
            + question
            + "\n\n😤 Non ho trovato un passaggio chiaro nei PDF che ho indicizzato.\n"
              "Prova a riformulare con parole chiave più specifiche (es: “piano astrale”, “corpo astrale”, “trapasso”)."
        )
        return

    parts = [intro + "📌 " + question + "\n\n<b>Riscontri dai testi:</b>\n"]
    for ch in hits:
        snippet = html.escape(clip(ch.text, 650))
        parts.append(f"\n<b>{html.escape(ch.book)}</b> — pag. {ch.page}\n“{snippet}”\n")

    await update.message.reply_text("".join(parts), parse_mode="HTML")


async def cmd_test_gm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    await send_good_morning(context)


async def cmd_test_gn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    await send_good_night(context)


# ===================== TEXT HANDLER =====================
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return

    msg = update.message
    if not msg:
        return

    chat = msg.chat
    user_first = msg.from_user.first_name if msg.from_user else "umano"
    text = msg.text or ""

    log.info("MSG | chat_type=%s chat_id=%s user=%s text=%s", chat.type, chat.id, user_first, text[:120])

    # In privato: rispondi sempre
    if chat.type == ChatType.PRIVATE:
        intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
        await msg.reply_text(intro + "Dimmi pure. (privato)")
        return

    # In gruppo: solo se menzionato o reply
    if not is_bot_mentioned(msg):
        return

    intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
    await msg.reply_text(intro + "Ok, ti sento. Usa /ask <domanda> 😈")


# ===================== ERROR HANDLER =====================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        log.error(
            "CONFLICT: Un'altra istanza sta facendo polling. "
            "Controlla Render: deve esserci UN SOLO servizio Anacleto attivo."
        )
    else:
        log.exception("Errore non gestito:", exc_info=err)


# ===================== STARTUP HOOK =====================
async def post_init(application):
    global PDF_INDEX, PDF_BOOKS
    PDF_INDEX, PDF_BOOKS = load_pdfs(PDF_DIR_ENV)

    if PDF_BOOKS:
        log.info("CF77 PDF pronto. books=%s pages=%s dir=%s", len(PDF_BOOKS), len(PDF_INDEX), PDF_DIR_ENV)
    else:
        log.warning("CF77 PDF NON pronto. books=0 pages=0 dir=%s", PDF_DIR_ENV)

    plan_today_jobs(application)

    if application.job_queue is None:
        log.error("JobQueue assente: impossibile schedulare daily planner.")
        return

    now = now_local()
    next_run = now.replace(hour=0, minute=5, second=0, microsecond=0)
    if next_run <= now:
        next_run = next_run + dt.timedelta(days=1)

    application.job_queue.run_repeating(
        daily_planner,
        interval=24 * 60 * 60,
        first=next_run,
        name="daily_planner",
    )
    log.info("Daily planner schedulato per: %s", next_run)


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("sources", cmd_sources))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("test_gm", cmd_test_gm))
    app.add_handler(CommandHandler("test_gn", cmd_test_gn))

    app.add_handler(MessageHandler(filters.TEXT, handle_text))

    print(f"{BOT_DISPLAY} è in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()