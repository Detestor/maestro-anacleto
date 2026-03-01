import asyncio
import logging
import signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("RUNNER")


async def start_anacleto():
    # anacleto_bot.py deve esporre: async def run_anacleto()
    from anacleto_bot import run_anacleto
    await run_anacleto()


async def start_kraken():
    # Kraken è sync e bloccante -> lo mandiamo in thread
    from tradingbotpaper.bot import run_kraken_sync
    await asyncio.to_thread(run_kraken_sync, "tradingbotpaper/config.yaml")


async def main():
    stop_event = asyncio.Event()

    def _stop(*_):
        log.info("Stop richiesto. Chiudo tutto...")
        stop_event.set()

    for s in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(s, _stop)
        except Exception:
            pass

    anacleto_task = asyncio.create_task(start_anacleto(), name="anacleto")
    kraken_task = asyncio.create_task(start_kraken(), name="kraken")
    stop_task = asyncio.create_task(stop_event.wait(), name="stop")

    done, pending = await asyncio.wait(
        [anacleto_task, kraken_task, stop_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # se uno dei bot crasha -> log
    for t in done:
        if t.get_name() in ("anacleto", "kraken"):
            exc = t.exception()
            if exc:
                log.exception("Task %s morto con errore:", t.get_name(), exc_info=exc)

    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())