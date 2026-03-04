import os
import logging
import asyncio
from pathlib import Path
from datetime import datetime
from typing import List

from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from openai import OpenAI
from pypdf import PdfReader

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("ANACLETO")

BOT_DISPLAY = "MAESTRO ANACLETO"

BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = Path(os.getenv("PDF_DIR", str(BASE_DIR / "data" / "pdfs"))).resolve()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY mancante nelle env vars")

client = OpenAI(api_key=OPENAI_API_KEY)

INDEX = {
    "built": False,
    "chunks": []
}

def lunar_phase():
    diff = datetime.utcnow() - datetime(2001, 1, 1)
    days = diff.days + diff.seconds / 86400
    lunations = 0.20439731 + days * 0.03386319269
    phases = ["🌑 Luna Nuova", "🌒 Crescente", "🌓 Primo Quarto", "🌔 Gibbosa Crescente",
              "🌕 Luna Piena", "🌖 Gibbosa Calante", "🌗 Ultimo Quarto", "🌘 Calante"]
    return phases[int((lunations % 1) * 8)]

def build_index():
    LOG.info("🔍 build_index START")
    INDEX["chunks"].clear()
    for pdf in PDF_DIR.glob("*.pdf"):
        reader = PdfReader(str(pdf))
        for page in reader.pages:
            text = page.extract_text()
            if text:
                INDEX["chunks"].append(text.strip())
    INDEX["built"] = True
    LOG.info(f"✅ Index built with {len(INDEX['chunks'])} chunks")

def rag_search(query: str, top_k: int = 5) -> List[str]:
    results = []
    for chunk in INDEX["chunks"]:
        if query.lower() in chunk.lower():
            results.append(chunk[:1500])
        if len(results) >= top_k:
            break
    return results

async def gpt_answer(query: str):
    context_chunks = rag_search(query)
    context_text = "\n\n".join(context_chunks)

    prompt = f"""
Sei MAESTRO ANACLETO.
Rispondi in modo completo e coerente.

CONTESTO CF77:
{context_text}

DOMANDA:
{query}

Amplia con conoscenze coerenti se necessario.
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )

    return response.choices[0].message.content

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🌟 MAESTRO ANACLETO è desto.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"PDF_DIR: {PDF_DIR}\nIndex built: {INDEX['built']}\nChunks: {len(INDEX['chunks'])}"
    )

async def quote_job(context: ContextTypes.DEFAULT_TYPE):
    phase = lunar_phase()
    text = await gpt_answer("Dammi una citazione spirituale coerente con CF77.")
    await context.bot.send_message(chat_id=context.job.chat_id,
                                   text=f"{phase}\n\n✨ Citazione:\n{text[:1500]}")

async def morning_job(context: ContextTypes.DEFAULT_TYPE):
    phase = lunar_phase()
    almanac = await gpt_answer("Scrivi un almanacco spirituale per oggi: fase lunare, energie planetarie e festività antiche.")
    await context.bot.send_message(chat_id=context.job.chat_id,
                                   text=f"🌅 Buongiorno.\n{phase}\n\n{almanac[:3000]}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    answer = await gpt_answer(query)
    await update.message.reply_text(answer[:4000])

def build_application():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN mancante")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    scheduler = AsyncIOScheduler()
    scheduler.start()

    scheduler.add_job(morning_job, "cron", hour=8, minute=0, args=[app])
    scheduler.add_job(quote_job, "interval", hours=4, args=[app])

    asyncio.get_event_loop().create_task(asyncio.to_thread(build_index))

    return app