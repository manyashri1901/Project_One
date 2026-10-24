from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Device(Base):
    """One ESP32 sensor-and-actuator node."""
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(80), nullable=True)      # farmer-facing zone name
    auto_mode: Mapped[int] = mapped_column(Integer, default=1)               # 0 after an accepted set_auto(false)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    summaries: Mapped[list["Summary"]] = relationship(back_populates="device")
    threshold: Mapped["Threshold | None"] = relationship(back_populates="device", uselist=False)


class Summary(Base):
    """One consolidated row per minute, as published by the firmware.
    Raw sensor logs never reach this table; they stay on the ESP32."""
    __tablename__ = "summaries"
    __table_args__ = (UniqueConstraint("device_id", "ts", "uptime_s", name="uq_summary_device_ts_uptime"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    # Device timestamp if the ESP32 had NTP time, otherwise server receive time.
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ts_from_device: Mapped[int] = mapped_column(Integer, default=0)  # 1 if ts came from the device clock
    uptime_s: Mapped[int] = mapped_column(Integer, default=0)
    samples: Mapped[int] = mapped_column(Integer, default=0)
    soil_raw_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_raw_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    soil_raw_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    soil_pct_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_c_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    hum_pct_mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    flow_pulses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flow_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    pump_on_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    faults: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rain: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1/0, null = forecast unknown
    water_added_ml: Mapped[float | None] = mapped_column(Float, nullable=True)  # experiment markers

    device: Mapped[Device] = relationship(back_populates="summaries")


class Threshold(Base):
    """Farmer-set irrigation thresholds (advisory; the ESP32 arbitrates locally)."""
    __tablename__ = "thresholds"

    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), primary_key=True)
    soil_dry_pct: Mapped[int] = mapped_column(Integer, default=30)
    soil_wet_pct: Mapped[int] = mapped_column(Integer, default=60)
    rain_probability: Mapped[float] = mapped_column(Float, default=0.40)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    device: Mapped[Device] = relationship(back_populates="threshold")


class Alert(Base):
    """Event published by the ESP32 (irrigation start/stop, predictive skip,
    faults, rejected commands). severity: fault | notice | info."""
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("device_id", "ts", "uptime_s", "type", name="uq_alert"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    uptime_s: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), default="info")
    detail: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged: Mapped[int] = mapped_column(Integer, default=0)


class Command(Base):
    """Advisory command sent to a device. The ESP32 arbitrates it against its
    safety interlocks and replies with an ack (accepted / rejected + reason)."""
    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    cmd_id: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    cmd: Mapped[str] = mapped_column(String(40))
    params: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="sent")   # sent | accepted | rejected | failed
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Prediction(Base):
    """Output of the predictive model for one device at one time (advisory)."""
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    model_name: Mapped[str] = mapped_column(String(40))
    horizon_min: Mapped[int] = mapped_column(Integer)
    dry_pct: Mapped[float] = mapped_column(Float)
    probability: Mapped[float] = mapped_column(Float)        # P(soil drops below dry_pct within horizon)
