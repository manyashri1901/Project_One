import hmac
import json
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import pandas as pd

from . import advice, config, firebase_sync, ingest
from .db import Base, SessionLocal, engine, get_db
from datetime import timedelta, timezone

from .models import Alert, Command, Device, Prediction, Summary, Threshold
from .schemas import (AlertOut, CommandIn, CommandOut, DailyWater, DeviceUpdate,
                      PredictionOut, SummaryOut, ThresholdIO, ThresholdOut,
                      WaterSaved, WaterTotal, ZoneOut)


def _store_prediction(db: Session, device_id: str) -> dict:
    """Run the model on a device's latest rows; store the result when the model is ready."""
    res = advice.advise(_recent_frame(db, device_id))
    if res.get("ready"):
        db.add(Prediction(device_id=device_id, model_name=res["model"], horizon_min=res["horizon_min"],
                          dry_pct=res["dry_pct"], probability=res["probability_dry_within_horizon"]))
        db.commit()
    return res


def run_prediction_job_once() -> int:
    """One pass of the scheduled job over every device. Returns how many predictions were stored."""
    stored = 0
    with SessionLocal() as db:
        for dev in db.scalars(select(Device)).all():
            try:
                if _store_prediction(db, dev.id).get("ready"):
                    stored += 1
            except Exception:                      # one bad device must not stop the others
                db.rollback()
    return stored


def _prediction_loop(stop: threading.Event):
    while not stop.wait(config.PREDICTION_INTERVAL_S):
        try:
            run_prediction_job_once()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    firebase_sync.init()
    client = ingest.start_mqtt() if config.MQTT_ENABLED else None
    stop = threading.Event()
    if config.PREDICTION_JOB_ENABLED:
        threading.Thread(target=_prediction_loop, args=(stop,), daemon=True, name="prediction-job").start()
    yield
    stop.set()
    if client is not None:
        client.loop_stop()
        client.disconnect()




def _bearer(authorization: str) -> str:
    return authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""


def _key_ok(x_api_key: str) -> bool:
    return bool(config.BACKEND_API_KEY) and hmac.compare_digest(x_api_key.encode(), config.BACKEND_API_KEY.encode())


def _firebase_user_ok(authorization: str) -> bool:
    """A valid Firebase login that is ALSO on the allow-list."""
    token = _bearer(authorization)
    if not token or not config.ALLOWED_FIREBASE_UIDS:
        return False
    claims = firebase_sync.verify_token(token)
    return bool(claims) and claims.get("uid") in config.ALLOWED_FIREBASE_UIDS


def require_api_key(x_api_key: str = Header(default=""), authorization: str = Header(default="")):
    """Protects every write endpoint: X-API-Key (operators/scripts) or a Firebase
    login on the allow-list (the Android app)."""
    if not config.BACKEND_API_KEY and not config.ALLOWED_FIREBASE_UIDS:
        raise HTTPException(503, "no credentials are configured on the server (BACKEND_API_KEY / ALLOWED_FIREBASE_UIDS)")
    if _key_ok(x_api_key) or _firebase_user_ok(authorization):
        return
    raise HTTPException(401, "invalid credentials")


def read_access(request: Request, x_api_key: str = Header(default=""), authorization: str = Header(default="")):
    """Applied to every route. Reads are open unless REQUIRE_AUTH_FOR_READS=true
    (then they need the key or any valid Firebase login). /health is always open."""
    if request.method != "GET" or request.url.path == "/health" or not config.REQUIRE_AUTH_FOR_READS:
        return
    if _key_ok(x_api_key):
        return
    token = _bearer(authorization)
    if token and firebase_sync.verify_token(token):
        return
    raise HTTPException(401, "authentication required")


app = FastAPI(title="JalBhoomi backend", version="0.3.0", lifespan=lifespan,
              dependencies=[Depends(read_access)])


def _device_or_404(db: Session, device_id: str) -> Device:
    d = db.get(Device, device_id)
    if d is None:
        raise HTTPException(404, "unknown device")
    return d


@app.get("/health")
def health():
    return {"status": "ok", "mqtt_enabled": config.MQTT_ENABLED}


@app.get("/devices")
def list_devices(db: Session = Depends(get_db)):
    return [d.id for d in db.scalars(select(Device).order_by(Device.id))]


@app.get("/devices/{device_id}/summaries", response_model=list[SummaryOut])
def get_summaries(device_id: str,
                  since: datetime | None = None,
                  until: datetime | None = None,
                  limit: int = Query(1440, ge=1, le=10000),
                  db: Session = Depends(get_db)):
    _device_or_404(db, device_id)
    q = select(Summary).where(Summary.device_id == device_id)
    if since:
        q = q.where(Summary.ts >= since)
    if until:
        q = q.where(Summary.ts <= until)
    q = q.order_by(Summary.ts.desc()).limit(limit)
    return list(reversed(db.scalars(q).all()))


