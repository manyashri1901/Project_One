"""FAKE data used only to check that the training code runs. It says nothing
about real soil, weather or the sensor. Never report results from it."""
import numpy as np
import pandas as pd


def make_synthetic(days: int = 6, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = days * 24 * 60
    start = 1_760_000_000
    soil = np.empty(n)
    rain = np.zeros(n)
    pump = np.zeros(n)
    s = 70.0
    for i in range(n):
        hour = ((start + i * 60) // 3600) % 24
        rate = 0.030 + 0.035 * max(0.0, np.sin((hour - 6) / 24 * 2 * np.pi))   # dries faster in the day
        if i % (9 * 60) == 0 and rng.random() < 0.5:
            rain[i:i + 180] = 1
        s -= rate * (0.4 if rain[i] else 1.0)
        if rain[i]:
            s += 0.03
        if s < 22:                                    # irrigation event
            pump[i:i + 3] = 60
            s += 40
        soil[i] = s + rng.normal(0, 0.15)
    hour_f = ((start + np.arange(n) * 60) // 3600) % 24
    return pd.DataFrame({
        "epoch": start + np.arange(n) * 60,
        "soil_pct_mean": np.clip(soil, 0, 100),
        "temp_c_mean": 27 + 6 * np.sin((hour_f - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.3, n),
        "hum_pct_mean": 60 - 15 * np.sin((hour_f - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n),
        "rain": rain,
        "pump_on_s": pump,
    })
