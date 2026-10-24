#!/usr/bin/env python3
"""
Convert a PUBLIC soil-moisture / weather CSV into the format backend/app/ml/train.py reads, so the
ML pipeline can be evaluated on REAL sensor data BEFORE this project has logged enough of its own.

    python tools/prepare_public_dataset.py raw.csv --out public.csv
    cd backend && python -m app.ml.train ../public.csv --row-minutes <printed value> --public-benchmark --dry-pct <see below>

What this is and is not
  * It tests the pipeline and the approach on someone else's data. The scores describe THAT dataset.
    They say nothing about the JalBhoomi hardware. Cite the dataset; never present them as this system's results.
  * Soil moisture units differ between datasets. If the column is not already 0-100 %, use --minmax and the values
    are rescaled to 0-100 (dataset min -> 0, max -> 100). Say so in the report.
  * Column names are guessed from their text (time/date, soil, temp, hum, rain). Check the printed mapping;
    override with --time-col/--soil-col/--temp-col/--hum-col/--rain-col if it guessed wrong.
"""
import argparse
import sys

import numpy as np
import pandas as pd


def pick(cols, words, exclude=()):
    for c in cols:
        low = str(c).lower()
        if any(w in low for w in words) and not any(x in low for x in exclude):
            return c
    return None


def to_rain(s: pd.Series) -> pd.Series:
    """1 = rain, 0 = no rain, NaN = unknown. Accepts 0/1, True/False, 'Rain'/'No Rain', 'Yes'/'No', etc."""
    num = pd.to_numeric(s, errors="coerce")
    if num.notna().mean() > 0.9:
        return (num > 0).astype(float).where(num.notna())
    txt = s.astype(str).str.strip().str.lower()
    neg = txt.str.contains(r"\bno\b|not|dry|none|false|^0$|clear|no rain", regex=True)
    pos = txt.str.contains(r"rain|wet|yes|true|^1$", regex=True) & ~neg
    out = pd.Series(np.nan, index=s.index)
    out[neg] = 0.0
    out[pos] = 1.0
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--out", default="public.csv")
    ap.add_argument("--time-col"); ap.add_argument("--soil-col"); ap.add_argument("--temp-col")
    ap.add_argument("--hum-col"); ap.add_argument("--rain-col")
    ap.add_argument("--dayfirst", action="store_true", help="dates are DD/MM/YYYY")
    ap.add_argument("--minmax", action="store_true", help="rescale the soil column to 0-100 (when it is raw / not percent)")
    a = ap.parse_args()

    raw = pd.read_csv(a.csv)
    cols = list(raw.columns)
    m = {
        "time": a.time_col or pick(cols, ["datetime", "date", "time", "timestamp"]),
        "soil": a.soil_col or pick(cols, ["soil", "moist"]),
        "temp": a.temp_col or pick(cols, ["temp"], exclude=["soil"]),
        "hum": a.hum_col or pick(cols, ["humid", "rh"], exclude=["soil"]),
        "rain": a.rain_col or pick(cols, ["rain"]),
    }
    print("column mapping:", m)
    for need in ("time", "soil", "temp", "hum"):
        if m[need] is None:
            sys.exit(f"Could not find the {need} column. Columns are: {cols}. Use --{need}-col NAME.")

    d = pd.DataFrame({"t": pd.to_datetime(raw[m["time"]], errors="coerce", dayfirst=a.dayfirst),
                      "soil": pd.to_numeric(raw[m["soil"]], errors="coerce"),
                      "temp": pd.to_numeric(raw[m["temp"]], errors="coerce"),
                      "hum": pd.to_numeric(raw[m["hum"]], errors="coerce")})
    d["rain"] = to_rain(raw[m["rain"]]) if m["rain"] else np.nan
    before = len(d)
    d = d.dropna(subset=["t", "soil"]).sort_values("t").drop_duplicates("t").reset_index(drop=True)
    print(f"rows: {before} read, {len(d)} usable (valid time and soil value, duplicates removed)")

    if a.minmax:
        lo, hi = d["soil"].min(), d["soil"].max()
        d["soil"] = (d["soil"] - lo) / (hi - lo) * 100.0
        print(f"soil rescaled to 0-100 (raw min {lo:g} -> 0, max {hi:g} -> 100)")
    elif d["soil"].max() > 100 or d["soil"].min() < 0:
        sys.exit("Soil values are outside 0-100: they are not percent. Re-run with --minmax.")

    gap_min = d["t"].diff().dt.total_seconds().median() / 60.0
    spans_days = (d["t"].iloc[-1] - d["t"].iloc[0]).total_seconds() / 86400
    print(f"median spacing {gap_min:.1f} min, covering {spans_days:.1f} days")
    if gap_min > 90:
        print("WARNING: rows are more than 90 minutes apart; the 30/60-minute features are too fine for this data.")
    # timestamps are treated as LOCAL clock time (the model's time-of-day features use IST)
    ts = d["t"] - pd.Timedelta(hours=5.5)
    out = pd.DataFrame({"epoch": (ts - pd.Timestamp("1970-01-01")).dt.total_seconds().astype("int64"),
                        "soil_pct_mean": d["soil"].round(2), "temp_c_mean": d["temp"], "hum_pct_mean": d["hum"],
                        "rain": d["rain"], "pump_on_s": 0})
    out.to_csv(a.out, index=False)
    print(f"wrote {a.out}. Suggested: --row-minutes {max(1, round(gap_min))}")
    print("Soil percentiles (pick --dry-pct so both classes occur):",
          {q: round(float(d['soil'].quantile(q / 100)), 1) for q in (5, 10, 25, 50)})


if __name__ == "__main__":
    main()