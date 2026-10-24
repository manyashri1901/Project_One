#!/usr/bin/env python3
"""Export the stored per-minute summary rows to a CSV that backend/app/ml/train.py can read.

    cd backend && python ../tools/export_summaries_csv.py --device jalbhoomi-esp32-01 --out summaries.csv

Uses DATABASE_URL (default: the local SQLite dev file). Rows whose device clock was not synced
(ts_from_device = 0) get epoch 0 and are ordered by arrival time.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Summary  # noqa: E402

COLS = ["epoch", "soil_pct_mean", "temp_c_mean", "hum_pct_mean", "rain", "pump_on_s", "flow_l", "faults"]

ap = argparse.ArgumentParser()
ap.add_argument("--device", required=True)
ap.add_argument("--out", default="summaries.csv")
a = ap.parse_args()

with SessionLocal() as db, open(a.out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(COLS)
    n = 0
    for r in db.scalars(select(Summary).where(Summary.device_id == a.device).order_by(Summary.ts)):
        epoch = int(r.ts.timestamp()) if r.ts_from_device else 0
        w.writerow([epoch, r.soil_pct_mean, r.temp_c_mean, r.hum_pct_mean, r.rain, r.pump_on_s, r.flow_l, r.faults])
        n += 1
print(f"wrote {n} rows to {a.out}")
