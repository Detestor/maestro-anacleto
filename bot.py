import os
import datetime as dt
import logging
import random
import asyncio
from dataclasses import dataclass
from typing import Optional, Tuple, List

from dotenv import load_dotenv
import httpx

from telegram import Update
from telegram.constants import ChatType
from telegram.error import Conflict
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger


# ===================== LOGGING =====================
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
# Evita log rumorosi (e soprattutto URL completi)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

log = logging.getLogger("ANACLETO")


# ===================== ENV =====================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "MaestroAnacletoBot").lstrip("@")

ALLOWED_GROUP_ID_RAW = os.getenv("ALLOWED_GROUP_ID", "").strip()
ALLOWED_GROUP_ID: Optional[int] = None
if ALLOWED_GROUP_ID_RAW:
    try:
        ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID_RAW)
    except ValueError:
        raise RuntimeError("ALLOWED_GROUP_ID nel .env deve essere un intero (es: -1001234567890)")

# Timezone (Render usa UTC se non specificato; noi gestiamo Europe/Rome)
TZ_NAME = os.getenv("TZ", "Europe/Rome")

# Facoltativo: un "nome bot" per testi
BOT_DISPLAY = os.getenv("BOT_DISPLAY", "MAESTRO ANACLETO")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN mancante. Mettilo nel file .env (locale) o nelle env vars di Render.")


# ===================== QUOTES =====================
@dataclass
class Quote:
    author: str
    text: str
    source: str  # es: "Wikiquote" o "Local"
    ref: str     # url o riferimento breve


# Autori “base” (mix spiritismo/ermetismo/filosofia)
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

# Fallback locale (se Wikiquote non risponde / rate limit / niente risultati)
LOCAL_QUOTES: List[Quote] = [
    Quote("Eraclito", "Tutto scorre.", "Local", "Frammenti (attrib.)"),
    Quote("Marco Aurelio", "La vita di un uomo è ciò che i suoi pensieri ne fanno.", "Local", "Meditazioni (attrib.)"),
    Quote("Platone", "La conoscenza è il nutrimento dell’anima.", "Local", "attrib."),
    Quote("Epitteto", "Non sono le cose a turbarci, ma i giudizi che diamo alle cose.", "Local", "Enchiridion (attrib.)"),
    Quote("Pitagora", "Educare non è riempire un vaso, ma accendere un fuoco.", "Local", "attrib."),
]


def mention_token() -> str:
    return f"@{BOT_USERNAME}"


def now_local() -> dt.datetime:
    # Render può essere UTC; noi useremo TZ solo per schedulazione.
    # Per messaggi/battute non serve conversione perfetta.
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


async def fetch_wikiquote_quote(author: str, client: httpx.AsyncClient) -> Optional[Quote]:
    """
    Prova a prendere una citazione da Wikiquote (MediaWiki API).
    Non garantisce: alcune pagine hanno formati strani.
    Se fallisce, ritorna None.
    """
    # Endpoint API Wikiquote italiana (va bene per autori greci/latini + kardec/steiner spesso ci sono)
    api = "https://it.wikiquote.org/w/api.php"

    # 1) Cerca il titolo più vicino
    params_search = {
        "format": "json",
        "action": "query",
        "list": "search",
        "srsearch": author,
        "srlimit": 1,
    }

    try:
        r = await client.get(api, params=params_search, timeout=10)
        r.raise_for_status()
        data = r.json()
        hits = data.get("query", {}).get("search", [])
        if not hits:
            return None
        title = hits[0].get("title")
        if not title:
            return None

        # 2) Prendi estratto “plaintext” della pagina (non è perfetto, ma spesso contiene citazioni)
        params_extract = {
            "format": "json",
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "exsectionformat": "plain",
            "titles": title,
        }
        r2 = await client.get(api, params=params_extract, timeout=10)
        r2.raise_for_status()
        data2 = r2.json()
        pages = data2.get("query", {}).get("pages", {})
        if not pages:
            return None
        page = next(iter(pages.values()))
        extract = (page.get("extract") or "").strip()
        if not extract:
            return None

        # 3) Estrai “una riga” plausibile (euristica)
        # Prendiamo righe non troppo corte, non intestazioni, non vuote.
        lines = [ln.strip("•- \t") for ln in extract.splitlines()]
        candidates = []
        for ln in lines:
            if len(ln) < 35:
                continue
            # evita robe tipo "Citazioni su..."
            if ln.lower().startswith("citazioni") or ln.lower().startswith("bibliografia"):
                continue
            # evita intestazioni troppo generiche
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
    async with httpx.AsyncClient(headers={"User-Agent": "MaestroAnacletoBot/1.0"}) as client:
        q = await fetch_wikiquote_quote(author, client)
        if q:
            return q

    # fallback
    return random.choice(LOCAL_QUOTES)


