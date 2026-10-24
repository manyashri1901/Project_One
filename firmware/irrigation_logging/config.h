#pragma once
/*
  ===========================================================================
  config.h - EVERY hardware-specific value for the JalBhoomi ESP32 firmware.
  ===========================================================================
  The hardware / testing team edits THIS FILE (and secrets.h) only. The main
  sketch (irrigation_logging.ino) should not need touching to set up a board.

  What to change after each bench test (see docs/INTEGRATION_CONTRACT.md):
    RELAY_ACTIVE_LOW ................ after the relay polarity test (hardware_test 'r')
    FLOW_CALIBRATION_PULSES_PER_L ... after the flow test (hardware_test 'f' + 'g')
    SOIL_DRY_VALUE / SOIL_WET_VALUE . after the 1 kg soil run (analysis/soil_calibration.py)
    EXPERIMENT_MODE ................. set false only after calibration is done
    DEVICE_ID ....................... one unique id per board (matches MQTT topics)
  Placeholders below are NOT measured values.
*/

// ---------------------------------------------------------------------------
// MODE
// ---------------------------------------------------------------------------
// true  = soil-calibration experiment: relay forced OFF, no pump logic,
//         no weather call. Use this for the 1 kg dry-to-saturated run.
// false = normal operation (predictive irrigation as in the earlier sketch).
const bool EXPERIMENT_MODE = true;

// ---------------------------------------------------------------------------
// CLOUD / WEATHER SETTINGS (credentials are NOT here: see secrets.h)
// ---------------------------------------------------------------------------

// MQTT over TLS, port 8883. While MQTT_HOST in secrets.h still starts with
// "YOUR_", cloud push stays OFF and summaries are only stored on the ESP32.
const int   MQTT_PORT = 8883;
const char* DEVICE_ID = "jalbhoomi-esp32-01";   // unique per board; used in MQTT topics

const char* CITY_LAT = "12.9716";
const char* CITY_LON = "77.5946";
float RAIN_PROBABILITY_THRESHOLD = 0.40;   // changeable by a set_thresholds command
const unsigned long WEATHER_CHECK_INTERVAL_MS = 15UL * 60UL * 1000UL;

// Timezone for timestamps (India Standard Time = UTC+5:30 = 19800 s)
const long GMT_OFFSET_SEC = 19800;

// ---------------------------------------------------------------------------
// PINS
// ---------------------------------------------------------------------------
#define SOIL_PIN   34
#define DHT_PIN    4
#define DHT_TYPE   DHT11
#define FLOW_PIN   27
#define RELAY_PIN  26


// RELAY POLARITY - VERIFY WITH YOUR OWN MODULE (still unconfirmed)
const bool RELAY_ACTIVE_LOW = true;

// ---------------------------------------------------------------------------
// CALIBRATION PLACEHOLDERS - the experiment is what produces the real values
// ---------------------------------------------------------------------------
int   SOIL_DRY_VALUE = 3000;                    // replace from the experiment
int   SOIL_WET_VALUE = 1200;                    // replace from the experiment
float FLOW_CALIBRATION_PULSES_PER_L = 450.0;    // placeholder, not measured

// ---------------------------------------------------------------------------
// IRRIGATION / SAFETY (used only when EXPERIMENT_MODE == false)
// ---------------------------------------------------------------------------
int   SOIL_DRY_THRESHOLD_PCT = 30;
int   SOIL_WET_THRESHOLD_PCT = 60;
unsigned long MAX_PUMP_RUN_MS  = 30000;
unsigned long NO_FLOW_GRACE_MS = 5000;

// ---------------------------------------------------------------------------
// LOGGING SETTINGS
// ---------------------------------------------------------------------------
const unsigned long SENSOR_READ_INTERVAL_MS = 2000;   // DHT11 needs >= 1-2 s
const unsigned long SUMMARY_PERIOD_MS       = 60000;  // one summary row / min
const int  RAW_LOG_EVERY_N_SAMPLES = 1;               // 1 = log every sample
const unsigned long RAW_FLUSH_MS   = 10000;           // write buffered rows
const size_t RAW_MAX_BYTES     = 300000;              // rotate raw.csv here
const size_t SUMMARY_MAX_BYTES = 400000;              // reset once all sent
const int  SOIL_ADC_AVG_SAMPLES = 8;                  // averaged per reading
const int  ROWS_PER_PUSH   = 10;
const int  MAX_PUSHES_PER_CYCLE = 3;

const unsigned long FAULT_LOCKOUT_MS = 60000;      // refuse remote irrigation after a fault
const unsigned long SKIP_EVENT_EVERY_MS = 30UL * 60UL * 1000UL;

// Valve/pump-stuck-open interlock (third interlock in the report): flow pulses
// while the pump is commanded OFF. Placeholder limits - tune after calibration.
const unsigned long STUCK_GRACE_MS   = 5000;     // ignore the drain-down after a stop
const unsigned long STUCK_WINDOW_MS  = 10000;    // look for pulses inside this window
const unsigned long STUCK_PULSE_LIMIT = 20;      // pulses in the window = water is flowing
