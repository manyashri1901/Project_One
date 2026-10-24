import json
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MQTT_ENABLED"] = "false"
os.environ["BACKEND_API_KEY"] = "test-key"
KEY = {"X-API-Key": "test-key"}

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as dbmod, ingest
from app.db import Base
from app.main import app

# one shared in-memory database for the whole test session
engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
dbmod.engine = engine
dbmod.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
ingest.SessionLocal = dbmod.SessionLocal
import app.main as mainmod
mainmod.engine = engine
mainmod.SessionLocal = dbmod.SessionLocal


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    def _get_db():
        s = dbmod.SessionLocal()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[dbmod.get_db] = _get_db
    app.dependency_overrides[mainmod.get_db] = _get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# the exact shape the firmware publishes (empty fields are omitted)
MSG = {"device_id": "esp-1", "epoch": 1761278400, "uptime_s": 120, "samples": 30,
       "soil_raw_mean": 2500.5, "soil_raw_min": 2490, "soil_raw_max": 2510,
       "soil_pct_mean": 27.0, "temp_c_mean": 29.1, "hum_pct_mean": 61.0,
       "flow_pulses": 450, "flow_L": 1.0, "pump_on_s": 30, "faults": 0, "water_added_ml": 100}


def publish(topic, obj):
    return ingest.handle_message(topic, json.dumps(obj).encode())


def test_ingest_and_query(client):
    assert publish("jalbhoomi/esp-1/summary", MSG)
    assert client.get("/devices").json() == ["esp-1"]
    rows = client.get("/devices/esp-1/summaries").json()
    assert len(rows) == 1 and rows[0]["flow_l"] == 1.0 and rows[0]["ts_from_device"] == 1


def test_duplicate_is_ignored(client):
    assert publish("jalbhoomi/esp-1/summary", MSG)
    assert not publish("jalbhoomi/esp-1/summary", MSG)
    assert len(client.get("/devices/esp-1/summaries").json()) == 1


def test_missing_optional_fields_and_no_ntp_time(client):
    m = {"device_id": "esp-2", "epoch": 0, "uptime_s": 60, "samples": 30, "soil_raw_mean": 2000}
    assert publish("jalbhoomi/esp-2/summary", m)
    r = client.get("/devices/esp-2/summaries").json()[0]
    assert r["ts_from_device"] == 0 and r["temp_c_mean"] is None


def test_bad_messages_are_rejected_not_raised(client):
    assert not ingest.handle_message("jalbhoomi/x/summary", b"not json")
    assert not publish("jalbhoomi/x/summary", {"device_id": "x", "soil_pct_mean": 250})
    assert not publish("jalbhoomi/other/summary", MSG)          # topic/device mismatch
    assert client.get("/devices").json() == []


def test_daily_water_total(client):
    publish("jalbhoomi/esp-1/summary", MSG)
    publish("jalbhoomi/esp-1/summary", {**MSG, "uptime_s": 180, "epoch": 1761278460, "flow_L": 0.5})
    d = client.get("/devices/esp-1/water/daily").json()
    assert d == [{"day": "2025-10-24", "litres": 1.5, "minutes_reported": 2}]


def test_thresholds(client):
    publish("jalbhoomi/esp-1/summary", MSG)
    assert client.get("/devices/esp-1/thresholds").json()["soil_dry_pct"] == 30
    ok = client.put("/devices/esp-1/thresholds", json={"soil_dry_pct": 25, "soil_wet_pct": 70, "rain_probability": 0.5}, headers=KEY)
    assert ok.status_code == 200 and ok.json()["pushed_to_device"] is False   # MQTT is off in tests
    assert client.get("/devices/esp-1/thresholds").json()["soil_wet_pct"] == 70
    bad = client.put("/devices/esp-1/thresholds", json={"soil_dry_pct": 80, "soil_wet_pct": 70, "rain_probability": 0.5}, headers=KEY)
    assert bad.status_code == 422


def test_unknown_device_404(client):
    assert client.get("/devices/nope/summaries").status_code == 404


# ----------------------------------------------------------- alerts / commands
def event(**kw):
    base = {"device_id": "esp-1", "epoch": 1761278500, "uptime_s": 200, "type": "fault_no_flow"}
    return {**base, **kw}


