import asyncio
import logging
import os
import signal
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("RUNNER")


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADING_DIR = os.path.join(BASE_DIR, "tradingbotpaper")

# Rende importabile "core" (che sta in tradingbotpaper/core)
# così "from core.xxx import yyy" funziona anche su Render.
if TRADING_DIR not in sys.path:
    sys.path.insert(0, TRADING_DIR)


async def start_anacleto():
    # anacleto_bot.py deve esporre: async def run_anacleto()
    from anacleto_bot import run_anacleto
    await run_anacleto()


async def kraken_supervisor():
    """
    Avvia Kraken in un thread (bot sync e bloccante).
    Se crasha, non abbatte tutto: aspetta e riprova.
    """
    while True:
        try:
            from tradingbotpaper.bot import run_kraken_sync
            cfg_path = os.path.join("tradingbotpaper", "config.yaml")
            log.info("Avvio Kraken bot con config: %s", cfg_path)
            await asyncio.to_thread(run_kraken_sync, cfg_path)
            # Se run_kraken_sync termina "normalmente" (strano), riparti comunque
            log.warning("Kraken bot terminato. Riavvio tra 10s...")
            await asyncio.sleep(10)
        except Exception as e:
            log.exception("Kraken bot crashato. Riprovo tra 30s...", exc_info=e)
            await asyncio.sleep(30)


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
    kraken_task = asyncio.create_task(kraken_supervisor(), name="kraken_supervisor")
    stop_task = asyncio.create_task(stop_event.wait(), name="stop")

    # Aspetta stop (Render shutdown) o fine Anacleto (non dovrebbe finire mai)
    done, pending = await asyncio.wait(
        [anacleto_task, stop_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Se Anacleto muore con errore, lo logghiamo
    for t in done:
        if t.get_name() == "anacleto":
            exc = t.exception()
            if exc:
                log.exception("Anacleto morto con errore:", exc_info=exc)

    # Chiudiamo Kraken supervisor
    kraken_task.cancel()
    await asyncio.gather(kraken_task, return_exceptions=True)

    # Cancella eventuali pending (stop_task ecc.)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())