from __future__ import annotations
from dataclasses import dataclass
from .utils import pct

@dataclass
class RiskLimits:
    max_trades_per_day: int
    risk_per_trade_pct: float
    daily_loss_limit_pct: float

class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits

    def risk_budget_usdt(self, base_equity_usdt: float) -> float:
        # capitale "a rischio" per trade (calcolato sulla base bloccata)
        return base_equity_usdt * pct(self.limits.risk_per_trade_pct)

    def hit_daily_stop(self, equity_start: float, equity_now: float) -> bool:
        dd = (equity_start - equity_now) / max(equity_start, 1e-9)
        return dd >= pct(self.limits.daily_loss_limit_pct)