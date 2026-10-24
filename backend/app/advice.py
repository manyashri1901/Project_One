"""Loads the trained model and turns recent summary rows into advice."""
import os
from functools import lru_cache

import joblib
import pandas as pd

from .ml.features import FEATURES, MIN_HISTORY_ROWS, prepare

MODEL_PATH = os.getenv("ML_MODEL_PATH", "model.joblib")
ALLOW_SYNTHETIC = os.getenv("ML_ALLOW_SYNTHETIC", "false").lower() in ("1", "true", "yes")
HISTORY_ROWS_NEEDED = MIN_HISTORY_ROWS


@lru_cache(maxsize=1)
def _load(path: str):
    return joblib.load(path) if os.path.exists(path) else None


def advise(rows: pd.DataFrame) -> dict:
    """rows: recent summary rows, oldest first (at least ~61 for the slope features)."""
    bundle = _load(MODEL_PATH)
    if bundle is None:
        return {"model_loaded": False, "reason": "no trained model file found"}
    if bundle["synthetic"] and not ALLOW_SYNTHETIC:
        return {"model_loaded": False, "reason": "model was trained on synthetic data"}
    if bundle.get("data_source") == "public_benchmark":
        return {"model_loaded": False,
                "reason": "model was trained on a public benchmark dataset, not on this system's own logs"}
    if float(bundle.get("row_minutes", 1.0)) != 1.0:
        return {"model_loaded": False, "reason": "model expects a different row spacing than the device's 1 minute"}
    if len(rows) < HISTORY_ROWS_NEEDED:
        return {"model_loaded": True, "ready": False,
                "reason": f"need at least {HISTORY_ROWS_NEEDED} recent rows, have {len(rows)}"}
    d = prepare(rows)
    last = d.iloc[[-1]][FEATURES]
    if last.isna().any(axis=None):
        return {"model_loaded": True, "ready": False, "reason": "missing sensor values in recent rows"}
    p = float(bundle["model"].predict_proba(last)[:, 1][0])
    return {"model_loaded": True, "ready": True, "model": bundle["name"],
            "horizon_min": bundle["horizon_min"], "dry_pct": bundle["dry_pct"],
            "probability_dry_within_horizon": round(p, 3),
            "note": "Advisory only. Trained on this project's own logs; not validated."}