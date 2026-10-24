#!/usr/bin/env python3
"""
Replay a PUBLIC dataset as if it were a live JalBhoomi board, so the WHOLE software chain
(backend -> database -> REST API -> app screens) can be shown and tested without hardware.

    python tools/replay_dataset.py DATASET.csv                      # straight into the backend database
    python tools/replay_dataset.py DATASET.csv --host localhost --port 1883 --no-tls   # through a real MQTT broker

Each dataset row becomes one per-minute "summary" message in exactly the format the ESP32 sends
(docs/INTEGRATION_CONTRACT.md). The device is named dataset-replay-01 so it can never be mistaken for a real board.

  *** The values come from someone else's dataset. For demos and software testing only. ***
  *** Never present them as measurements made with the JalBhoomi hardware. ***

Columns are found by name (soil / moist, temp, hum, pump / motor / irrigation). The dataset has no flow sensor,
so flow fields are left out. Soil values that are not already 0-100 are rescaled to 0-100 (dataset min -> 0,
max -> 100). If higher raw values mean DRY soil in your dataset, add --invert.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def pick(cols, words):
    for c in cols:
        if any(w in str(c).lower() for w in words):
            return c
    return None


def build_messages(df: pd.DataFrame, device_id: str, soil_col, temp_col, hum_col, pump_col,
                   invert: bool, step_s: int, end_epoch: int):
    """Return a list of (topic, json_string) summary messages, oldest first."""
    soil = pd.to_numeric(df[soil_col], errors="coerce")
    lo, hi = float(soil.min()), float(soil.max())
    already_pct = lo >= 0 and hi <= 100
    n = len(df)
    msgs = []
    for i in range(n):
        s = soil.iloc[i]
        if pd.isna(s):
            continue
        if already_pct:
            pct = float(s)
        else:
            pct = (float(s) - lo) / (hi - lo) * 100.0 if hi > lo else 50.0
        if invert:
            pct = 100.0 - pct
        pct = max(0.0, min(100.0, pct))
        m = {"device_id": device_id,
             "epoch": int(end_epoch - (n - 1 - i) * step_s),
             "uptime_s": int(i * step_s),
             "samples": int(step_s),
             "soil_raw_mean": round(float(s), 2),
             "soil_pct_mean": round(pct, 1)}
        if temp_col:
            t = pd.to_numeric(df[temp_col].iloc[i], errors="coerce")
            if not pd.isna(t):
                m["temp_c_mean"] = round(float(t), 1)
        if hum_col:
            h = pd.to_numeric(df[hum_col].iloc[i], errors="coerce")
            if not pd.isna(h) and 0 <= h <= 100:
                m["hum_pct_mean"] = round(float(h), 1)
        if pump_col:
            p = pd.to_numeric(df[pump_col].iloc[i], errors="coerce")
            if not pd.isna(p):
                m["pump_on_s"] = int(step_s) if p >= 1 else 0
        m["faults"] = 0
        msgs.append((f"jalbhoomi/{device_id}/summary", json.dumps(m)))
    return msgs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--device-id", default="dataset-replay-01")
    ap.add_argument("--rows", type=int, default=1440, help="how many rows to replay (default 1440 = one day at 60 s)")
    ap.add_argument("--start", type=int, default=0, help="first row to use")
    ap.add_argument("--step-s", type=int, default=60, help="seconds between replayed rows")
    ap.add_argument("--invert", action="store_true", help="higher raw soil value = DRIER soil")
    ap.add_argument("--soil-col"); ap.add_argument("--temp-col"); ap.add_argument("--hum-col"); ap.add_argument("--pump-col")
    ap.add_argument("--print", action="store_true", help="only print the first messages, store nothing")
    ap.add_argument("--host", help="publish through this MQTT broker instead of writing to the database")
    ap.add_argument("--port", type=int, default=8883)
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--user", default=""); ap.add_argument("--password", default="")
    ap.add_argument("--ca", default="")
    a = ap.parse_args()

    df = pd.read_csv(a.csv).iloc[a.start:a.start + a.rows].reset_index(drop=True)
    cols = list(df.columns)
    soil = a.soil_col or pick(cols, ["soil", "moist"])
    temp = a.temp_col or pick(cols, ["temp"])
    hum = a.hum_col or pick(cols, ["hum"])
    pump = a.pump_col or pick(cols, ["pump", "motor", "irrigation"])
    if not soil:
        sys.exit(f"No soil-moisture column found. Use --soil-col. Columns: {cols}")
    print(f"columns used -> soil={soil!r} temp={temp!r} humidity={hum!r} pump={pump!r}  (rows: {len(df)})")

    msgs = build_messages(df, a.device_id, soil, temp, hum, pump, a.invert, a.step_s, int(time.time()))
    if not msgs:
        sys.exit("No usable rows.")

    sys.stderr.write("DATASET REPLAY: values come from a public dataset, not from the JalBhoomi hardware.\n")
    if a.print:
        for t, p in msgs[:5]:
            print(t, p)
        print(f"... {len(msgs)} messages in total")
        return

    if a.host:
        import ssl
        import paho.mqtt.client as mqtt
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=a.device_id)
        if a.user:
            c.username_pw_set(a.user, a.password)
        if not a.no_tls:
            c.tls_set(ca_certs=a.ca or None, cert_reqs=ssl.CERT_REQUIRED)
        c.connect(a.host, a.port, 30)
        c.loop_start()
        for t, p in msgs:
            c.publish(t, p, qos=1).wait_for_publish()
        c.loop_stop()
        c.disconnect()
        print(f"published {len(msgs)} summaries through {a.host}:{a.port}")
        return

    if "DATABASE_URL" not in os.environ:
        os.chdir(ROOT / "backend")          # same SQLite file that `python -m uvicorn app.main:app` uses from backend/
    from app import ingest, models  # noqa: F401
    from app.db import Base, engine
    Base.metadata.create_all(engine)
    stored = sum(1 for t, p in msgs if ingest.handle_message(t, p.encode()))
    print(f"stored {stored} of {len(msgs)} summaries for '{a.device_id}' (duplicates are skipped)")
    print(f"Open http://localhost:8000/zones  and  http://localhost:8000/devices/{a.device_id}/summaries")


if __name__ == "__main__":
    main()