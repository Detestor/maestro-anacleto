from core.liveexecutor import KrakenExecutor

SYMBOL = "BTC/EUR"

def main():
    ex = KrakenExecutor()

    bal = ex.fetch_balance()
    eur_free = float(bal.get("free", {}).get("EUR", 0.0))
    eur_total = float(bal.get("total", {}).get("EUR", 0.0))
    btc_free = float(bal.get("free", {}).get("BTC", 0.0))
    btc_total = float(bal.get("total", {}).get("BTC", 0.0))

    ticker = ex.fetch_ticker(SYMBOL)
    last = float(ticker.get("last") or 0.0)
    bid = float(ticker.get("bid") or 0.0)
    ask = float(ticker.get("ask") or 0.0)

    equity_est = eur_free + btc_free * last

    print("=== KRAKEN CHECK ===")
    print(f"SYMBOL: {SYMBOL}")
    print(f"PRICE last/bid/ask: {last:.2f} / {bid:.2f} / {ask:.2f}")
    print("")
    print("=== BALANCES ===")
    print(f"EUR free/total: {eur_free:.2f} / {eur_total:.2f}")
    print(f"BTC free/total: {btc_free:.8f} / {btc_total:.8f}")
    print(f"Equity stimata (EUR free + BTC*last): {equity_est:.2f}")
    print("")
    print("=== OPEN ORDERS ===")
    try:
        orders = ex.fetch_open_orders(SYMBOL)
        if not orders:
            print("Nessun ordine aperto su BTC/EUR.")
        else:
            for o in orders:
                oid = o.get("id")
                side = o.get("side")
                otype = o.get("type")
                price = o.get("price")
                amount = o.get("amount")
                status = o.get("status")
                print(f"- id={oid} side={side} type={otype} price={price} amount={amount} status={status}")
    except Exception as e:
        print(f"Errore fetch_open_orders: {e}")

if __name__ == "__main__":
    main()