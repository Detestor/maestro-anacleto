import pandas as pd
import numpy as np

from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, precision_recall_fscore_support
from sklearn.preprocessing import StandardScaler


df = pd.read_csv("dataset_btc.csv")

features = [
    "ret_1",
    "ret_3",
    "ret_6",
    "ema_dist",
    "volatility_10",
    "rsi",
]

X = df[features]
y = df["target"]

# Split temporale (no shuffle)
split_index = int(len(df) * 0.7)
X_train = X.iloc[:split_index]
X_test = X.iloc[split_index:]
y_train = y.iloc[:split_index]
y_test = y.iloc[split_index:]

scaler = StandardScaler()
X_train_s = scaler.fit_transform(X_train)
X_test_s = scaler.transform(X_test)

# Modello più "sensibile" ai 1
model = LogisticRegression(class_weight="balanced", max_iter=1000)
model.fit(X_train_s, y_train)

probs = model.predict_proba(X_test_s)[:, 1]

print("Prob media classe 1:", np.mean(probs))
print("Prob min/max:", np.min(probs), np.max(probs))
print("Support test: n=", len(y_test), " | ones=", int(y_test.sum()), "zeros=", int((y_test==0).sum()))
print()

# Scan soglie
candidates = np.arange(0.10, 0.91, 0.05)
rows = []
for th in candidates:
    y_pred = (probs >= th).astype(int)
    p, r, f1, _ = precision_recall_fscore_support(y_test, y_pred, average=None, labels=[0,1], zero_division=0)
    # p/r/f1 sono array per classe 0 e 1
    rows.append((th, p[1], r[1], f1[1], int(y_pred.sum())))

print("THRESH | Prec(1) | Rec(1) | F1(1) | Pred_1_count")
for th, p1, r1, f1_1, pred1 in rows:
    print(f"{th:>5.2f} | {p1:>7.3f} | {r1:>6.3f} | {f1_1:>5.3f} | {pred1:>12d}")

# Scegli soglia con F1(1) massimo
best = max(rows, key=lambda x: x[3])
best_th = best[0]
print("\nBest threshold by F1(1):", best_th)

y_best = (probs >= best_th).astype(int)
print("\n=== Classification report @ best_th ===")
print(classification_report(y_test, y_best, zero_division=0))