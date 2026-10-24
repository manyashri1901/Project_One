"""Feature and label construction for the predictive model.

Input rows are the per-minute summary rows (one row per minute). Required
columns: epoch, soil_pct_mean, temp_c_mean, hum_pct_mean, rain, pump_on_s.
"""
import numpy as np
import pandas as pd

FEATURES = ["soil_pct", "slope_30m", "slope_60m", "temp_c", "hum_pct",
            "rain", "pump_on_60m", "hour_sin", "hour_cos"]
TZ = "Asia/Kolkata"
MIN_HISTORY_ROWS = 61        # the 60-minute slope needs 60 earlier rows


def rows_for(minutes: float, row_minutes: float = 1.0) -> int:
    """Number of rows that span `minutes` when rows are `row_minutes` apart (at least 1)."""
    return max(1, int(round(minutes / row_minutes)))


def history_rows(row_minutes: float = 1.0) -> int:
    """Rows of history needed before the longest (60 min) slope is defined."""
    return rows_for(60, row_minutes) + 1


def prepare(df: pd.DataFrame, row_minutes: float = 1.0) -> pd.DataFrame:
    """row_minutes = spacing between rows. The device logs 1 row per minute (default); a public
    dataset may be coarser. The windows below are expressed in minutes and converted to rows."""
    d = df.copy()
    d["epoch"] = pd.to_numeric(d.get("epoch", 0), errors="coerce").fillna(0)
    d = d.sort_values("epoch" if (d["epoch"] > 0).any() else d.index.name or d.columns[0]).reset_index(drop=True)
    if (d["epoch"] > 0).any():
        ts = pd.to_datetime(d["epoch"], unit="s", utc=True).dt.tz_convert(TZ)
        hour = ts.dt.hour + ts.dt.minute / 60.0
        d["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        d["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    else:                                   # device never had NTP time
        d["hour_sin"] = 0.0
        d["hour_cos"] = 0.0
    d["soil_pct"] = pd.to_numeric(d["soil_pct_mean"], errors="coerce")
    n30, n60 = rows_for(30, row_minutes), rows_for(60, row_minutes)
    d["slope_30m"] = (d["soil_pct"] - d["soil_pct"].shift(n30)) / (n30 * row_minutes / 60.0)   # % per hour
    d["slope_60m"] = (d["soil_pct"] - d["soil_pct"].shift(n60)) / (n60 * row_minutes / 60.0)
    d["temp_c"] = pd.to_numeric(d["temp_c_mean"], errors="coerce").ffill().bfill()
    d["hum_pct"] = pd.to_numeric(d["hum_pct_mean"], errors="coerce").ffill().bfill()
    d["rain"] = pd.to_numeric(d.get("rain"), errors="coerce").fillna(-1)  # -1 = forecast unknown
    d["pump_on_60m"] = pd.to_numeric(d.get("pump_on_s"), errors="coerce").fillna(0).rolling(n60, min_periods=1).sum()
    return d


def add_labels(d: pd.DataFrame, dry_pct: float, horizon_min: int, row_minutes: float = 1.0) -> pd.DataFrame:
    """Label = soil moisture falls below dry_pct within the next horizon_min
    minutes. Only rows that are NOT already dry are kept (otherwise it is trivial)."""
    win = rows_for(horizon_min, row_minutes)
    fut_min = d["soil_pct"].shift(-1)[::-1].rolling(win, min_periods=win).min()[::-1]
    out = d.copy()
    out["label"] = (fut_min < dry_pct).astype(float)
    out.loc[fut_min.isna(), "label"] = np.nan
    out = out[(out["soil_pct"] >= dry_pct)]
    return out.dropna(subset=FEATURES + ["label"])