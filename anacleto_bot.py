import os
import re
import datetime as dt
import logging
import random
from typing import Optional, Tuple, List, Dict
from dataclasses import dataclass

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

# PDF (serve in requirements: pypdf)
try:
    from pypdf import PdfReader  # type: ignore
except Exception:
    PdfReader = None


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

PDF_DIR = os.getenv("PDF_DIR", "data/pdfs").strip() or "data/pdfs"
MAX_PAGES_PER_BOOK = int(os.getenv("MAX_PAGES_PER_BOOK", "250"))  # per sicurezza
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", "900"))
MIN_EXTRACT_CHARS_PER_PAGE = int(os.getenv("MIN_EXTRACT_CHARS_PER_PAGE", "25"))

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
    """
    Finestra: 23:00–00:45 (attraversa mezzanotte)
    """
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
    "Aristotle",
    "Plotinus",
    "Marcus Aurelius",
    "Epictetus",
    "Socrate (filosofo)",  # <-- evita il calciatore
]

LOCAL_QUOTES: List[Quote] = [
    Quote("Eraclito", "Tutto scorre.", "Local", "Frammenti (attrib.)"),
    Quote("Marco Aurelio", "La vita di un uomo è ciò che i suoi pensieri ne fanno.", "Local", "Meditazioni (attrib.)"),
    Quote("Epitteto", "Non sono le cose a turbarci, ma i giudizi che diamo alle cose.", "Local", "Enchiridion (attrib.)"),
    Quote("Platone", "La conoscenza è il nutrimento dell’anima.", "Local", "attrib."),
]


_BAD_QUOTE_PATTERNS = [
    r"\bcalciatore\b",
    r"\bnato\b",
    r"\bmorto\b",
    r"\bmeglio noto\b",
    r"\bè stato\b",
    r"\bbiografia\b",
    r"\bvide\b",
    r"\bprofili\b",
]
_BAD_QUOTE_RE = re.compile("|".join(_BAD_QUOTE_PATTERNS), flags=re.IGNORECASE)


def looks_like_real_quote(line: str) -> bool:
    s = (line or "").strip()
    if len(s) < 35:
        return False
    if _BAD_QUOTE_RE.search(s):
        return False
    # scarta titoli/sezioni
    if s.endswith(":"):
        return False
    # scarta righe "troppo enciclopediche"
    if re.search(r"\(\d{4}\s*[–-]\s*\d{4}\)", s):
        return False
    return True


