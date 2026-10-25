"""Live demo simulator: plays the ESP32 INSIDE the backend so the dashboard and the Android app can be shown
working end to end (readings arriving, Start/Stop/Auto commands accepted or refused) without any hardware.

  *** EVERYTHING THIS PRODUCES IS SIMULATED. Never quote it as a result. ***

Design rules
  * OFF by default (LIVE_SIM_ENABLED=false). When it is off nothing in this file runs and the backend behaves
    exactly as before.
  * It has its own device id (LIVE_SIM_DEVICE_ID, default "sim-live-01"). It never touches the id of a real
    board, so a real ESP32 and the simulator can run side by side. Commands for any other device id go the
    normal MQTT way.
  * It reuses tools/simulate_device.py (the same fake board the command-line simulator uses, with the same
    command checks and refusal reasons as the firmware) and feeds its messages through the normal ingest code
    (ingest.handle_message), so every row, alert and command acknowledgement is stored exactly like real data.
  * Time is accelerated: LIVE_SIM_SPEED simulated seconds pass per real second (default 30, so one
    one-minute summary every 2 seconds). Row timestamps are the real time at which the row was produced.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from . import config, consolidate, ingest
from . import db as dbmod
from .models import Device, Summary, UsageLimit

log = logging.getLogger("jalbhoomi.livesim")

SCENARIOS = ("normal", "rain_skip", "fault_no_flow", "stuck_open")
BACKFILL_S = 6 * 3600           # history simulated once, at the first start, so the charts are not empty
_TOOLS_FILE = Path(__file__).resolve().parents[2] / "tools" / "simulate_device.py"

_lock = threading.RLock()
_dev = None                     # the SimDevice that is running, or None
_thread: threading.Thread | None = None
_stop: threading.Event | None = None
_scenario = "normal"
_module = None


def _sim_module():
    """Load tools/simulate_device.py by path (the tools folder is not a Python package)."""
    global _module
    if _module is None:
        if not _TOOLS_FILE.exists():
            raise RuntimeError(f"{_TOOLS_FILE} not found: the live simulator needs the repository's tools folder")
        spec = importlib.util.spec_from_file_location("jalbhoomi_simulate_device", _TOOLS_FILE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _module = mod
    return _module


def _publish(messages, live_epoch: int | None = None, only_summaries: bool = False) -> None:
    """Hand simulated messages to the normal ingest path. live_epoch replaces the device clock with the real
    time, so the rows show up 'now' on the dashboard."""
    for topic, payload in messages:
        leaf = topic.rsplit("/", 1)[-1]
        if only_summaries and leaf != "summary":
            continue
        if live_epoch is not None and leaf in ("summary", "event"):
            doc = json.loads(payload)
            doc["epoch"] = live_epoch
            payload = json.dumps(doc)
        try:
            ingest.handle_message(topic, payload.encode())
        except Exception:                                   # never let one bad row stop the demo
            log.exception("live simulator: could not store a %s message", leaf)


def _rows_for(device_id: str) -> tuple[int, float | None]:
    with dbmod.SessionLocal() as db:
        n = db.execute(select(func.count()).select_from(Summary).where(Summary.device_id == device_id)).scalar() or 0
        last = db.execute(select(Summary.soil_pct_mean).where(Summary.device_id == device_id)
                          .order_by(Summary.ts.desc()).limit(1)).scalar()
    return int(n), last


def _make_device(scenario: str):
    """Build the fake board. On the very first start it first simulates BACKFILL_S seconds of history (summaries
    only) so the dashboard has something to show; later starts continue from the last stored soil value."""
    sim = _sim_module()
    device_id = config.LIVE_SIM_DEVICE_ID
    now = int(time.time())
    n_rows, last_soil = _rows_for(device_id)
    soil = last_soil
    if n_rows < 30:
        hist = sim.SimDevice(device_id, "normal", epoch0=now - BACKFILL_S, seed=7)
        for _ in range(BACKFILL_S):
            _publish(hist.tick(1.0), only_summaries=True)
        soil = hist.soil
    dev = sim.SimDevice(device_id, scenario, epoch0=now, seed=int(now) % 1000)
    if soil is not None:
        dev.soil = float(soil)
    if scenario != "normal":
        dev.soil = min(dev.soil, 28.0)       # start dry (below the 30 % threshold) so the scenario shows up at once
    return dev


def _run(stop: threading.Event, dev, speed: int) -> None:
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            with _lock:
                out = []
                for _ in range(speed):
                    out += dev.tick(1.0)
            _publish(out, live_epoch=int(time.time()))
        except Exception:
            log.exception("live simulator step failed")
        stop.wait(max(0.0, 1.0 - (time.monotonic() - t0)))


def start(scenario: str = "normal") -> None:
    """(Re)start the simulated board with the given scenario."""
    global _dev, _thread, _stop, _scenario
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    stop()
    dev = _make_device(scenario)
    ev = threading.Event()
    speed = max(1, min(120, int(config.LIVE_SIM_SPEED)))
    with _lock:
        _dev, _stop, _scenario = dev, ev, scenario
    _thread = threading.Thread(target=_run, args=(ev, dev, speed), daemon=True, name="live-sim")
    _thread.start()
    log.info("live simulator started: device %s, scenario %s, speed x%d", dev.id, scenario, speed)


def stop() -> None:
    global _dev, _thread, _stop
    with _lock:
        ev, th = _stop, _thread
        _dev = None
    if ev is not None:
        ev.set()
    if th is not None and th.is_alive() and th is not threading.current_thread():
        th.join(timeout=3)
    _stop = _thread = None


def start_from_config() -> None:
    """Called by the app at start-up. Does nothing unless LIVE_SIM_ENABLED is true. Never raises."""
    if not config.LIVE_SIM_ENABLED:
        return
    try:
        start(config.LIVE_SIM_SCENARIO if config.LIVE_SIM_SCENARIO in SCENARIOS else "normal")
    except Exception:
        log.exception("live simulator could not start (the backend keeps running without it)")


def handles(device_id: str) -> bool:
    return _dev is not None and device_id == _dev.id


def deliver(device_id: str, payload: dict) -> bool:
    """Called by ingest.publish_command for the simulated device: the fake board checks the command against
    the same rules as the firmware and answers (ack + events) straight away. False if the simulator is not
    running or the id is not the simulated board."""
    with _lock:
        if _dev is None or device_id != _dev.id:
            return False
        out = _dev.on_command(json.dumps(payload))
    _publish(out, live_epoch=int(time.time()))
    return True


# ------------------------------------------------------------------ usage-alert demo (simulated history)
DEMO_LIMITS = (3.0, 10.0)        # daily minimum and maximum litres set for the demo zone
DEMO_LITRES_PER_PUMP_MIN = 0.5


def build_usage_demo_rows(now_utc: datetime) -> list[dict]:
    """Three IST days of INVENTED one-minute rows that end 'now', built so that the three usage alerts can be
    shown: the day before yesterday uses too much water (about 14 L), yesterday too little (about 1.5 L), and
    the last ~75 minutes the soil is below the dry threshold with the pump off. Pure function (testable)."""
    ist = consolidate.IST
    now_min = now_utc.astimezone(timezone.utc).replace(second=0, microsecond=0)
    today0 = now_min.astimezone(ist).replace(hour=0, minute=0)
    start = (today0 - timedelta(days=2)).astimezone(timezone.utc)
    n = int((now_min - start).total_seconds() // 60) + 1
    ramp_from = n - 150                      # last 150 minutes: soil falls from 38 % to 22 %, pump off
    rows, soil = [], 50.0
    for m in range(n):
        ts = start + timedelta(minutes=m)
        local = ts.astimezone(ist)
        offset = (local.date() - today0.date()).days          # -2, -1 or 0
        mod = local.hour * 60 + local.minute
        if offset == -2:
            pump, drain = (mod % 50 == 0), 0.18
        elif offset == -1:
            pump, drain = (mod in (360, 780, 1140)), 0.05
        else:
            pump, drain = (mod in (360, 540, 720) and m < ramp_from - 10), 0.10
        if m >= ramp_from:
            pump = False
            soil = 38.0 - 16.0 * (m - ramp_from) / 150.0
        else:
            soil = min(62.0, max(38.0, soil + (9.0 if pump else -drain)))
        rows.append({"ts": ts, "uptime_s": m * 60, "soil": round(soil, 1), "pump_s": 60 if pump else 0,
                     "litres": DEMO_LITRES_PER_PUMP_MIN if pump else 0.0,
                     "temp": round(28 + 3 * math.sin(m / 230.0), 1), "hum": round(55 + 5 * math.cos(m / 310.0), 1)})
    return rows


def load_usage_demo() -> dict:
    """Store the simulated history in a NEW simulated zone (nothing existing is changed or deleted), set the demo
    limits and run the normal usage check, so the three usage alerts appear."""
    now = datetime.now(timezone.utc)
    device_id = "sim-usage-" + now.astimezone(consolidate.IST).strftime("%m%d-%H%M")
    rows = build_usage_demo_rows(now)
    with dbmod.SessionLocal() as db:
        if db.get(Device, device_id) is None:
            db.add(Device(id=device_id, name="Usage-alert demo (simulated)"))
            db.flush()
        db.add_all([Summary(device_id=device_id, ts=r["ts"], ts_from_device=1, uptime_s=r["uptime_s"], samples=60,
                            soil_pct_mean=r["soil"], temp_c_mean=r["temp"], hum_pct_mean=r["hum"],
                            flow_l=r["litres"], pump_on_s=r["pump_s"], faults=0) for r in rows])
        db.merge(UsageLimit(device_id=device_id, daily_min_litres=DEMO_LIMITS[0], daily_max_litres=DEMO_LIMITS[1]))
        db.commit()
        created = consolidate.run_usage_check(db, device_id, now)
    return {"device_id": device_id, "rows": len(rows), "daily_min_litres": DEMO_LIMITS[0],
            "daily_max_litres": DEMO_LIMITS[1], "alerts_created": created,
            "note": "Simulated history. All values are invented and are not results."}


def status() -> dict:
    running = _dev is not None
    return {"enabled": bool(config.LIVE_SIM_ENABLED), "running": running,
            "device_id": config.LIVE_SIM_DEVICE_ID, "scenario": _scenario if running else None,
            "speed": int(config.LIVE_SIM_SPEED), "scenarios": list(SCENARIOS),
            "note": "Simulated board. All values are invented and are not results."}


class ScenarioIn(BaseModel):
    scenario: str = "normal"


def make_router(api_key_dep) -> APIRouter:
    r = APIRouter()

    @r.get("/live-sim")
    def live_sim_status():
        return status()

    def _need_enabled():
        if not config.LIVE_SIM_ENABLED:
            raise HTTPException(409, "the live simulator is off: start the backend with LIVE_SIM_ENABLED=true")

    @r.post("/live-sim/scenario", dependencies=[Depends(api_key_dep)])
    def live_sim_scenario(body: ScenarioIn):
        _need_enabled()
        if body.scenario not in SCENARIOS:
            raise HTTPException(422, f"scenario must be one of {', '.join(SCENARIOS)}")
        start(body.scenario)
        return status()

    @r.post("/live-sim/start", dependencies=[Depends(api_key_dep)])
    def live_sim_start():
        _need_enabled()
        start(_scenario)
        return status()

    @r.post("/live-sim/stop", dependencies=[Depends(api_key_dep)])
    def live_sim_stop():
        _need_enabled()
        stop()
        return status()

    @r.post("/live-sim/usage-demo", dependencies=[Depends(api_key_dep)])
    def live_sim_usage_demo():
        _need_enabled()
        return load_usage_demo()

    return r