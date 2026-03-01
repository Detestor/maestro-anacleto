from __future__ import annotations

import os
import ccxt
from dotenv import load_dotenv


class KrakenExecutor:
    """
    Esecuzione ordini SPOT su Kraken via ccxt.
    Workaround stop orders: su Kraken/ccxt a volte il parametro 'price' viene omesso
    -> passiamo 'price' anche in params, e usiamo stop-loss-limit (trigger + limit).
    """

    def __init__(self):
        load_dotenv()
        api_key = os.getenv("KRAKEN_API_KEY", "").strip()
        secret = os.getenv("KRAKEN_SECRET", "").strip()
        if not api_key or not secret:
            raise ValueError("API Key o Secret mancanti nel file .env (KRAKEN_API_KEY / KRAKEN_SECRET)")

        self.ex = ccxt.kraken({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        })

    # ---------- Read ----------
    def fetch_balance(self) -> dict:
        return self.ex.fetch_balance()

    def fetch_ticker(self, symbol: str) -> dict:
        return self.ex.fetch_ticker(symbol)

    def fetch_open_orders(self, symbol: str) -> list[dict]:
        return self.ex.fetch_open_orders(symbol)

    def cancel_order(self, order_id: str, symbol: str):
        return self.ex.cancel_order(order_id, symbol)

    # ---------- Precision (strings) ----------
    def p(self, symbol: str, price: float) -> str:
        return self.ex.price_to_precision(symbol, price)

    def a(self, symbol: str, amount: float) -> str:
        return self.ex.amount_to_precision(symbol, amount)

    # ---------- Orders ----------
    def create_market_buy(self, symbol: str, amount_btc: float) -> dict:
        amt = self.a(symbol, amount_btc)
        return self.ex.create_order(symbol, "market", "buy", amt)

    def create_market_sell(self, symbol: str, amount_btc: float) -> dict:
        amt = self.a(symbol, amount_btc)
        return self.ex.create_order(symbol, "market", "sell", amt)

    def create_limit_sell(self, symbol: str, amount_btc: float, price_eur: float) -> dict:
        amt = self.a(symbol, amount_btc)
        price = self.p(symbol, price_eur)
        return self.ex.create_order(symbol, "limit", "sell", amt, price)

    def create_stop_loss_sell(self, symbol: str, amount_btc: float, stop_price_eur: float) -> dict:
        """
        STOP LOSS robusto su Kraken SPOT:
        Usiamo stop-loss-limit:
          - price  = trigger (stop)
          - price2 = limit price (leggermente peggiore, per aumentare chance di fill)
        e passiamo price/price2 anche nei params (workaround ccxt/Kraken).
        """
        amt = self.a(symbol, amount_btc)

        trigger = float(stop_price_eur)
        # limit un filo più basso del trigger (sell) per aumentare la probabilità di esecuzione
        limit_exec = trigger * 0.999

        trigger_s = self.p(symbol, trigger)
        limit_s = self.p(symbol, limit_exec)

        params = {
            # spesso Kraken richiede l'accettazione dell'accordo trading via API
            "trading_agreement": "agree",
            # workaround: ripassiamo price/price2 anche qui
            "price": trigger_s,
            "price2": limit_s,
        }

        # Nota: su Kraken per stop-loss-limit il price argomento può venire ignorato/omesso da ccxt,
        # perciò lo ribadiamo nei params.
        return self.ex.create_order(
            symbol,
            "stop-loss-limit",
            "sell",
            amt,
            trigger_s,
            params=params,
        )