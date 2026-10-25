"""Consolidation: turn the local per-minute rows into the FINAL consolidated data.

Faculty requirement: every timestamped reading stays in the LOCAL database; only consolidated data is stored
in the cloud and used for graphs. Consolidated data here means

  * one row per irrigation cycle  (start, run time, litres, soil before and after, outcome)
  * one row per day               (litres, cycles, soil min/mean/max, temperature, humidity, skips, faults,
                                   usage status against the farmer's limits)

A cycle is a run of consecutive per-minute rows in which the pump was on (pump_on_s > 0). Days are Indian
Standard Time days. Nothing here invents data: a value that was not measured stays None.
"""
from __future__ import annotations

import logging
import statistics
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from sqlalchemy import DateTime, Float, Integer, String, create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from . import config, firebase_sync
from .models import Alert, Summary, Threshold, UsageLimit

log = logging.getLogger("jalbhoomi.consolidate")

IST = timezone(timedelta(hours=5, minutes=30))     # India has no daylight saving time
CYCLE_GAP_S = 180                                  # rows further apart than this are not the same cycle
SKIP_TYPES = ("predictive_skip", "advisory_skip")


def aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def ist_day(ts: datetime) -> str:
    return aware(ts).astimezone(IST).strftime("%Y-%m-%d")


def _r(v, n=2):
    return None if v is None else round(float(v), n)


# ---------------------------------------------------------------------------- cycles
def build_cycles(rows) -> list[dict]:
    """rows: Summary-like objects sorted by ts (attributes ts, pump_on_s, flow_l, soil_pct_mean, faults)."""
    cycles: list[dict] = []
    cur: dict | None = None
    prev = None

    def close():
        nonlocal cur
        if cur is None:
            return
        lit = cur.pop("_litres")
        cur["litres"] = _r(sum(lit), 3) if lit else None
        cur["soil_before"] = _r(cur["soil_before"], 1)
        cur["soil_after"] = _r(cur["soil_after"], 1)
        if cur["soil_before"] is not None and cur["soil_after"] is not None:
            cur["soil_rise"] = _r(cur["soil_after"] - cur["soil_before"], 1)
        else:
            cur["soil_rise"] = None
        cur["outcome"] = "fault" if cur.pop("_faults") > 0 else "normal"
        cur.pop("_last_ts")
        cycles.append(cur)
        cur = None

    for r in rows:
        ts = aware(r.ts)
        on = (r.pump_on_s or 0) > 0
        if on:
            if cur is not None and (ts - cur["_last_ts"]).total_seconds() <= CYCLE_GAP_S:
                cur["end_ts"] = ts
                cur["_last_ts"] = ts
                cur["minutes"] += 1
                cur["pump_s"] += int(r.pump_on_s or 0)
                if r.flow_l is not None:
                    cur["_litres"].append(float(r.flow_l))
                cur["_faults"] += int(r.faults or 0)
                if r.soil_pct_mean is not None:
                    cur["soil_after"] = float(r.soil_pct_mean)
            else:
                close()
                before = None
                if prev is not None and (ts - aware(prev.ts)).total_seconds() <= CYCLE_GAP_S:
                    before = prev.soil_pct_mean
                if before is None:
                    before = r.soil_pct_mean
                cur = {"start_ts": ts, "end_ts": ts, "_last_ts": ts, "minutes": 1,
                       "pump_s": int(r.pump_on_s or 0),
                       "_litres": [float(r.flow_l)] if r.flow_l is not None else [],
                       "_faults": int(r.faults or 0),
                       "soil_before": before, "soil_after": r.soil_pct_mean}
        elif cur is not None:
            if (ts - cur["_last_ts"]).total_seconds() <= CYCLE_GAP_S and r.soil_pct_mean is not None:
                cur["soil_after"] = float(r.soil_pct_mean)
            close()
        prev = r
    close()
    return cycles


# ---------------------------------------------------------------------------- daily
def usage_status(litres, limits, day: str, today: str) -> str:
    lo = getattr(limits, "daily_min_litres", None) if limits else None
    hi = getattr(limits, "daily_max_litres", None) if limits else None
    if lo is None and hi is None:
        return "no_limit"
    if litres is None:
        return "no_flow_data"
    if hi is not None and litres > hi:
        return "high"
    if lo is not None and day < today and litres < lo:     # "too little" is judged only on finished days
        return "low"
    return "ok"


def alert_counts(db: Session, device_id: str, since: datetime | None = None) -> tuple[dict, dict]:
    """(skips per IST day, faults per IST day) from the alerts table."""
    q = select(Alert).where(Alert.device_id == device_id)
    if since is not None:
        q = q.where(Alert.ts >= since)
    skips: dict[str, int] = {}
    faults: dict[str, int] = {}
    for a in db.scalars(q):
        d = ist_day(a.ts)
        if a.type in SKIP_TYPES:
            skips[d] = skips.get(d, 0) + 1
        if a.severity == "fault":
            faults[d] = faults.get(d, 0) + 1
    return skips, faults


