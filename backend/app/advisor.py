"""Watering advisor: a plain-language recommendation for the farmer, from what the sensors measured.

Inputs (all measured by this project's sensors): latest soil moisture and its recent trend, temperature and
humidity, the rain forecast flag, the thresholds, past irrigation cycles (litres delivered and the soil rise they
caused), and today's litres against the farmer's limits.

What it is NOT: a crop-water-requirement model. There is no sunlight, wind or crop-stage input, so it does not
compute evapotranspiration. It is advisory; the ESP32 still makes the final decision.
"""
from __future__ import annotations

import statistics

from .consolidate import aware

LOOKBACK_MIN = 60           # minutes of history used for the trend
MIN_SPAN_MIN = 30           # need at least this much history before trusting a trend
SOON_HOURS = 3.0            # "will reach the dry threshold soon" window
HOT_C, DRY_AIR = 35.0, 40.0  # wording only: hot and dry air dries soil faster


def litres_per_percent(cycles) -> float | None:
    """Median litres needed per 1 point of soil moisture, from measured cycles. None if none are usable."""
    vals = [c["litres"] / c["soil_rise"] for c in cycles
            if c.get("litres") and c.get("soil_rise") and c["soil_rise"] >= 0.5 and c["litres"] > 0]
    return statistics.median(vals) if vals else None


def soil_trend_per_hour(rows) -> float | None:
    """Change in soil moisture per hour over the last LOOKBACK_MIN minutes. None if not enough history."""
    pts = [(aware(r.ts), r.soil_pct_mean) for r in rows if r.soil_pct_mean is not None]
    if len(pts) < 2:
        return None
    last_ts, last_val = pts[-1]
    window = [p for p in pts if (last_ts - p[0]).total_seconds() <= LOOKBACK_MIN * 60]
    first_ts, first_val = window[0]
    span_h = (last_ts - first_ts).total_seconds() / 3600
    if span_h * 60 < MIN_SPAN_MIN:
        return None
    return (last_val - first_val) / span_h


def recommend(rows, dry: float, wet: float, cycles, today_litres, limits=None) -> dict:
    """rows: recent Summary-like objects, oldest first."""
    basis = ("Advisory, from measured soil moisture, its recent trend, the rain forecast flag and past cycles. "
             "Not a crop-water-requirement calculation.")
    if not rows or rows[-1].soil_pct_mean is None:
        return {"action": "no_data", "message": "No soil readings yet.", "litres_suggested": None,
                "hours_to_dry": None, "reasons": [], "basis": basis}
    last = rows[-1]
    soil = float(last.soil_pct_mean)
    slope = soil_trend_per_hour(rows)
    lpp = litres_per_percent(cycles)
    rain = next((r.rain for r in reversed(rows) if r.rain is not None), None)
    reasons = [f"Soil moisture is {soil:.0f}% (dry below {dry:g}%, wet at {wet:g}%)."]
    hours_to_dry = None
    if slope is not None:
        reasons.append(f"Trend over the last hour: {slope:+.1f} points per hour.")
        if slope < 0 and soil > dry:
            hours_to_dry = round((soil - dry) / -slope, 1)
    else:
        reasons.append("Not enough history yet to estimate the trend.")
    if last.temp_c_mean is not None and last.hum_pct_mean is not None \
            and last.temp_c_mean >= HOT_C and last.hum_pct_mean <= DRY_AIR:
        reasons.append("Air is hot and dry, so the soil dries faster than usual.")
    hi = getattr(limits, "daily_max_litres", None) if limits else None
    over = hi is not None and today_litres is not None and today_litres >= hi
    if over:
        reasons.append(f"Today's use ({today_litres:.1f} L) has reached the daily limit ({hi:g} L).")

    litres = None
    if lpp is not None and soil < wet:
        litres = round((wet - soil) * lpp, 2)

    if soil >= wet:
        action, msg = "ok", "Soil is wet enough. No watering needed."
    elif rain == 1:
        action, msg = "skip_rain", "Rain is forecast. Wait before watering."
        reasons.append("The rain forecast flag is set.")
    elif soil <= dry:
        if over:
            action, msg = "hold_limit", "Soil is dry, but today's water limit has been reached. Check the limit before watering."
        else:
            action = "water_now"
            msg = (f"Soil is dry. Water about {litres:g} L to reach the wet threshold." if litres is not None
                   else "Soil is dry. Water now. The amount is unknown until a few cycles have been measured.")
    elif hours_to_dry is not None and hours_to_dry <= SOON_HOURS:
        action, msg = "water_soon", f"Soil will reach the dry threshold in about {hours_to_dry:g} h. Plan to water then."
    else:
        action = "ok"
        msg = "Soil moisture is fine for now."
        if hours_to_dry is not None:
            msg += f" At the current rate it reaches the dry threshold in about {hours_to_dry:g} h."
    if lpp is None:
        reasons.append("No measured irrigation cycle with a soil rise yet, so the amount of water cannot be estimated.")
    else:
        reasons.append(f"From past cycles, about {lpp:.2f} L raises this soil by 1 point.")
    return {"action": action, "message": msg, "litres_suggested": litres if action == "water_now" else None,
            "hours_to_dry": hours_to_dry, "reasons": reasons, "basis": basis}