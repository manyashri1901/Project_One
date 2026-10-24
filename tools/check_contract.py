#!/usr/bin/env python3
"""
Contract check: fails if the firmware, the backend schemas and the simulator disagree
about field names, event types, command names or ack reasons.
It reads the source text; it does NOT run the firmware. Run from the repo root:
    python tools/check_contract.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ino = (ROOT / "firmware/irrigation_logging/irrigation_logging.ino").read_text()
schemas = (ROOT / "backend/app/schemas.py").read_text()
firebase = (ROOT / "backend/app/firebase_sync.py").read_text()
sim = (ROOT / "tools/simulate_device.py").read_text()
problems = []


def model_fields(cls):
    m = re.search(rf"class {cls}\(.*?\):\n(.*?)(?=\nclass |\Z)", schemas, re.S)
    return set(re.findall(r"^    ([a-zA-Z_][a-zA-Z0-9_]*)\s*:", m.group(1), re.M)) if m else set()


# 1. summary columns the firmware sends == fields SummaryIn accepts
hdr = re.search(r'SUMMARY_HEADER =\s*((?:"[^"]*"\s*)+);', ino, re.S)
cols = set(re.sub(r'"\s*"', "", hdr.group(1)).strip('"').split(",")) if hdr else set()
accepted = model_fields("SummaryIn")
accepted_ci = {a.lower() for a in accepted}
for c in sorted(cols):
    if c.lower() not in accepted_ci:
        problems.append(f"firmware sends summary column '{c}' but SummaryIn has no such field")
if not cols:
    problems.append("could not find SUMMARY_HEADER in the firmware")

# 2. event types the firmware emits are known to the backend severity logic / alert text
def event_names(src, call_words):
    names = set()
    for line in src.splitlines():
        if any(w in line for w in call_words) and "def " not in line:
            names |= {s for s in re.findall(r'"([a-z]+(?:_[a-z]+)+)"', line)}
    return names


fw_events = event_names(ino, ["noteEvent("])
sim_events = event_names(sim, ["_event(", "_fault("])
for e in sorted(fw_events ^ sim_events):
    problems.append(f"event '{e}' exists in only one of firmware / simulator")
for e in sorted(fw_events):
    if e.startswith("fault_") or e in ("predictive_skip", "advisory_skip", "command_rejected"):
        if e not in firebase and not e.startswith("fault_"):
            problems.append(f"no push-alert text for event '{e}' in firebase_sync.py")

# 3. commands: firmware handles exactly what CommandIn allows
fw_cmds = set(re.findall(r'cmd == "([a-z_]+)"', ino))
m = re.search(r'cmd: Literal\[(.*?)\]', schemas)
be_cmds = set(re.findall(r'"([a-z_]+)"', m.group(1))) if m else set()
if fw_cmds != be_cmds:
    problems.append(f"command names differ: firmware {sorted(fw_cmds)} vs backend {sorted(be_cmds)}")

# 4. ack reasons: firmware vs simulator
fw_reasons = set(re.findall(r'(?:reason = |return )"([a-z_]+)"', ino)) - {"ok"} | {"ok"}
sim_reasons = set(re.findall(r'(?:reason = |return )"([a-z_]+)"', sim)) | {"ok"}
for r in sorted(fw_reasons ^ sim_reasons):
    problems.append(f"ack reason '{r}' exists in only one of firmware / simulator")

# 5. ack reason / id lengths fit the backend limits (max_length=40)
for r in fw_reasons:
    if len(r) > 40:
        problems.append(f"ack reason '{r}' longer than the backend's 40-character limit")

# 6. topics
if 'String("jalbhoomi/") + DEVICE_ID' not in ino:
    problems.append("firmware topic prefix is no longer 'jalbhoomi/<id>/...'")

print(f"summary columns: {len(cols)}, events: {len(fw_events)}, commands: {sorted(fw_cmds)}, ack reasons: {len(fw_reasons)}")
if problems:
    print("\nCONTRACT PROBLEMS:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("contract check: OK (source-level only; this does not prove the firmware works on a board)")
