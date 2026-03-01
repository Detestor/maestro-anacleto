import os
import asyncio
import datetime as dt
import logging
import random
from typing import Optional, Tuple, List
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
import httpx

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

from rag_cf77 import CF77Rag

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

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "MaestroAnacletoBot").lstrip("@")
ALLOWED_GROUP_ID_RAW = os.getenv("ALLOWED_GROUP_ID", "").strip()
TZ_NAME = os.getenv("TZ", "Europe/Rome")
BOT_DISPLAY = os.getenv("BOT_DISPLAY", "MAESTRO ANACLETO")

# PDF dir configurabile; default: "<repo_root>/data/pdfs"
PDF_DIR_ENV = os.getenv("CF77_PDF_DIR", "").strip()

ALLOWED_GROUP_ID: Optional[int] = None
if ALLOWED_GROUP_ID_RAW:
    try:
        ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID_RAW)
    except ValueError:
        raise RuntimeError("ALLOWED_GROUP_ID nel .env deve essere un intero (es: -1001234567890)")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN mancante. Mettilo nelle env vars di Render o nel .env locale.")

# ===================== TIME / WINDOWS =====================
def mention_token() -> str:
    return f"@{BOT_USERNAME}"

def is_night_now() -> bool:
    h = dt.datetime.now().hour
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
    now = dt.datetime.now()
    start = now.replace(hour=start_hm[0], minute=start_hm[1], second=0, microsecond=0)
    end = now.replace(hour=end_hm[0], minute=end_hm[1], second=0, microsecond=0)
    delta = int((end - start).total_seconds())
    if delta <= 0:
        return start
    return start + dt.timedelta(seconds=random.randint(0, delta))

