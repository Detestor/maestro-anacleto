import pandas as pd
import numpy as np


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def range_signal(df):
    """
    Mean reversion semplice: compra quando RSI molto basso, vendi quando RSI molto alto.
    Usare SOLO quando regime = RANGE.
    """
    df = df.copy()
    df["rsi"] = compute_rsi(df["close"], 14)

    latest = df.iloc[-1]
    rsi = latest["rsi"]

    if rsi < 30:
        return "BUY"
    if rsi > 70:
        return "SELL"

    return None