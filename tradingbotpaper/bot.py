from __future__ import annotations

import os
import time
import yaml
import signal
import sys

from rich import print

from core.datafeed import DataFeed
from core.liveexecutor import KrakenExecutor
from core.notifier import send_telegram
from core.regime_detector import detect_regime, compute_atr
from core.strategy_trend import trend_signal
from core.strategy_range import range_signal


LOCK_FILE = "bot.lock"
SYMBOL_DEFAULT = "BTC/EUR"
IN_CRITICAL = False

STATE = {
    "in_pos": False,
    "entry_price": None,
    "tp_price": None,
    "sl_price": None,
    "qty_btc": None,
    "mode": None,
}


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_lock():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                pid = int((f.read() or "0").strip())
        except Exception:
            pid = 0

        if pid and pid_exists(pid):
            print("[red]Bot già in esecuzione![/red]")
            # IMPORTANT: non usare sys.exit qui se siamo importati
            raise RuntimeError("Bot già in esecuzione (lock attivo).")
        else:
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass

    with open(LOCK_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def handle_exit(signum, frame):
    global IN_CRITICAL
    if IN_CRITICAL:
        print("\n[yellow]Operazione in corso: ignoro la chiusura. Riprova tra 5 sec.[/yellow]")
        try:
            send_telegram("⚠️ Tentata chiusura bot durante un'operazione: bloccata per sicurezza.")
        except Exception:
            pass
        return

    print("\n[red]Chiusura controllata...[/red]")
    try:
        send_telegram("🛑 Bot chiuso manualmente.")
    except Exception:
        pass
    release_lock()

    # IMPORTANT: in ambiente importato, evitare sys.exit(0)
    raise SystemExit(0)


# Segnali ok anche su Render, ma se gira in thread potrebbe non riceverli:
# non dà problemi lasciarli; se non arrivano, nessun crash.
try:
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)
except Exception:
    pass


def read_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def get_bar_key(df):
    """
    Ritorna una chiave "tempo" dell'ultima candela in modo robusto.
    Supporta:
    - colonna 'timestamp'
    - colonna 'ts'
    - colonna 'datetime'
    - index datetime/numero
    - fallback: tuple OHLCV ultima riga
    """
    cols = list(df.columns)

    if "timestamp" in cols:
        return df.iloc[-1]["timestamp"]
    if "ts" in cols:
        return df.iloc[-1]["ts"]
    if "datetime" in cols:
        return df.iloc[-1]["datetime"]

    try:
        return df.index[-1]
    except Exception:
        pass

    last = df.iloc[-1]
    keys = [k for k in ["open", "high", "low", "close", "volume"] if k in cols]
    if keys:
        return tuple(last[k] for k in keys)

    return None


