import pandas as pd
import numpy as np


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Returns
    df["ret_1"] = df["close"].pct_change(1)
    df["ret_3"] = df["close"].pct_change(3)
    df["ret_6"] = df["close"].pct_change(6)

    # EMA
    df["ema_10"] = df["close"].ewm(span=10).mean()
    df["ema_30"] = df["close"].ewm(span=30).mean()

    df["ema_dist"] = (df["close"] - df["ema_30"]) / df["ema_30"]

    # Volatility
    df["volatility_10"] = df["ret_1"].rolling(10).std()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / (avg_loss + 1e-9)
    df["rsi"] = 100 - (100 / (1 + rs))

    df.dropna(inplace=True)

    return df


def create_target(df: pd.DataFrame, future_periods=12, threshold=0.005):
    df = df.copy()

    future_return = df["close"].shift(-future_periods) / df["close"] - 1

    df["target"] = (future_return > threshold).astype(int)

    df.dropna(inplace=True)

    return df