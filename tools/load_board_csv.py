#!/usr/bin/env python3
"""
Load the REAL board's own log into the backend when live WiFi/MQTT is not working (fallback "Level 2").

On the board, type  DUMPS  in the Serial Monitor. It prints /summary.csv (one row per minute). Copy the text
(the header line and the data rows; other Serial text is ignored) into a file, e.g. board_summary.txt.

    python tools/load_board_csv.py board_summary.txt --device-id jalbhoomi-esp32-01
    python tools/load_board_csv.py board_summary.txt --print          # check the parsing, store nothing

The rows go through the same validation and storage as live MQTT messages, so the API, the app and the model
tools see them exactly as if the board had sent them. Duplicates are skipped, so running it twice is safe.

Time: if the board never had internet time (epoch 0), real clock times are unknown. The rows are then placed
in order, ending at --end-time (default: now), and this is printed as a warning. Say so in the report.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

HEADER = ("epoch,uptime_s,samples,soil_raw_mean,soil_raw_min,soil_raw_max,soil_pct_mean,"
          "temp_c_mean,hum_pct_mean,flow_pulses,flow_L,pump_on_s,faults,rain,water_added_ml").split(",")
INT_FIELDS = {"epoch", "uptime_s", "samples", "soil_raw_min", "soil_raw_max", "flow_pulses",
              "pump_on_s", "faults", "rain"}


def parse_summary_text(text: str, device_id: str, end_epoch: int | None = None):
    """Return (rows, warnings). Each row is a dict in the SummaryIn format. Lines that are not summary rows are ignored."""
    rows, warnings = [], []
    for line in text.splitlines():
        parts = [p.strip() for p in line.strip().split(",")]
        if len(parts) != len(HEADER) or parts[0] == "epoch" or not parts[0].lstrip("-").isdigit():
            continue
        m = {"device_id": device_id}
        try:
            for name, val in zip(HEADER, parts):
                if val == "" or val.lower() in ("nan", "null"):
                    continue                                  # unknown fields are omitted, never null
                m[name] = int(float(val)) if name in INT_FIELDS else float(val)
        except ValueError:
            warnings.append(f"skipped an unreadable line: {line[:60]!r}")
            continue
        rows.append(m)
    if not rows:
        return rows, ["no summary rows found; did you copy the output of DUMPS?"]
    if any(r.get("epoch", 0) <= 1_700_000_000 for r in rows):
        end = int(end_epoch if end_epoch is not None else time.time())
        last_up = max(r.get("uptime_s", 0) for r in rows)
        for r in rows:
            if r.get("epoch", 0) <= 1_700_000_000:
                r["epoch"] = end - (last_up - r.get("uptime_s", 0))
        warnings.append("The board had no internet time (epoch 0). Row times were rebuilt from uptime and end at "
                        f"{datetime.fromtimestamp(end, tz=timezone.utc).isoformat(timespec='seconds')}. "
                        "They are ordered and spaced correctly but the clock time is approximate.")
    return rows, warnings


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="text file with the DUMPS output")
    ap.add_argument("--device-id", default="jalbhoomi-esp32-01", help="must match DEVICE_ID in config.h")
    ap.add_argument("--end-time", help="when the dump was taken, ISO format, e.g. 2026-10-25T14:30:00+05:30 "
                                       "(only used if the board had no clock)")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()

    end_epoch = int(datetime.fromisoformat(a.end_time).timestamp()) if a.end_time else None
    text = Path(a.file).read_text(encoding="utf-8", errors="replace")
    rows, warnings = parse_summary_text(text, a.device_id, end_epoch)
    for w in warnings:
        print("WARNING:", w, file=sys.stderr)
    if not rows:
        sys.exit(1)
    print(f"parsed {len(rows)} summary rows for '{a.device_id}'")
    if a.print:
        for r in rows[:3]:
            print(json.dumps(r))
        return

    if "DATABASE_URL" not in os.environ:
        os.chdir(ROOT / "backend")
    from app import ingest, models  # noqa: F401
    from app.db import Base, engine
    Base.metadata.create_all(engine)
    topic = f"jalbhoomi/{a.device_id}/summary"
    stored = sum(1 for r in rows if ingest.handle_message(topic, json.dumps(r).encode()))
    print(f"stored {stored} of {len(rows)} rows (the rest were duplicates or failed validation)")
    print(f"Open http://localhost:8000/devices/{a.device_id}/summaries")


if __name__ == "__main__":
    main()