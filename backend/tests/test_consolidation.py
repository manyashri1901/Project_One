"""Tests for consolidation (cycles, daily rows, usage alerts), the watering advisor, the cloud copy and the
dashboard page. All data is generated inside the tests; nothing is a result."""
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from test_backend import KEY, client, fresh_db, publish  # noqa: F401  (shared in-memory database and fixtures)

from app import advisor, config, consolidate
from app import db as dbmod

UTC = timezone.utc


def row(ts, soil=40.0, pump=0, litres=None, faults=0, temp=29.0, hum=60.0, rain=None):
    return SimpleNamespace(ts=ts, soil_pct_mean=soil, pump_on_s=pump, flow_l=litres, faults=faults,
                           temp_c_mean=temp, hum_pct_mean=hum, rain=rain)


T0 = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


def minute(i):
    return T0 + timedelta(minutes=i)


# ----------------------------------------------------------------- cycles
def test_cycle_is_a_run_of_pump_on_rows_with_soil_before_and_after():
    rows = [row(minute(0), 30), row(minute(1), 30),
            row(minute(2), 31, pump=60, litres=0.2), row(minute(3), 35, pump=60, litres=0.2),
            row(minute(4), 42, pump=60, litres=0.2), row(minute(5), 44),
            row(minute(30), 44, pump=60, litres=0.1), row(minute(31), 46)]
    cycles = consolidate.build_cycles(rows)
    assert len(cycles) == 2
    c = cycles[0]
    assert c["minutes"] == 3 and c["pump_s"] == 180 and c["litres"] == 0.6
    assert c["soil_before"] == 30 and c["soil_after"] == 44 and c["soil_rise"] == 14
    assert c["outcome"] == "normal"
    assert cycles[1]["litres"] == 0.1 and cycles[1]["start_ts"] == minute(30)


def test_cycle_without_flow_data_has_no_litres_and_fault_is_marked():
    rows = [row(minute(0), 30), row(minute(1), 30, pump=60, faults=1), row(minute(2), 30)]
    c = consolidate.build_cycles(rows)[0]
    assert c["litres"] is None and c["outcome"] == "fault"


# ----------------------------------------------------------------- daily rows and usage status
def test_daily_rows_and_usage_status():
    limits = SimpleNamespace(daily_min_litres=5.0, daily_max_litres=10.0)
    rows = []
    for i in range(3):                                   # 20 Oct: 3 litres
        rows.append(row(T0 + timedelta(minutes=i), 40, pump=60, litres=1.0))
    day2 = T0 + timedelta(days=1)
    for i in range(12):                                  # 21 Oct: 12 litres
        rows.append(row(day2 + timedelta(minutes=i), 50, pump=60, litres=1.0))
    day3 = T0 + timedelta(days=2)
    rows.append(row(day3, 55, pump=0, litres=0.0))       # 22 Oct (today): 0 litres so far, "low" is not judged yet
    now = day3 + timedelta(hours=1)
    cycles = consolidate.build_cycles(rows)
    daily = consolidate.build_daily(rows, cycles, {"2026-10-21": 1}, {}, limits, now)
    by = {d["day"]: d for d in daily}
    assert by["2026-10-20"]["litres"] == 3.0 and by["2026-10-20"]["usage_status"] == "low"
    assert by["2026-10-21"]["litres"] == 12.0 and by["2026-10-21"]["usage_status"] == "high"
    assert by["2026-10-21"]["skips"] == 1 and by["2026-10-21"]["cycles"] == 1
    assert by["2026-10-22"]["usage_status"] == "ok"
    assert consolidate.usage_status(None, limits, "2026-10-20", "2026-10-22") == "no_flow_data"
    assert consolidate.usage_status(3.0, None, "2026-10-20", "2026-10-22") == "no_limit"


# ----------------------------------------------------------------- advisor
def test_advisor_recommendations():
    cycles = [{"litres": 1.0, "soil_rise": 5.0}]          # 0.2 L per soil point
    assert advisor.litres_per_percent(cycles) == pytest.approx(0.2)
    falling = [row(minute(i), 50 - i / 6) for i in range(0, 61, 5)]          # 50 -> 40 in one hour
    r = advisor.recommend(falling, 30, 60, cycles, 0.0)
    assert r["action"] == "water_soon" and r["hours_to_dry"] == pytest.approx(1.0, abs=0.1)
    dry = [row(minute(i), 25) for i in range(0, 61, 5)]
    r = advisor.recommend(dry, 30, 60, cycles, 0.0)
    assert r["action"] == "water_now" and r["litres_suggested"] == pytest.approx(7.0)
    limits = SimpleNamespace(daily_max_litres=5.0, daily_min_litres=None)
    assert advisor.recommend(dry, 30, 60, cycles, 5.0, limits)["action"] == "hold_limit"
    rainy = [row(minute(i), 40, rain=1) for i in range(0, 61, 5)]
    assert advisor.recommend(rainy, 30, 60, cycles, 0.0)["action"] == "skip_rain"
    wet = [row(minute(i), 70) for i in range(0, 61, 5)]
    assert advisor.recommend(wet, 30, 60, cycles, 0.0)["action"] == "ok"
    assert advisor.recommend([], 30, 60, cycles, 0.0)["action"] == "no_data"
    nocycle = advisor.recommend(dry, 30, 60, [], 0.0)
    assert nocycle["litres_suggested"] is None and "unknown" in nocycle["message"]


