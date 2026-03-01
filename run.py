from __future__ import annotations

import os
import sys
import logging

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("RUNNER")


def main():
    log.info("RUNNER | avvio SOLO Maestro Anacleto (senza Kraken).")
    log.info("CWD=%s", os.getcwd())
    log.info("PYTHON=%s", sys.version.replace("\n", " "))

    from anacleto_bot import main as anacleto_main
    anacleto_main()


if __name__ == "__main__":
    main()