@app.get("/devices/{device_id}/water/daily", response_model=list[DailyWater])
def water_daily(device_id: str, db: Session = Depends(get_db)):
    """Litres per day = sum of the per-minute flow volume.
    Accuracy depends on the flow sensor's calibration constant."""
    _device_or_404(db, device_id)
    day = func.date(Summary.ts)
    rows = db.execute(
        select(day, func.coalesce(func.sum(Summary.flow_l), 0.0), func.count(Summary.id))
        .where(Summary.device_id == device_id)
        .group_by(day).order_by(day)
    ).all()
    return [DailyWater(day=str(d), litres=round(float(l), 3), minutes_reported=n) for d, l, n in rows]



# ---------------------------------------------------------------- zones
@app.get("/zones", response_model=list[ZoneOut])
def list_zones(db: Session = Depends(get_db)):
    """One zone = one ESP32 node. Latest readings plus open fault count."""
    out = []
    now = datetime.now(timezone.utc)
    for d in db.scalars(select(Device).order_by(Device.id)):
        last = db.scalars(select(Summary).where(Summary.device_id == d.id)
                          .order_by(Summary.ts.desc()).limit(1)).first()
        faults = db.scalar(select(func.count(Alert.id)).where(
            Alert.device_id == d.id, Alert.severity == "fault", Alert.acknowledged == 0)) or 0
        last_ts = last.ts if last else None
        if last_ts is not None and last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        out.append(ZoneOut(
            device_id=d.id, name=d.name, auto_mode=bool(d.auto_mode), last_seen=last_ts,
            soil_pct=last.soil_pct_mean if last else None,
            temp_c=last.temp_c_mean if last else None,
            hum_pct=last.hum_pct_mean if last else None,
            pump_on_recently=bool(last and (last.pump_on_s or 0) > 0 and last_ts and now - last_ts < timedelta(minutes=5)),
            open_faults=int(faults)))
    return out


@app.put("/devices/{device_id}", dependencies=[Depends(require_api_key)])
def rename_zone(device_id: str, body: DeviceUpdate, db: Session = Depends(get_db)):
    d = _device_or_404(db, device_id)
    d.name = body.name
    db.commit()
    return {"device_id": d.id, "name": d.name}


# ---------------------------------------------------------------- alerts
@app.get("/devices/{device_id}/alerts", response_model=list[AlertOut])
def get_alerts(device_id: str,
               severity: str | None = Query(None, pattern="^(fault|notice|info)$"),
               unacknowledged: bool = False,
               limit: int = Query(100, ge=1, le=1000),
               db: Session = Depends(get_db)):
    """Events from the ESP32. Use severity=fault for hardware faults and
    severity=notice for predictive/advisory skips and rejected commands."""
    _device_or_404(db, device_id)
    q = select(Alert).where(Alert.device_id == device_id)
    if severity:
        q = q.where(Alert.severity == severity)
    if unacknowledged:
        q = q.where(Alert.acknowledged == 0)
    return db.scalars(q.order_by(Alert.ts.desc(), Alert.id.desc()).limit(limit)).all()


@app.post("/alerts/{alert_id}/ack", response_model=AlertOut, dependencies=[Depends(require_api_key)])
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db)):
    a = db.get(Alert, alert_id)
    if a is None:
        raise HTTPException(404, "unknown alert")
    a.acknowledged = 1
    db.commit()
    return a


# -------------------------------------------------------------- commands
@app.post("/devices/{device_id}/commands", response_model=CommandOut, status_code=202,
          dependencies=[Depends(require_api_key)])
def send_command(device_id: str, body: CommandIn, db: Session = Depends(get_db)):
    """Send an ADVISORY command. The ESP32 checks it against its safety
    interlocks and may reject it; read the outcome from GET .../commands."""
    _device_or_404(db, device_id)
    cmd_id = uuid.uuid4().hex[:12]
    payload = {"id": cmd_id, **body.to_payload()}
    row = Command(device_id=device_id, cmd_id=cmd_id, cmd=body.cmd,
                  params=json.dumps(body.to_payload()))
    db.add(row)
    db.commit()
    if not ingest.publish_command(device_id, payload):
        row.status, row.reason = "failed", "mqtt_unavailable"
        db.commit()
        raise HTTPException(503, f"command {cmd_id} stored but MQTT is not connected")
    return row


@app.get("/devices/{device_id}/commands", response_model=list[CommandOut])
def list_commands(device_id: str, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)):
    _device_or_404(db, device_id)
    q = select(Command).where(Command.device_id == device_id).order_by(Command.id.desc()).limit(limit)
    return db.scalars(q).all()


def _cycle_stats(db: Session, device_id: str, since: datetime | None):
    """Irrigation cycles = irrigation_start events; litres = per-minute flow sum."""
    q = select(func.coalesce(func.sum(Summary.flow_l), 0.0), func.count(Summary.id)).where(Summary.device_id == device_id)
    if since:
        q = q.where(Summary.ts >= since)
    total, minutes = db.execute(q).one()
    cq = select(func.count(Alert.id)).where(Alert.device_id == device_id, Alert.type == "irrigation_start")
    if since:
        cq = cq.where(Alert.ts >= since)
    cycles = db.scalar(cq) or 0
    return float(total), int(minutes), int(cycles)


