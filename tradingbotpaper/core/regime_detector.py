import pandas as pd
import numpy as np


def compute_atr(df, period=14):
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    return atr


def compute_adx(df, period=14):
    df = df.copy()

    plus_dm = df["high"].diff()
    minus_dm = df["low"].diff()

    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm > 0] = 0
    minus_dm = abs(minus_dm)

    tr = compute_atr(df, period)

    plus_di = 100 * (plus_dm.rolling(period).mean() / tr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / tr)

    dx = (abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)) * 100
    adx = dx.rolling(period).mean()

    return adx


def detect_regime(df):
    df = df.copy()

    df["atr"] = compute_atr(df)
    df["adx"] = compute_adx(df)

    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    latest = df.iloc[-1]

    adx = latest["adx"]
    atr = latest["atr"]
    price = latest["close"]

    volatility_ratio = atr / price

    # --- Regime logic ---
    if adx > 25:
        return "TREND"

    if adx < 20 and volatility_ratio < 0.01:
        return "RANGE"

    return "CHAOS"