def run_live(cfg: dict):
    global IN_CRITICAL

    acquire_lock()
    send_telegram("🚀 Bot LIVE (AUTO A/B) avviato. (SL reale + TP software)")

    live_cfg = cfg.get("live", {})
    symbol = str(live_cfg.get("symbol", SYMBOL_DEFAULT))
    timeframe = str(live_cfg.get("timeframe", "1h"))
    ohlcv_limit = int(live_cfg.get("ohlcv_limit", 500))

    min_trade_eur = float(live_cfg.get("min_trade_eur", 6.0))
    spend_pct = float(live_cfg.get("spend_pct", 0.50))

    stratA = live_cfg.get("strategy_A", {})
    stratB = live_cfg.get("strategy_B", {})

    A_sl_atr = float(stratA.get("sl_atr", 1.5))
    A_tp_atr = float(stratA.get("tp_atr", 3.0))

    B_sl_atr = float(stratB.get("sl_atr", 1.0))
    B_tp_atr = float(stratB.get("tp_atr", 1.2))

    feed = DataFeed(cfg.get("exchange", "kraken"))
    ex = KrakenExecutor()

    last_eval_key = None
    last_regime = None

    print(f"[bold red]LIVE AUTO bot avviato[/bold red] | {symbol} | tf={timeframe}")
    print(f"spend_pct={spend_pct:.2f} | min_trade_eur={min_trade_eur:.2f}")

    while True:
        # ---- balances ----
        bal = ex.fetch_balance()
        eur_free = safe_float(bal.get("free", {}).get("EUR", 0.0))
        btc_free = safe_float(bal.get("free", {}).get("BTC", 0.0))
        in_position = btc_free > 0.00000010

        # ---- ticker ----
        ticker = ex.fetch_ticker(symbol)
        last_price = safe_float(ticker.get("last"), 0.0)
        bid = safe_float(ticker.get("bid"), last_price)
        ask = safe_float(ticker.get("ask"), last_price)

        print(f"LIVE status | price={last_price:.2f} | EUR={eur_free:.2f} | BTC={btc_free:.8f} | tf={timeframe}")

        # ---- Se siamo in posizione: gestiamo TP software ----
        if in_position and STATE["tp_price"] is not None:
            if last_price >= float(STATE["tp_price"]):
                try:
                    IN_CRITICAL = True
                    qty = btc_free
                    send_telegram(f"🎯 TP software raggiunto ({STATE['mode']})\nVendo a market.\nPrezzo: {last_price:.2f}")
                    ex.create_market_sell(symbol, qty)
                    STATE.update({"in_pos": False, "entry_price": None, "tp_price": None, "sl_price": None, "qty_btc": None, "mode": None})
                    IN_CRITICAL = False
                except Exception as e:
                    IN_CRITICAL = False
                    send_telegram(f"❌ ERRORE TP software sell: {e}")

            time.sleep(60)
            continue

        # ---- OHLCV ----
        try:
            df = feed.fetch_ohlcv(symbol, timeframe=timeframe, limit=ohlcv_limit)
        except Exception as e:
            print(f"[yellow]Datafeed warning[/yellow] {e}")
            time.sleep(15)
            continue

        current_key = get_bar_key(df)
        new_bar = (last_eval_key is None) or (current_key != last_eval_key)
        if not new_bar:
            time.sleep(60)
            continue

        last_eval_key = current_key

        # ---- Regime ----
        regime = detect_regime(df)
        if regime != last_regime:
            send_telegram(f"🧭 Regime: {regime} (tf={timeframe})")
            last_regime = regime

        # se siamo in posizione (e non c'era tp_state) non facciamo altro
        if in_position:
            time.sleep(10)
            continue

        if regime == "CHAOS":
            send_telegram("⛔ CHAOS: nessun trade.")
            time.sleep(10)
            continue

        # ---- Signal per regime ----
        if regime == "TREND":
            sig = trend_signal(df)
            sl_mult = A_sl_atr
            tp_mult = A_tp_atr
            mode = "A/TREND"
        else:
            sig = range_signal(df)
            sl_mult = B_sl_atr
            tp_mult = B_tp_atr
            mode = "B/RANGE"

        if sig != "BUY":
            time.sleep(10)
            continue

        eur_to_spend = min(eur_free * spend_pct, max(0.0, eur_free - 0.5))
        if eur_to_spend < min_trade_eur:
            send_telegram(f"⚠️ Setup {mode} ma EUR insufficienti (free={eur_free:.2f}).")
            time.sleep(10)
            continue

        qty_btc = eur_to_spend / ask

        df_atr = df.copy()
        df_atr["atr"] = compute_atr(df_atr)
        atr = safe_float(df_atr.iloc[-1]["atr"], 0.0)
        if atr <= 0:
            send_telegram("⚠️ ATR non valido: skip trade.")
            time.sleep(10)
            continue

        stop_price = ask - (sl_mult * atr)
        tp_price = ask + (tp_mult * atr)

        try:
            IN_CRITICAL = True

            ex.create_market_buy(symbol, qty_btc)
            send_telegram(f"🟢 BUY {symbol} ({mode})\nSpesa: {eur_to_spend:.2f}€\nPrezzo: {ask:.2f}")

            time.sleep(3)

            bal2 = ex.fetch_balance()
            btc_after = safe_float(bal2.get("free", {}).get("BTC", 0.0))
            if btc_after <= 0.00000010:
                send_telegram("⚠️ BUY fatto ma BTC non visibile nel saldo (attendo).")
                IN_CRITICAL = False
                time.sleep(10)
                continue

            # SOLO SL reale (evita insufficient funds del TP)
            try:
                ex.create_stop_loss_sell(symbol, btc_after, stop_price)
            except Exception as e:
                send_telegram(f"❌ ERRORE SL: {e}\nChiudo posizione per sicurezza.")
                ex.create_market_sell(symbol, btc_after)
                IN_CRITICAL = False
                time.sleep(10)
                continue

            STATE.update({
                "in_pos": True,
                "entry_price": ask,
                "tp_price": tp_price,
                "sl_price": stop_price,
                "qty_btc": btc_after,
                "mode": mode,
            })

            send_telegram(
                f"🛡 Protezioni {mode}\n"
                f"ATR: {atr:.2f}\n"
                f"SL reale: {stop_price:.2f}\n"
                f"TP software: {tp_price:.2f}"
            )

            IN_CRITICAL = False

        except Exception as e:
            IN_CRITICAL = False
            send_telegram(f"❌ ERRORE entry (generico): {e}")

        time.sleep(10)


def run_kraken_sync(config_path: str = "config.yaml"):
    """
    Entry-point IMPORTABILE per Render/run.py.
    """
    cfg = read_yaml(config_path)
    run_live(cfg)


def main():
    # Manteniamo compatibilità: se lo lanci a mano con `python bot.py`, va.
    run_kraken_sync("config.yaml")


if __name__ == "__main__":
    main()