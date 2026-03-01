from __future__ import annotations

import os
import sys
import time
import signal
import threading
import logging
import hashlib

# ---- logging ----
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("RUNNER")

LOCK_FILE = "/tmp/maestro_anacleto_runner.lock"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_runner_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                old_pid = int((f.read() or "0").strip())
        except Exception:
            old_pid = 0

        if old_pid and _pid_alive(old_pid):
            raise RuntimeError(f"RUNNER già in esecuzione (pid={old_pid}). Stoppo istanza doppia.")
        else:
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass

    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def release_runner_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


STOP = False


def handle_exit(signum, frame):
    global STOP
    STOP = True
    log.warning("Stop richiesto (signal=%s). Chiudo tutto…", signum)
    release_runner_lock()
    raise SystemExit(0)


def add_paths():
    # assicura che "tradingbotpaper" e root siano importabili
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)

    trading_dir = os.path.join(root, "tradingbotpaper")
    if trading_dir not in sys.path:
        sys.path.insert(0, trading_dir)

    return root, trading_dir


def token_fingerprint(token: str) -> str:
    if not token:
        return "missing"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]


def kraken_supervisor():
    """
    Kraken gira in loop e se crasha riparte dopo 30s.
    """
    cfg_path = os.getenv("KRAKEN_CONFIG", "tradingbotpaper/config.yaml")
    while not STOP:
        try:
            log.info("KRAKEN: avvio con config=%s", cfg_path)
            from tradingbotpaper.bot import run_kraken_sync
            run_kraken_sync(cfg_path)
            log.warning("KRAKEN: terminato (strano). Riprovo tra 30s…")
        except Exception as e:
            log.exception("KRAKEN: crashato. Riprovo tra 30s… (%s)", e)

        for _ in range(30):
            if STOP:
                return
            time.sleep(1)


def main():
    global STOP

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    acquire_runner_lock()
    root, trading_dir = add_paths()

    bot_token = os.getenv("BOT_TOKEN", "")
    log.info("==============================================")
    log.info("RUNNER START | PID=%s", os.getpid())
    log.info("HOST=%s", os.getenv("HOSTNAME", "n/a"))
    log.info("RENDER_SERVICE_NAME=%s | RENDER_INSTANCE_ID=%s",
             os.getenv("RENDER_SERVICE_NAME", "n/a"),
             os.getenv("RENDER_INSTANCE_ID", "n/a"))
    log.info("BOT_TOKEN_FPR=%s (sha256[:10])", token_fingerprint(bot_token))
    log.info("TRADING_DIR in sys.path=%s", trading_dir in sys.path)
    log.info("==============================================")

    # ---- start Kraken in background thread ----
    t = threading.Thread(target=kraken_supervisor, name="kraken_supervisor", daemon=True)
    t.start()

    # ---- run Anacleto in main thread (ONE polling ONLY) ----
    log.info("ANACLETO: avvio…")
    from anacleto_bot import main as anacleto_main
    anacleto_main()

    # se Anacleto esce, chiudiamo tutto
    STOP = True
    release_runner_lock()


if __name__ == "__main__":
    main()