def build_daily(rows, cycles, skips: dict, faults: dict, limits=None, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    today = ist_day(now)
    days: dict[str, dict] = {}
    for r in rows:
        d = ist_day(r.ts)
        s = days.setdefault(d, {"n": 0, "soil": [], "temp": [], "hum": [], "lit": []})
        s["n"] += 1
        if r.soil_pct_mean is not None:
            s["soil"].append(float(r.soil_pct_mean))
        if r.temp_c_mean is not None:
            s["temp"].append(float(r.temp_c_mean))
        if r.hum_pct_mean is not None:
            s["hum"].append(float(r.hum_pct_mean))
        if r.flow_l is not None:
            s["lit"].append(float(r.flow_l))
    n_cycles: dict[str, int] = {}
    for c in cycles:
        d = ist_day(c["start_ts"])
        n_cycles[d] = n_cycles.get(d, 0) + 1
    out = []
    for d in sorted(days):
        s = days[d]
        litres = _r(sum(s["lit"]), 3) if s["lit"] else None
        out.append({
            "day": d, "minutes_reported": s["n"], "litres": litres, "cycles": n_cycles.get(d, 0),
            "soil_min": _r(min(s["soil"]), 1) if s["soil"] else None,
            "soil_mean": _r(statistics.fmean(s["soil"]), 1) if s["soil"] else None,
            "soil_max": _r(max(s["soil"]), 1) if s["soil"] else None,
            "temp_mean": _r(statistics.fmean(s["temp"]), 1) if s["temp"] else None,
            "hum_mean": _r(statistics.fmean(s["hum"]), 1) if s["hum"] else None,
            "skips": skips.get(d, 0), "faults": faults.get(d, 0),
            "usage_status": usage_status(litres, limits, d, today),
        })
    return out


# ---------------------------------------------------------------------------- local reads
def load_rows(db: Session, device_id: str, since: datetime | None = None):
    q = select(Summary).where(Summary.device_id == device_id)
    if since is not None:
        q = q.where(Summary.ts >= since)
    return db.scalars(q.order_by(Summary.ts)).all()


def local_consolidated(db: Session, device_id: str, days: int = 14, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    rows = load_rows(db, device_id, since)
    cycles = build_cycles(rows)
    skips, faults = alert_counts(db, device_id, since)
    limits = db.get(UsageLimit, device_id)
    return cycles, build_daily(rows, cycles, skips, faults, limits, now)


# ---------------------------------------------------------------------------- usage alerts
def _make_alert(db: Session, device_id: str, ts: datetime, typ: str, detail: str) -> bool:
    """One alert per (device, ts, type). Returns True if it was newly created."""
    exists = db.execute(select(Alert.id).where(Alert.device_id == device_id, Alert.ts == ts,
                                               Alert.uptime_s == 0, Alert.type == typ)).first()
    if exists:
        return False
    db.add(Alert(device_id=device_id, ts=ts, uptime_s=0, type=typ, severity="notice", detail=detail[:120]))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    firebase_sync.notify_alert(device_id, typ, "notice", detail)
    return True


def run_usage_check(db: Session, device_id: str, now: datetime | None = None) -> list[str]:
    """Raise water-usage and soil-dry alerts. Returns the types created. Needs limits for the usage alerts."""
    now = now or datetime.now(timezone.utc)
    created: list[str] = []
    limits = db.get(UsageLimit, device_id)
    cycles, daily = local_consolidated(db, device_id, days=7, now=now)
    if limits is not None:
        for d in daily:
            if d["usage_status"] not in ("high", "low"):
                continue
            midnight = datetime.strptime(d["day"], "%Y-%m-%d").replace(tzinfo=IST).astimezone(timezone.utc)
            if d["usage_status"] == "high":
                typ, detail = "water_usage_high", f"{d['litres']:.1f} L used on {d['day']}, limit {limits.daily_max_litres:g} L"
            else:
                typ, detail = "water_usage_low", f"{d['litres']:.1f} L used on {d['day']}, minimum {limits.daily_min_litres:g} L"
            if _make_alert(db, device_id, midnight, typ, detail):
                created.append(typ)

    # soil dry for too long (pump off) at the latest rows
    th = db.get(Threshold, device_id)
    dry = th.soil_dry_pct if th else 30
    recent = load_rows(db, device_id, now - timedelta(hours=12))
    start = None
    for r in recent:
        if r.soil_pct_mean is not None and r.soil_pct_mean < dry and (r.pump_on_s or 0) == 0:
            start = start or aware(r.ts)
        else:
            start = None
    if start is not None and recent:
        last = aware(recent[-1].ts)
        mins = (last - start).total_seconds() / 60
        if mins >= config.SOIL_ALERT_MINUTES and _make_alert(
                db, device_id, start, "soil_dry_long",
                f"Soil below {dry}% for {int(mins)} min with the pump off"):
            created.append("soil_dry_long")
    return created


def run_usage_checks_all(session_factory) -> int:
    """Used by the scheduled job: check every device. One failing device does not stop the others."""
    from .models import Device
    n = 0
    with session_factory() as db:
        for dev in db.scalars(select(Device)).all():
            try:
                n += len(run_usage_check(db, dev.id))
            except Exception:       # noqa: BLE001
                db.rollback()
                log.exception("usage check failed for %s", dev.id)
    return n


# ---------------------------------------------------------------------------- cloud (consolidated only)
class CloudBase(DeclarativeBase):
    pass


class ConsolidatedCycle(CloudBase):
    __tablename__ = "consolidated_cycles"
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int] = mapped_column(Integer)
    pump_s: Mapped[int] = mapped_column(Integer)
    litres: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_rise: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(10))


