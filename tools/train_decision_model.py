#!/usr/bin/env python3
"""
Train and evaluate an "irrigate now: yes / no" model on a PUBLIC dataset (no hardware needed).

    python tools/train_decision_model.py DATASET.csv --name amritpal --source "<where the dataset came from>"

What it does
  * finds the label column (a column whose name contains pump / irrigation / motor, values 0/1)
  * uses every other column as a feature (text columns such as CropType are one-hot encoded)
  * stratified 70/30 train/test split with a fixed seed, plus 5-fold cross-validation on the training part
  * trains Random Forest and Gradient Boosting (as in the report) and compares them with a
    "always predict the majority class" baseline
  * writes ml/results/decision_<name>.json (metrics, confusion matrix, sample predictions)
    and ml/results/decision_<name>_PUBLIC_BENCHMARK.joblib

What the numbers mean
  * They describe THAT dataset only. They say nothing about the JalBhoomi sensor or about soil in your field.
  * The rows of these datasets are not a time series, so this is NOT the "will the soil dry out in the next
    6-24 h" model; it is a same-moment irrigation decision.
  * If the model is no better than the baseline, the script says so. Report that honestly; do not hide it.
  * The backend never uses this model to advise a real device (it is flagged public_benchmark).
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import (accuracy_score, confusion_matrix, f1_score, precision_score,
                             recall_score, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split

ROOT = Path(__file__).resolve().parents[1]
TARGET_WORDS = ("pump", "irrigation", "motor")


def find_target(df: pd.DataFrame, forced: str | None) -> str:
    if forced:
        if forced not in df.columns:
            sys.exit(f"--target {forced!r} is not a column. Columns: {list(df.columns)}")
        return forced
    for c in df.columns:
        if any(w in str(c).lower() for w in TARGET_WORDS):
            return c
    sys.exit(f"Could not find the label column automatically. Use --target. Columns: {list(df.columns)}")


def metrics(y_true, pred, proba) -> dict:
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "accuracy": round(float(accuracy_score(y_true, pred)), 4),
        "precision": round(float(precision_score(y_true, pred, zero_division=0)), 4),
        "recall": round(float(recall_score(y_true, pred, zero_division=0)), 4),
        "f1": round(float(f1_score(y_true, pred, zero_division=0)), 4),
        "roc_auc": round(float(roc_auc_score(y_true, proba)), 4) if len(set(y_true)) == 2 else None,
        "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--name", required=True, help="short id for the output files, e.g. amritpal")
    ap.add_argument("--source", default="", help="where the dataset came from (author, site, DOI/URL, licence)")
    ap.add_argument("--target", default=None, help="label column (auto-detected if omitted)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", default=str(ROOT / "ml" / "results"))
    a = ap.parse_args(argv)

    raw = pd.read_csv(a.csv)
    target = find_target(raw, a.target)
    y_all = pd.to_numeric(raw[target], errors="coerce")
    ok = y_all.isin([0, 1])
    if ok.sum() < 50:
        sys.exit(f"Label column {target!r} must hold 0/1 values; only {int(ok.sum())} usable rows.")
    df = raw.loc[ok].copy()
    y = y_all.loc[ok].astype(int)
    X = pd.get_dummies(df.drop(columns=[target]), dtype=float)
    X = X.apply(pd.to_numeric, errors="coerce")
    keep = X.notna().all(axis=1)
    X, y = X.loc[keep], y.loc[keep]
    features = list(X.columns)
    n_dup = int(pd.concat([X, y], axis=1).duplicated().sum())

    counts = y.value_counts().to_dict()
    print(f"dataset={Path(a.csv).name}  rows={len(X)}  label={target!r}  "
          f"class0={counts.get(0, 0)}  class1={counts.get(1, 0)}  duplicate rows={n_dup}")
    print(f"features ({len(features)}): {features}")
    if min(counts.get(0, 0), counts.get(1, 0)) < 25:
        sys.exit("One class has fewer than 25 rows; this dataset cannot be evaluated meaningfully.")

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.30, stratify=y, random_state=a.seed)
    majority = int(ytr.mode().iloc[0])
    base_pred = np.full(len(yte), majority)
    base = metrics(yte, base_pred, np.full(len(yte), float(majority)))
    base["description"] = f"always predict class {majority} (the majority class in the training part)"

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=a.seed)
    models = {
        "RandomForest": RandomForestClassifier(n_estimators=200, min_samples_leaf=3, random_state=a.seed, n_jobs=-1),
        "GradientBoosting": GradientBoostingClassifier(random_state=a.seed),
    }
    results, fitted = [], {}
    for name, m in models.items():
        cvf1 = cross_val_score(m, Xtr, ytr, cv=cv, scoring="f1")
        m.fit(Xtr, ytr)
        fitted[name] = m
        r = {"model": name,
             "cv_f1_mean": round(float(cvf1.mean()), 4), "cv_f1_std": round(float(cvf1.std()), 4)}
        r.update(metrics(yte, m.predict(Xte), m.predict_proba(Xte)[:, 1]))
        results.append(r)

    best = max(results, key=lambda r: (r["roc_auc"] or 0, r["f1"]))
    bm = fitted[best["model"]]
    imp = sorted(zip(features, bm.feature_importances_), key=lambda t: -t[1])
    importances = [{"feature": f, "importance": round(float(v), 4)} for f, v in imp[:10]]

    auc = best["roc_auc"] or 0.5
    if auc < 0.60:
        verdict = ("WEAK: the best model's ROC-AUC is below 0.60 (0.50 = guessing). These features do not "
                   "predict this label in this dataset. Do not present it as a working model.")
    elif auc < 0.75:
        verdict = "MODERATE: some signal, but limited. Report the numbers as they are."
    else:
        verdict = "CLEAR SIGNAL on this dataset. It still says nothing about the JalBhoomi sensor."

    proba = bm.predict_proba(Xte)[:, 1]
    sample = []
    for i in range(min(15, len(Xte))):
        row = {k: round(float(v), 3) for k, v in Xte.iloc[i].items()}
        row.update({"actual": int(yte.iloc[i]), "predicted": int(proba[i] >= 0.5),
                    "probability_irrigate": round(float(proba[i]), 3)})
        sample.append(row)

    out = {
        "name": a.name,
        "task": "irrigate-now classifier (same-moment decision, not a 6-24 h forecast)",
        "data_source": "public_benchmark",
        "dataset_file": Path(a.csv).name,
        "dataset_source": a.source or "NOT SPECIFIED: add --source with author, site and licence before citing",
        "label_column": target,
        "features": features,
        "rows": int(len(X)),
        "class_counts": {"0": int(counts.get(0, 0)), "1": int(counts.get(1, 0))},
        "duplicate_rows": n_dup,
        "split": f"stratified 70/30, seed {a.seed}; train={len(Xtr)}, test={len(Xte)}; 5-fold CV on the training part",
        "baseline": base,
        "models": results,
        "best_model": best["model"],
        "top_features": importances,
        "verdict": verdict,
        "caveats": [
            "Public dataset, not collected with the JalBhoomi hardware: says nothing about its accuracy.",
            "Rows are not a time series, so this is not the 6-24 h prediction described in the report.",
            "The backend never uses this model to advise a real device.",
        ],
        "sample_predictions": sample,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if n_dup:
        out["caveats"].append(f"{n_dup} duplicate rows exist; identical rows can fall in both train and test and "
                              "make scores look better than they are.")

    od = Path(a.out_dir)
    od.mkdir(parents=True, exist_ok=True)
    jpath = od / f"decision_{a.name}.json"
    mpath = od / f"decision_{a.name}_PUBLIC_BENCHMARK.joblib"
    jpath.write_text(json.dumps(out, indent=2), encoding="utf-8")
    joblib.dump({"model": bm, "name": best["model"], "features": features, "target": target,
                 "data_source": "public_benchmark", "metrics": results}, mpath)

    print("\nBaseline (majority class):", {k: base[k] for k in ("accuracy", "precision", "recall", "f1")})
    print(pd.DataFrame([{k: r[k] for k in ("model", "accuracy", "precision", "recall", "f1", "roc_auc",
                                            "cv_f1_mean", "cv_f1_std")} for r in results]).to_string(index=False))
    print("best:", best["model"], "confusion matrix:", best["confusion_matrix"])
    print("top features:", importances[:5])
    print("\nVERDICT:", verdict)
    print(f"saved {jpath}\nsaved {mpath}")
    return out


if __name__ == "__main__":
    main()