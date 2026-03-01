from __future__ import annotations
import pandas as pd

def build_report(trades) -> pd.DataFrame:
    rows = []
    for t in trades:
        rows.append({
            "ts": t.ts,
            "symbol": t.symbol,
            "side": t.side,
            "qty": t.qty,
            "entry": t.entry,
            "stop": t.stop,
            "take": t.take,
            "exit": t.exit_price,
            "pnl": t.pnl,
            "fees": t.fee_paid,
            "reason": t.reason
        })
    return pd.DataFrame(rows)

def summarize(trades) -> dict:
    closed = [t for t in trades if t.pnl is not None]
    pnl_total = sum(t.pnl for t in closed) if closed else 0.0
    wins = sum(1 for t in closed if (t.pnl or 0) > 0)
    losses = sum(1 for t in closed if (t.pnl or 0) < 0)
    winrate = (wins / max(len(closed), 1)) * 100.0
    return {
        "closed_trades": len(closed),
        "pnl_total": pnl_total,
        "wins": wins,
        "losses": losses,
        "winrate_pct": winrate
    }