async def fetch_wikiquote_quote(author: str) -> Optional[Quote]:
    api = "https://it.wikiquote.org/w/api.php"
    try:
        async with httpx.AsyncClient(headers={"User-Agent": "MaestroAnacletoBot/1.2"}) as client:
            r = await client.get(
                api,
                params={
                    "format": "json",
                    "action": "query",
                    "list": "search",
                    "srsearch": author,
                    "srlimit": 1,
                },
                timeout=12,
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
                timeout=12,
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
            candidates = [ln for ln in lines if looks_like_real_quote(ln)]

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


def escape_markdown(text: str) -> str:
    # PTB Markdown (legacy). Escapiamo giusto il minimo per evitare BadRequest a caso.
    # Se vuoi, passiamo a MarkdownV2 più avanti, ma qui non ti faccio esplodere Render.
    if not text:
        return ""
    return text.replace("*", "").replace("_", "").replace("`", "")


# ===================== GROUP LOCK / FILTERS =====================
def in_allowed_context(update: Update) -> bool:
    if not update.effective_chat:
        return False

    chat = update.effective_chat

    # Privato ok (test e uso principale)
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


# ===================== PDF RAG (semplice ma utile) =====================
@dataclass
class Chunk:
    book: str
    page: int
    text: str


RAG: Dict[str, object] = {
    "chunks": [],          # type: ignore
    "books": [],           # type: ignore
    "pages_total": 0,
    "pages_with_text": 0,
    "chars_total": 0,
    "last_build": None,
    "scan_like": False,    # se sembra tutto immagine
    "pdfreader_ok": False,
}


def list_pdf_files() -> List[str]:
    if not os.path.isdir(PDF_DIR):
        return []
    files = []
    for name in os.listdir(PDF_DIR):
        if name.lower().endswith(".pdf"):
            files.append(os.path.join(PDF_DIR, name))
    files.sort()
    return files


def human_kb(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    kb = n / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    return f"{mb:.2f} MB"


def extract_pdf_text(pdf_path: str) -> Tuple[int, int, int, List[Chunk]]:
    """
    Ritorna: (pages_total, pages_with_text, chars_total, chunks)
    """
    base = os.path.basename(pdf_path)
    if PdfReader is None:
        return (0, 0, 0, [])

    try:
        reader = PdfReader(pdf_path)
    except Exception as e:
        log.error("PDF open fail: %s | %s", base, e)
        return (0, 0, 0, [])

    pages_total = min(len(reader.pages), MAX_PAGES_PER_BOOK)
    pages_with_text = 0
    chars_total = 0
    chunks: List[Chunk] = []

    for i in range(pages_total):
        try:
            page = reader.pages[i]
            txt = (page.extract_text() or "").strip()
        except Exception:
            txt = ""

        if len(txt) >= MIN_EXTRACT_CHARS_PER_PAGE:
            pages_with_text += 1
            chars_total += len(txt)

            # spezza in chunk
            txt_clean = re.sub(r"\s+", " ", txt).strip()
            for start in range(0, len(txt_clean), MAX_CHUNK_CHARS):
                part = txt_clean[start : start + MAX_CHUNK_CHARS].strip()
                if part:
                    chunks.append(Chunk(book=base, page=i + 1, text=part))

    return (pages_total, pages_with_text, chars_total, chunks)


def build_rag_index():
    pdfs = list_pdf_files()
    books = [os.path.basename(p) for p in pdfs]

    all_chunks: List[Chunk] = []
    pages_total = 0
    pages_with_text = 0
    chars_total = 0
    pdfreader_ok = PdfReader is not None

    for p in pdfs:
        pt, pwt, ct, ch = extract_pdf_text(p)
        pages_total += pt
        pages_with_text += pwt
        chars_total += ct
        all_chunks.extend(ch)

    scan_like = (pages_total > 0 and pages_with_text == 0)

    RAG["chunks"] = all_chunks
    RAG["books"] = books
    RAG["pages_total"] = pages_total
    RAG["pages_with_text"] = pages_with_text
    RAG["chars_total"] = chars_total
    RAG["last_build"] = dt.datetime.now().isoformat(timespec="seconds")
    RAG["scan_like"] = scan_like
    RAG["pdfreader_ok"] = pdfreader_ok

    log.info(
        "CF77 RAG pronto. books=%s pages=%s text_pages=%s chars=%s dir=%s pdfreader=%s scan_like=%s",
        len(books), pages_total, pages_with_text, chars_total, PDF_DIR, pdfreader_ok, scan_like
    )


def normalize_query(q: str) -> str:
    q = (q or "").strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q


def expand_query_terms(q: str) -> List[str]:
    """
    Se la domanda è generica, aggiungiamo parole chiave tipiche CF77.
    """
    base = normalize_query(q)
    terms = set(base.split())

    # sinonimi/piste
    expansions = {
        "morte": ["trapasso", "disincarnazione", "oltre", "astrale", "piani", "corpo sottile"],
        "dopo": ["dopo la morte", "oltre la morte", "post mortem"],
        "succede": ["accade", "avviene", "processo"],
        "astrale": ["piano astrale", "corpo astrale", "mondo astrale"],
        "spirito": ["piani spirituali", "entità", "evoluzione"],
    }

    for t in list(terms):
        if t in expansions:
            for e in expansions[t]:
                terms.add(e)

    # se troppo generica, aggiungi sempre alcune “ancore”
    if len(base) < 18 or len(terms) <= 3:
        for e in ["piano astrale", "corpo astrale", "trapasso", "piani spirituali", "morte"]:
            terms.add(e)

    # terms mix: manteniamo frasi e parole
    out = []
    for t in terms:
        if not t:
            continue
        out.append(t)
    return out


def score_chunk(chunk: Chunk, terms: List[str]) -> int:
    text = chunk.text.lower()
    s = 0
    for t in terms:
        if not t:
            continue
        if t in text:
            # boost per frasi con spazio (più specifiche)
            s += 4 if " " in t else 2
    return s


def search_rag(query: str, top_k: int = 3) -> List[Chunk]:
    chunks: List[Chunk] = RAG.get("chunks", [])  # type: ignore
    if not chunks:
        return []

    terms = expand_query_terms(query)
    scored = []
    for ch in chunks:
        sc = score_chunk(ch, terms)
        if sc > 0:
            scored.append((sc, ch))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:top_k]]

