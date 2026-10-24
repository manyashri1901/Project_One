"""Tests for the public-dataset tools (replay + decision-model evaluation) and GET /benchmarks.
All data here is generated inside the tests; nothing is a result."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("MQTT_ENABLED", "false")
os.environ.setdefault("BACKEND_API_KEY", "test-key")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from app.schemas import SummaryIn  # noqa: E402
import replay_dataset  # noqa: E402
import train_decision_model  # noqa: E402


def _frame(n=300, seed=0):
    rng = np.random.default_rng(seed)
    soil = rng.uniform(200, 900, n)
    return pd.DataFrame({"Soil Moisture": soil,
                         "Temperature": rng.uniform(15, 40, n),
                         "Air Humidity": rng.uniform(20, 90, n),
                         "Pump Data": (soil < 500).astype(int)})


def test_replay_messages_match_the_device_contract():
    df = _frame(50)
    msgs = replay_dataset.build_messages(df, "dataset-replay-01", "Soil Moisture", "Temperature",
                                         "Air Humidity", "Pump Data", False, 60, 1_800_000_000)
    assert len(msgs) == 50
    epochs = []
    for topic, payload in msgs:
        assert topic == "jalbhoomi/dataset-replay-01/summary"
        m = SummaryIn.model_validate(json.loads(payload))       # same validation the backend applies
        assert 0 <= m.soil_pct_mean <= 100
        assert m.pump_on_s in (0, 60)
        assert m.flow_pulses is None and m.flow_L is None        # the dataset has no flow sensor
        epochs.append(m.epoch)
    assert epochs == sorted(epochs) and epochs[-1] == 1_800_000_000
    assert any(json.loads(p)["soil_pct_mean"] == 0.0 for _, p in msgs)     # min-max reaches 0
    assert any(json.loads(p)["soil_pct_mean"] == 100.0 for _, p in msgs)   # ... and 100


def test_replay_invert_flips_the_soil_scale():
    df = _frame(20)
    a = replay_dataset.build_messages(df, "d", "Soil Moisture", None, None, None, False, 60, 1_800_000_000)
    b = replay_dataset.build_messages(df, "d", "Soil Moisture", None, None, None, True, 60, 1_800_000_000)
    for (_, pa), (_, pb) in zip(a, b):
        assert abs(json.loads(pa)["soil_pct_mean"] + json.loads(pb)["soil_pct_mean"] - 100) < 0.11


def test_decision_model_finds_a_clear_signal_and_writes_results(tmp_path):
    csv = tmp_path / "data.csv"
    _frame(400).to_csv(csv, index=False)
    out = train_decision_model.main([str(csv), "--name", "unit", "--out-dir", str(tmp_path / "res"),
                                     "--source", "generated in the test"])
    assert out["data_source"] == "public_benchmark"
    assert out["label_column"] == "Pump Data"
    best = next(m for m in out["models"] if m["model"] == out["best_model"])
    assert best["roc_auc"] > 0.9                                   # label was built from soil, so it is learnable
    assert best["accuracy"] > out["baseline"]["accuracy"]
    assert (tmp_path / "res" / "decision_unit.json").is_file()
    assert (tmp_path / "res" / "decision_unit_PUBLIC_BENCHMARK.joblib").is_file()


def test_decision_model_says_weak_when_label_is_random(tmp_path):
    df = _frame(400)
    df["Pump Data"] = np.random.default_rng(5).integers(0, 2, len(df))
    csv = tmp_path / "rand.csv"
    df.to_csv(csv, index=False)
    out = train_decision_model.main([str(csv), "--name", "rand", "--out-dir", str(tmp_path / "res")])
    assert not out["verdict"].startswith("CLEAR")                  # random labels must never be called a clear signal
    best = next(m for m in out["models"] if m["model"] == out["best_model"])
    assert best["roc_auc"] < 0.7


def test_benchmarks_endpoint_lists_result_files(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config
    from app.main import app
    (tmp_path / "decision_x.json").write_text(json.dumps({"name": "x", "data_source": "public_benchmark"}))
    (tmp_path / "other.json").write_text("{}")                      # ignored: wrong name
    monkeypatch.setattr(config, "BENCHMARK_DIR", str(tmp_path))
    r = TestClient(app).get("/benchmarks")
    assert r.status_code == 200
    body = r.json()
    assert [b["name"] for b in body["benchmarks"]] == ["x"]
    assert "Not measurements" in body["note"]


def test_benchmarks_endpoint_with_no_folder_is_empty(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import config
    from app.main import app
    monkeypatch.setattr(config, "BENCHMARK_DIR", str(tmp_path / "does_not_exist"))
    assert TestClient(app).get("/benchmarks").json()["benchmarks"] == []


def test_board_csv_loader_parses_dumps_output():
    import load_board_csv
    text = """boot ok
----- BEGIN summary.csv -----
epoch,uptime_s,samples,soil_raw_mean,soil_raw_min,soil_raw_max,soil_pct_mean,temp_c_mean,hum_pct_mean,flow_pulses,flow_L,pump_on_s,faults,rain,water_added_ml
1800000060,60,60,2100.5,2090,2111,50.1,29.5,61.0,0,0.000,0,0,,
1800000120,120,60,2095.0,2080,2110,50.4,29.6,61.0,90,0.200,20,0,0,
----- END -----
random serial text, with, commas
"""
    rows, warns = load_board_csv.parse_summary_text(text, "dev-1")
    assert len(rows) == 2 and not warns
    assert "rain" not in rows[0]                                   # empty field is omitted, never null
    assert rows[1]["flow_pulses"] == 90 and rows[1]["pump_on_s"] == 20
    for r in rows:
        SummaryIn.model_validate(r)                                # same validation the backend applies


def test_board_csv_loader_rebuilds_times_when_board_had_no_clock():
    import load_board_csv
    text = ("epoch,uptime_s,samples,soil_raw_mean,soil_raw_min,soil_raw_max,soil_pct_mean,temp_c_mean,hum_pct_mean,"
            "flow_pulses,flow_L,pump_on_s,faults,rain,water_added_ml\n"
            "0,60,60,2100,2090,2111,50.0,29,60,0,0,0,0,,\n"
            "0,120,60,2100,2090,2111,50.0,29,60,0,0,0,0,,\n")
    rows, warns = load_board_csv.parse_summary_text(text, "dev-1", end_epoch=1_800_000_000)
    assert [r["epoch"] for r in rows] == [1_799_999_940, 1_800_000_000]
    assert warns and "approximate" in warns[0]
    assert load_board_csv.parse_summary_text("nothing useful", "d")[0] == []