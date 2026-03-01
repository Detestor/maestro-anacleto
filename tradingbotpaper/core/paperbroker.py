from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from .utils import Trade, pct

@dataclass
class PaperSettings:
    taker_fee_pct: float
    slippage_pct: float

class PaperBroker:
    def __init__(self, starting_usdt: float, settings: PaperSettings):
        self.cash = starting_usdt
        self.position_qty = 0.0
        self.avg_entry = 0.0
        self.settings = settings
        self.trades: list[Trade] = []

    def equity(self, mark_price: float) -> float:
        return self.cash + self.position_qty * mark_price

    def _apply_slippage(self, price: float, side: str) -> float:
        slip = price * pct(self.settings.slippage_pct)
        return price + slip if side == "buy" else price - slip

    def _fee(self, notional: float) -> float:
        return notional * pct(self.settings.taker_fee_pct)

    def open_long(
        self,
        ts: datetime,
        symbol: str,
        price: float,
        risk_usdt: float,
        stop_pct: float,
        rr: float,
        reason: str
    ):
        if self.position_qty != 0:
            return  # già in posizione

        fill = self._apply_slippage(price, "buy")
        stop = fill * (1 - pct(stop_pct))
        take = fill * (1 + pct(stop_pct) * rr)

        # qty tale che se stop colpisce perdi ~risk_usdt
        per_unit_risk = max(fill - stop, 1e-9)
        qty = risk_usdt / per_unit_risk

        notional = qty * fill
        fee = self._fee(notional)

        # se non basta cash, ridimensiona
        if notional + fee > self.cash:
            qty = max((self.cash / fill) * 0.98, 0.0)
            notional = qty * fill
            fee = self._fee(notional)

        if qty <= 0:
            return

        self.cash -= (notional + fee)
        self.position_qty = qty
        self.avg_entry = fill

        self.trades.append(
            Trade(
                ts=ts, symbol=symbol, side="buy", qty=qty,
                entry=fill, stop=stop, take=take,
                fee_paid=fee, reason=reason
            )
        )

    def close_long(self, ts: datetime, symbol: str, price: float, reason: str):
        if self.position_qty == 0:
            return

        fill = self._apply_slippage(price, "sell")
        notional = self.position_qty * fill
        fee = self._fee(notional)

        pnl = (fill - self.avg_entry) * self.position_qty - fee
        self.cash += (notional - fee)

        t = self.trades[-1]
        t.exit_price = fill
        t.pnl = pnl
        t.fee_paid += fee
        t.reason = (t.reason + " | " + reason).strip(" | ")

        self.position_qty = 0.0
        self.avg_entry = 0.0

    def update_stops(self, ts: datetime, symbol: str, high: float, low: float):
        if self.position_qty == 0:
            return

        t = self.trades[-1]

        # conservativo: se nella stessa candela tocchi sia stop che take,
        # assumo che passi prima dallo stop.
        if low <= t.stop:
            self.close_long(ts, symbol, t.stop, "STOP")
        elif high >= t.take:
            self.close_long(ts, symbol, t.take, "TAKE")