"""Creates a FAKE raw.csv only to test that soil_calibration.py runs.
The numbers are invented. Never use the output as a result."""
import random

random.seed(1)
rows = ["epoch,ms,soil_raw,soil_pct,temp_c,hum_pct,pulses,volume_L,pump,rain,water_added_ml,event"]
ms, total = 0, 0
raw_levels = [3050, 2900, 2700, 2480, 2300, 2180, 2120, 2100, 2098]   # dry -> saturated (fake)
for step, level in enumerate(raw_levels):
    event = "" if step == 0 else "water_add_50ml"
    total = step * 50
    for k in range(60):                       # 2 min of samples at 2 s
        settle = max(0.0, 1 - k / 20)         # transient right after pouring
        prev = raw_levels[step - 1] if step else level
        raw = level + (prev - level) * settle + random.gauss(0, 4)
        rows.append(f"0,{ms},{int(raw)},50,28.0,60,0,0.000,0,,{total},{event if k == 0 else ''}")
        ms += 2000
open("raw_fake.csv", "w").write("\n".join(rows) + "\n")
