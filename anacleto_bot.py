from __future__ import annotations

import os
import re
import random
import logging
import datetime as dt
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

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
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# PDF text extraction (no OCR)
try:
    from pypdf import PdfReader  # pip install pypdf
    PDF_READER_OK = True
except Exception:
    PdfReader = None
    PDF_READER_OK = False

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None


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

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "MaestroAnacletoBot").lstrip("@").strip()
ALLOWED_GROUP_ID_RAW = os.getenv("ALLOWED_GROUP_ID", "").strip()
TZ_NAME = os.getenv("TZ", "Europe/Rome").strip()
BOT_DISPLAY = os.getenv("BOT_DISPLAY", "MAESTRO ANACLETO").strip()

PDF_DIR = os.getenv("PDF_DIR", "data/pdfs").strip()

ALLOWED_GROUP_ID: Optional[int] = None
if ALLOWED_GROUP_ID_RAW:
    try:
        ALLOWED_GROUP_ID = int(ALLOWED_GROUP_ID_RAW)
    except ValueError:
        raise RuntimeError("ALLOWED_GROUP_ID deve essere un intero (es: -1001234567890)")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN mancante. Mettilo nelle env vars di Render o nel .env locale.")


# ===================== TIME HELPERS =====================
def tz_now() -> dt.datetime:
    if ZoneInfo is None:
        return dt.datetime.now()
    try:
        return dt.datetime.now(ZoneInfo(TZ_NAME))
    except Exception:
        return dt.datetime.now()


def mention_token() -> str:
    return f"@{BOT_USERNAME}"


def is_night_now() -> bool:
    h = tz_now().hour
    return h >= 23 or h < 7


def night_intro(user_first: str) -> str:
    intros = [
        f"@{user_first}, soffri di insonnia? 😈\n",
        f"Eh… @{user_first}. A quest’ora. 🌙\n",
        f"@{user_first} evocazioni notturne? Va bene… parla. 🕯️\n",
    ]
    return random.choice(intros)


def day_intro(user_first: str) -> str:
    return f"Salve, @{user_first}. Hai chiamato il {BOT_DISPLAY}. 📚\n"


def random_time_today_window(start_hm: Tuple[int, int], end_hm: Tuple[int, int]) -> dt.datetime:
    now = tz_now()
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
    now = tz_now()
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


# Lista “safe”: evitiamo omonimie tipo Sócrates calciatore
QUOTE_AUTHORS = [
    "Allan Kardec",
    "Rudolf Steiner",
    "Helena Blavatsky",
    "Georges Ivanovich Gurdjieff",
    "Eraclito",
    "Marco Aurelio",
    "Epitteto",
    "Platone",
]

LOCAL_QUOTES: List[Quote] = [
    Quote("Eraclito", "Tutto scorre.", "Local", "Frammenti (attrib.)"),
    Quote("Marco Aurelio", "La vita di un uomo è ciò che i suoi pensieri ne fanno.", "Local", "Meditazioni (attrib.)"),
    Quote("Epitteto", "Non sono le cose a turbarci, ma i giudizi che diamo alle cose.", "Local", "Enchiridion (attrib.)"),
    Quote("Platone", "La conoscenza è il nutrimento dell’anima.", "Local", "attrib."),
    Quote("Allan Kardec", "Il futuro appartiene allo spirito, non alla materia.", "Local", "attrib."),
]


def _looks_like_bio(line: str) -> bool:
    low = line.lower()
    bad = [
        "meglio noto",
        "nato",
        "morto",
        "calciatore",
        "attore",
        "cantante",
        "politico",
        "biografia",
        "è un",
        "fu un",
    ]
    return any(b in low for b in bad)


async def fetch_wikiquote_quote(author: str) -> Optional[Quote]:
    """
    Heuristics: usiamo l’estratto ma scartiamo righe-biografia.
    Non perfetto, ma evita le ciofeche più comuni.
    """
    api = "https://it.wikiquote.org/w/api.php"
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "MaestroAnacletoBot/1.0"}) as client:
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
            title = (hits[0].get("title") or "").strip()
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
            page = next(iter(pages.values())) if pages else {}
            extract = (page.get("extract") or "").strip()
            if not extract:
                return None

            lines = [ln.strip("•- \t") for ln in extract.splitlines()]
            candidates: List[str] = []
            for ln in lines:
                if len(ln) < 25:
                    continue
                if ln.endswith(":"):
                    continue
                if _looks_like_bio(ln):
                    continue
                # deve sembrare una frase-citazione
                if "“" in ln or "”" in ln or "«" in ln or "»" in ln or ln.count(",") >= 1:
                    candidates.append(ln)

            if not candidates:
                return None

            text = random.choice(candidates).strip()
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

    # Privato: OK (lo vuoi in privato)
    if chat.type == ChatType.PRIVATE:
        return True

    # Se non hai impostato group id, accetta tutto
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


# ===================== CF77 PDF RAG (lite, no OCR) =====================
@dataclass
class PageHit:
    book: str
    page: int
    text: str
    score: int


