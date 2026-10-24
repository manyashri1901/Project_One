"""MQTT <-> backend: ingestion of summaries, events and acks, and publishing of
advisory commands. The ESP32 decides what to do with a command."""
import json
import logging
import ssl
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import config, firebase_sync
from .db import SessionLocal
from .models import Alert, Command, Device, Summary
from .schemas import AckIn, EventIn, SummaryIn

log = logging.getLogger("jalbhoomi.ingest")

_client = None   # paho client once start_mqtt() has run


def _device_ts(epoch: int, received_at: datetime):
    if epoch > 1_700_000_000:
        return datetime.fromtimestamp(epoch, tz=timezone.utc), 1
    return received_at, 0   # device had no NTP time yet


def _ensure_device(db: Session, device_id: str):
    if db.get(Device, device_id) is None:
        db.add(Device(id=device_id))
        db.flush()


def store_summary(db: Session, msg: SummaryIn, received_at: datetime | None = None) -> bool:
    """Insert one summary row. Returns False if it was a duplicate."""
    received_at = received_at or datetime.now(timezone.utc)
    ts, from_dev = _device_ts(msg.epoch, received_at)
    _ensure_device(db, msg.device_id)

    exists = db.execute(
        select(Summary.id).where(Summary.device_id == msg.device_id,
                                 Summary.ts == ts, Summary.uptime_s == msg.uptime_s)
    ).first()
    if exists:
        return False
    db.add(Summary(
        device_id=msg.device_id, ts=ts, ts_from_device=from_dev,
        uptime_s=msg.uptime_s, samples=msg.samples,
        soil_raw_mean=msg.soil_raw_mean, soil_raw_min=msg.soil_raw_min,
        soil_raw_max=msg.soil_raw_max, soil_pct_mean=msg.soil_pct_mean,
        temp_c_mean=msg.temp_c_mean, hum_pct_mean=msg.hum_pct_mean,
        flow_pulses=msg.flow_pulses, flow_l=msg.flow_L, pump_on_s=msg.pump_on_s,
        faults=msg.faults, rain=msg.rain, water_added_ml=msg.water_added_ml,
    ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    firebase_sync.push_status(msg.device_id, {
        "ts": int(ts.timestamp()), "soil_pct": msg.soil_pct_mean, "temp_c": msg.temp_c_mean,
        "hum_pct": msg.hum_pct_mean, "flow_l": msg.flow_L, "pump_on_s": msg.pump_on_s,
        "faults": msg.faults, "rain": msg.rain})
    return True


def severity_of(event_type: str) -> str:
    if event_type.startswith("fault_"):
        return "fault"
    if event_type in ("predictive_skip", "advisory_skip", "command_rejected"):
        return "notice"
    return "info"


def store_event(db: Session, msg: EventIn, received_at: datetime | None = None) -> bool:
    received_at = received_at or datetime.now(timezone.utc)
    ts, _ = _device_ts(msg.epoch, received_at)
    _ensure_device(db, msg.device_id)
    exists = db.execute(
        select(Alert.id).where(Alert.device_id == msg.device_id, Alert.ts == ts,
                               Alert.uptime_s == msg.uptime_s, Alert.type == msg.type)
    ).first()
    if exists:
        return False
    sev = severity_of(msg.type)
    db.add(Alert(device_id=msg.device_id, ts=ts, uptime_s=msg.uptime_s, type=msg.type,
                 severity=sev, detail=msg.detail))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    firebase_sync.notify_alert(msg.device_id, msg.type, sev, msg.detail)
    return True


def store_ack(db: Session, msg: AckIn) -> bool:
    """Mark the matching command accepted/rejected. False if it is unknown."""
    cmd = db.execute(select(Command).where(Command.cmd_id == msg.id,
                                           Command.device_id == msg.device_id)).scalar_one_or_none()
    if cmd is None:
        return False
    cmd.status = "accepted" if msg.accepted else "rejected"
    cmd.reason = msg.reason or None
    cmd.acked_at = datetime.now(timezone.utc)
    if msg.accepted and cmd.cmd == "set_auto":
        try:
            enabled = bool(json.loads(cmd.params).get("enabled", True))
            dev = db.get(Device, msg.device_id)
            if dev is not None:
                dev.auto_mode = 1 if enabled else 0
        except (ValueError, TypeError):
            pass
    db.commit()
    return True


def handle_message(topic: str, payload: bytes) -> bool:
    """Route one MQTT message by its topic leaf (summary | event | ack).
    Never raises on bad input; returns True only if something was stored."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[2] not in ("summary", "event", "ack"):
        return False
    device_from_topic, leaf = parts[1], parts[2]
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("payload is not a JSON object")
        # The topic carries the device id; it must match the payload.
        if data.get("device_id") != device_from_topic:
            log.warning("device_id mismatch: topic=%s payload=%s", device_from_topic, data.get("device_id"))
            return False
        model = {"summary": SummaryIn, "event": EventIn, "ack": AckIn}[leaf].model_validate(data)
    except (ValueError, ValidationError) as exc:
        log.warning("rejected %s message from %s: %s", leaf, device_from_topic, exc)
        return False

    with SessionLocal() as db:
        if leaf == "summary":
            return store_summary(db, model)
        if leaf == "event":
            return store_event(db, model)
        return store_ack(db, model)


def publish_command(device_id: str, payload: dict) -> bool:
    """Publish an advisory command. False if MQTT is not connected."""
    if _client is None or not _client.is_connected():
        return False
    topic = f"{config.MQTT_TOPIC_PREFIX}/{device_id}/cmd"
    info = _client.publish(topic, json.dumps(payload), qos=1)
    return info.rc == 0


def start_mqtt():
    """Start the background MQTT client (called from the app lifespan)."""
    global _client
    import paho.mqtt.client as mqtt

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="jalbhoomi-backend")
    if config.MQTT_USER:
        client.username_pw_set(config.MQTT_USER, config.MQTT_PASS)
    if config.MQTT_TLS:
        client.tls_set(ca_certs=config.MQTT_CA_CERT or None, cert_reqs=ssl.CERT_REQUIRED)

    def on_connect(c, _userdata, _flags, reason_code, _props):
        log.info("MQTT connected (%s)", reason_code)
        for leaf in ("summary", "event", "ack"):
            c.subscribe(f"{config.MQTT_TOPIC_PREFIX}/+/{leaf}", qos=1)

    def on_message(_c, _userdata, m):
        handle_message(m.topic, m.payload)

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=60)
    client.loop_start()
    _client = client
    return client
