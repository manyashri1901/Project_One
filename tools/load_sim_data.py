#!/usr/bin/env python3
"""
Load SIMULATED device data straight into the backend database (no MQTT broker needed),
so the REST API, /docs and the Android app have something to show.

  *** ALL DATA IS INVENTED. For demos and testing only. Never use it as a result. ***

Run it from anywhere; it uses the same database file the backend uses when started from backend/
(default sqlite:///./jalbhoomi_dev.db), or DATABASE_URL if you set it.

    python tools/load_sim_data.py                                  # 2 hours, normal scenario
    python tools/load_sim_data.py --scenario fault_no_flow --device-id sim-esp32-02 --minutes 30
Scenarios: normal | rain_skip | fault_no_flow | stuck_open | experiment
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))
if "DATABASE_URL" not in os.environ:
    os.chdir(ROOT / "backend")                 # same relative SQLite file as `uvicorn` run from backend/

from app import ingest, models  # noqa: E402,F401
from app.db import Base, engine  # noqa: E402
from simulate_device import SimDevice  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--device-id", default="sim-esp32-01")
ap.add_argument("--scenario", default="normal",
                choices=["normal", "rain_skip", "fault_no_flow", "stuck_open", "experiment"])
ap.add_argument("--minutes", type=int, default=120, help="how many simulated minutes ending now")
a = ap.parse_args()

Base.metadata.create_all(engine)
dev = SimDevice(a.device_id, a.scenario, epoch0=int(time.time()) - a.minutes * 60)
stored = 0
for _ in range(a.minutes * 60):
    for topic, payload in dev.tick(1.0):
        if ingest.handle_message(topic, payload.encode()):
            stored += 1
print(f"SIMULATED data: stored {stored} messages for '{a.device_id}' ({a.scenario}, {a.minutes} min)")
print("Open http://localhost:8000/zones and http://localhost:8000/devices/%s/summaries" % a.device_id)