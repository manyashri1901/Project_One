"""
Soil calibration analysis for the 1 kg experiment.

INPUT : the raw log exported from the ESP32 with the serial command  DUMP
        (save the Serial Monitor text between the BEGIN/END lines as raw.csv).
OUTPUT: calibration_steps.csv, calibration_plot.png and a printed summary with
        SOIL_DRY_VALUE / SOIL_WET_VALUE to paste into the firmware.

How it works
  * Rows are grouped by the running total water_added_ml (set by your W50,
    W50, ... commands). Group 0 is the dry baseline.
  * For each group the *settled* reading is the median of the last half of
    its samples, so the first seconds after pouring (water still spreading)
    are ignored.
  * Saturation = the first step after which the settled reading changes by
    less than --plateau (default 2 %) of the full dry-to-wet span.

Assumptions you must state in the report
  * 1 mL of water = 1 g, so  water content (g/g) = added_mL / soil_mass_g.
  * The result is for THIS soil, packing and sensor position only.
  * This script does not choose the irrigation thresholds: that is an
    agronomic decision (discuss with your supervisor).

Usage:  python soil_calibration.py raw.csv --soil-g 1000
"""
import argparse
import io
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


def load(path: str) -> pd.DataFrame:
    # The Serial Monitor text can contain ----- BEGIN/END lines and the live
    # "raw=... soil=..." status lines. Keep only the CSV header and data rows
    # (12 comma-separated fields).
    with open(path, "r", errors="replace") as f:
        lines = [ln.strip() for ln in f]
    header = next((ln for ln in lines if ln.startswith("epoch,ms,soil_raw")), None)
    if header is None:
        sys.exit("Could not find the CSV header line 'epoch,ms,soil_raw,...' in the file.")
    nfields = header.count(",") + 1
    rows = [ln for ln in lines if ln.count(",") + 1 == nfields and not ln.startswith("epoch,")]
    df = pd.read_csv(io.StringIO("\n".join([header] + rows)))
    need = {"ms", "soil_raw", "water_added_ml"}
    if not need.issubset(df.columns):
        sys.exit(f"CSV must contain columns {sorted(need)}; found {list(df.columns)}")
    df = df.dropna(subset=["soil_raw", "water_added_ml"]).copy()
    df["soil_raw"] = pd.to_numeric(df["soil_raw"], errors="coerce")
    df["water_added_ml"] = pd.to_numeric(df["water_added_ml"], errors="coerce")
    df = df.dropna(subset=["soil_raw", "water_added_ml"])
    df["ms"] = pd.to_numeric(df["ms"], errors="coerce")
    df = df.dropna(subset=["ms"]).sort_values("ms")
    if (df["ms"].diff() < 0).any():
        print("WARNING: uptime went backwards (the ESP32 rebooted). "
              "Analyse one continuous run only.")
    df["minutes"] = (df["ms"] - df["ms"].iloc[0]) / 60000.0
    return df.reset_index(drop=True)


def step_table(df: pd.DataFrame, soil_g: float, min_samples: int) -> pd.DataFrame:
    rows = []
    for ml, g in df.groupby("water_added_ml", sort=True):
        if len(g) < min_samples:
            continue
        tail = g.iloc[len(g) // 2:]            # last half = settled part
        rows.append({
            "cumulative_ml": ml,
            "water_content_pct_gg": 100.0 * ml / soil_g,
            "samples": len(g),
            "settled_raw_median": float(tail["soil_raw"].median()),
            "settled_raw_std": float(tail["soil_raw"].std(ddof=0)),
            "minutes_at_step": float(g["minutes"].iloc[-1] - g["minutes"].iloc[0]),
        })
    return pd.DataFrame(rows)


def find_saturation(steps: pd.DataFrame, plateau_frac: float):
    span = abs(steps["settled_raw_median"].iloc[0] - steps["settled_raw_median"].iloc[-1])
    if span == 0:
        return len(steps) - 1, span
    for i in range(1, len(steps)):
        change = abs(steps["settled_raw_median"].iloc[i] - steps["settled_raw_median"].iloc[i - 1])
        if change < plateau_frac * span:
            return i, span
    return len(steps) - 1, span


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--soil-g", type=float, default=1000.0, help="dry soil mass in grams")
    ap.add_argument("--plateau", type=float, default=0.02, help="saturation plateau, fraction of span")
    ap.add_argument("--min-samples", type=int, default=5)
    a = ap.parse_args()

    df = load(a.csv)
    steps = step_table(df, a.soil_g, a.min_samples)
    if len(steps) < 3:
        sys.exit("Need at least 3 water steps with enough samples each. "
                 "Check that you typed W50 etc. and waited between steps.")
    steps.to_csv("calibration_steps.csv", index=False)

    sat_i, span = find_saturation(steps, a.plateau)
    dry = steps["settled_raw_median"].iloc[0]
    wet = steps["settled_raw_median"].iloc[sat_i]

    print("\n=== Settled reading per step ===")
    print(steps.round(1).to_string(index=False))
    print("\n=== Result (this soil, this sensor position) ===")
    print(f"Dry baseline (0 mL)        : raw = {dry:.0f}")
    print(f"Saturation first reached at: {steps['cumulative_ml'].iloc[sat_i]:.0f} mL "
          f"({steps['water_content_pct_gg'].iloc[sat_i]:.1f} % g/g), raw = {wet:.0f}")
    print(f"Span (dry - wet)           : {abs(dry - wet):.0f} ADC counts")
    print("\nPaste into the firmware:")
    print(f"  int SOIL_DRY_VALUE = {dry:.0f};")
    print(f"  int SOIL_WET_VALUE = {wet:.0f};")
    for pct in (30, 60):
        raw_at = dry + (wet - dry) * pct / 100.0
        print(f"  soil % = {pct:>2}  <->  raw = {raw_at:.0f}")
    noisy = steps["settled_raw_std"].max()
    print(f"\nLargest within-step noise (std): {noisy:.1f} counts")
    if span and noisy > 0.05 * span:
        print("NOTE: noise is more than 5 % of the span; repeat the run or fix the sensor position.")
    if span < 300:
        print("NOTE: the dry-to-wet span is small; check the sensor wiring and that it is buried to the line.")

    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].plot(df["minutes"], df["soil_raw"], lw=0.8)
    ax[0].set_xlabel("time (min)")
    ax[0].set_ylabel("soil sensor raw ADC")
    ax[0].set_title("Raw readings")
    changes = df["water_added_ml"].ne(df["water_added_ml"].shift())
    for t in df.loc[changes, "minutes"].iloc[1:]:
        ax[0].axvline(t, color="gray", lw=0.4, ls="--")
    ax[1].errorbar(steps["cumulative_ml"], steps["settled_raw_median"],
                   yerr=steps["settled_raw_std"], marker="o", capsize=3)
    ax[1].axvline(steps["cumulative_ml"].iloc[sat_i], color="red", ls=":", label="saturation")
    ax[1].set_xlabel(f"water added (mL) to {a.soil_g:.0f} g dry soil")
    ax[1].set_ylabel("settled raw ADC")
    ax[1].set_title("Calibration curve")
    ax[1].legend()
    fig.tight_layout()
    fig.savefig("calibration_plot.png", dpi=150)
    print("\nSaved calibration_steps.csv and calibration_plot.png")


if __name__ == "__main__":
    main()
