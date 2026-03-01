from core.datafeed import DataFeed
from core.regime_detector import detect_regime

symbol = "BTC/EUR"
timeframe = "1h"
limit = 500

feed = DataFeed("kraken")
df = feed.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

regime = detect_regime(df)

print("Regime attuale:", regime)