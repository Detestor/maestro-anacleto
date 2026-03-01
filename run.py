import asyncio
import hashlib
import logging
import os
import signal
import socket
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("RUNNER")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADING_DIR = os.path.join(BASE_DIR, "tradingbotpaper")

# Rende importabile "core" (tradingbotpaper/core)
if TRADING_DIR not in sys.path:
    sys.path.insert(0, TRADING_DIR)


def _short_hash(s: str) -> str:
    if not s:
        return "none"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:10]


def log_identity():
    pid = os.getpid()
    host = socket.gethostname()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    # Render spesso espone queste env (non sempre)
    render_service = os.getenv("RENDER_SERVICE_NAME", "")
    render_instance = os.getenv("RENDER_INSTANCE_ID", "")
    bot_token = os.getenv("BOT_TOKEN", "")
    token_fpr = _short_hash(bot_token)

    log.info("==============================================")
    log.info("RUNNER START | %s", now)
    log.info("PID=%s | HOST=%s", pid, host)
    if render_service or render_instance:
        log.info("RENDER_SERVICE_NAME=%s | RENDER_INSTANCE_ID=%s", render_service, render_instance)
    log.info("BOT_TOKEN_FPR=%s (sha256[:10])", token_fpr)
    log.info("TRADING_DIR in sys.path=%s", TRADING_DIR in sys.path)
    log.info("==============================================")


async def anacleto_supervisor():
    """
    Avvia Anacleto (async). Se crasha, riprova.
    """
    while True:
        try:
            from anacleto_bot import run_anacleto
            log.info("ANACLETO: avvio…")
            await run_anacleto()
            log.warning("ANACLETO: terminato (strano). Riavvio tra 10s…")
            await asyncio.sleep(10)
        except Exception as e:
            log.exception("ANACLETO: crash. Riprovo tra 30s…", exc_info=e)
            await asyncio.sleep(30)


async def kraken_supervisor():
    """
    Avvia Kraken in thread (sync). Se crasha, riprova.
    """
    while True:
        try:
            from tradingbotpaper.bot import run_kraken_sync
            cfg_path = os.path.join("tradingbotpaper", "config.yaml")
            log.info("KRAKEN: avvio con config=%s", cfg_path)
            await asyncio.to_thread(run_kraken_sync, cfg_path)
            log.warning("KRAKEN: terminato. Riavvio tra 10s…")
            await asyncio.sleep(10)
        except Exception as e:
            log.exception("KRAKEN: crash. Riprovo tra 30s…", exc_info=e)
            await asyncio.sleep(30)


async def main():
    log_identity()

    stop_event = asyncio.Event()

    def _stop(*_):
        log.info("STOP: ricevuto SIGTERM/SIGINT. Spengo…")
        stop_event.set()

    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _stop)
        except Exception:
            pass

    t_anacleto = asyncio.create_task(anacleto_supervisor(), name="anacleto_supervisor")
    t_kraken = asyncio.create_task(kraken_supervisor(), name="kraken_supervisor")

    await stop_event.wait()

    for t in (t_anacleto, t_kraken):
        t.cancel()
    await asyncio.gather(t_anacleto, t_kraken, return_exceptions=True)

    log.info("STOP: completo.")


if __name__ == "__main__":
    asyncio.run(main())