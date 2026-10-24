/*
  ===========================================================================
  Autonomous PREDICTIVE Drip Irrigation & Water-Consumption Monitoring System
  Full prototype firmware — ESP32
  ===========================================================================

  HARDWARE CONNECTED (as confirmed):
    Capacitive soil moisture sensor -> ESP32 3V3, GND, GPIO34 (ADC1, analog)
    DHT11 temp/humidity sensor      -> ESP32 3V3, GND, GPIO4  (digital, 1-wire)
    Hall-effect flow sensor         -> ESP32 GND, GPIO27 (interrupt, pulse)
                                        VCC -> 5V (check your sensor's datasheet)
    Relay module (pump driver)      -> ESP32 3V3/5V, GND, GPIO26 (digital out)
    Pump                            -> OWN separate 7-9V supply, switched via
                                        the relay's COM/NO. NEVER to the ESP32.
                                        Pump supply (-) MUST also connect to
                                        ESP32 GND (shared ground).

  ===========================================================================
  WHERE THIS PROJECT'S NOVELTY LIVES IN THIS CODE
  ===========================================================================
  A plain "soil dry -> turn pump on" system is REACTIVE — that already exists
  in many published projects. This code is PREDICTIVE: it also checks an
  online weather forecast before irrigating, and SKIPS irrigation if rain is
  expected soon, even if the soil is currently dry. Search this file for the
  tag "[NOVELTY]" to find exactly where that logic lives.
  ===========================================================================

  YOU MUST FILL IN / CALIBRATE THESE BEFORE TRUSTING ANY OUTPUT:
    1. WIFI_SSID / WIFI_PASSWORD           (your WiFi network)
    2. OPENWEATHER_API_KEY / CITY_LAT/LON  (free key from openweathermap.org)
    3. SOIL_DRY_VALUE / SOIL_WET_VALUE     (measure yourself, see comments)
    4. FLOW_CALIBRATION_PULSES_PER_L       (measure yourself, see comments)
  ===========================================================================

  LIBRARIES REQUIRED (install via Arduino IDE -> Library Manager):
    - "DHT sensor library" by Adafruit  (also installs "Adafruit Unified Sensor")
    - "ArduinoJson" by Benoit Blanchon  (version 6.x)
  ===========================================================================
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <DHT.h>

// ---------------------------------------------------------------------------
// WIFI / WEATHER API SETTINGS — FILL THESE IN
// ---------------------------------------------------------------------------
const char* WIFI_SSID     = "YOUR_WIFI_NAME";       // <-- REPLACE
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";   // <-- REPLACE

const char* OPENWEATHER_API_KEY = "YOUR_API_KEY";   // <-- REPLACE (free tier: openweathermap.org)
const char* CITY_LAT = "12.9716";                   // <-- REPLACE with your city's latitude
const char* CITY_LON = "77.5946";                   // <-- REPLACE with your city's longitude

// Probability-of-precipitation threshold that counts as "rain expected".
// 0.4 = 40%. Adjust based on how conservative you want the prediction to be.
const float RAIN_PROBABILITY_THRESHOLD = 0.40;

// How often to check the weather forecast (weather doesn't change every
// second, so we do NOT call this every loop — that would be wasteful and
// could hit free-tier API rate limits).
const unsigned long WEATHER_CHECK_INTERVAL_MS = 15UL * 60UL * 1000UL; // 15 minutes

// ---------------------------------------------------------------------------
// PIN MAP
// ---------------------------------------------------------------------------
#define SOIL_PIN    34   // ADC1 pin only — do not move this to an ADC2 pin
#define DHT_PIN     4
#define DHT_TYPE    DHT11
#define FLOW_PIN    27   // must be interrupt-capable
#define RELAY_PIN   26

DHT dht(DHT_PIN, DHT_TYPE);

// ---------------------------------------------------------------------------
// RELAY POLARITY — VERIFY WITH YOUR OWN MODULE
// ---------------------------------------------------------------------------
const bool RELAY_ACTIVE_LOW = true;   // set to false if your relay is active-HIGH

// ---------------------------------------------------------------------------
// CALIBRATION PLACEHOLDERS — REPLACE WITH YOUR OWN MEASURED VALUES
// ---------------------------------------------------------------------------
// Soil sensor: read raw analogRead(SOIL_PIN) in open air (dry) and dipped in
// water (wet), then put those two numbers here. Do NOT skip this step.
int SOIL_DRY_VALUE = 3000;   // <-- PLACEHOLDER, replace with your dry reading
int SOIL_WET_VALUE = 1200;   // <-- PLACEHOLDER, replace with your wet reading

// Flow sensor: pour a known volume (e.g. 1.0 L) through the sensor, count the
// pulses, then set this = pulses / litres. Repeat 3-5 times and average.
float FLOW_CALIBRATION_PULSES_PER_L = 450.0;  // <-- PLACEHOLDER, must be measured

// ---------------------------------------------------------------------------
// IRRIGATION / SAFETY SETTINGS — reasonable starting defaults, adjust as needed
// ---------------------------------------------------------------------------
int   SOIL_DRY_THRESHOLD_PCT  = 30;     // irrigate when soil moisture % falls below this
int   SOIL_WET_THRESHOLD_PCT  = 60;     // stop irrigating once soil moisture % reaches this
unsigned long MAX_PUMP_RUN_MS   = 30000; // safety timeout: force pump off after 30s
unsigned long NO_FLOW_GRACE_MS  = 5000;  // if pump ON but no flow for 5s, treat as fault
unsigned long SENSOR_READ_INTERVAL_MS = 2000; // DHT11 needs >=1-2s between reads

// ---------------------------------------------------------------------------
// STATE VARIABLES
// ---------------------------------------------------------------------------
volatile unsigned long pulseCount = 0;   // updated inside the interrupt

bool pumpOn = false;
unsigned long pumpStartTime = 0;
unsigned long lastPulseSeenTime = 0;
unsigned long lastPulseCountAtCheck = 0;
float cumulativeVolumeL = 0.0;

bool rainExpected = false;               // [NOVELTY] set by checkWeatherForecast()
unsigned long lastWeatherCheckTime = 0;
bool weatherAvailable = false;           // false until first successful API call

// ---------------------------------------------------------------------------
// INTERRUPT SERVICE ROUTINE — keep this minimal, no Serial prints in here
// ---------------------------------------------------------------------------
void IRAM_ATTR countPulse() {
  pulseCount++;
}

// ---------------------------------------------------------------------------
// WIFI CONNECTION
// ---------------------------------------------------------------------------
void connectWiFi() {
  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttempt < 15000) {
    delay(400);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println(" connected.");
    Serial.print("IP address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println(" FAILED to connect. Will retry later. Local (reactive) logic still works offline.");
  }
}

// ---------------------------------------------------------------------------
// [NOVELTY] WEATHER FORECAST CHECK
// This is the core predictive feature: it asks an online weather API whether
// rain is likely in the next few hours, and stores the result in
// rainExpected, which the irrigation decision logic below then uses.
// ---------------------------------------------------------------------------
void checkWeatherForecast() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[NOVELTY] Skipping weather check — WiFi not connected.");
    weatherAvailable = false;
    return;
  }

  HTTPClient http;
  String url = "http://api.openweathermap.org/data/2.5/forecast?lat=" + String(CITY_LAT) +
               "&lon=" + String(CITY_LON) +
               "&appid=" + String(OPENWEATHER_API_KEY) +
               "&units=metric";

  http.begin(url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    String payload = http.getString();

    DynamicJsonDocument doc(6144);
    DeserializationError err = deserializeJson(doc, payload);

    if (!err) {
      // Use the nearest upcoming 3-hour forecast block's probability of precipitation.
      float pop = doc["list"][0]["pop"] | 0.0;  // "pop" = probability of precipitation, 0.0-1.0
      const char* condition = doc["list"][0]["weather"][0]["main"] | "Unknown";

      rainExpected = (pop >= RAIN_PROBABILITY_THRESHOLD);
      weatherAvailable = true;

      Serial.print("[NOVELTY] Weather forecast updated: condition=");
      Serial.print(condition);
      Serial.print(" pop=");
      Serial.print(pop * 100.0);
      Serial.print("% -> rainExpected=");
      Serial.println(rainExpected ? "YES" : "NO");
    } else {
      Serial.println("[FAULT] Failed to parse weather JSON. Keeping previous forecast state.");
    }
  } else {
    Serial.print("[FAULT] Weather API request failed, HTTP code=");
    Serial.println(httpCode);
    Serial.println("Keeping previous forecast state (or falling back to reactive-only logic).");
  }

  http.end();
  lastWeatherCheckTime = millis();
}

// ---------------------------------------------------------------------------
// PUMP CONTROL — this is the ONLY function that writes to RELAY_PIN
// ---------------------------------------------------------------------------
void setPump(bool on) {
  digitalWrite(RELAY_PIN, on ? (RELAY_ACTIVE_LOW ? LOW : HIGH)
                             : (RELAY_ACTIVE_LOW ? HIGH : LOW));
  if (on && !pumpOn) {
    pumpStartTime = millis();
    lastPulseSeenTime = millis();
    lastPulseCountAtCheck = pulseCount;
    Serial.println("[EVENT] irrigation_start");
  }
  if (!on && pumpOn) {
    Serial.print("[EVENT] irrigation_stop, volume_this_reading_L=");
    Serial.println(cumulativeVolumeL, 3);
  }
  pumpOn = on;
}

// ---------------------------------------------------------------------------
// SOIL MOISTURE — converts raw ADC to a 0-100% scale using YOUR calibration
// ---------------------------------------------------------------------------
int readSoilPercent() {
  int raw = analogRead(SOIL_PIN);
  int pct = map(raw, SOIL_DRY_VALUE, SOIL_WET_VALUE, 0, 100);
  pct = constrain(pct, 0, 100);
  return pct;
}

// ---------------------------------------------------------------------------
// FLOW CALCULATION — uses YOUR measured calibration factor
// ---------------------------------------------------------------------------
float pulsesToLitres(unsigned long pulses) {
  if (FLOW_CALIBRATION_PULSES_PER_L <= 0) return 0.0; // guard against divide-by-zero
  return (float)pulses / FLOW_CALIBRATION_PULSES_PER_L;
}

// ---------------------------------------------------------------------------
// SAFETY CHECK — runs every loop while the pump is on
// ---------------------------------------------------------------------------
void checkPumpSafety() {
  if (!pumpOn) return;

  unsigned long now = millis();

  if (now - pumpStartTime > MAX_PUMP_RUN_MS) {
    Serial.println("[FAULT] max_time_exceeded -> stopping pump");
    setPump(false);
    return;
  }

  if (pulseCount > lastPulseCountAtCheck) {
    lastPulseCountAtCheck = pulseCount;
    lastPulseSeenTime = now;
  } else if (now - lastPulseSeenTime > NO_FLOW_GRACE_MS) {
    Serial.println("[FAULT] no_flow_detected -> stopping pump (check tubing/pump/water level)");
    setPump(false);
    return;
  }
}

// ---------------------------------------------------------------------------
// [NOVELTY] IRRIGATION DECISION — combines soil moisture (reactive) with
// the weather forecast (predictive). This function is the actual proof that
// this project is predictive, not just reactive like a basic soil sensor demo.
// ---------------------------------------------------------------------------
void decideIrrigation(int soilPct) {
  if (!pumpOn) {
    if (soilPct < SOIL_DRY_THRESHOLD_PCT) {
      if (weatherAvailable && rainExpected) {
        Serial.println("[NOVELTY] PREDICTIVE SKIP: soil is dry, but rain is forecast soon -> irrigation withheld");
      } else {
        Serial.println("[DECISION] soil dry, no rain forecast (or forecast unavailable) -> starting pump");
        setPump(true);
      }
    }
  } else {
    if (soilPct >= SOIL_WET_THRESHOLD_PCT) {
      Serial.println("[DECISION] soil reached wet threshold -> stopping pump");
      setPump(false);
    }
  }
}

// ---------------------------------------------------------------------------
// SETUP
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("Booting predictive irrigation prototype...");

  pinMode(RELAY_PIN, OUTPUT);
  setPump(false); // ensure pump starts OFF and in a known state

  pinMode(FLOW_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), countPulse, FALLING);

  dht.begin();

  connectWiFi();
  if (WiFi.status() == WL_CONNECTED) {
    checkWeatherForecast(); // get an initial forecast right away
  }

  Serial.println("Setup complete. Reminder: calibration values are still placeholders");
  Serial.println("until you replace SOIL_DRY_VALUE/SOIL_WET_VALUE and");
  Serial.println("FLOW_CALIBRATION_PULSES_PER_L with your own measured numbers.");
}

// ---------------------------------------------------------------------------
// MAIN LOOP
// ---------------------------------------------------------------------------
void loop() {
  // ---- 1. Re-check weather periodically (not every loop) ----
  if (WiFi.status() == WL_CONNECTED &&
      (millis() - lastWeatherCheckTime > WEATHER_CHECK_INTERVAL_MS)) {
    checkWeatherForecast();
  }
  // If WiFi dropped, try to reconnect quietly without blocking the whole loop
  if (WiFi.status() != WL_CONNECTED) {
    static unsigned long lastReconnectAttempt = 0;
    if (millis() - lastReconnectAttempt > 30000) {
      lastReconnectAttempt = millis();
      WiFi.reconnect();
    }
  }

  // ---- 2. Read sensors ----
  int soilPct = readSoilPercent();
  float tempC = dht.readTemperature();
  float humidity = dht.readHumidity();
  bool dhtOk = !(isnan(tempC) || isnan(humidity));

  // ---- 3. Compute flow / volume ----
  unsigned long pulsesNow = pulseCount;
  cumulativeVolumeL = pulsesToLitres(pulsesNow);

  // ---- 4. Print current readings ----
  Serial.print("soil=");
  Serial.print(soilPct);
  Serial.print("% ");
  if (dhtOk) {
    Serial.print("temp=");
    Serial.print(tempC);
    Serial.print("C hum=");
    Serial.print(humidity);
    Serial.print("% ");
  } else {
    Serial.print("temp/hum=SENSOR_FAULT ");
  }
  Serial.print("pulses=");
  Serial.print(pulsesNow);
  Serial.print(" volume_L=");
  Serial.print(cumulativeVolumeL, 3);
  Serial.print(" rainExpected=");
  Serial.print(weatherAvailable ? (rainExpected ? "YES" : "NO") : "UNKNOWN");
  Serial.print(" pump=");
  Serial.println(pumpOn ? "ON" : "OFF");

  // ---- 5. Safety checks (only matters while pump is on) ----
  checkPumpSafety();

  // ---- 6. Irrigation decision (predictive + reactive combined) ----
  decideIrrigation(soilPct);

  delay(SENSOR_READ_INTERVAL_MS);
}
