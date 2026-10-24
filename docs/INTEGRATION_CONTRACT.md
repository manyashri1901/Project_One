# JalBhoomi integration contract (firmware <-> backend <-> app)

One page that both sides code to. If something here and the code disagree, that is a bug:
run `python tools/check_contract.py` (it compares the firmware's field names with the backend schemas).

Status: written from the code. Not yet exercised with a real ESP32 + broker.

## 1. Who edits what

| Person | Edits | Never edits |
|---|---|---|
| Hardware / testing | `firmware/irrigation_logging/config.h`, `secrets.h` (copy of `secrets.example.h`) | the `.ino` logic, backend, app |
| Software | `.ino` logic, `backend/`, `android/`, `tools/` | measured values in `config.h` (ask hardware) |

`config.h` values that only a measurement can give: `RELAY_ACTIVE_LOW`, `FLOW_CALIBRATION_PULSES_PER_L`,
`SOIL_DRY_VALUE`, `SOIL_WET_VALUE`, then `EXPERIMENT_MODE = false`. Each has a bench step in section 8.

## 2. MQTT topics (prefix `jalbhoomi`, id = `DEVICE_ID` in `config.h`)

| Topic | Direction | QoS | Payload |
|---|---|---|---|
| `jalbhoomi/<id>/summary` | device -> backend | 0 (PubSubClient limit) | one summary row, section 3 |
| `jalbhoomi/<id>/event` | device -> backend | 0 | section 4 |
| `jalbhoomi/<id>/ack` | device -> backend | 0 | section 6 |
| `jalbhoomi/<id>/cmd` | backend -> device | 1 sent, device receives at 0 | section 5 |

Rules: the `device_id` inside every JSON must equal `<id>` in the topic, or the backend drops the message.
Port 8883 (TLS), per-device username/password. Payloads are JSON, UTF-8, under 380 bytes for `cmd`.

## 3. Summary message (one per 60 s, oldest unsent first)

| Field | Type | Unit / meaning |
|---|---|---|
| `device_id` | string | topic id |
| `epoch` | int | Unix seconds from NTP. `0` = clock not synced yet |
| `uptime_s` | int | seconds since boot (with `device_id` + `epoch` it is the de-duplication key) |
| `samples` | int | readings averaged into this row |
| `soil_raw_mean`, `soil_raw_min`, `soil_raw_max` | number | ADC counts (0-4095) |
| `soil_pct_mean` | number | 0-100 from `SOIL_DRY_VALUE`/`SOIL_WET_VALUE` |
| `temp_c_mean` | number | deg C (DHT11, whole degrees) |
| `hum_pct_mean` | number | % RH |
| `flow_pulses` | int | Hall pulses this minute |
| `flow_L` | number | litres this minute = pulses / `FLOW_CALIBRATION_PULSES_PER_L` |
| `pump_on_s` | int | seconds the pump was on this minute |
| `faults` | int | fault count this minute |
| `rain` | int 0/1 | forecast says rain. Field is **omitted** when unknown |
| `water_added_ml` | number | experiment only: running total typed with `W<ml>` |

Empty / unknown values are omitted from the JSON, never sent as `null` or `""`.
Raw rows (`/raw.csv`) never leave the ESP32. They are read with the serial `DUMP` command.

## 4. Event message

`{"device_id","epoch","uptime_s","type","detail"?}` — `type` matches `^[a-z_]{1,40}$`, `detail` <= 120 chars.

| `type` | Meaning | Backend severity | App text |
|---|---|---|---|
| `irrigation_start`, `irrigation_stop`, `override_end`, `auto_on`, `auto_off`, `command_accepted` | normal activity | info | |
| `predictive_skip` | rain forecast, watering withheld (rate-limited 30 min) | notice | "Irrigation skipped: rain forecast" |
| `advisory_skip` | backend advised skip, honoured | notice | |
| `command_rejected` | `detail` = `<cmd>:<reason>` | notice | |
| `fault_max_time` | pump hit `MAX_PUMP_RUN_MS`, stopped | fault | red banner + push |
| `fault_no_flow` | pump on, no pulses for `NO_FLOW_GRACE_MS` | fault | red banner + push |
| `fault_valve_stuck_open` | pulses while pump is off | fault | red banner + push |

## 5. Command message (backend -> device)

`{"id":"<12 hex>","cmd":"...", ...params}`. The device **may refuse**: commands are advisory.

| `cmd` | Params | Device behaviour |
|---|---|---|
| `override_on` | `duration_s` (1-600, clamped to `MAX_PUMP_RUN_MS`) | start pump for that long |
| `override_off` | none | stop pump now |
| `set_thresholds` | `soil_dry_pct`, `soil_wet_pct` (dry < wet), `rain_probability` 0-1 | store + persist |
| `set_auto` | `enabled` bool | `false` = never start the pump by itself; persisted |
| `advise` | `action` = `skip` (+`hours` 1-24) or `irrigate` (+`duration_s`) | skip window / start pump |

## 6. Ack message (device -> backend), one per command

`{"device_id","id","cmd","accepted":bool,"reason"}`; `id` echoes the command id.

`reason` values: `ok`, `experiment_mode`, `fault_lockout` (60 s after a fault), `soil_already_wet`,
`bad_duration`, `bad_thresholds`, `bad_action`, `unknown_command`, `bad_json`.

Backend command status: `sent` -> `accepted` | `rejected` (from the ack) or `failed` (`mqtt_unavailable`).

## 7. REST used by the app (backend `GET /docs` lists everything)

Reads are open unless `REQUIRE_AUTH_FOR_READS=true`. Writes need `X-API-Key` or a Firebase login whose UID is in
`ALLOWED_FIREBASE_UIDS`. Key reads: `/zones`, `/devices/{id}/summaries|water/daily|water/total|water/saved|alerts|commands|advice|thresholds`.
Key writes: `POST /devices/{id}/commands`, `PUT /devices/{id}/thresholds`, `POST /alerts/{id}/ack`, `PUT /devices/{id}`.

## 8. Bench checks (hardware team) and what to send back

| # | Do | Send to software |
|---|---|---|
| 1 | `hardware_test` `s`, `d`: soil and DHT11 read sane values | the numbers |
| 2 | `hardware_test` `r` (pump disconnected) | `LOW` or `HIGH` turns the relay on |
| 3 | `hardware_test` `f` then `g <mL>` | pulses per litre |
| 4 | 1 kg soil run in `irrigation_logging` (`W50` per step, then `DUMP`) | the DUMP text |
| 5 | fill `secrets.h`, power on | the `STATUS` serial output and whether summaries reach the backend |

Hardware sends *numbers or serial output*; software changes `config.h` / code. No other coupling.

## 9. Testing without hardware

`python tools/simulate_device.py` plays the ESP32 (same topics, same JSON, same arbitration rules and reasons).
Its data is invented: use it to test the backend and app only, never as results.
`./run_tests.sh` runs the backend tests, the contract check and a simulator round-trip.
