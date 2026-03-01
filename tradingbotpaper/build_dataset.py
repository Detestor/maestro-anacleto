import pandas as pd
from core.datafeed import DataFeed
from core.feature_engineering import compute_features, create_target

symbol = "BTC/EUR"
timeframe = "5m"
limit = 2000

feed = DataFeed("kraken")

print("Scarico dati storici...")
df = feed.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

print("Calcolo feature...")
df_feat = compute_features(df)

print("Creo target...")
df_final = create_target(df_feat)

print("Dataset pronto:", df_final.shape)

df_final.to_csv("dataset_btc.csv", index=False)

print("Salvato dataset_btc.csv")