class CF77Rag:
    def __init__(self, pdf_dir: str):
        self.pdf_dir = pdf_dir
        self.books: List[str] = []
        self.pages: List[Tuple[str, int, str]] = []  # (book, page, text)
        self.total_pages = 0
        self.text_pages = 0
        self.total_chars = 0
        self.ready = False
        self.scan_like = False

    def _norm(self, s: str) -> str:
        s = s.lower()
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def load(self):
        self.books = []
        self.pages = []
        self.total_pages = 0
        self.text_pages = 0
        self.total_chars = 0
        self.ready = False
        self.scan_like = False

        if not os.path.isdir(self.pdf_dir):
            log.warning("CF77 PDF dir non trovato: %s", self.pdf_dir)
            self.ready = True
            return

        files = sorted([f for f in os.listdir(self.pdf_dir) if f.lower().endswith(".pdf")])
        self.books = files

        if not PDF_READER_OK:
            log.warning("pypdf non disponibile: non posso estrarre testo dai PDF.")
            self.ready = True
            return

        for fn in files:
            path = os.path.join(self.pdf_dir, fn)
            try:
                reader = PdfReader(path)
                n = len(reader.pages)
                self.total_pages += n
                for i in range(n):
                    raw = (reader.pages[i].extract_text() or "").strip()
                    if raw:
                        self.text_pages += 1
                        self.total_chars += len(raw)
                    self.pages.append((fn, i + 1, raw))
            except Exception as e:
                log.warning("Errore lettura PDF %s: %s", fn, e)

        # se quasi zero testo -> probabilmente scan
        if self.total_pages > 0 and self.total_chars < 5000:
            self.scan_like = True

        self.ready = True

    def sources(self) -> List[str]:
        return list(self.books)

    def search(self, query: str, top_k: int = 3) -> List[PageHit]:
        q = self._norm(query)
        if not q:
            return []

        # keywords: parole “utili”
        toks = [t for t in re.split(r"[^\wàèìòù]+", q) if len(t) >= 3]
        if not toks:
            return []

        hits: List[PageHit] = []
        for (book, page, text) in self.pages:
            if not text:
                continue
            norm = self._norm(text)
            score = sum(1 for t in toks if t in norm)
            if score <= 0:
                continue
            # snippet semplice
            snippet = text[:1200].strip()
            hits.append(PageHit(book=book, page=page, text=snippet, score=score))

        hits.sort(key=lambda h: (h.score, len(h.text)), reverse=True)
        return hits[:top_k]


RAG = CF77Rag(PDF_DIR)


def format_hit(hit: PageHit) -> str:
    # evitiamo Markdown per non spaccare parse entities
    snippet = hit.text.replace("\n", " ").strip()
    snippet = re.sub(r"\s+", " ", snippet)
    if len(snippet) > 420:
        snippet = snippet[:420].rstrip() + "…"
    return f"📖 {hit.book} — pag. {hit.page}\n{snippet}"

