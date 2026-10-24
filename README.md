# JalBhoomi: Autonomous Predictive Drip Irrigation and Water Consumption Monitoring System Using Hall Effect Sensor

An ESP32-based drip-irrigation controller that combines a local soil-moisture
threshold with a short-term rainfall forecast, so irrigation is withheld when
rain is already expected. A Hall-effect flow sensor measures the water
delivered. BCSE497J project, Vellore Institute of Technology (VIT), Vellore.

> **Status: bench prototype, software complete, hardware testing pending.**
> Nothing here has been validated yet: soil and flow calibration, relay polarity,
> water savings and forecast-skip accuracy are all still open. The backend passes
> 36 automated tests (SQLite, simulated device); the firmware and Android app have
> been syntax-checked only, never run on a board / built. See "Status by component".

**Software and hardware meet at one page: [`docs/INTEGRATION_CONTRACT.md`](docs/INTEGRATION_CONTRACT.md).**
Run `bash run_tests.sh` (or `run_tests.bat`) to check everything that can be checked without hardware.

## Tech stack (as specified in the project report, Section 3.3.2)

| Layer | Technology |
|---|---|
| Firmware | Arduino-ESP32 framework, C++ |
| Communication | WiFi (802.11) with MQTT over TLS |
| Backend | FastAPI (Python) |
| Database | PostgreSQL |
| Predictive model | Classical ML (Random Forest / Gradient Boosting), scikit-learn |
| Mobile app | Native Android (Kotlin, Jetpack Compose), Firebase |
| Dev environment | Arduino IDE / PlatformIO, Git |

## Data flow

```
sensors -> ESP32 --(raw rows)--> ESP32 flash (LittleFS), never leaves the device
                 --(1 summary row / minute, JSON, MQTT over TLS)--> Mosquitto
                   -> FastAPI subscriber -> PostgreSQL -> REST API -> app / plots
```

Raw readings stay on the ESP32. Only one consolidated row per minute is sent.
The pump is always arbitrated locally by hardware interlocks (maximum run-time,
no-flow fault); cloud input is advisory only.

## Status by component

| Component | Folder | Status |
|---|---|---|
| Hardware bench test | `firmware/hardware_test/` | Written; syntax-checked only; **not yet run on the board**. Soil, DHT11, flow, relay polarity and a short pump test over serial |
| ESP32 logging firmware (v2) | `firmware/irrigation_logging/` | Written; syntax-checked only; **not tested on the board**. Hardware values in `config.h`, credentials in `secrets.h`. Local logging, MQTT/TLS summaries and events, command channel with local arbitration, max-time / no-flow / valve-stuck-open interlocks. TLS currently does not verify the broker certificate (`setInsecure`) |
| Earlier prototype sketch | `firmware/irrigation_prototype_predictive/` | Ran on bench hardware (serial telemetry); placeholder calibration |
| FastAPI backend | `backend/` | 36 tests pass on SQLite, incl. simulated-device round-trips. Ingestion, alerts, commands, thresholds, water totals, optional Firebase mirror/push, scheduled prediction job. **Not yet run against PostgreSQL / Mosquitto** |
| Docker stack | `docker-compose.yml`, `mosquitto/` | Written; not yet run |
| Predictive model | `backend/app/ml/` | Training + advice code written (Random Forest, Gradient Boosting). **No model trained on real data; none is active.** Synthetic data exists only to test the code and is refused by default |
| Android app | `android/` | Kotlin + Jetpack Compose + Firebase source written (login, home, zones, control, analytics, settings). **Syntax-checked only: never built or run.** Needs your own Firebase project |
| Device simulator | `tools/simulate_device.py` | Plays the ESP32 for software testing. **Invented data, never a result** |
| Contract check | `tools/check_contract.py` | Source-level check that firmware, backend and simulator agree on names |
| IEEE paper | `docs/paper/` | LaTeX source, PDF and figures. **Describes the earlier sketch; to be updated after testing** |

## Hardware

| Component | Interface |
|---|---|
| ESP32-WROOM-32 DevKit | main controller |
| Capacitive soil-moisture sensor | GPIO34 (ADC1) |
| DHT11 temperature/humidity sensor | GPIO4 |
| Hall-effect turbine flow sensor | GPIO27 (interrupt, falling edge) |
| 1-channel opto-isolated relay module | GPIO26 |
| Small DC pump on its own supply, switched by the relay | via relay COM/NO |

