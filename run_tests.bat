@echo off
REM Windows version of run_tests.sh. Activate the venv first:  .venv\Scripts\activate
cd /d "%~dp0"
echo == 1/3 contract check
python tools\check_contract.py || exit /b 1
echo == 2/3 backend tests
pushd backend
python -m pytest -q || exit /b 1
popd
echo == 3/3 simulator smoke run (invented data)
python tools\simulate_device.py --print --minutes 3
echo ALL AUTOMATED CHECKS PASSED. This does not test hardware, PostgreSQL, Mosquitto, Firebase or the Android build.