# ===================== SCHEDULED MESSAGES (PTB JobQueue) =====================
async def send_good_morning(context: ContextTypes.DEFAULT_TYPE):
    if ALLOWED_GROUP_ID is None:
        return
    q = await get_random_quote()
    text = (
        "☀️ Buongiorno, Metavoice.\n"
        "Lo so… è mattina. Anche per me è un trauma.\n\n"
        f"📜 *{escape_markdown(q.author)}*\n"
        f"“{escape_markdown(q.text)}”\n\n"
        f"Fonte: {escape_markdown(q.source)}\n{q.ref}"
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
        f"📜 *{escape_markdown(q.author)}*\n"
        f"“{escape_markdown(q.text)}”\n\n"
        f"Fonte: {escape_markdown(q.source)}\n{q.ref}"
    )
    await context.bot.send_message(
        chat_id=ALLOWED_GROUP_ID,
        text=text,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


def plan_today_jobs(application):
    """
    Pianifica ogni giorno:
    - buongiorno random 08:00–09:00
    - buonanotte random 23:00–00:45
    """
    if ALLOWED_GROUP_ID is None:
        log.warning("ALLOWED_GROUP_ID non impostato: non pianifico buongiorno/buonanotte.")
        return

    if application.job_queue is None:
        log.error(
            "JobQueue non disponibile (application.job_queue=None). "
            "Installa python-telegram-bot con extra [job-queue] e apscheduler."
        )
        return

    now = dt.datetime.now()

    gm_time = random_time_today_window((8, 0), (9, 0))
    gn_time = random_time_night_window()

    if gm_time <= now:
        gm_time = now + dt.timedelta(minutes=2)
    if gn_time <= now:
        gn_time = now + dt.timedelta(minutes=3)

    # Rimuovi job vecchi
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
        "• /sources\n"
        "• /ask <domanda>\n"
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

    books = len(RAG.get("books", []))  # type: ignore
    pages_total = int(RAG.get("pages_total", 0) or 0)
    pages_text = int(RAG.get("pages_with_text", 0) or 0)
    scan_like = bool(RAG.get("scan_like", False))
    pdf_ok = bool(RAG.get("pdfreader_ok", False))
    last_build = str(RAG.get("last_build") or "n/d")

    pdf_line = f"✅ {books} libri / {pages_total} pagine (testo su {pages_text})"
    if not pdf_ok:
        pdf_line = "❌ (manca libreria pypdf nel requirements.txt)"
    elif pages_total == 0 and books > 0:
        pdf_line = f"⚠️ {books} libri ma pagine=0 (lettura PDF fallita)"
    elif scan_like:
        pdf_line += " | ⚠️ sembra scansione (testo=0) → serve OCR"

    await update.message.reply_text(
        "📌 Status\n"
        f"• Username: {mention_token()}\n"
        f"• {lock}\n"
        f"• JobQueue: {jq}\n"
        f"• PDF: {pdf_line}\n"
        f"• Indice: {last_build}\n"
        "• Quote: ✅ (Wikiquote + fallback)\n",
    )


async def cmd_sources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return

    pdfs = list_pdf_files()
    if not pdfs:
        await update.message.reply_text(f"📚 Nessun PDF trovato in: {PDF_DIR}")
        return

    lines = ["📚 Libri CF77 (diagnostica)"]
    if PdfReader is None:
        lines.append("⚠️ pypdf NON installato → aggiungi `pypdf==4.2.0` al requirements.txt")
        lines.append("")
        lines.append("File trovati:")
        for p in pdfs:
            lines.append(f"• {os.path.basename(p)}")
        await update.message.reply_text("\n".join(lines))
        return

    total_pages = 0
    total_text_pages = 0
    total_chars = 0
    scan_hits = 0

    for p in pdfs:
        base = os.path.basename(p)
        try:
            size = human_kb(os.path.getsize(p))
        except Exception:
            size = "n/d"

        pt, pwt, ct, _ = extract_pdf_text(p)
        total_pages += pt
        total_text_pages += pwt
        total_chars += ct
        if pt > 0 and pwt == 0:
            scan_hits += 1

        lines.append(f"• {base} | size={size} | pagine={pt} | testo_su={pwt} | chars={ct}")

    lines.append("")
    lines.append(f"Totale: libri={len(pdfs)} | pagine={total_pages} | testo_su={total_text_pages} | chars={total_chars}")

    if total_pages > 0 and total_text_pages == 0:
        lines.append("🚨 Risultato: testo estratto = 0. Quasi certamente PDF scansiti/immagine → serve OCR.")
    elif scan_hits > 0:
        lines.append(f"⚠️ Attenzione: {scan_hits} PDF sembrano scansioni (testo=0).")

    await update.message.reply_text("\n".join(lines))


async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not in_allowed_context(update):
        return
    q = await get_random_quote()
    await update.message.reply_text(
        f"📜 *{escape_markdown(q.author)}*\n"
        f"“{escape_markdown(q.text)}”\n\n"
        f"Fonte: {escape_markdown(q.source)}\n{q.ref}",
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

    # se indice vuoto o pdf scansiti
    pages_total = int(RAG.get("pages_total", 0) or 0)
    pages_text = int(RAG.get("pages_with_text", 0) or 0)
    pdf_ok = bool(RAG.get("pdfreader_ok", False))
    scan_like = bool(RAG.get("scan_like", False))

    if not pdf_ok:
        await update.message.reply_text(
            intro
            + "📌 Domanda ricevuta:\n"
            + question
            + "\n\n❌ Non posso consultare i PDF perché manca `pypdf` nel requirements.\n"
            + "Aggiungi `pypdf==4.2.0` e redeploy."
        )
        return

    if pages_total > 0 and pages_text == 0 and scan_like:
        await update.message.reply_text(
            intro
            + "📌 Domanda ricevuta:\n"
            + question
            + "\n\n🚨 I PDF risultano *scansioni* (testo estratto = 0), quindi non ho nulla da cercare.\n"
            + "Serve OCR (prossimo step)."
        )
        return

    hits = search_rag(question, top_k=3)
    if not hits:
        await update.message.reply_text(
            intro
            + "📌 Domanda ricevuta:\n"
            + question
            + "\n\n😤 Non ho trovato un passaggio chiaro nei PDF indicizzati.\n"
            + "Tip: prova parole-ancora tipo: “piano astrale”, “trapasso”, “corpo sottile”, “piani spirituali”."
        )
        return

    # costruisci risposta con citazioni (book + page)
    out = [intro + "📌 Domanda ricevuta:\n" + question + "\n\n🔎 *Passaggi trovati (CF77):*"]
    for i, ch in enumerate(hits, start=1):
        snippet = ch.text.strip()
        if len(snippet) > 450:
            snippet = snippet[:450].rstrip() + "…"
        out.append(f"\n{i}) *{escape_markdown(ch.book)}* — pag. {ch.page}\n“{escape_markdown(snippet)}”")

    await update.message.reply_text("\n".join(out), parse_mode="Markdown")


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

    # Privato: rispondi sempre (ma senza fare “AI”, per ora)
    if chat.type == ChatType.PRIVATE:
        intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
        await msg.reply_text(intro + "Dimmi pure. Se vuoi una risposta dai libri: usa /ask 😈")
        return

    # Gruppo: solo se mention/reply
    if not is_bot_mentioned(msg):
        return

    intro = night_intro(user_first) if is_night_now() else day_intro(user_first)
    await msg.reply_text(intro + "Ok, ti sento. (Per cercare nei testi: /ask <domanda>) 😈")


# ===================== ERROR HANDLER =====================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    if isinstance(err, Conflict):
        log.error("CONFLICT: Un'altra istanza del bot sta facendo polling. Chiudine una.")
    else:
        log.exception("Errore non gestito:", exc_info=err)


# ===================== STARTUP HOOK =====================
async def post_init(application):
    # indicizza subito
    build_rag_index()

    # Pianifica subito all'avvio
    plan_today_jobs(application)

    # Pianifica ripianificazione giornaliera alle 00:05
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


def main():
    def build_application():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_error_handler(on_error)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("test_gm", cmd_test_gm))
    app.add_handler(CommandHandler("test_gn", cmd_test_gn))

    app.add_handler(MessageHandler(filters.TEXT, handle_text))

    return app


def main_polling():
    app = build_application()
    print(f"{BOT_DISPLAY} è in ascolto… (polling locale)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main_polling()