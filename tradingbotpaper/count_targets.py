import pandas as pd

df = pd.read_csv("dataset_btc.csv")
print(df["target"].value_counts())
print("ratio_1 =", df["target"].mean())