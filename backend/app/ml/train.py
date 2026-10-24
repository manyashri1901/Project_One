"""Train the predictive model (scikit-learn), as specified in the report:
Random Forest and Gradient Boosting on per-minute summary rows.

  python -m app.ml.train summaries.csv --dry-pct 30 --horizon-min 360 --out model.joblib
  python -m app.ml.train --synthetic            # pipeline smoke test ONLY (fake data)
  python -m app.ml.train public.csv --row-minutes 15 --public-benchmark --dry-pct 30   # PUBLIC dataset (tools/prepare_public_dataset.py)

The label is "soil moisture will drop below --dry-pct within --horizon-min
minutes". The split is by TIME (first 70 % train, last 30 % test), never
shuffled. The script refuses to train on too little real data.
"""
import argparse
import sys

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from .features import FEATURES, add_labels, history_rows, prepare
from .synthetic import make_synthetic

MIN_ROWS_REAL = 2880          # about 2 days of one-minute rows
MIN_POSITIVES = 30


def evaluate(model, X, y, name):
    pred = model.predict(X)
    out = {"model": name,
           "accuracy": accuracy_score(y, pred),
           "precision": precision_score(y, pred, zero_division=0),
           "recall": recall_score(y, pred, zero_division=0),
           "f1": f1_score(y, pred, zero_division=0)}
    out["roc_auc"] = roc_auc_score(y, model.predict_proba(X)[:, 1]) if len(set(y)) == 2 else float("nan")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--synthetic", action="store_true", help="FAKE data, smoke test only")
    ap.add_argument("--dry-pct", type=float, default=30.0)
    ap.add_argument("--horizon-min", type=int, default=360, help="6 h; the report uses 6-24 h")
    ap.add_argument("--out", default=None)
    ap.add_argument("--row-minutes", type=float, default=1.0,
                    help="minutes between rows (1 for this device's summaries; larger for a coarser public dataset)")
    ap.add_argument("--public-benchmark", action="store_true",
                    help="data is a PUBLIC dataset, not this system's logs: the model is flagged and never used by the backend")
    a = ap.parse_args(argv)

    if a.synthetic:
        raw = make_synthetic()
        print("*" * 70, "\nSYNTHETIC DATA: this only tests the code. The numbers below mean "
              "nothing\nabout real soil, weather or your sensor. Do NOT put them in the paper.\n" + "*" * 70)
    elif a.csv:
        raw = pd.read_csv(a.csv)
    else:
        ap.error("give a summaries CSV, or --synthetic")

    d = prepare(raw, a.row_minutes)
    expected = a.row_minutes * 60
    gap = d["epoch"].diff().median() if (d["epoch"] > 0).any() else expected
    if gap and abs(gap - expected) > 0.1 * expected:
        print(f"WARNING: median row spacing is {gap:.0f} s but --row-minutes {a.row_minutes:g} means {expected:.0f} s; "
              f"windows are computed from --row-minutes.")
    data = add_labels(d, a.dry_pct, a.horizon_min, a.row_minutes)
    data = data.iloc[history_rows(a.row_minutes):]

    npos = int(data["label"].sum())
    print(f"rows usable={len(data)}  positives={npos}  negatives={len(data) - npos}")
    if not a.synthetic and (len(data) < MIN_ROWS_REAL or npos < MIN_POSITIVES or len(data) - npos < MIN_POSITIVES):
        sys.exit(f"Not enough real data to train a meaningful model (need >= {MIN_ROWS_REAL} usable rows "
                 f"and >= {MIN_POSITIVES} of each class). Keep logging and try again.")

    cut = int(len(data) * 0.7)
    train, test = data.iloc[:cut], data.iloc[cut:]
    Xtr, ytr, Xte, yte = train[FEATURES], train["label"].astype(int), test[FEATURES], test["label"].astype(int)
    if len(set(ytr)) < 2 or len(set(yte)) < 2:
        sys.exit("Train or test split has only one class; collect more varied data.")

    base_pred = (test["slope_60m"] < 0) & (test["soil_pct"] < a.dry_pct + 10)
    print(f"baseline (falling and within 10 points of dry): "
          f"precision={precision_score(yte, base_pred, zero_division=0):.2f} "
          f"recall={recall_score(yte, base_pred, zero_division=0):.2f}")

    models = {
        "RandomForest": RandomForestClassifier(n_estimators=200, min_samples_leaf=5, random_state=0, n_jobs=-1),
        "GradientBoosting": GradientBoostingClassifier(random_state=0),
    }
    results, fitted = [], {}
    for name, m in models.items():
        m.fit(Xtr, ytr)
        fitted[name] = m
        results.append(evaluate(m, Xte, yte, name))
    print(pd.DataFrame(results).round(3).to_string(index=False))

    best = max(results, key=lambda r: r["f1"])["model"]
    out = a.out or ("model_SYNTHETIC.joblib" if a.synthetic else
                    "model_PUBLIC_BENCHMARK.joblib" if a.public_benchmark else "model.joblib")
    if a.public_benchmark:
        print("NOTE: trained on a PUBLIC dataset. These scores describe that dataset only, NOT this system. "
              "The backend will not use this model.")
    joblib.dump({"model": fitted[best], "name": best, "features": FEATURES, "dry_pct": a.dry_pct,
                 "horizon_min": a.horizon_min, "synthetic": bool(a.synthetic),
                 "row_minutes": a.row_minutes,
                 "data_source": "synthetic" if a.synthetic else ("public_benchmark" if a.public_benchmark else "device"),
                 "train_rows": len(train), "test_rows": len(test), "metrics": results}, out)
    print(f"saved {best} to {out}")


if __name__ == "__main__":
    main()