# ===================== SCHEDULED MESSAGES (JobQueue) =====================
async def send_good_morning(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID is None:
        return
    q = await get_random_quote()
    text = (
        "☀️ Messaggio del mattino (casuale)\n"
        "Non mi chiedere perché sono sveglio.\n\n"
        f"📜 {q.author}\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}"
    )
    await context.bot.send_message(
        chat_id=ALLOWED_GROUP_ID,
        text=text,
        disable_web_page_preview=True,
    )


async def send_good_night(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID is None:
        return
    q = await get_random_quote()
    text = (
        "🌙 Messaggio della notte (casuale)\n"
        "Io torno nel mio piano dimensionale.\n\n"
        f"📜 {q.author}\n"
        f"“{q.text}”\n\n"
        f"Fonte: {q.source}\n{q.ref}"
    )
    await context.bot.send_message(
        chat_id=ALLOWED_GROUP_ID,
        text=text,
        disable_web_page_preview=True,
    )


def plan_today_jobs(application):
    """
    Pianifica ogni giorno:
    - mattino random 08:00–09:00
    - notte random 23:00–00:45
    """
    if ALLOWED_GROUP_ID is None:
        log.warning("ALLOWED_GROUP_ID non impostato: non pianifico messaggi.")
        return

    if application.job_queue is None:
        log.error("JobQueue non disponibile. Assicurati di usare python-telegram-bot[job-queue].")
        return

    now = tz_now()
    gm_time = random_time_today_window((8, 0), (9, 0))
    gn_time = random_time_night_window()

    # se il bot si avvia “tardi”, scheduliamo a breve per non saltare il giorno
    if gm_time <= now:
        gm_time = now + dt.timedelta(minutes=2)
    if gn_time <= now:
        gn_time = now + dt.timedelta(minutes=3)

    for name in ("good_morning", "good_night"):
        for j in application.job_queue.get_jobs_by_name(name):
            j.schedule_removal()

    application.job_queue.run_once(send_good_morning, when=gm_time, name="good_morning")
    application.job_queue.run_once(send_good_night, when=gn_time, name="good_night")

    log.info("Pianificato mattino: %s | notte: %s", gm_time, gn_time)


async def daily_planner(context: ContextTypes.DEFAULT_TYPE):
    plan_today_jobs(context.application)


# ===================== COMMANDS =====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    await update.message.reply_text(
        f"Sono {BOT_DISPLAY} 🕯️\n\n"
        "✅ In privato rispondo sempre.\n"
        "✅ In gruppo rispondo solo se mi menzioni o mi fai reply.\n"
        "✅ Messaggi random: mattino 08:00–09:00, notte 23:00–00:45.\n\n"
        "Comandi:\n"
        "• /ping\n"
        "• /status\n"
        "• /sources\n"
        "• /ask <domanda>\n"
        "• /quote\n"
        "• /test_gm\n"
        "• /test_gn\n"
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    await update.message.reply_text("🏓 Pong. Io sono sveglio. Purtroppo.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    lock = f"🔒 ALLOWED_GROUP_ID={ALLOWED_GROUP_ID}" if ALLOWED_GROUP_ID is not None else "🔓 ALLOWED_GROUP_ID non impostato"
    jq = "✅" if (context.application.job_queue is not None) else "❌"
    pdf = f"✅ {len(RAG.books)} libri / {RAG.total_pages} pagine / testo:{RAG.text_pages} / chars:{RAG.total_chars}"
    if RAG.scan_like:
        pdf += " ⚠️ (sembra scansione → serve OCR)"
    await update.message.reply_text(
        "📌 Status\n"
        f"• Username: {mention_token()}\n"
        f"• {lock}\n"
        f"• JobQueue: {jq}\n"
        f"• PDF: {pdf}\n"
        "• Quote: ✅ (Wikiquote + fallback)\n"
    )


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    if not RAG.books:
        await update.message.reply_text("📚 Nessun PDF trovato in data/pdfs.")
        return
    lines = "\n".join([f"• {b}" for b in RAG.books])
    await update.message.reply_text("📚 Libri CF77 caricati\n" + lines)


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    q = await get_random_quote()
    # niente Markdown: evita BadRequest parse entities
    await update.message.reply_text(
        f"📜 {q.author}\n“{q.text}”\n\nFonte: {q.source}\n{q.ref}",
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

    if not RAG.ready:
        await update.message.reply_text(intro + "⏳ Sto ancora caricando i PDF. Riprova tra poco.")
        return

    if RAG.total_pages == 0 or not RAG.books:
        await update.message.reply_text(intro + "😤 Non ho PDF caricati. Mettili in data/pdfs e fai redeploy.")
        return

    hits = RAG.search(question, top_k=3)

    if not hits:
        extra = ""
        if RAG.scan_like:
            extra = "\n\n⚠️ Nota: questi PDF sembrano scansioni (testo quasi assente). Per risposte vere serve OCR/EPUB."
        await update.message.reply_text(
            intro
            + "📌 Domanda ricevuta:\n"
            + question
            + "\n\n😤 Non ho trovato un passaggio chiaro nei PDF indicizzati."
            + "\nProva con parole chiave più specifiche (es: “piano astrale”, “trapasso”, “corpo sottile”, “dimensione mentale”)."
            + extra
        )
        return

    parts = [format_hit(h) for h in hits]
    body = "\n\n— — —\n\n".join(parts)

    await update.message.reply_text(
        intro
        + "📌 Domanda:\n"
        + question
        + "\n\n🔎 Trovato nei testi:\n\n"
        + body
    )


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

    log.info("MSG | chat_type=%s chat_id=%s user=%s text=%s", chat.type, chat.id, user_first, text[:160])

    if chat.type == ChatType.PRIVATE:
        intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
        await msg.reply_text(intro + "Dimmi pure. (privato)")
        return

    if not is_bot_mentioned(msg):
        return

    intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
    await msg.reply_text(intro + "Ok. Ti ascolto. 😈")


# ===================== ERROR HANDLER =====================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        log.error("CONFLICT: un'altra istanza sta facendo polling. Se sei in webhook, NON usare polling.")
    else:
        log.exception("Errore non gestito:", exc_info=err)


# ===================== STARTUP HOOK =====================
async def post_init(application):
    # 1) Load PDFs (once at boot)
    try:
        RAG.load()
    except Exception as e:
        log.warning("CF77 RAG load error: %s", e)

    log.info(
        "CF77 RAG pronto. books=%s pages=%s text_pages=%s chars=%s dir=%s pdfreader=%s scan_like=%s",
        len(RAG.books), RAG.total_pages, RAG.text_pages, RAG.total_chars, RAG.pdf_dir, PDF_READER_OK, RAG.scan_like
    )

    # 2) schedule daily jobs
    plan_today_jobs(application)

    # 3) daily planner at 00:05
    if application.job_queue is None:
        log.error("JobQueue assente: non posso schedulare daily planner.")
        return

    now = tz_now()
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


# ===================== BUILD APP (for Webhook / WebService) =====================
def build_application():
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

    return app


def run_polling_blocking():
    """
    SOLO per locale/test. In produzione (webhook) non usare polling.
    """
    app = build_application()
    print(f"{BOT_DISPLAY} è in ascolto…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def main():
    run_polling_blocking()


if __name__ == "__main__":
    main()