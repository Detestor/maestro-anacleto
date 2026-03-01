from __future__ import annotations

import os
import sys
import asyncio
import logging
import hashlib
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("RUNNER")


def fpr(s: str) -> str:
    if not s:
        return "none"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:10]


def ensure_project_root() -> Path:
    root = Path(__file__).resolve().parent
    os.chdir(str(root))
    return root


def run_anacleto_thread_entry():
    # dentro questo entrypoint, anacleto_bot.py crea il loop e disabilita i segnali
    from anacleto_bot import run_polling_blocking
    run_polling_blocking()


async def run_anacleto_supervisor():
    backoff = 8
    while True:
        try:
            log.info("ANACLETO: avvio…")
            await asyncio.to_thread(run_anacleto_thread_entry)

            log.warning("ANACLETO: terminato. Riavvio tra 5s…")
            await asyncio.sleep(5)
            backoff = 8

        except Exception as e:
            log.exception("ANACLETO: crash. Riprovo tra %ss… (%s)", backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def run_kraken_sync_wrapper(cfg_path: str):
    from tradingbotpaper.bot import run_kraken_sync
    run_kraken_sync(cfg_path)


async def kraken_supervisor(cfg_path: str):
    backoff = 30
    while True:
        try:
            log.info("KRAKEN: avvio con config=%s", cfg_path)
            await asyncio.to_thread(run_kraken_sync_wrapper, cfg_path)

            log.warning("KRAKEN: terminato. Riavvio tra 10s…")
            await asyncio.sleep(10)
            backoff = 30

        except Exception as e:
            log.exception("KRAKEN: crash. Riprovo tra %ss… (%s)", backoff, e)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)


async def main_async():
    root = ensure_project_root()

    bot_token = os.getenv("BOT_TOKEN", "")
    trading_dir = (root / "tradingbotpaper")
    if str(trading_dir) not in sys.path:
        sys.path.insert(0, str(trading_dir))

    log.info("==============================================")
    log.info("RUNNER START | PID=%s", os.getpid())
    log.info("HOST=%s", os.getenv("HOSTNAME", "n/a"))
    log.info(
        "RENDER_SERVICE_NAME=%s | RENDER_INSTANCE_ID=%s",
        os.getenv("RENDER_SERVICE_NAME", "n/a"),
        os.getenv("RENDER_INSTANCE_ID", "n/a"),
    )
    log.info("BOT_TOKEN_FPR=%s (sha256[:10])", fpr(bot_token))
    log.info("TRADING_DIR in sys.path=%s", str(trading_dir) in sys.path)
    log.info("CWD=%s", os.getcwd())
    log.info("==============================================")

    cfg_path = "tradingbotpaper/config.yaml"

    t1 = asyncio.create_task(kraken_supervisor(cfg_path))
    t2 = asyncio.create_task(run_anacleto_supervisor())

    await asyncio.gather(t1, t2)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()