Power the pump from its own supply, never from the ESP32, and join the grounds.
Confirm the pump's voltage rating before connecting it. Relay polarity
(`RELAY_ACTIVE_LOW`) is not yet confirmed. The circuit schematic is in the paper.

## Hardware bench test (do this first)

Open `firmware/hardware_test/hardware_test.ino`, upload, and type commands in the
Serial Monitor (115200 baud, Newline): `s` soil, `d` DHT11, `f` flow pulses,
`g <mL>` flow calibration, `r` relay polarity (disconnect the pump first),
`pol low|high` to record the result, `p <sec>` short pump test.

## Firmware: getting started

1. Open `firmware/irrigation_logging/irrigation_logging.ino` in the Arduino IDE
   (ESP32 board package) or build with PlatformIO (`platformio.ini`).
2. Libraries: DHT sensor library (Adafruit), Adafruit Unified Sensor,
   ArduinoJson 6.x, PubSubClient.
3. Copy `secrets.example.h` to `secrets.h` and fill in `WIFI_SSID`, `WIFI_PASSWORD`,
   `MQTT_HOST`, `MQTT_USER`, `MQTT_PASS` and (normal mode) `OPENWEATHER_API_KEY`.
   `secrets.h` is git-ignored: **never commit real credentials.** With the
   `YOUR_` placeholders, nothing is sent and summaries stay on the device.
   Pins, relay polarity, calibration values and safety limits are in `config.h`.
4. `EXPERIMENT_MODE = true` (default) holds the relay OFF and skips the weather
   call, for the 1 kg soil calibration run: log dry soil, add water in 50 mL
   steps, type `W50` in the serial monitor each time, then `DUMP` to export the
   raw log. The dry and wet raw readings give `SOIL_DRY_VALUE` / `SOIL_WET_VALUE`.
5. Set `EXPERIMENT_MODE = false` only after confirming the pump voltage and the
   relay polarity.

## Backend: getting started

```
cd backend
pip install -r requirements.txt
python -m pytest                         # in-memory SQLite, no broker needed
uvicorn app.main:app --reload            # SQLite by default, MQTT off; docs at /docs
```

Full stack (PostgreSQL + Mosquitto + backend): create the certificates and
password file as described in `mosquitto/README.md`, copy
`backend/.env.example` to `backend/.env`, set the passwords and `BACKEND_API_KEY`,
then `docker compose up --build`. Write endpoints (commands, thresholds, alert
acknowledgement, rename) need the `X-API-Key` header or a Firebase login whose
UID is listed in `ALLOWED_FIREBASE_UIDS`. All endpoints: `/docs`.

## Testing without hardware

```
bash run_tests.sh      # Windows: run_tests.bat
python tools/simulate_device.py --print --scenario fault_no_flow --minutes 4
python tools/simulate_device.py --host localhost --port 1883 --no-tls --user dev --password pw   # live broker
```

Scenarios: `normal`, `rain_skip`, `fault_no_flow`, `stuck_open`, `experiment`. The simulator
also answers commands on `jalbhoomi/<id>/cmd` with the firmware's refusal reasons.

## Android app

See `android/README.md`: open the `android/` folder in Android Studio, add your Firebase
`google-services.json`, set the backend address, run. Not yet built by the authors.

## Repository layout

```
firmware/   ESP32 sketches (irrigation_logging = current; config.h = hardware values)
backend/    FastAPI service, SQLAlchemy models, ML code (app/ml), tests
mosquitto/  MQTT broker config and TLS setup notes
android/    Android app source (Kotlin, Jetpack Compose, Firebase)
tools/      simulate_device.py, check_contract.py
docs/INTEGRATION_CONTRACT.md   topics, JSON fields, commands, bench checks
ml/         notes on the predictive model (code is in backend/app/ml)
docs/paper/ IEEE conference paper (LaTeX, PDF, figures, references)
docs/diagrams/ architecture.drawio (open in diagrams.net)
analysis/   soil_calibration.py: turns the 1 kg experiment log into SOIL_DRY/WET values
```

Rebuild the paper: `cd docs/paper && pdflatex main.tex` (twice).

## Authors

Harsh Singh, Manya Shrivastava, A. Charanjith Reddy, and Naresh K (project
guide), School of Computer Science and Engineering, Vellore Institute of
Technology, Vellore, India.

## License

MIT, see `LICENSE`.
