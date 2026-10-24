from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SummaryIn(BaseModel):
    """JSON published by the firmware on jalbhoomi/<device_id>/summary.
    Field names match the firmware's summary.csv header."""
    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(min_length=1, max_length=64)
    epoch: int = 0
    uptime_s: int = 0
    samples: int = 0
    soil_raw_mean: float | None = None
    soil_raw_min: int | None = None
    soil_raw_max: int | None = None
    soil_pct_mean: float | None = None
    temp_c_mean: float | None = None
    hum_pct_mean: float | None = None
    flow_pulses: int | None = None
    flow_L: float | None = None
    pump_on_s: int | None = None
    faults: int | None = None
    rain: int | None = None
    water_added_ml: float | None = None

    @model_validator(mode="after")
    def _sane(self):
        if self.soil_pct_mean is not None and not 0 <= self.soil_pct_mean <= 100:
            raise ValueError("soil_pct_mean out of range")
        if self.samples < 0 or self.uptime_s < 0:
            raise ValueError("negative counters")
        return self


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    ts: datetime
    ts_from_device: int
    uptime_s: int
    samples: int
    soil_raw_mean: float | None
    soil_raw_min: int | None
    soil_raw_max: int | None
    soil_pct_mean: float | None
    temp_c_mean: float | None
    hum_pct_mean: float | None
    flow_pulses: int | None
    flow_l: float | None
    pump_on_s: int | None
    faults: int | None
    rain: int | None
    water_added_ml: float | None


class ThresholdIO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    soil_dry_pct: int = Field(30, ge=0, le=100)
    soil_wet_pct: int = Field(60, ge=0, le=100)
    rain_probability: float = Field(0.40, ge=0, le=1)

    @model_validator(mode="after")
    def _order(self):
        if self.soil_dry_pct >= self.soil_wet_pct:
            raise ValueError("soil_dry_pct must be below soil_wet_pct")
        return self


class DailyWater(BaseModel):
    day: str
    litres: float
    minutes_reported: int


class EventIn(BaseModel):
    """JSON published by the firmware on jalbhoomi/<device_id>/event."""
    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(min_length=1, max_length=64)
    epoch: int = 0
    uptime_s: int = 0
    type: str = Field(pattern=r"^[a-z_]{1,40}$")
    detail: str | None = Field(default=None, max_length=120)


class AckIn(BaseModel):
    """JSON published by the firmware on jalbhoomi/<device_id>/ack."""
    model_config = ConfigDict(extra="ignore")

    device_id: str = Field(min_length=1, max_length=64)
    id: str = Field(default="", max_length=40)
    cmd: str = Field(default="", max_length=40)
    accepted: bool
    reason: str = Field(default="", max_length=40)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: str
    ts: datetime
    uptime_s: int
    type: str
    severity: str
    detail: str | None
    acknowledged: int


class CommandIn(BaseModel):
    """Body of POST /devices/{id}/commands. Advisory only: the ESP32 may refuse."""
    cmd: Literal["override_on", "override_off", "set_thresholds", "set_auto", "advise"]
    duration_s: int | None = Field(default=None, ge=1, le=600)       # override_on / advise irrigate
    soil_dry_pct: int | None = Field(default=None, ge=0, le=100)     # set_thresholds
    soil_wet_pct: int | None = Field(default=None, ge=0, le=100)
    rain_probability: float | None = Field(default=None, ge=0, le=1)
    action: Literal["skip", "irrigate"] | None = None                # advise
    hours: int | None = Field(default=None, ge=1, le=24)             # advise skip
    enabled: bool | None = None                                      # set_auto

    @model_validator(mode="after")
    def _check(self):
        if self.cmd == "set_thresholds":
            if self.soil_dry_pct is None or self.soil_wet_pct is None:
                raise ValueError("set_thresholds needs soil_dry_pct and soil_wet_pct")
            if self.soil_dry_pct >= self.soil_wet_pct:
                raise ValueError("soil_dry_pct must be below soil_wet_pct")
        if self.cmd == "set_auto" and self.enabled is None:
            raise ValueError("set_auto needs enabled = true | false")
        if self.cmd == "advise" and self.action is None:
            raise ValueError("advise needs action = skip | irrigate")
        return self

    def to_payload(self) -> dict:
        d = {k: v for k, v in self.model_dump().items() if v is not None}
        return d


class CommandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cmd_id: str
    device_id: str
    cmd: str
    params: str
    created_at: datetime
    status: str
    reason: str | None
    acked_at: datetime | None


class ThresholdOut(ThresholdIO):
    pushed_to_device: bool = False


class DeviceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ZoneOut(BaseModel):
    device_id: str
    name: str | None
    auto_mode: bool
    last_seen: datetime | None
    soil_pct: float | None
    temp_c: float | None
    hum_pct: float | None
    pump_on_recently: bool
    open_faults: int


class WaterTotal(BaseModel):
    total_litres: float
    minutes_reported: int
    irrigation_cycles: int
    avg_litres_per_cycle: float | None
    note: str


class WaterSaved(BaseModel):
    estimate: bool = True
    skip_episodes: int
    cycles_measured: int
    avg_litres_per_cycle: float | None
    litres_avoided_estimate: float | None
    basis: str


class PredictionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: str
    ts: datetime
    model_name: str
    horizon_min: int
    dry_pct: float
    probability: float
