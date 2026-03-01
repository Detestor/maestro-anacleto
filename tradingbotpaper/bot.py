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

# Telemetria condivisa (root del repo)
try:
    from shared_state import kraken_update
except Exception:
    kraken_update = None  # fallback


LOCK_FILE = "/tmp/bot.lock"
SYMBOL_DEFAULT = "BTC/EUR"

IN_CRITICAL = False
STOP_REQUESTED = False

# Heartbeat (ogni N ore) — default 2 ore
HEARTBEAT_HOURS = float(os.getenv("KRAKEN_HEARTBEAT_HOURS", "2") or "2")
HEARTBEAT_SECONDS = max(15 * 60, int(HEARTBEAT_HOURS * 3600))  # minimo 15 min anti-bug

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


def request_stop(reason: str = ""):
    global STOP_REQUESTED
    STOP_REQUESTED = True
    if reason:
        try:
            send_telegram(f"🛑 Stop richiesto: {reason}")
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
    request_stop("SIGTERM/SIGINT (standalone)")
    release_lock()
    raise SystemExit(0)


def read_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def safe_float(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default


def get_bar_key(df):
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


def _telemetry(**kwargs):
    if kraken_update:
        try:
            kraken_update(**kwargs)
        except Exception:
            pass


def _fmt_money(x):
    try:
        return f"{float(x):.2f}"
    except Exception:
        return str(x)


def _fmt_btc(x):
    try:
        return f"{float(x):.8f}"
    except Exception:
        return str(x)


def _heartbeat_message(symbol, timeframe, price, eur_free, btc_free, in_position, regime, mode):
    pos = "✅ IN POSIZIONE" if in_position else "❌ fuori posizione"
    return (
        "🫀 *Heartbeat Kraken*\n"
        f"• {symbol} | tf={timeframe}\n"
        f"• price: `{_fmt_money(price)}`\n"
        f"• EUR: `{_fmt_money(eur_free)}` | BTC: `{_fmt_btc(btc_free)}`\n"
        f"• stato: {pos}\n"
        f"• regime: `{regime}` | mode: `{mode}`\n"
    )


def run_live(cfg: dict):
    global IN_CRITICAL, STOP_REQUESTED

    acquire_lock()

    try:
        send_telegram("🚀 Bot LIVE (AUTO A/B) avviato. (SL reale + TP software)")
    except Exception:
        pass

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

    # Heartbeat timer
    next_hb = time.time() + HEARTBEAT_SECONDS

    print(f"[bold red]LIVE AUTO bot avviato[/bold red] | {symbol} | tf={timeframe}")
    print(f"spend_pct={spend_pct:.2f} | min_trade_eur={min_trade_eur:.2f}")

    _telemetry(symbol=symbol, timeframe=timeframe, note="Kraken bot started")

    while True:
        if STOP_REQUESTED:
            print("[yellow]Stop richiesto: esco dal loop LIVE.[/yellow]")
            _telemetry(note="Stop requested")
            break

        # ---- balances ----
        bal = ex.fetch_balance()
        eur_free = safe_float(bal.get("free", {}).get("EUR", 0.0))
        btc_free = safe_float(bal.get("free", {}).get("BTC", 0.0))
        in_position = btc_free > 0.00000010

        # ---- ticker ----
        ticker = ex.fetch_ticker(symbol)
        last_price = safe_float(ticker.get("last"), 0.0)
        ask = safe_float(ticker.get("ask"), last_price)

        # ---- heartbeat every N hours ----
        now_ts = time.time()
        if now_ts >= next_hb:
            try:
                hb = _heartbeat_message(
                    symbol=symbol,
                    timeframe=timeframe,
                    price=last_price,
                    eur_free=eur_free,
                    btc_free=btc_free,
                    in_position=in_position,
                    regime=(last_regime or "n/a"),
                    mode=(STATE.get("mode") or "n/a"),
                )
                send_telegram(hb)
            except Exception:
                pass
            next_hb = now_ts + HEARTBEAT_SECONDS
            _telemetry(note="Heartbeat sent")

        print(f"LIVE status | price={last_price:.2f} | EUR={eur_free:.2f} | BTC={btc_free:.8f} | tf={timeframe}")

        _telemetry(
            price=last_price,
            eur_free=eur_free,
            btc_free=btc_free,
            in_position=in_position,
            mode=STATE.get("mode"),
            tp_price=STATE.get("tp_price"),
            sl_price=STATE.get("sl_price"),
        )

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
                    _telemetry(note="TP hit -> market sell")
                except Exception as e:
                    IN_CRITICAL = False
                    try:
                        send_telegram(f"❌ ERRORE TP software sell: {e}")
                    except Exception:
                        pass

            time.sleep(60)
            continue

        # ---- OHLCV ----
        try:
            df = feed.fetch_ohlcv(symbol, timeframe=timeframe, limit=ohlcv_limit)
        except Exception as e:
            print(f"[yellow]Datafeed warning[/yellow] {e}")
            _telemetry(note=f"Datafeed warning: {e}")
            time.sleep(15)
            continue

        current_key = get_bar_key(df)
        new_bar = (last_eval_key is None) or (current_key != last_eval_key)
        if not new_bar:
            time.sleep(60)
            continue

        last_eval_key = current_key
        _telemetry(last_eval_key=str(current_key))

        # ---- Regime ----
        regime = detect_regime(df)
        if regime != last_regime:
            try:
                send_telegram(f"🧭 Regime: {regime} (tf={timeframe})")
            except Exception:
                pass
            last_regime = regime
            _telemetry(regime=regime, note="Regime changed")

        if in_position:
            time.sleep(10)
            continue

        if regime == "CHAOS":
            try:
                send_telegram("⛔ CHAOS: nessun trade.")
            except Exception:
                pass
            _telemetry(note="CHAOS: no trade")
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
            _telemetry(note=f"No BUY signal (mode={mode})")
            time.sleep(10)
            continue

        eur_to_spend = min(eur_free * spend_pct, max(0.0, eur_free - 0.5))
        if eur_to_spend < min_trade_eur:
            try:
                send_telegram(f"⚠️ Setup {mode} ma EUR insufficienti (free={eur_free:.2f}).")
            except Exception:
                pass
            _telemetry(note=f"EUR insufficient for trade (mode={mode})")
            time.sleep(10)
            continue

        qty_btc = eur_to_spend / ask

        df_atr = df.copy()
        df_atr["atr"] = compute_atr(df_atr)
        atr = safe_float(df_atr.iloc[-1]["atr"], 0.0)
        if atr <= 0:
            try:
                send_telegram("⚠️ ATR non valido: skip trade.")
            except Exception:
                pass
            _telemetry(note="ATR invalid: skip trade")
            time.sleep(10)
            continue

        stop_price = ask - (sl_mult * atr)
        tp_price = ask + (tp_mult * atr)

        try:
            IN_CRITICAL = True

            ex.create_market_buy(symbol, qty_btc)
            try:
                send_telegram(f"🟢 BUY {symbol} ({mode})\nSpesa: {eur_to_spend:.2f}€\nPrezzo: {ask:.2f}")
            except Exception:
                pass

            time.sleep(3)

            bal2 = ex.fetch_balance()
            btc_after = safe_float(bal2.get("free", {}).get("BTC", 0.0))
            if btc_after <= 0.00000010:
                try:
                    send_telegram("⚠️ BUY fatto ma BTC non visibile nel saldo (attendo).")
                except Exception:
                    pass
                IN_CRITICAL = False
                _telemetry(note="BUY done but BTC not visible yet")
                time.sleep(10)
                continue

            try:
                ex.create_stop_loss_sell(symbol, btc_after, stop_price)
            except Exception as e:
                try:
                    send_telegram(f"❌ ERRORE SL: {e}\nChiudo posizione per sicurezza.")
                except Exception:
                    pass
                ex.create_market_sell(symbol, btc_after)
                IN_CRITICAL = False
                _telemetry(note=f"SL error -> closed position: {e}")
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

            _telemetry(mode=mode, tp_price=tp_price, sl_price=stop_price, note="Position opened with SL real + TP software")

            try:
                send_telegram(
                    f"🛡 Protezioni {mode}\n"
                    f"ATR: {atr:.2f}\n"
                    f"SL reale: {stop_price:.2f}\n"
                    f"TP software: {tp_price:.2f}"
                )
            except Exception:
                pass

            IN_CRITICAL = False

        except Exception as e:
            IN_CRITICAL = False
            try:
                send_telegram(f"❌ ERRORE entry (generico): {e}")
            except Exception:
                pass
            _telemetry(note=f"Entry error: {e}")

        time.sleep(10)

    release_lock()


def run_kraken_sync(config_path: str = "config.yaml"):
    cfg = read_yaml(config_path)
    run_live(cfg)


def main():
    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    cfg = read_yaml("config.yaml")
    run_live(cfg)


if __name__ == "__main__":
    main()