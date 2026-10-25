"""Tests for the live demo simulator. All data is generated inside the tests; nothing is a result.
The simulator thread is not started here: the tests drive the simulated board step by step so they are stable."""
import time

import pytest

from test_backend import KEY, client, fresh_db, publish  # noqa: F401  (shared in-memory database and fixtures)

from app import config, livesim

SIM_ID = "sim-live-t1"


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.setattr(config, "LIVE_SIM_ENABLED", True)
    monkeypatch.setattr(config, "LIVE_SIM_DEVICE_ID", SIM_ID)
    monkeypatch.setattr(livesim, "BACKFILL_S", 3600)
    dev = livesim._make_device("normal")         # simulates one hour of history and stores its summaries
    dev.soil = 40.0                              # between the dry (30) and wet (60) thresholds
    livesim._dev = dev
    yield dev
    livesim._dev = None


def _status(client, cmd_id=None):
    rows = client.get(f"/devices/{SIM_ID}/commands").json()
    return rows[0] if cmd_id is None else next(r for r in rows if r["cmd_id"] == cmd_id)


def test_simulator_is_off_by_default(client):
    s = client.get("/live-sim").json()
    assert s["enabled"] is False and s["running"] is False
    assert client.post("/live-sim/scenario", json={"scenario": "normal"}, headers=KEY).status_code == 409


def test_first_start_stores_history_for_the_simulated_board(client, sim):
    rows = client.get(f"/devices/{SIM_ID}/summaries?limit=500").json()
    assert len(rows) >= 50                       # one hour = 60 one-minute summaries
    assert SIM_ID in client.get("/devices").json()
    assert livesim.status()["device_id"] == SIM_ID and livesim.status()["running"] is True


def test_commands_reach_the_simulated_board_and_are_accepted(client, sim):
    r = client.post(f"/devices/{SIM_ID}/commands", json={"cmd": "override_on", "duration_s": 5}, headers=KEY)
    assert r.status_code == 202
    assert _status(client, r.json()["cmd_id"])["status"] == "accepted"
    # let the fake board run for two simulated minutes and store what it reports
    for _ in range(120):
        livesim._publish(sim.tick(1.0), live_epoch=int(time.time()))
    rows = client.get(f"/devices/{SIM_ID}/summaries?limit=500").json()
    assert any((x["pump_on_s"] or 0) > 0 for x in rows)
    r = client.post(f"/devices/{SIM_ID}/commands", json={"cmd": "set_auto", "enabled": False}, headers=KEY)
    assert _status(client, r.json()["cmd_id"])["status"] == "accepted"


def test_board_rules_can_refuse_a_command(client, sim):
    sim.soil = 90.0                              # already wet: the firmware refuses to water
    r = client.post(f"/devices/{SIM_ID}/commands", json={"cmd": "override_on", "duration_s": 5}, headers=KEY)
    row = _status(client, r.json()["cmd_id"])
    assert row["status"] == "rejected" and row["reason"] == "soil_already_wet"


def test_other_devices_still_use_the_mqtt_path(client, sim):
    assert publish("jalbhoomi/esp-9/summary", {"device_id": "esp-9", "epoch": int(time.time()), "uptime_s": 60,
                                              "samples": 60, "soil_pct_mean": 40.0, "pump_on_s": 0})
    r = client.post("/devices/esp-9/commands", json={"cmd": "override_off"}, headers=KEY)
    assert r.status_code == 503 and "MQTT is not connected" in r.json()["detail"]


def test_scenario_endpoint_needs_the_key_and_a_known_scenario(client, monkeypatch):
    monkeypatch.setattr(config, "LIVE_SIM_ENABLED", True)
    assert client.post("/live-sim/scenario", json={"scenario": "normal"}).status_code == 401
    assert client.post("/live-sim/scenario", json={"scenario": "bogus"}, headers=KEY).status_code == 422


# ----------------------------------------------------------------- usage-alert demo (simulated history)
def test_usage_demo_history_is_built_so_the_three_alerts_can_be_shown():
    from datetime import datetime, timezone
    rows = livesim.build_usage_demo_rows(datetime.now(timezone.utc))
    assert len(rows) > 2 * 24 * 60
    assert rows[-1]["soil"] < 30 and rows[-1]["pump_s"] == 0          # the soil is dry right now, pump off


def test_usage_demo_creates_high_low_and_soil_dry_alerts(client, monkeypatch):
    monkeypatch.setattr(config, "LIVE_SIM_ENABLED", True)
    r = client.post("/live-sim/usage-demo", headers=KEY)
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["alerts_created"]) == ["soil_dry_long", "water_usage_high", "water_usage_low"]
    types = [a["type"] for a in client.get(f"/devices/{body['device_id']}/alerts").json()]
    assert {"water_usage_high", "water_usage_low", "soil_dry_long"} <= set(types)
    assert client.post("/live-sim/usage-demo").status_code == 401