# ===================== GROUP LOCK / FILTERS =====================
def in_allowed_context(update: Update) -> bool:
    if not update.effective_chat:
        return False

    chat = update.effective_chat

    # Privato: per ora lo lasciamo attivo (test). Se vuoi, lo chiudiamo.
    if chat.type == ChatType.PRIVATE:
        return True

    # Se non impostato, risponde ovunque (ma noi lo impostiamo)
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

    # stringa
    if target in text.lower():
        return True

    # entities
    entities = msg.entities or []
    for ent in entities:
        if ent.type == "mention":
            part = text[ent.offset: ent.offset + ent.length].lower()
            if part == target:
                return True
        if ent.type == "text_mention" and ent.user:
            if (ent.user.username or "").lower() == BOT_USERNAME.lower():
                return True

    return False


# ===================== SCHEDULER (Random windows) =====================
scheduler: Optional[AsyncIOScheduler] = None


def random_time_today_window(start_hm: Tuple[int, int], end_hm: Tuple[int, int]) -> dt.datetime:
    """
    Ritorna un datetime 'today' random in una finestra che NON attraversa mezzanotte.
    start_hm <= end_hm.
    """
    now = now_local()
    start = now.replace(hour=start_hm[0], minute=start_hm[1], second=0, microsecond=0)
    end = now.replace(hour=end_hm[0], minute=end_hm[1], second=0, microsecond=0)

    delta = int((end - start).total_seconds())
    if delta <= 0:
        # fallback: start
        return start

    offset = random.randint(0, delta)
    return start + dt.timedelta(seconds=offset)


def random_time_night_window() -> dt.datetime:
    """
    Finestra buonanotte: tra 23:00 e 00:45 (attraversa mezzanotte).
    Quindi scegliamo:
    - o oggi tra 23:00 e 23:59:59
    - o domani tra 00:00 e 00:45
    """
    now = now_local()
    today_2300 = now.replace(hour=23, minute=0, second=0, microsecond=0)
    today_235959 = now.replace(hour=23, minute=59, second=59, microsecond=0)

    tomorrow = now + dt.timedelta(days=1)
    tomorrow_0000 = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_0045 = tomorrow.replace(hour=0, minute=45, second=0, microsecond=0)

    # Pesiamo un po’ di più la fascia 23–00:00 (più naturale)
    if random.random() < 0.65:
        delta = int((today_235959 - today_2300).total_seconds())
        return today_2300 + dt.timedelta(seconds=random.randint(0, max(delta, 1)))
    else:
        delta = int((tomorrow_0045 - tomorrow_0000).total_seconds())
        return tomorrow_0000 + dt.timedelta(seconds=random.randint(0, max(delta, 1)))


async def send_good_morning(app, chat_id: int):
    q = await get_random_quote()
    text = (
        f"☀️ Buongiorno, Metavoice.\n"
        f"Lo so, è mattina. Anche per me è un trauma.\n\n"
        f"📜 *{q.author}*\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}"
    )
    await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


async def send_good_night(app, chat_id: int):
    q = await get_random_quote()
    text = (
        f"🌙 Buonanotte, Metavoice.\n"
        f"Se è fatta na certa… io mi ritiro nel mio piano dimensionale.\n\n"
        f"📜 *{q.author}*\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}"
    )
    await app.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")