@app.get("/devices/{device_id}/water/total", response_model=WaterTotal)
def water_total(device_id: str, since: datetime | None = None, db: Session = Depends(get_db)):
    """Cumulative water consumption (litres) from the flow sensor."""
    _device_or_404(db, device_id)
    total, minutes, cycles = _cycle_stats(db, device_id, since)
    return WaterTotal(total_litres=round(total, 3), minutes_reported=minutes, irrigation_cycles=cycles,
                      avg_litres_per_cycle=round(total / cycles, 3) if cycles else None,
                      note="Litres depend on the flow sensor's calibration constant.")


@app.get("/devices/{device_id}/water/saved", response_model=WaterSaved)
def water_saved(device_id: str, since: datetime | None = None, db: Session = Depends(get_db)):
    """ESTIMATE of water not used because irrigation was skipped on a forecast
    or advice. = skip episodes x average litres per measured irrigation cycle.
    It is not a measurement and needs a controlled trial to be a real claim."""
    _device_or_404(db, device_id)
    total, _minutes, cycles = _cycle_stats(db, device_id, since)
    sq = select(func.count(Alert.id)).where(Alert.device_id == device_id,
                                            Alert.type.in_(("predictive_skip", "advisory_skip")))
    if since:
        sq = sq.where(Alert.ts >= since)
    skips = int(db.scalar(sq) or 0)
    avg = total / cycles if cycles else None
    return WaterSaved(skip_episodes=skips, cycles_measured=cycles,
                      avg_litres_per_cycle=round(avg, 3) if avg is not None else None,
                      litres_avoided_estimate=round(skips * avg, 3) if avg is not None else None,
                      basis="skip episodes (one alert per 30 min while skipping) x mean litres per measured cycle; an estimate")


@app.get("/devices/{device_id}/advice")
def get_advice(device_id: str, db: Session = Depends(get_db)):
    """Probability that soil moisture drops below the dry threshold within the
    model's horizon. Returns model_loaded=false until a model trained on real
    data is installed (see ml/README.md)."""
    _device_or_404(db, device_id)
    df = _recent_frame(db, device_id)
    return advice.advise(df)



def _recent_frame(db: Session, device_id: str) -> pd.DataFrame:
    rows = db.scalars(select(Summary).where(Summary.device_id == device_id)
                      .order_by(Summary.ts.desc()).limit(120)).all()
    return pd.DataFrame([{"epoch": int(r.ts.timestamp()) if r.ts_from_device else 0,
                          "soil_pct_mean": r.soil_pct_mean, "temp_c_mean": r.temp_c_mean,
                          "hum_pct_mean": r.hum_pct_mean, "rain": r.rain,
                          "pump_on_s": r.pump_on_s} for r in reversed(rows)])


@app.post("/devices/{device_id}/predictions/run", dependencies=[Depends(require_api_key)])
def run_prediction(device_id: str, db: Session = Depends(get_db)):
    """Run the model on the latest rows and store the result."""
    _device_or_404(db, device_id)
    return _store_prediction(db, device_id)


@app.get("/devices/{device_id}/predictions", response_model=list[PredictionOut])
def list_predictions(device_id: str, limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db)):
    _device_or_404(db, device_id)
    return db.scalars(select(Prediction).where(Prediction.device_id == device_id)
                      .order_by(Prediction.ts.desc()).limit(limit)).all()


# ------------------------------------------------------------ thresholds
@app.get("/devices/{device_id}/thresholds", response_model=ThresholdIO)
def get_thresholds(device_id: str, db: Session = Depends(get_db)):
    _device_or_404(db, device_id)
    t = db.get(Threshold, device_id)
    return ThresholdIO.model_validate(t) if t else ThresholdIO()


@app.put("/devices/{device_id}/thresholds", response_model=ThresholdOut,
         dependencies=[Depends(require_api_key)])
def put_thresholds(device_id: str, body: ThresholdIO, db: Session = Depends(get_db)):
    """Stores the farmer's setting and sends it to the device as an advisory
    set_thresholds command (the ESP32 validates it before applying)."""
    _device_or_404(db, device_id)
    t = db.get(Threshold, device_id) or Threshold(device_id=device_id)
    t.soil_dry_pct, t.soil_wet_pct, t.rain_probability = body.soil_dry_pct, body.soil_wet_pct, body.rain_probability
    db.add(t)
    cmd_id = uuid.uuid4().hex[:12]
    params = {"cmd": "set_thresholds", "soil_dry_pct": body.soil_dry_pct,
              "soil_wet_pct": body.soil_wet_pct, "rain_probability": body.rain_probability}
    db.add(Command(device_id=device_id, cmd_id=cmd_id, cmd="set_thresholds", params=json.dumps(params)))
    db.commit()
    pushed = ingest.publish_command(device_id, {"id": cmd_id, **params})
    if not pushed:
        c = db.execute(select(Command).where(Command.cmd_id == cmd_id)).scalar_one()
        c.status, c.reason = "failed", "mqtt_unavailable"
        db.commit()
    return ThresholdOut(**body.model_dump(), pushed_to_device=pushed)
