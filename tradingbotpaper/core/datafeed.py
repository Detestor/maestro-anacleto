from __future__ import annotations
import ccxt
import pandas as pd

class DataFeed:
    def __init__(self, exchange_name: str):
        ex_class = getattr(ccxt, exchange_name)
        self.ex = ex_class({"enableRateLimit": True})

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        ohlcv = self.ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df