from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def pct(x: float) -> float:
    return x / 100.0


@dataclass
class Trade:
    ts: datetime
    symbol: str
    side: str
    qty: float
    entry: float
    stop: float
    take: float
    fee_paid: float = 0.0
    exit_price: float | None = None
    pnl: float | None = None
    reason: str = ""