def random_time_night_window() -> dt.datetime:
    now = dt.datetime.now()
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
        async with httpx.AsyncClient(headers={"User-Agent": "MaestroAnacletoBot/1.0"}) as client:
            r = await client.get(
                api,
                params={"format":"json","action":"query","list":"search","srsearch":author,"srlimit":1},
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
                params={"format":"json","action":"query","prop":"extracts","explaintext":1,"exsectionformat":"plain","titles":title},
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

# ===================== CF77 RAG INIT =====================
RAG: Optional[CF77Rag] = None
RAG_BOOKS = 0
RAG_PAGES = 0
RAG_DIR_STR = ""

def init_rag():
    global RAG, RAG_BOOKS, RAG_PAGES, RAG_DIR_STR

    # repo root = directory dove sta questo file (src/)
    here = Path(__file__).resolve().parent
    default_dir = (here / "data" / "pdfs").resolve()

    pdf_dir = Path(PDF_DIR_ENV).expanduser().resolve() if PDF_DIR_ENV else default_dir
    RAG_DIR_STR = str(pdf_dir)

    rag = CF77Rag(pdf_dir=pdf_dir)
    books, pages = rag.build()

    RAG = rag
    RAG_BOOKS = books
    RAG_PAGES = pages

    log.info("CF77 RAG pronto. books=%s pages=%s dir=%s", books, pages, pdf_dir)

def rag_answer(question: str, top_k: int = 4) -> str:
    if not RAG or RAG_BOOKS == 0 or RAG_PAGES == 0:
        return (
            "🧠 RAG CF77 non pronto (PDF non trovati o vuoti).\n"
            f"📁 Dir attesa: {RAG_DIR_STR}\n"
            "Tip: controlla che i PDF siano presenti su Render nel path indicato."
        )

    hits = RAG.query(question, top_k=top_k)
    if not hits:
        return "Non trovo un passaggio chiaro nei testi (o la domanda è troppo generica). Prova a riformulare."

    # mini-sintesi + estratti
    out = []
    out.append("📚 *Secondo i testi del Cerchio Firenze 77:*")
    out.append("")

    for h in hits:
        snippet = h.text.strip().replace("\n", " ")
        if len(snippet) > 450:
            snippet = snippet[:450].rstrip() + "…"
        out.append(f"— _{h.book}_, p.{h.page}")
        out.append(f"“{snippet}”")
        out.append("")

    return "\n".join(out)

# ===================== SCHEDULED MESSAGES =====================
async def send_good_morning(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID is None:
        return
    q = await get_random_quote()
    text = (
        "☀️ Buongiorno, Metavoice.\n"
        "Lo so… è mattina. Anche per me è un trauma.\n\n"
        f"📜 *{q.author}*\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}"
    )
    await context.bot.send_message(
        chat_id=ALLOWED_GROUP_ID,
        text=text,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

async def send_good_night(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID is None:
        return
    q = await get_random_quote()
    text = (
        "🌙 Buonanotte, Metavoice.\n"
        "Se è fatta na certa… io mi ritiro nel mio piano dimensionale.\n\n"
        f"📜 *{q.author}*\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}"
    )
    await context.bot.send_message(
        chat_id=ALLOWED_GROUP_ID,
        text=text,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )

def plan_today_jobs(application):
    if ALLOWED_GROUP_ID is None:
        log.warning("ALLOWED_GROUP_ID non impostato: non pianifico buongiorno/buonanotte.")
        return
    if application.job_queue is None:
        log.error("JobQueue non disponibile: manca extra [job-queue].")
        return

    now = dt.datetime.now()

    gm_time = random_time_today_window((8, 0), (9, 0))
    gn_time = random_time_night_window()

    # se siamo già oltre le 10:00, il buongiorno si fa domani
    if now.hour >= 10:
        gm_time = (now + dt.timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        gm_time = random_time_today_window((8, 0), (9, 0))

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
        f"Sono *{BOT_DISPLAY}* 🕯️\n\n"
        "✅ In gruppo rispondo solo se mi menzioni o mi fai reply.\n"
        "✅ Buongiorno random 08:00–09:00 e buonanotte random 23:00–00:45.\n\n"
        "Comandi:\n"
        "• /ping\n"
        "• /status\n"
        "• /ask <domanda>\n"
        "• /cf77 <domanda>\n"
        "• /quote\n"
        "• /test_gm\n"
        "• /test_gn\n",
        parse_mode="Markdown",
    )

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    await update.message.reply_text("🏓 Pong. Render non dorme. Io quasi.")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    lock = f"🔒 ALLOWED_GROUP_ID={ALLOWED_GROUP_ID}" if ALLOWED_GROUP_ID is not None else "🔓 ALLOWED_GROUP_ID non impostato"
    jq = "✅" if (context.application.job_queue is not None) else "❌ (manca extra [job-queue])"
    rag_ok = "✅" if (RAG_BOOKS > 0 and RAG_PAGES > 0) else "❌"
    await update.message.reply_text(
        "📌 Status\n"
        f"• Username: {mention_token()}\n"
        f"• {lock}\n"
        f"• JobQueue: {jq}\n"
        f"• RAG CF77: {rag_ok} (books={RAG_BOOKS} pages={RAG_PAGES})\n"
        f"• PDF dir: {RAG_DIR_STR}\n"
        "• Quote: ✅ (Wikiquote + fallback)\n"
    )

async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    q = await get_random_quote()
    await update.message.reply_text(
        f"📜 *{q.author}*\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}",
        parse_mode="Markdown",
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
    await update.message.reply_text(intro + "📌 Domanda ricevuta:\n" + question + "\n\n🧠 (usa /cf77 per risposta dai libri)")

async def cmd_cf77(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    question = " ".join(context.args).strip()
    if not question:
        await update.message.reply_text("Usa: /cf77 <domanda>")
        return
    await update.message.reply_text(rag_answer(question), parse_mode="Markdown")

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

    if chat.type == ChatType.PRIVATE:
        intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
        await msg.reply_text(intro + "Dimmi pure. (privato: ok)")
        return

    if not is_bot_mentioned(msg):
        return

    intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
    await msg.reply_text(intro + "Ok, ti sento. Usa /cf77 <domanda> per consultare i libri. 😈")

# ===================== ERROR HANDLER =====================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        log.error("CONFLICT: Un'altra istanza del bot sta facendo polling. Chiudine una.")
    else:
        log.exception("Errore non gestito:", exc_info=err)

# ===================== STARTUP HOOK =====================
async def post_init(application):
    init_rag()
    plan_today_jobs(application)

    if application.job_queue is None:
        log.error("JobQueue assente: impossibile schedulare daily planner.")
        return

    now = dt.datetime.now()
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

def build_app():
    return ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

def run_polling_blocking():
    app = build_app()
    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("cf77", cmd_cf77))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("test_gm", cmd_test_gm))
    app.add_handler(CommandHandler("test_gn", cmd_test_gn))
    app.add_handler(MessageHandler(filters.TEXT, handle_text))

    print(f"{BOT_DISPLAY} è in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    run_polling_blocking()

if __name__ == "__main__":
    main()