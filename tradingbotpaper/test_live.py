from core.livebroker import KrakenLive

def main():
    broker = KrakenLive()

    print("Connessione a Kraken...")

    balance = broker.get_balance()

    # Saldo EUR disponibile
    eur_free = balance.get("free", {}).get("EUR", 0)
    eur_total = balance.get("total", {}).get("EUR", 0)

    print(f"\nSaldo EUR FREE : {eur_free}")
    print(f"Saldo EUR TOTAL: {eur_total}")

    # Se vuoi vedere TUTTE le valute non-zero (utile se compare qualcosa di strano)
    print("\n=== BALANCE (non-zero TOTAL) ===")
    totals = balance.get("total", {})
    for k, v in totals.items():
        try:
            if v and float(v) != 0.0:
                print(f"TOTAL {k}: {v}")
        except Exception:
            pass

    print("\n=== BALANCE (non-zero FREE) ===")
    free = balance.get("free", {})
    for k, v in free.items():
        try:
            if v and float(v) != 0.0:
                print(f"FREE  {k}: {v}")
        except Exception:
            pass

    # Ticker BTC/EUR
    ticker = broker.get_ticker("BTC/EUR")
    last = ticker.get("last")
    bid = ticker.get("bid")
    ask = ticker.get("ask")

    print("\n=== TICKER BTC/EUR ===")
    print(f"LAST: {last}")
    print(f"BID : {bid}")
    print(f"ASK : {ask}")

if __name__ == "__main__":
    main()