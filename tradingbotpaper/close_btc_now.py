from core.liveexecutor import KrakenExecutor

SYMBOL = "BTC/EUR"
CONFIRM = True  # <-- METTI True SOLO quando vuoi chiudere davvero

def main():
    ex = KrakenExecutor()

    bal = ex.fetch_balance()
    btc_free = float(bal.get("free", {}).get("BTC", 0.0))
    eur_free = float(bal.get("free", {}).get("EUR", 0.0))

    print("=== CLOSE BTC NOW ===")
    print(f"BTC free: {btc_free:.8f}")
    print(f"EUR free: {eur_free:.2f}")

    if btc_free <= 0.00000001:
        print("Non hai BTC da vendere. Stop.")
        return

    if not CONFIRM:
        print("\n!!! BLOCCATO !!!")
        print("Per vendere davvero, apri il file e metti CONFIRM = True")
        return

    # Cancel eventuali sell aperti (prudente)
    try:
        open_orders = ex.fetch_open_orders(SYMBOL)
        for o in open_orders:
            if o.get("side") == "sell":
                ex.cancel_order(o["id"], SYMBOL)
        print("Ordini SELL aperti cancellati (se presenti).")
    except Exception as e:
        print(f"Warning: non riesco a cancellare ordini aperti: {e}")

    # Vendi a mercato (più sicuro per “chiudi subito”)
    try:
        amt = ex.a(SYMBOL, btc_free)  # arrotonda alla precisione giusta
        order = ex.ex.create_order(SYMBOL, "market", "sell", amt)
        print("SELL MARKET inviato.")
        print(order)
    except Exception as e:
        print(f"ERRORE sell market: {e}")

if __name__ == "__main__":
    main()