# ----------------------------------------------------------------- API with stored data
def load_cycle_rows(device="esp-2", minutes=20, pump_from=5, pump_to=17, litres=1.0, soil=40.0):
    base = int(time.time()) - minutes * 60
    for i in range(minutes):
        on = pump_from <= i < pump_to
        msg = {"device_id": device, "epoch": base + 60 * i, "uptime_s": 60 * (i + 1), "samples": 60,
               "soil_pct_mean": soil + (i if on else 0), "temp_c_mean": 30.0, "hum_pct_mean": 50.0,
               "pump_on_s": 60 if on else 0, "faults": 0}
        if on:
            msg["flow_L"] = litres
        assert publish(f"jalbhoomi/{device}/summary", msg)


def test_cycles_daily_and_recommendation_endpoints(client):
    load_cycle_rows()
    cycles = client.get("/devices/esp-2/cycles").json()
    assert len(cycles) == 1 and cycles[0]["litres"] == 12.0 and cycles[0]["minutes"] == 12
    daily = client.get("/devices/esp-2/daily").json()
    assert round(sum(d["litres"] or 0 for d in daily), 3) == 12.0
    reco = client.get("/devices/esp-2/recommendation").json()
    assert reco["action"] in ("ok", "water_now", "water_soon", "skip_rain", "hold_limit")
    assert client.get("/devices/none/cycles").status_code == 404


def test_usage_limits_need_the_key_and_raise_a_high_usage_alert_once(client):
    load_cycle_rows()
    assert client.put("/devices/esp-2/usage-limits", json={"daily_max_litres": 3}).status_code == 401
    r = client.put("/devices/esp-2/usage-limits", json={"daily_min_litres": 1, "daily_max_litres": 3}, headers=KEY)
    assert r.status_code == 200 and client.get("/devices/esp-2/usage-limits").json()["daily_max_litres"] == 3
    assert client.put("/devices/esp-2/usage-limits", json={"daily_min_litres": 9, "daily_max_litres": 3},
                      headers=KEY).status_code == 422
    first = client.post("/devices/esp-2/usage-check", headers=KEY).json()["alerts_created"]
    assert "water_usage_high" in first
    assert client.post("/devices/esp-2/usage-check", headers=KEY).json()["alerts_created"] == []
    types = [a["type"] for a in client.get("/devices/esp-2/alerts").json()]
    assert "water_usage_high" in types
    assert client.post("/devices/esp-2/usage-check").status_code == 401


def test_soil_dry_for_too_long_raises_an_alert(client):
    base = int(time.time()) - 90 * 60
    for i in range(90):
        assert publish("jalbhoomi/esp-3/summary", {"device_id": "esp-3", "epoch": base + 60 * i, "uptime_s": 60 * (i + 1),
                                                  "samples": 60, "soil_pct_mean": 20.0, "pump_on_s": 0})
    created = client.post("/devices/esp-3/usage-check", headers=KEY).json()["alerts_created"]
    assert created == ["soil_dry_long"]
    assert client.post("/devices/esp-3/usage-check", headers=KEY).json()["alerts_created"] == []


# ----------------------------------------------------------------- cloud copy: consolidated rows only
def test_cloud_copy_holds_only_consolidated_rows(client, tmp_path, monkeypatch):
    load_cycle_rows()
    url = f"sqlite:///{tmp_path / 'cloud.db'}"
    monkeypatch.setattr(config, "CLOUD_DATABASE_URL", url)
    r = client.post("/devices/esp-2/consolidate/sync", headers=KEY)
    assert r.status_code == 200 and r.json()["cycles_synced"] == 1
    from sqlalchemy import inspect
    eng = consolidate.cloud_engine()
    assert sorted(inspect(eng).get_table_names()) == ["consolidated_cycles", "consolidated_daily"]   # no summaries table
    local = client.get("/devices/esp-2/cycles").json()
    cloud = client.get("/devices/esp-2/cycles?source=cloud").json()
    assert len(cloud) == len(local) == 1 and cloud[0]["litres"] == local[0]["litres"]
    assert client.get("/devices/esp-2/daily?source=cloud").json()
    client.post("/devices/esp-2/consolidate/sync", headers=KEY)                  # upsert: no duplicates
    assert len(client.get("/devices/esp-2/cycles?source=cloud").json()) == 1


def test_cloud_source_without_configuration_is_503(client, monkeypatch):
    load_cycle_rows()
    monkeypatch.setattr(config, "CLOUD_DATABASE_URL", "")
    assert client.get("/devices/esp-2/cycles?source=cloud").status_code == 503
    assert client.post("/devices/esp-2/consolidate/sync", headers=KEY).status_code == 503


def test_dashboard_page_is_served(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "JalBhoomi dashboard" in r.text