def test_event_becomes_alert_with_severity(client):
    assert publish("jalbhoomi/esp-1/event", event())
    assert publish("jalbhoomi/esp-1/event", event(type="predictive_skip", uptime_s=300, epoch=1761278600))
    assert publish("jalbhoomi/esp-1/event", event(type="irrigation_start", uptime_s=400, epoch=1761278700))
    all_ = client.get("/devices/esp-1/alerts").json()
    assert {a["type"]: a["severity"] for a in all_} == {
        "fault_no_flow": "fault", "predictive_skip": "notice", "irrigation_start": "info"}
    faults = client.get("/devices/esp-1/alerts?severity=fault").json()
    assert [a["type"] for a in faults] == ["fault_no_flow"]
    assert not publish("jalbhoomi/esp-1/event", event())          # duplicate ignored


def test_bad_event_rejected(client):
    assert not publish("jalbhoomi/esp-1/event", event(type="DROP TABLE"))
    assert not publish("jalbhoomi/esp-1/event", event(device_id="other"))


def test_acknowledge_alert_needs_key(client):
    publish("jalbhoomi/esp-1/event", event())
    aid = client.get("/devices/esp-1/alerts").json()[0]["id"]
    assert client.post(f"/alerts/{aid}/ack").status_code == 401
    r = client.post(f"/alerts/{aid}/ack", headers=KEY)
    assert r.status_code == 200 and r.json()["acknowledged"] == 1
    assert client.get("/devices/esp-1/alerts?unacknowledged=true").json() == []