async def plan_today_jobs(app):
    """
    Pianifica i messaggi giornalieri con orari random.
    Viene chiamata una volta al giorno.
    """
    if ALLOWED_GROUP_ID is None:
        log.warning("ALLOWED_GROUP_ID non impostato: non pianifico buongiorno/buonanotte.")
        return

    # Buongiorno tra 08:00 e 09:00
    gm_time = random_time_today_window((8, 0), (9, 0))

    # Buonanotte tra 23:00 e 00:45
    gn_time = random_time_night_window()

    # Se per qualche motivo siamo già oltre l’orario (es avvio tardivo), spostiamo a "tra poco"
    now = now_local()
    if gm_time <= now:
        gm_time = now + dt.timedelta(minutes=2)
    if gn_time <= now:
        gn_time = now + dt.timedelta(minutes=3)

    # Scheduliamo come one-shot jobs
    scheduler.add_job(send_good_morning, "date", run_date=gm_time, args=[app, ALLOWED_GROUP_ID], id=f"gm_{gm_time.date()}", replace_existing=True)
    scheduler.add_job(send_good_night, "date", run_date=gn_time, args=[app, ALLOWED_GROUP_ID], id=f"gn_{gm_time.date()}", replace_existing=True)

    log.info("Pianificato buongiorno: %s | buonanotte: %s", gm_time, gn_time)


# ===================== COMMANDS =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    msg = update.message
    if not msg:
        return

    await msg.reply_text(
        f"Sono *{BOT_DISPLAY}* 🕯️\n\n"
        "✅ In gruppo rispondo solo se mi menzioni o mi fai reply.\n"
        "✅ Buongiorno random 08:00–09:00 e buonanotte random 23:00–00:45.\n\n"
        "Comandi:\n"
        "• /ping\n"
        "• /status\n"
        "• /ask <domanda>\n"
        "• /quote\n",
        parse_mode="Markdown",
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    msg = update.message
    if not msg:
        return
    await msg.reply_text("🏓 Pong. Un solo Anacleto alla volta, grazie.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    msg = update.message
    if not msg:
        return

    lock = f"🔒 ALLOWED_GROUP_ID={ALLOWED_GROUP_ID}" if ALLOWED_GROUP_ID is not None else "🔓 ALLOWED_GROUP_ID non impostato"
    await msg.reply_text(
        "📌 Status\n"
        f"• Username: {mention_token()}\n"
        f"• {lock}\n"
        "• Scheduler: ✅\n"
        "• Quote (web+fallback): ✅\n"
        "• RAG PDF: ❌ (prossimo step)\n"
    )


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    msg = update.message
    if not msg:
        return

    q = await get_random_quote()
    await msg.reply_text(
        f"📜 *{q.author}*\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}",
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    msg = update.message
    if not msg:
        return

    user_first = msg.from_user.first_name if msg.from_user else "umano"
    question = " ".join(context.args).strip()

    if not question:
        await msg.reply_text("Usa: /ask <domanda>")
        return

    intro = night_intro(user_first) if is_night_now() else day_intro(user_first)

    # Per ora: risposta placeholder. Poi qui ci mettiamo RAG dai PDF.
    await msg.reply_text(
        intro
        + "📌 Domanda ricevuta:\n"
        + question
        + "\n\n🧠 Risposta (test): presto consulterò i testi del Cerchio Firenze 77 e citerò libro+pagina."
    )


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

    # PRIVATO: se vuoi lo disattiviamo dopo; per ora utile
    if chat.type == ChatType.PRIVATE:
        intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
        await msg.reply_text(intro + "Dimmi pure. (privato: modalità test)")
        return

    # GRUPPO: risponde SOLO se menzionato/reply
    if not is_bot_mentioned(msg):
        return

    intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
    await msg.reply_text(intro + "Ok, ti sento. (RAG PDF arriverà a breve) 😈")


# ===================== ERROR HANDLER =====================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        log.error("CONFLICT: Un'altra istanza del bot sta facendo polling. Chiudine una.")
    else:
        log.exception("Errore non gestito:", exc_info=err)


# ===================== MAIN =====================
def main():
    global scheduler

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_error_handler(on_error)

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("quote", cmd_quote))

    # Texts
    app.add_handler(MessageHandler(filters.TEXT, handle_text))

    # Scheduler: pianifica ogni giorno (alle 00:05) i job random per buongiorno/buonanotte
    scheduler = AsyncIOScheduler(timezone=TZ_NAME)
    scheduler.add_job(lambda: asyncio.create_task(plan_today_jobs(app)), CronTrigger(hour=0, minute=5))
    scheduler.start()

    # Pianifica anche subito all’avvio (così se deployi ora, parte subito)
    asyncio.get_event_loop().create_task(plan_today_jobs(app))

    print(f"{BOT_DISPLAY} è in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()