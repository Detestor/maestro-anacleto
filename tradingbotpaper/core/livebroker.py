from __future__ import annotations

import os
import ccxt
from dotenv import load_dotenv


class KrakenLive:
    def __init__(self):
        load_dotenv()

        api_key = os.getenv("KRAKEN_API_KEY", "").strip()
        secret = os.getenv("KRAKEN_SECRET", "").strip()

        if not api_key or not secret:
            raise ValueError("API Key o Secret mancanti nel file .env")

        # Debug sicuro: solo lunghezze, non stampa le chiavi
        print(f"API key length: {len(api_key)} | Secret length: {len(secret)}")

        self.exchange = ccxt.kraken({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
        })

    def get_balance(self) -> dict:
        return self.exchange.fetch_balance()

    def get_ticker(self, symbol: str) -> dict:
        return self.exchange.fetch_ticker(symbol)