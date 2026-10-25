"""Consolidated data, watering recommendation, usage limits and the web dashboard.

Endpoints (reads are open unless REQUIRE_AUTH_FOR_READS; writes need the API key or an allow-listed login):
  GET  /dashboard                              the web dashboard (works on a phone browser)
  GET  /devices/{id}/cycles?days=14&source=local|cloud
  GET  /devices/{id}/daily?days=14&source=local|cloud
  GET  /devices/{id}/recommendation
  GET  /devices/{id}/usage-limits
  PUT  /devices/{id}/usage-limits              (write)
  POST /devices/{id}/usage-check               (write)  raise too-much / too-little / soil-dry alerts now
  POST /devices/{id}/consolidate/sync          (write)  copy ONLY the consolidated rows to CLOUD_DATABASE_URL
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from . import advisor, consolidate
from .db import get_db
from .models import Device, Threshold, UsageLimit

STATIC = Path(__file__).resolve().parent / "static"


class UsageLimitIO(BaseModel):
    daily_min_litres: float | None = Field(default=None, ge=0)
    daily_max_litres: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _order(self):
        lo, hi = self.daily_min_litres, self.daily_max_litres
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("daily_min_litres must not be above daily_max_litres")
        return self


def make_router(api_key_dep) -> APIRouter:
    r = APIRouter()

    def device_or_404(db: Session, device_id: str) -> Device:
        d = db.get(Device, device_id)
        if d is None:
            raise HTTPException(404, "unknown device")
        return d

    def consolidated(db: Session, device_id: str, days: int, source: str):
        if source == "cloud":
            eng = consolidate.cloud_engine()
            if eng is None:
                raise HTTPException(503, "CLOUD_DATABASE_URL is not set on the server")
            return consolidate.read_cloud(eng, device_id, days)
        return consolidate.local_consolidated(db, device_id, days)

    @r.get("/dashboard", include_in_schema=False)
    def dashboard_page():
        return FileResponse(STATIC / "dashboard.html", media_type="text/html")

    @r.get("/devices/{device_id}/cycles")
    def get_cycles(device_id: str, days: int = Query(14, ge=1, le=365),
                   source: str = Query("local", pattern="^(local|cloud)$"), db: Session = Depends(get_db)):
        """Consolidated irrigation cycles: start, run time, litres, soil before and after, outcome."""
        device_or_404(db, device_id)
        return consolidated(db, device_id, days, source)[0]

    @r.get("/devices/{device_id}/daily")
    def get_daily(device_id: str, days: int = Query(14, ge=1, le=365),
                  source: str = Query("local", pattern="^(local|cloud)$"), db: Session = Depends(get_db)):
        """Consolidated daily rows (Indian Standard Time days) with the usage status against the limits."""
        device_or_404(db, device_id)
        return consolidated(db, device_id, days, source)[1]

    @r.get("/devices/{device_id}/recommendation")
    def get_recommendation(device_id: str, db: Session = Depends(get_db)):
        device_or_404(db, device_id)
        now = datetime.now(timezone.utc)
        rows = consolidate.load_rows(db, device_id, now - timedelta(hours=6))
        cycles, daily = consolidate.local_consolidated(db, device_id, 14, now)
        today = consolidate.ist_day(now)
        today_litres = next((d["litres"] for d in daily if d["day"] == today), None)
        th = db.get(Threshold, device_id)
        dry, wet = (th.soil_dry_pct, th.soil_wet_pct) if th else (30, 60)
        return advisor.recommend(rows, dry, wet, cycles, today_litres, db.get(UsageLimit, device_id))

    @r.get("/devices/{device_id}/usage-limits", response_model=UsageLimitIO)
    def get_limits(device_id: str, db: Session = Depends(get_db)):
        device_or_404(db, device_id)
        u = db.get(UsageLimit, device_id)
        return UsageLimitIO(daily_min_litres=u.daily_min_litres, daily_max_litres=u.daily_max_litres) if u else UsageLimitIO()

    @r.put("/devices/{device_id}/usage-limits", response_model=UsageLimitIO, dependencies=[Depends(api_key_dep)])
    def put_limits(device_id: str, body: UsageLimitIO, db: Session = Depends(get_db)):
        """Set the daily water limits. Leave a field empty to switch that limit off."""
        device_or_404(db, device_id)
        u = db.get(UsageLimit, device_id) or UsageLimit(device_id=device_id)
        u.daily_min_litres, u.daily_max_litres = body.daily_min_litres, body.daily_max_litres
        db.add(u)
        db.commit()
        return body

    @r.post("/devices/{device_id}/usage-check", dependencies=[Depends(api_key_dep)])
    def usage_check(device_id: str, db: Session = Depends(get_db)):
        device_or_404(db, device_id)
        return {"alerts_created": consolidate.run_usage_check(db, device_id)}

    @r.post("/devices/{device_id}/consolidate/sync", dependencies=[Depends(api_key_dep)])
    def sync(device_id: str, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
        """Copy only the consolidated cycles and daily rows to the cloud database."""
        device_or_404(db, device_id)
        eng = consolidate.cloud_engine()
        if eng is None:
            raise HTTPException(503, "CLOUD_DATABASE_URL is not set on the server")
        n_cycles, n_daily = consolidate.sync_to_cloud(db, eng, device_id, days)
        return {"cycles_synced": n_cycles, "daily_rows_synced": n_daily,
                "note": "only consolidated rows were sent; per-minute readings stay in the local database"}

    return r