import numpy as np

def trend_signal(df):
    df = df.copy()

    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    df["ema200"] = df["close"].ewm(span=200).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # Condizioni trend long
    if latest["ema50"] > latest["ema200"]:

        # prezzo sopra ema50
        if latest["close"] > latest["ema50"]:

            # pullback vicino ema20 (entro 0.3%)
            distance = abs(latest["close"] - latest["ema20"]) / latest["close"]

            if distance < 0.003:

                # breakout massimo candela precedente
                if latest["close"] > prev["high"]:
                    return "BUY"

    return None