class ConsolidatedDaily(CloudBase):
    __tablename__ = "consolidated_daily"
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    minutes_reported: Mapped[int] = mapped_column(Integer)
    litres: Mapped[float | None] = mapped_column(Float, nullable=True)
    cycles: Mapped[int] = mapped_column(Integer)
    soil_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    hum_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    skips: Mapped[int] = mapped_column(Integer)
    faults: Mapped[int] = mapped_column(Integer)
    usage_status: Mapped[str] = mapped_column(String(16))


def make_cloud_engine(url: str):
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=args, pool_pre_ping=True)
    CloudBase.metadata.create_all(eng)
    return eng


_CLOUD_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _cached_engine(url: str):
    return make_cloud_engine(url)


def _configured_engine(url: str):
    # The dashboard asks for daily rows and cycles at the same moment. Without the lock both requests try to
    # create the cloud tables for the first time and the second one fails with "table already exists".
    with _CLOUD_LOCK:
        return _cached_engine(url)


def cloud_engine():
    """Engine of the cloud database from CLOUD_DATABASE_URL, or None when not configured."""
    return _configured_engine(config.CLOUD_DATABASE_URL) if config.CLOUD_DATABASE_URL else None


def sync_to_cloud(db: Session, engine, device_id: str, days: int = 30, now: datetime | None = None) -> tuple[int, int]:
    """Copy ONLY the consolidated cycles and daily rows of one device to the cloud database (upsert).
    Per-minute rows and raw readings never leave the local database."""
    cycles, daily = local_consolidated(db, device_id, days, now)
    with Session(engine) as cs:
        for c in cycles:
            cs.merge(ConsolidatedCycle(
                device_id=device_id, start_ts=c["start_ts"].astimezone(timezone.utc),
                end_ts=c["end_ts"].astimezone(timezone.utc), minutes=c["minutes"], pump_s=c["pump_s"],
                litres=c["litres"], soil_before=c["soil_before"], soil_after=c["soil_after"],
                soil_rise=c["soil_rise"], outcome=c["outcome"]))
        for d in daily:
            cs.merge(ConsolidatedDaily(device_id=device_id, **d))
        cs.commit()
    return len(cycles), len(daily)


def read_cloud(engine, device_id: str, days: int = 14, now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    since_day = (now - timedelta(days=days)).astimezone(IST).strftime("%Y-%m-%d")
    since_ts = now - timedelta(days=days)
    with Session(engine) as cs:
        cyc = cs.scalars(select(ConsolidatedCycle).where(ConsolidatedCycle.device_id == device_id)
                         .order_by(ConsolidatedCycle.start_ts)).all()
        dly = cs.scalars(select(ConsolidatedDaily).where(ConsolidatedDaily.device_id == device_id,
                                                          ConsolidatedDaily.day >= since_day)
                         .order_by(ConsolidatedDaily.day)).all()
    cycles = [{"start_ts": aware(c.start_ts), "end_ts": aware(c.end_ts), "minutes": c.minutes, "pump_s": c.pump_s,
               "litres": c.litres, "soil_before": c.soil_before, "soil_after": c.soil_after,
               "soil_rise": c.soil_rise, "outcome": c.outcome}
              for c in cyc if aware(c.start_ts) >= since_ts]
    daily = [{"day": d.day, "minutes_reported": d.minutes_reported, "litres": d.litres, "cycles": d.cycles,
              "soil_min": d.soil_min, "soil_mean": d.soil_mean, "soil_max": d.soil_max,
              "temp_mean": d.temp_mean, "hum_mean": d.hum_mean, "skips": d.skips, "faults": d.faults,
              "usage_status": d.usage_status} for d in dly]
    return cycles, daily