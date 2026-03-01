from core.datafeed import DataFeed
from core.regime_detector import detect_regime
from core.strategy_trend import trend_signal
from core.strategy_range import range_signal

symbol = "BTC/EUR"
timeframe = "1h"
limit = 500

feed = DataFeed("kraken")
df = feed.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

regime = detect_regime(df)

if regime == "TREND":
    sig = trend_signal(df)
elif regime == "RANGE":
    sig = range_signal(df)
else:
    sig = None

print("Regime:", regime)
print("Signal:", sig)