def test_write_endpoints_need_key(client):
    publish("jalbhoomi/esp-1/summary", MSG)
    body = {"cmd": "override_off"}
    assert client.post("/devices/esp-1/commands", json=body).status_code == 401
    assert client.post("/devices/esp-1/commands", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.put("/devices/esp-1/thresholds",
                      json={"soil_dry_pct": 20, "soil_wet_pct": 60, "rain_probability": 0.4}).status_code == 401


def test_command_validation(client):
    publish("jalbhoomi/esp-1/summary", MSG)
    bad = [{"cmd": "reboot"},
           {"cmd": "set_thresholds", "soil_dry_pct": 70, "soil_wet_pct": 60},
           {"cmd": "advise"},
           {"cmd": "override_on", "duration_s": 100000}]
    for b in bad:
        assert client.post("/devices/esp-1/commands", json=b, headers=KEY).status_code == 422, b


def test_command_roundtrip_with_ack(client, monkeypatch):
    publish("jalbhoomi/esp-1/summary", MSG)
    sent = []
    monkeypatch.setattr(ingest, "publish_command", lambda dev, payload: sent.append((dev, payload)) or True)
    r = client.post("/devices/esp-1/commands", json={"cmd": "override_on", "duration_s": 20}, headers=KEY)
    assert r.status_code == 202 and r.json()["status"] == "sent"
    dev, payload = sent[0]
    assert dev == "esp-1" and payload["cmd"] == "override_on" and payload["duration_s"] == 20
    cmd_id = payload["id"]

    # the ESP32 refuses (e.g. it is in a post-fault lockout)
    assert publish("jalbhoomi/esp-1/ack", {"device_id": "esp-1", "id": cmd_id, "cmd": "override_on",
                                          "accepted": False, "reason": "fault_lockout"})
    c = client.get("/devices/esp-1/commands").json()[0]
    assert c["status"] == "rejected" and c["reason"] == "fault_lockout" and c["acked_at"]
    # an ack for an unknown command is ignored
    assert not publish("jalbhoomi/esp-1/ack", {"device_id": "esp-1", "id": "nope", "accepted": True})


def test_command_when_mqtt_down_is_marked_failed(client):
    publish("jalbhoomi/esp-1/summary", MSG)
    r = client.post("/devices/esp-1/commands", json={"cmd": "override_off"}, headers=KEY)
    assert r.status_code == 503
    c = client.get("/devices/esp-1/commands").json()[0]
    assert c["status"] == "failed" and c["reason"] == "mqtt_unavailable"


def test_threshold_put_sends_command_and_reports_pushed(client, monkeypatch):
    publish("jalbhoomi/esp-1/summary", MSG)
    sent = []
    monkeypatch.setattr(ingest, "publish_command", lambda dev, payload: sent.append(payload) or True)
    r = client.put("/devices/esp-1/thresholds",
                   json={"soil_dry_pct": 25, "soil_wet_pct": 65, "rain_probability": 0.5}, headers=KEY)
    assert r.json()["pushed_to_device"] is True
    assert sent[0]["cmd"] == "set_thresholds" and sent[0]["soil_wet_pct"] == 65


def test_writes_refused_when_server_has_no_key(client, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "BACKEND_API_KEY", "")
    monkeypatch.setattr(config, "ALLOWED_FIREBASE_UIDS", [])
    publish("jalbhoomi/esp-1/summary", MSG)
    assert client.post("/devices/esp-1/commands", json={"cmd": "override_off"}, headers=KEY).status_code == 503


# ------------------------------------------------- zones, water, predictions, ML
def test_zones_and_rename(client):
    publish("jalbhoomi/esp-1/summary", MSG)
    publish("jalbhoomi/esp-1/event", event())                       # unacknowledged fault
    z = client.get("/zones").json()
    assert z[0]["device_id"] == "esp-1" and z[0]["soil_pct"] == 27.0 and z[0]["open_faults"] == 1
    assert z[0]["auto_mode"] is True and z[0]["name"] is None
    assert client.put("/devices/esp-1", json={"name": "Tomato bed"}).status_code == 401
    assert client.put("/devices/esp-1", json={"name": "Tomato bed"}, headers=KEY).status_code == 200
    assert client.get("/zones").json()[0]["name"] == "Tomato bed"


def test_set_auto_ack_updates_zone(client, monkeypatch):
    publish("jalbhoomi/esp-1/summary", MSG)
    sent = []
    monkeypatch.setattr(ingest, "publish_command", lambda dev, payload: sent.append(payload) or True)
    assert client.post("/devices/esp-1/commands", json={"cmd": "set_auto"}, headers=KEY).status_code == 422
    r = client.post("/devices/esp-1/commands", json={"cmd": "set_auto", "enabled": False}, headers=KEY)
    assert r.status_code == 202
    assert client.get("/zones").json()[0]["auto_mode"] is True        # not changed until the device accepts
    publish("jalbhoomi/esp-1/ack", {"device_id": "esp-1", "id": sent[0]["id"], "cmd": "set_auto",
                                    "accepted": True, "reason": "ok"})
    assert client.get("/zones").json()[0]["auto_mode"] is False


def test_water_total_and_saved_estimate(client):
    publish("jalbhoomi/esp-1/summary", MSG)                                         # 1.0 L
    publish("jalbhoomi/esp-1/summary", {**MSG, "uptime_s": 180, "epoch": 1761278460, "flow_L": 3.0})
    for i, t in enumerate(("irrigation_start", "irrigation_start")):
        publish("jalbhoomi/esp-1/event", event(type=t, uptime_s=500 + i, epoch=1761278500 + i * 60))
    publish("jalbhoomi/esp-1/event", event(type="predictive_skip", uptime_s=900, epoch=1761279000))
    publish("jalbhoomi/esp-1/event", event(type="predictive_skip", uptime_s=2900, epoch=1761281000))
    t = client.get("/devices/esp-1/water/total").json()
    assert t["total_litres"] == 4.0 and t["irrigation_cycles"] == 2 and t["avg_litres_per_cycle"] == 2.0
    s = client.get("/devices/esp-1/water/saved").json()
    assert s["estimate"] is True and s["skip_episodes"] == 2 and s["litres_avoided_estimate"] == 4.0


def test_saved_is_null_without_measured_cycles(client):
    publish("jalbhoomi/esp-1/summary", MSG)
    publish("jalbhoomi/esp-1/event", event(type="predictive_skip"))
    s = client.get("/devices/esp-1/water/saved").json()
    assert s["skip_episodes"] == 1 and s["litres_avoided_estimate"] is None     # no basis -> no number


def test_prediction_run_stores_result(client, monkeypatch):
    from app import advice
    publish("jalbhoomi/esp-1/summary", MSG)
    monkeypatch.setattr(advice, "advise", lambda df: {"model_loaded": True, "ready": True, "model": "RandomForest",
                                                       "horizon_min": 360, "dry_pct": 30.0,
                                                       "probability_dry_within_horizon": 0.42})
    assert client.post("/devices/esp-1/predictions/run").status_code == 401
    assert client.post("/devices/esp-1/predictions/run", headers=KEY).json()["ready"] is True
    p = client.get("/devices/esp-1/predictions").json()
    assert len(p) == 1 and p[0]["probability"] == 0.42


def test_advice_without_model(client, monkeypatch):
    from app import advice
    monkeypatch.setattr(advice, "MODEL_PATH", "/nonexistent/model.joblib")
    advice._load.cache_clear()
    publish("jalbhoomi/esp-1/summary", MSG)
    assert client.get("/devices/esp-1/advice").json()["model_loaded"] is False


def test_ml_pipeline_smoke_and_guards(tmp_path, monkeypatch):
    import pandas as pd
    from app import advice
    from app.ml import train
    from app.ml.synthetic import make_synthetic

    out = tmp_path / "m_SYNTHETIC.joblib"
    train.main(["--synthetic", "--out", str(out)])
    assert out.exists()

    monkeypatch.setattr(advice, "MODEL_PATH", str(out))
    advice._load.cache_clear()
    monkeypatch.setattr(advice, "ALLOW_SYNTHETIC", False)
    assert advice.advise(make_synthetic().tail(200))["model_loaded"] is False      # fake model refused
    monkeypatch.setattr(advice, "ALLOW_SYNTHETIC", True)
    res = advice.advise(make_synthetic().tail(200))
    assert res["ready"] is True and 0.0 <= res["probability_dry_within_horizon"] <= 1.0
    assert advice.advise(make_synthetic().tail(20))["ready"] is False              # not enough history

    tiny = tmp_path / "tiny.csv"
    make_synthetic().head(500).to_csv(tiny, index=False)
    with pytest.raises(SystemExit):
        train.main([str(tiny)])                                                    # too little real data


# ------------------------------------------------------------------ Firebase hooks
class FakeSink:
    def __init__(self, fail=False):
        self.latest, self.alerts, self.fail = [], [], fail

    def set_latest(self, device_id, data):
        if self.fail:
            raise RuntimeError("firebase down")
        self.latest.append((device_id, data))

    def send_alert(self, device_id, title, body, data):
        if self.fail:
            raise RuntimeError("firebase down")
        self.alerts.append((device_id, title, data["type"]))


def test_firebase_status_and_push_alerts(client, monkeypatch):
    from app import firebase_sync
    fake = FakeSink()
    monkeypatch.setattr(firebase_sync, "sink", fake)
    publish("jalbhoomi/esp-1/summary", MSG)
    assert fake.latest[0][0] == "esp-1" and fake.latest[0][1]["soil_pct"] == 27.0
    publish("jalbhoomi/esp-1/event", event(type="fault_no_flow"))
    publish("jalbhoomi/esp-1/event", event(type="predictive_skip", uptime_s=300, epoch=1761278600))
    publish("jalbhoomi/esp-1/event", event(type="irrigation_start", uptime_s=400, epoch=1761278700))
    assert [a[2] for a in fake.alerts] == ["fault_no_flow", "predictive_skip"]     # info events are not pushed
    assert fake.alerts[0][1] == "Pump stopped: no flow"


def test_firebase_failure_never_breaks_ingestion(client, monkeypatch):
    from app import firebase_sync
    monkeypatch.setattr(firebase_sync, "sink", FakeSink(fail=True))
    assert publish("jalbhoomi/esp-1/summary", MSG)
    assert publish("jalbhoomi/esp-1/event", event())
    assert len(client.get("/devices/esp-1/summaries").json()) == 1


def test_firebase_off_by_default(client):
    from app import firebase_sync
    assert firebase_sync.sink is None
    assert publish("jalbhoomi/esp-1/summary", MSG)


# --------------------------------------------------------- Firebase login for the app
class AuthSink(FakeSink):
    TOKENS = {"good-owner": {"uid": "owner1"}, "good-stranger": {"uid": "stranger"}}

    def verify_token(self, token):
        return self.TOKENS.get(token)


def test_app_login_needs_allowlist_for_writes(client, monkeypatch):
    from app import config, firebase_sync
    monkeypatch.setattr(firebase_sync, "sink", AuthSink())
    publish("jalbhoomi/esp-1/summary", MSG)
    body = {"cmd": "override_off"}
    # allow-list empty -> even a valid login cannot send commands
    r = client.post("/devices/esp-1/commands", json=body, headers={"Authorization": "Bearer good-owner"})
    assert r.status_code == 401
    monkeypatch.setattr(config, "ALLOWED_FIREBASE_UIDS", ["owner1"])
    ok = client.post("/devices/esp-1/commands", json=body, headers={"Authorization": "Bearer good-owner"})
    assert ok.status_code in (202, 503)          # authorised (503 only because MQTT is off in tests)
    assert client.post("/devices/esp-1/commands", json=body,
                       headers={"Authorization": "Bearer good-stranger"}).status_code == 401
    assert client.post("/devices/esp-1/commands", json=body,
                       headers={"Authorization": "Bearer forged"}).status_code == 401


def test_reads_can_require_auth(client, monkeypatch):
    from app import config, firebase_sync
    monkeypatch.setattr(firebase_sync, "sink", AuthSink())
    publish("jalbhoomi/esp-1/summary", MSG)
    monkeypatch.setattr(config, "REQUIRE_AUTH_FOR_READS", True)
    assert client.get("/health").status_code == 200
    assert client.get("/zones").status_code == 401
    assert client.get("/zones", headers={"Authorization": "Bearer good-stranger"}).status_code == 200
    assert client.get("/zones", headers=KEY).status_code == 200


def test_prediction_job_is_noop_without_a_real_model(client):
    """No trained model installed -> the scheduled job stores nothing and does not crash."""
    assert publish("jalbhoomi/esp-1/summary", MSG)
    assert mainmod.run_prediction_job_once() == 0
    assert client.get("/devices/esp-1/predictions").json() == []


# ------------------------------------------------------------------------------------
# Simulator round-trips. The simulator (tools/simulate_device.py) plays the ESP32 with the
# firmware's message formats and refusal rules. Invented data: tests the plumbing only.
# ------------------------------------------------------------------------------------
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from simulate_device import SimDevice  # noqa: E402


def run_sim(dev, seconds):
    for _ in range(seconds):
        for topic, payload in dev.tick(1.0):
            ingest.handle_message(topic, payload.encode())


def wire_commands_to(dev, monkeypatch):
    """Replace the MQTT publish with the simulated board: command in, ack/events out."""
    def fake_publish(device_id, payload):
        for topic, body in dev.on_command(json.dumps(payload)):
            ingest.handle_message(topic, body.encode())
        return True
    monkeypatch.setattr(ingest, "publish_command", fake_publish)


def test_sim_summaries_are_accepted_by_the_backend(client):
    dev = SimDevice("sim-1")
    run_sim(dev, 5 * 60)
    rows = client.get("/devices/sim-1/summaries").json()
    assert len(rows) == 5 and all(r["samples"] == 60 for r in rows)


def test_sim_fault_reaches_alerts_and_zone_status(client):
    dev = SimDevice("sim-1", scenario="fault_no_flow")
    run_sim(dev, 90)
    alerts = client.get("/devices/sim-1/alerts?severity=fault").json()
    assert alerts and alerts[0]["type"] == "fault_no_flow"
    zone = client.get("/zones").json()[0]
    assert zone["open_faults"] >= 1


def test_sim_predictive_skip_is_a_notice_not_a_fault(client):
    dev = SimDevice("sim-1", scenario="rain_skip")
    run_sim(dev, 30)
    assert client.get("/devices/sim-1/alerts?severity=notice").json()[0]["type"] == "predictive_skip"
    assert client.get("/devices/sim-1/alerts?severity=fault").json() == []


def test_command_accepted_by_board_is_recorded_accepted(client, monkeypatch):
    dev = SimDevice("sim-1", scenario="rain_skip")           # starts dry, pump idle
    run_sim(dev, 10)
    wire_commands_to(dev, monkeypatch)
    r = client.post("/devices/sim-1/commands", json={"cmd": "override_on", "duration_s": 5}, headers=KEY)
    assert r.status_code == 202
    cmds = client.get("/devices/sim-1/commands").json()
    assert cmds[0]["status"] == "accepted"


def test_board_refusals_are_visible_with_reason(client, monkeypatch):
    # experiment mode: the board must refuse every irrigation request, and say why
    dev = SimDevice("sim-1", scenario="experiment")
    run_sim(dev, 61)                                         # first summary registers the device
    wire_commands_to(dev, monkeypatch)
    client.post("/devices/sim-1/commands", json={"cmd": "override_on", "duration_s": 5}, headers=KEY)
    c = client.get("/devices/sim-1/commands").json()[0]
    assert c["status"] == "rejected" and c["reason"] == "experiment_mode"


def test_set_auto_false_updates_zone_and_stops_automatic_starts(client, monkeypatch):
    dev = SimDevice("sim-1", scenario="fault_no_flow")       # dry soil
    wire_commands_to(dev, monkeypatch)
    run_sim(dev, 2)
    client.post("/devices/sim-1/commands", json={"cmd": "set_auto", "enabled": False}, headers=KEY)
    assert client.get("/zones").json()[0]["auto_mode"] is False
    before = len([a for a in client.get("/devices/sim-1/alerts").json() if a["type"] == "irrigation_start"])
    run_sim(dev, 200)
    after = len([a for a in client.get("/devices/sim-1/alerts").json() if a["type"] == "irrigation_start"])
    assert after == before


def test_offline_backlog_is_delivered_in_order_without_duplicates(client):
    dev = SimDevice("sim-1")
    dev.online = False
    for _ in range(5 * 60):
        dev.tick(1.0)                                        # nothing delivered while offline
    assert client.get("/devices").json() == []
    dev.online = True
    for topic, payload in dev.tick(1.0):
        ingest.handle_message(topic, payload.encode())
    rows = client.get("/devices/sim-1/summaries").json()
    assert len(rows) == 5
    assert [r["uptime_s"] for r in rows] == sorted(r["uptime_s"] for r in rows)
