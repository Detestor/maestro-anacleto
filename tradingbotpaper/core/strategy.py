from __future__ import annotations
import pandas as pd

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

class EmaCrossStrategy:
    def __init__(self, fast: int, slow: int):
        self.fast = fast
        self.slow = slow

    def signal(self, df: pd.DataFrame) -> str:
        # ritorna: "buy", "sell", "hold"
        close = df["close"]
        f = ema(close, self.fast)
        s = ema(close, self.slow)

        if len(df) < max(self.fast, self.slow) + 5:
            return "hold"

        prev = (f.iloc[-2] - s.iloc[-2])
        now  = (f.iloc[-1] - s.iloc[-1])

        if prev <= 0 and now > 0:
            return "buy"
        if prev >= 0 and now < 0:
            return "sell"
        return "hold"