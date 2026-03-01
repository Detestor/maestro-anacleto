from __future__ import annotations

import time
from threading import Lock
from typing import Any, Dict

_LOCK = Lock()

# Snapshot condiviso tra Kraken e Anacleto
_KRAKEN_STATE: Dict[str, Any] = {
    "ts": None,               # epoch seconds
    "symbol": "BTC/EUR",
    "timeframe": "1h",
    "price": None,
    "eur_free": None,
    "btc_free": None,
    "in_position": None,
    "regime": None,
    "mode": None,
    "tp_price": None,
    "sl_price": None,
    "last_eval_key": None,
    "note": None,
}


def kraken_update(**kwargs):
    """Aggiorna lo snapshot Kraken in modo thread-safe."""
    with _LOCK:
        _KRAKEN_STATE.update(kwargs)
        _KRAKEN_STATE["ts"] = time.time()


def kraken_snapshot() -> Dict[str, Any]:
    """Ritorna una copia dello snapshot Kraken."""
    with _LOCK:
        return dict(_KRAKEN_STATE)