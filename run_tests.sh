#!/usr/bin/env bash
# One command to check everything that can be checked WITHOUT hardware.
#   ./run_tests.sh
# Needs: python3 -m venv .venv && . .venv/bin/activate && pip install -r backend/requirements.txt
set -e
cd "$(dirname "$0")"
echo "== 1/3 contract check (firmware <-> backend <-> simulator names)"
python tools/check_contract.py
echo
echo "== 2/3 backend tests (incl. simulated-device round-trips)"
(cd backend && python -m pytest -q)
echo
echo "== 3/3 simulator smoke run (prints 3 simulated minutes; data is invented)"
python tools/simulate_device.py --print --minutes 3 | head -4
echo
echo "ALL AUTOMATED CHECKS PASSED."
echo "This does NOT test the firmware on a board, PostgreSQL, Mosquitto, Firebase or the Android build."
