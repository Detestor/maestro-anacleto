from core.datafeed import DataFeed
from core.strategy_trend import trend_signal

symbol = "BTC/EUR"
timeframe = "1h"

feed = DataFeed("kraken")
df = feed.fetch_ohlcv(symbol, timeframe=timeframe, limit=500)

signal = trend_signal(df)

print("Segnale trend:", signal)