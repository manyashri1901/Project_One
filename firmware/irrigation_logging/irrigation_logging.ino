/*
  ===========================================================================
  JalBhoomi - Predictive Drip Irrigation - LOGGING FIRMWARE (v2)  - ESP32
  ===========================================================================

  WHAT CHANGED FROM irrigation_prototype_predictive.ino
    * RAW sensor readings are stored ONLY on the ESP32 (LittleFS: /raw.csv).
      Nothing raw is sent to the cloud.
    * Every 60 s the readings are CONSOLIDATED into ONE summary row
      (/summary.csv). Only these summary rows are pushed to the backend,
      one JSON message per row over MQTT with TLS (topic
      jalbhoomi/<DEVICE_ID>/summary), as in the project report's stack.
      Graphs/plots are generated from them.
    * Rows that could not be sent (WiFi down) stay in /summary.csv and are
      sent later, oldest first, so nothing is lost while offline.
    * EXPERIMENT_MODE (default: true) for the 1 kg soil calibration run:
      pump automation and the weather call are disabled, the relay is held
      OFF, and you log how much water you add through the Serial Monitor.
    * No hardware change. Same pin map as before.
    * COMMAND CHANNEL (from the report's design): the backend can send
      ADVISORY commands on jalbhoomi/<DEVICE_ID>/cmd (override on/off,
      thresholds, skip/irrigate advice). The ESP32 ALWAYS arbitrates them
      against its local interlocks and answers on .../ack with accepted or
      rejected plus a reason. Safety limits are never bypassed.
    * ALERT EVENTS: irrigation start/stop, predictive/advisory skip, faults
      and rejected commands are published on jalbhoomi/<DEVICE_ID>/event.

  FILES: all hardware-specific values (pins, relay polarity, calibration, safety
  limits, DEVICE_ID) are in config.h. Credentials go in secrets.h (copy
  secrets.example.h -> secrets.h; git-ignored). This file holds the logic only.

  STATUS: written but NOT yet tested on the board. Compile it, test it with
  the pump disconnected first, and fix anything that differs on your setup.

  PIN MAP (unchanged)
    Capacitive soil sensor  -> GPIO34 (ADC1)
    DHT11                   -> GPIO4
    Hall-effect flow sensor -> GPIO27 (interrupt, FALLING, pull-up)
    Relay module            -> GPIO26   (polarity NOT confirmed - see below)

  LIBRARIES (Library Manager, or platformio.ini)
    - "DHT sensor library" (Adafruit) + "Adafruit Unified Sensor"
    - "ArduinoJson" 6.x   (only used for the weather call)
    - "PubSubClient" by Nick O'Leary (MQTT)
    LittleFS, WiFi, HTTPClient come with the ESP32 Arduino core.

  SERIAL COMMANDS (115200 baud, send with newline)
    W50            water added: 50 mL (any number; also "W 50"). Logged as an
                   event on the next raw row and added to the running total.
    MARK text      write a free-text event, e.g.  MARK dry_baseline
                   or  MARK saturated
    STATUS         print storage use, counters, cloud backlog
    DUMP           print /raw.csv       (copy it out before it rotates)
    DUMPPREV       print /raw_prev.csv
    DUMPS          print /summary.csv
    CLEAR          delete all logs and the water-added total
    HELP           list commands
  ===========================================================================
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <DHT.h>
#include <LittleFS.h>
#include <time.h>

// All hardware-specific values live in config.h; credentials live in secrets.h
// (copy secrets.example.h -> secrets.h). Without secrets.h the placeholders are used.
#include "config.h"
#if __has_include("secrets.h")
  #include "secrets.h"
#else
  #include "secrets.example.h"
#endif

DHT dht(DHT_PIN, DHT_TYPE);

// ---------------------------------------------------------------------------
// DATA FORMAT - part of the integration contract (docs/INTEGRATION_CONTRACT.md).
// Do not rename columns without updating the backend and the contract.
// ---------------------------------------------------------------------------
const char* RAW_FILE      = "/raw.csv";
const char* RAW_PREV_FILE = "/raw_prev.csv";
const char* SUMMARY_FILE  = "/summary.csv";
const char* SENT_FILE     = "/sent.txt";

const char* RAW_HEADER =
  "epoch,ms,soil_raw,soil_pct,temp_c,hum_pct,pulses,volume_L,pump,rain,water_added_ml,event";
const char* SUMMARY_HEADER =
  "epoch,uptime_s,samples,soil_raw_mean,soil_raw_min,soil_raw_max,soil_pct_mean,"
  "temp_c_mean,hum_pct_mean,flow_pulses,flow_L,pump_on_s,faults,rain,water_added_ml";

// ---------------------------------------------------------------------------
// STATE
// ---------------------------------------------------------------------------
volatile unsigned long pulseCount = 0;

bool pumpOn = false;
unsigned long pumpStartTime = 0;
unsigned long lastPulseSeenTime = 0;
unsigned long lastPulseCountAtCheck = 0;

bool rainExpected = false;
bool weatherAvailable = false;
unsigned long lastWeatherCheckTime = 0;

float waterAddedMl = 0;            // running total from "W<ml>" commands
String pendingEvent = "";          // written into the next raw row
String rawBuf = "";                // buffered raw rows (flushed every 10 s)
unsigned long lastRawFlush = 0;
unsigned long lastSample = 0;
unsigned long sampleCounter = 0;
unsigned long sentOffset = 0;      // bytes of summary.csv already sent
bool fsOk = false;
bool timeSynced = false;

// Remote-command / alert state
bool autoMode = true;                      // false = farmer switched automatic irrigation OFF for this zone
bool overrideActive = false;               // farmer/advisory irrigation window
unsigned long overrideUntilMs = 0;
unsigned long adviseSkipUntilMs = 0;       // backend advised "skip irrigation" until
unsigned long lastFaultMs = 0;             // for the post-fault lockout
unsigned long lastSkipEventMs = 0;         // rate-limit skip alerts
unsigned long lastAdvSkipEventMs = 0;
int lastSoilPct = 0;
unsigned long pumpStoppedMs = 0;
unsigned long stuckWindowStart = 0;
unsigned long stuckBaseCount = 0;
unsigned long lastStuckEventMs = 0;

// One-minute accumulator
struct MinuteAgg {
  uint16_t n;
  uint16_t dhtN;
  uint32_t rawSum;
  int      rawMin;
  int      rawMax;
  uint32_t pctSum;
  float    tSum;
  float    hSum;
  unsigned long pulseStart;
  uint16_t pumpOnSamples;
  uint8_t  faults;
  unsigned long startMs;
} agg;

void IRAM_ATTR countPulse() { pulseCount++; }

// ---------------------------------------------------------------------------
// TIME
// ---------------------------------------------------------------------------
unsigned long epochNow() {
  time_t t = time(nullptr);
  if (t > 1700000000) { timeSynced = true; return (unsigned long)t; }
  return 0;   // not synced yet; use the uptime columns instead
}

// ---------------------------------------------------------------------------
// WIFI
// ---------------------------------------------------------------------------
void connectWiFi() {
  Serial.print("Connecting to WiFi");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(400);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print(" connected, IP ");
    Serial.println(WiFi.localIP());
    configTime(GMT_OFFSET_SEC, 0, "pool.ntp.org", "time.google.com");
  } else {
    Serial.println(" FAILED. Logging continues locally; cloud push will retry.");
  }
}

bool cloudConfigured() {
  return strstr(MQTT_HOST, "YOUR_") == nullptr && strlen(MQTT_HOST) > 3;
}

// ---------------------------------------------------------------------------
// WEATHER (unchanged logic; skipped in EXPERIMENT_MODE)
// ---------------------------------------------------------------------------
void checkWeatherForecast() {
  if (WiFi.status() != WL_CONNECTED) { weatherAvailable = false; return; }

  HTTPClient http;
  String url = "http://api.openweathermap.org/data/2.5/forecast?lat=" + String(CITY_LAT) +
               "&lon=" + String(CITY_LON) + "&appid=" + String(OPENWEATHER_API_KEY) +
               "&units=metric";
  http.begin(url);
  int code = http.GET();
  if (code == 200) {
    DynamicJsonDocument doc(6144);
    if (!deserializeJson(doc, http.getString())) {
      float pop = doc["list"][0]["pop"] | 0.0;
      rainExpected = (pop >= RAIN_PROBABILITY_THRESHOLD);
      weatherAvailable = true;
      Serial.print("[WEATHER] pop=");
      Serial.print(pop * 100.0);
      Serial.print("% rainExpected=");
      Serial.println(rainExpected ? "YES" : "NO");
    } else {
      Serial.println("[FAULT] weather JSON parse failed");
    }
  } else {
    Serial.print("[FAULT] weather HTTP ");
    Serial.println(code);
  }
  http.end();
  lastWeatherCheckTime = millis();
}

// ---------------------------------------------------------------------------
// MQTT OVER TLS + ALERT EVENTS
// ---------------------------------------------------------------------------
WiFiClientSecure mqttNet;
PubSubClient mqtt(mqttNet);

void onMqttMessage(char* topic, byte* payload, unsigned int len);   // defined below

String topicFor(const char* leaf) {
  return String("jalbhoomi/") + DEVICE_ID + "/" + leaf;
}

bool mqttConnect() {
  if (mqtt.connected()) return true;
  // PROTOTYPE ONLY: the broker certificate is not verified. For anything
  // beyond a bench test, use mqttNet.setCACert(<broker root CA>) instead.
  mqttNet.setInsecure();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(768);
  mqtt.setSocketTimeout(5);
  mqtt.setCallback(onMqttMessage);
  if (!mqtt.connect(DEVICE_ID, MQTT_USER, MQTT_PASS)) return false;
  mqtt.subscribe(topicFor("cmd").c_str());
  return true;
}

// Keep only [A-Za-z0-9_-] so server-supplied text can never break our JSON.
String sanitizeToken(const char* in) {
  String o;
  if (!in) return o;
  for (size_t i = 0; in[i] != 0 && o.length() < 40; i++) {
    char c = in[i];
    if (isalnum((unsigned char)c) || c == '-' || c == '_') o += c;
  }
  return o;
}

// Raw log marker + (if connected) an alert event for the backend.
// type/detail are fixed strings from this file or sanitized tokens.
void noteEvent(const char* type, const String& detail = "") {
  pendingEvent = type;
  if (!cloudConfigured() || !mqtt.connected()) return;
  String json = "{\"device_id\":\"";
  json += DEVICE_ID;
  json += "\",\"epoch\":";
  json += String(epochNow());
  json += ",\"uptime_s\":";
  json += String(millis() / 1000);
  json += ",\"type\":\"";
  json += type;
  json += "\"";
  if (detail.length() > 0) { json += ",\"detail\":\""; json += detail; json += "\""; }
  json += "}";
  mqtt.publish(topicFor("event").c_str(), json.c_str());
}

void publishAck(const String& id, const String& cmd, bool accepted, const char* reason) {
  if (!cloudConfigured() || !mqtt.connected()) return;
  String json = "{\"device_id\":\"";
  json += DEVICE_ID;
  json += "\",\"id\":\"";
  json += id;
  json += "\",\"cmd\":\"";
  json += cmd;
  json += "\",\"accepted\":";
  json += accepted ? "true" : "false";
  json += ",\"reason\":\"";
  json += reason;
  json += "\"}";
  mqtt.publish(topicFor("ack").c_str(), json.c_str());
}

// ---------------------------------------------------------------------------
// PUMP (the only function that writes to RELAY_PIN)
// ---------------------------------------------------------------------------
void setPump(bool on) {
  digitalWrite(RELAY_PIN, on ? (RELAY_ACTIVE_LOW ? LOW : HIGH)
                             : (RELAY_ACTIVE_LOW ? HIGH : LOW));
  if (on && !pumpOn) {
    pumpStartTime = millis();
    lastPulseSeenTime = millis();
    lastPulseCountAtCheck = pulseCount;
    noteEvent("irrigation_start");
  }
  if (!on && pumpOn) {
    pumpStoppedMs = millis();
    noteEvent("irrigation_stop");
  }
  pumpOn = on;
}

void checkPumpSafety() {
  if (!pumpOn) return;
  unsigned long now = millis();
  if (now - pumpStartTime > MAX_PUMP_RUN_MS) {
    Serial.println("[FAULT] max_time_exceeded -> pump OFF");
    agg.faults++;
    setPump(false);
    lastFaultMs = millis();
    overrideActive = false;
    noteEvent("fault_max_time");
    return;
  }
  if (pulseCount > lastPulseCountAtCheck) {
    lastPulseCountAtCheck = pulseCount;
    lastPulseSeenTime = now;
  } else if (now - lastPulseSeenTime > NO_FLOW_GRACE_MS) {
    Serial.println("[FAULT] no_flow -> pump OFF");
    agg.faults++;
    setPump(false);
    lastFaultMs = millis();
    overrideActive = false;
    noteEvent("fault_no_flow");
  }
}

// Third interlock: water still flowing although the pump is OFF.
// It can only raise an alert and block remote irrigation (fault lockout); the
// firmware cannot close a stuck valve by itself.
void checkValveStuck() {
  if (pumpOn) { stuckWindowStart = 0; return; }
  unsigned long now = millis();
  if (pumpStoppedMs != 0 && now - pumpStoppedMs < STUCK_GRACE_MS) { stuckWindowStart = 0; return; }
  if (stuckWindowStart == 0) { stuckWindowStart = now; stuckBaseCount = pulseCount; return; }
  if (pulseCount - stuckBaseCount >= STUCK_PULSE_LIMIT) {
    lastFaultMs = now;
    agg.faults++;
    if (lastStuckEventMs == 0 || now - lastStuckEventMs > 10UL * 60UL * 1000UL) {
      lastStuckEventMs = now;
      Serial.println("[FAULT] flow detected while pump is OFF (valve stuck open?)");
      noteEvent("fault_valve_stuck_open");
    }
    stuckWindowStart = 0;
  } else if (now - stuckWindowStart > STUCK_WINDOW_MS) {
    stuckWindowStart = now;
    stuckBaseCount = pulseCount;
  }
}

void decideIrrigation(int soilPct) {
  if (!pumpOn) {
    if (!autoMode) return;                     // manual zone: only an accepted override may start the pump
    // After any fault, do not restart the pump automatically until the lockout has passed
    // (otherwise a no-flow fault would be followed by an immediate restart, again and again).
    if (lastFaultMs != 0 && millis() - lastFaultMs < FAULT_LOCKOUT_MS) return;
    if (soilPct < SOIL_DRY_THRESHOLD_PCT) {
      // An advised skip is honoured only while the soil is not critically dry.
      bool advisedSkip = (millis() < adviseSkipUntilMs) &&
                         (soilPct >= SOIL_DRY_THRESHOLD_PCT - 10);
      if (advisedSkip) {
        if (millis() - lastAdvSkipEventMs > SKIP_EVENT_EVERY_MS || lastAdvSkipEventMs == 0) {
          lastAdvSkipEventMs = millis();
          noteEvent("advisory_skip");
        }
      } else if (weatherAvailable && rainExpected) {
        if (millis() - lastSkipEventMs > SKIP_EVENT_EVERY_MS || lastSkipEventMs == 0) {
          lastSkipEventMs = millis();
          noteEvent("predictive_skip");
        }
      } else {
        setPump(true);
      }
    }
  } else if (soilPct >= SOIL_WET_THRESHOLD_PCT) {
    setPump(false);
  }
}

// ---------------------------------------------------------------------------
// THRESHOLD PERSISTENCE (so a set_thresholds command survives a reboot)
// ---------------------------------------------------------------------------
const char* THRESH_FILE = "/thresholds.txt";

void saveThresholds() {
  File f = LittleFS.open(THRESH_FILE, FILE_WRITE);
  if (!f) return;
  f.print(String(SOIL_DRY_THRESHOLD_PCT) + "," + String(SOIL_WET_THRESHOLD_PCT) + "," +
          String(RAIN_PROBABILITY_THRESHOLD, 2) + "," + String(autoMode ? 1 : 0));
  f.close();
}

void loadThresholds() {
  if (!LittleFS.exists(THRESH_FILE)) return;
  File f = LittleFS.open(THRESH_FILE, FILE_READ);
  if (!f) return;
  String t = f.readString();
  f.close();
  int c1 = t.indexOf(','), c2 = t.indexOf(',', c1 + 1), c3 = t.indexOf(',', c2 + 1);
  if (c1 < 0 || c2 < 0) return;
  int dry = t.substring(0, c1).toInt();
  int wet = t.substring(c1 + 1, c2).toInt();
  float pop = t.substring(c2 + 1, c3 < 0 ? t.length() : c3).toFloat();
  if (c3 > 0) autoMode = t.substring(c3 + 1).toInt() != 0;
  if (dry >= 0 && wet <= 100 && dry < wet && pop >= 0 && pop <= 1) {
    SOIL_DRY_THRESHOLD_PCT = dry;
    SOIL_WET_THRESHOLD_PCT = wet;
    RAIN_PROBABILITY_THRESHOLD = pop;
  }
}

// ---------------------------------------------------------------------------
// REMOTE COMMANDS - ADVISORY ONLY. Every command is arbitrated here against
// local state; the interlocks (max run-time, no-flow) keep running regardless.
// Returns "ok" or a short reason for refusing.
// ---------------------------------------------------------------------------
const char* tryStartIrrigation(int durationS) {
  if (EXPERIMENT_MODE) return "experiment_mode";
  if (lastFaultMs != 0 && millis() - lastFaultMs < FAULT_LOCKOUT_MS) return "fault_lockout";
  if (lastSoilPct >= SOIL_WET_THRESHOLD_PCT) return "soil_already_wet";
  if (durationS <= 0) return "bad_duration";
  int maxS = (int)(MAX_PUMP_RUN_MS / 1000);
  if (durationS > maxS) durationS = maxS;          // never longer than the safety limit
  overrideActive = true;
  overrideUntilMs = millis() + (unsigned long)durationS * 1000UL;
  if (!pumpOn) setPump(true);
  return "ok";
}

void handleRemoteCommand(const String& text) {
  StaticJsonDocument<384> doc;
  if (deserializeJson(doc, text)) {
    publishAck("", "invalid", false, "bad_json");
    return;
  }
  String id  = sanitizeToken(doc["id"] | "");
  String cmd = sanitizeToken(doc["cmd"] | "");
  const char* reason = "unknown_command";

  if (cmd == "override_on") {
    reason = tryStartIrrigation(doc["duration_s"] | 20);
  } else if (cmd == "override_off") {
    overrideActive = false;
    if (pumpOn) setPump(false);
    reason = "ok";
  } else if (cmd == "set_thresholds") {
    int dry = doc["soil_dry_pct"] | SOIL_DRY_THRESHOLD_PCT;
    int wet = doc["soil_wet_pct"] | SOIL_WET_THRESHOLD_PCT;
    float pop = doc["rain_probability"] | RAIN_PROBABILITY_THRESHOLD;
    if (dry < 0 || wet > 100 || dry >= wet || pop < 0 || pop > 1) {
      reason = "bad_thresholds";
    } else {
      SOIL_DRY_THRESHOLD_PCT = dry;
      SOIL_WET_THRESHOLD_PCT = wet;
      RAIN_PROBABILITY_THRESHOLD = pop;
      saveThresholds();
      reason = "ok";
    }
  } else if (cmd == "set_auto") {
    bool enabled = doc["enabled"] | true;
    autoMode = enabled;
    saveThresholds();
    if (!enabled && pumpOn && !overrideActive) setPump(false);   // stop an automatic cycle
    noteEvent(enabled ? "auto_on" : "auto_off");
    reason = "ok";
  } else if (cmd == "advise") {
    String action = sanitizeToken(doc["action"] | "");
    int hours = doc["hours"] | 6;
    if (hours < 1) hours = 1;
    if (hours > 24) hours = 24;
    if (action == "skip") {
      adviseSkipUntilMs = millis() + (unsigned long)hours * 3600000UL;
      reason = "ok";
    } else if (action == "irrigate") {
      reason = tryStartIrrigation(doc["duration_s"] | 20);
    } else {
      reason = "bad_action";
    }
  }

  bool accepted = (strcmp(reason, "ok") == 0);
  publishAck(id, cmd, accepted, reason);
  noteEvent(accepted ? "command_accepted" : "command_rejected", cmd + ":" + reason);
}

void onMqttMessage(char* topic, byte* payload, unsigned int len) {
  (void)topic;
  if (len == 0 || len > 380) return;
  char buf[384];
  memcpy(buf, payload, len);
  buf[len] = 0;
  handleRemoteCommand(String(buf));
}

// ---------------------------------------------------------------------------
// SENSORS
// ---------------------------------------------------------------------------
int readSoilRaw() {
  long sum = 0;
  for (int i = 0; i < SOIL_ADC_AVG_SAMPLES; i++) {
    sum += analogRead(SOIL_PIN);
    delayMicroseconds(200);
  }
  return (int)(sum / SOIL_ADC_AVG_SAMPLES);
}

int soilPercent(int raw) {
  int pct = map(raw, SOIL_DRY_VALUE, SOIL_WET_VALUE, 0, 100);
  return constrain(pct, 0, 100);
}

float pulsesToLitres(unsigned long pulses) {
  if (FLOW_CALIBRATION_PULSES_PER_L <= 0) return 0.0;
  return (float)pulses / FLOW_CALIBRATION_PULSES_PER_L;
}

// ---------------------------------------------------------------------------
// FILE HELPERS
// ---------------------------------------------------------------------------
void ensureHeader(const char* path, const char* header) {
  if (!LittleFS.exists(path)) {
    File f = LittleFS.open(path, FILE_WRITE);
    if (f) { f.print(header); f.print('\n'); f.close(); }   // '\n' only, so byte offsets match
  }
}

size_t fileSize(const char* path) {
  if (!LittleFS.exists(path)) return 0;
  File f = LittleFS.open(path, FILE_READ);
  size_t s = f ? f.size() : 0;
  if (f) f.close();
  return s;
}

void saveSentOffset() {
  File f = LittleFS.open(SENT_FILE, FILE_WRITE);
  if (f) { f.print(sentOffset); f.close(); }
}

void loadSentOffset() {
  sentOffset = strlen(SUMMARY_HEADER) + 1;   // just past the header line
  if (LittleFS.exists(SENT_FILE)) {
    File f = LittleFS.open(SENT_FILE, FILE_READ);
    if (f) { sentOffset = (unsigned long)f.readString().toInt(); f.close(); }
  }
  size_t sz = fileSize(SUMMARY_FILE);
  if (sentOffset > sz) sentOffset = strlen(SUMMARY_HEADER) + 1;
}

// Append buffered raw rows; rotate raw.csv when it gets large.
void flushRaw() {
  if (!fsOk || rawBuf.length() == 0) { lastRawFlush = millis(); return; }

  if (fileSize(RAW_FILE) > RAW_MAX_BYTES) {
    if (LittleFS.exists(RAW_PREV_FILE)) LittleFS.remove(RAW_PREV_FILE);
    LittleFS.rename(RAW_FILE, RAW_PREV_FILE);
    ensureHeader(RAW_FILE, RAW_HEADER);
    Serial.println("[LOG] raw.csv rotated to raw_prev.csv (older rows of raw_prev are discarded)");
  }
  File f = LittleFS.open(RAW_FILE, FILE_APPEND);
  if (f) { f.print(rawBuf); f.close(); rawBuf = ""; }
  else   { Serial.println("[FAULT] cannot open raw.csv"); }
  lastRawFlush = millis();
}

void appendRaw(int raw, int pct, bool dhtOk, float t, float h,
               unsigned long pulses, float volL) {
  String row;
  row.reserve(110);
  row += String(epochNow());           row += ',';
  row += String(millis());             row += ',';
  row += String(raw);                  row += ',';
  row += String(pct);                  row += ',';
  row += dhtOk ? String(t, 1) : String("");  row += ',';
  row += dhtOk ? String(h, 1) : String("");  row += ',';
  row += String(pulses);               row += ',';
  row += String(volL, 3);              row += ',';
  row += pumpOn ? '1' : '0';           row += ',';
  row += weatherAvailable ? (rainExpected ? "1" : "0") : "";  row += ',';
  row += String(waterAddedMl, 0);      row += ',';
  row += pendingEvent;
  row += '\n';
  rawBuf += row;
  if (rawBuf.length() > 900 || millis() - lastRawFlush > RAW_FLUSH_MS) flushRaw();
}

// ---------------------------------------------------------------------------
// MINUTE AGGREGATION  (raw -> ONE summary row)
// ---------------------------------------------------------------------------
void resetAgg() {
  agg.n = 0; agg.dhtN = 0; agg.rawSum = 0;
  agg.rawMin = 4095; agg.rawMax = 0;
  agg.pctSum = 0; agg.tSum = 0; agg.hSum = 0;
  agg.pulseStart = pulseCount;
  agg.pumpOnSamples = 0; agg.faults = 0;
  agg.startMs = millis();
}

void addToAgg(int raw, int pct, bool dhtOk, float t, float h) {
  agg.n++;
  agg.rawSum += raw;
  if (raw < agg.rawMin) agg.rawMin = raw;
  if (raw > agg.rawMax) agg.rawMax = raw;
  agg.pctSum += pct;
  if (dhtOk) { agg.dhtN++; agg.tSum += t; agg.hSum += h; }
  if (pumpOn) agg.pumpOnSamples++;
}

// Turn one CSV summary row into a JSON object using the header names.
// Numbers are copied as text, so epoch values stay exact. Empty fields
// (e.g. no DHT reading that minute) are left out.
String csvRowToJson(const char* header, const String& row) {
  String hdr(header);
  String json = "{\"device_id\":\"";
  json += DEVICE_ID;
  json += "\"";
  int hs = 0, rs = 0;
  while (hs <= (int)hdr.length()) {
    int he = hdr.indexOf(',', hs);  if (he < 0) he = hdr.length();
    int re = row.indexOf(',', rs);  if (re < 0) re = row.length();
    String key = hdr.substring(hs, he);
    String val = row.substring(rs, re);
    val.trim();
    if (val.length() > 0) {
      json += ",\"";
      json += key;
      json += "\":";
      json += val;
    }
    if (he >= (int)hdr.length()) break;
    hs = he + 1;
    rs = re + 1;
  }
  json += "}";
  return json;
}

// Publish unsent summary rows (oldest first), one JSON message per row.
// Raw data is never sent. NOTE: PubSubClient publishes at QoS 0, so a
// successful publish() means "handed to the socket", not "stored by the
// backend"; a row can still be lost if the link drops at that moment.
void syncSummaries() {
  if (!fsOk || !cloudConfigured() || WiFi.status() != WL_CONNECTED) return;
  if (!mqttConnect()) {
    Serial.print("[MQTT] connect failed, state=");
    Serial.println(mqtt.state());
    return;
  }

  String topic = String("jalbhoomi/") + DEVICE_ID + "/summary";

  for (int push = 0; push < MAX_PUSHES_PER_CYCLE; push++) {
    File f = LittleFS.open(SUMMARY_FILE, FILE_READ);
    if (!f) return;
    if (sentOffset >= f.size()) { f.close(); break; }   // nothing pending
    f.seek(sentOffset);

    int rows = 0;
    bool failed = false;
    unsigned long newOffset = sentOffset;
    while (f.available() && rows < ROWS_PER_PUSH) {
      String line = f.readStringUntil('\n');
      if (line.length() == 0) break;
      String json = csvRowToJson(SUMMARY_HEADER, line);
      if (!mqtt.publish(topic.c_str(), json.c_str())) { failed = true; break; }
      rows++;
      newOffset = f.position();
    }
    f.close();
    mqtt.loop();

    if (rows > 0) {
      sentOffset = newOffset;
      saveSentOffset();
      Serial.print("[MQTT] published ");
      Serial.print(rows);
      Serial.println(" summary row(s)");
    }
    if (failed) {
      Serial.println("[MQTT] publish failed - rows kept, will retry");
      break;
    }
    if (rows == 0) break;
  }

  // Everything sent and the file is big: start a fresh summary file.
  size_t sz = fileSize(SUMMARY_FILE);
  if (sz > SUMMARY_MAX_BYTES && sentOffset >= sz) {
    LittleFS.remove(SUMMARY_FILE);
    ensureHeader(SUMMARY_FILE, SUMMARY_HEADER);
    sentOffset = strlen(SUMMARY_HEADER) + 1;
    saveSentOffset();
  }
}

void closeMinute() {
  if (agg.n == 0) { resetAgg(); return; }

  unsigned long pulses = pulseCount - agg.pulseStart;
  float rawMean = (float)agg.rawSum / agg.n;
  float pctMean = (float)agg.pctSum / agg.n;
  String tMean = agg.dhtN ? String(agg.tSum / agg.dhtN, 1) : String("");
  String hMean = agg.dhtN ? String(agg.hSum / agg.dhtN, 1) : String("");
  unsigned long pumpOnS = (unsigned long)agg.pumpOnSamples * SENSOR_READ_INTERVAL_MS / 1000;

  String row;
  row.reserve(140);
  row += String(epochNow());            row += ',';
  row += String(millis() / 1000);       row += ',';
  row += String(agg.n);                 row += ',';
  row += String(rawMean, 1);            row += ',';
  row += String(agg.rawMin);            row += ',';
  row += String(agg.rawMax);            row += ',';
  row += String(pctMean, 1);            row += ',';
  row += tMean;                         row += ',';
  row += hMean;                         row += ',';
  row += String(pulses);                row += ',';
  row += String(pulsesToLitres(pulses), 3); row += ',';
  row += String(pumpOnS);               row += ',';
  row += String(agg.faults);            row += ',';
  row += weatherAvailable ? (rainExpected ? "1" : "0") : "";  row += ',';
  row += String(waterAddedMl, 0);
  row += '\n';

  if (fsOk) {
    File f = LittleFS.open(SUMMARY_FILE, FILE_APPEND);
    if (f) { f.print(row); f.close(); }
    else   { Serial.println("[FAULT] cannot open summary.csv"); }
  }
  Serial.print("[SUMMARY] ");
  Serial.print(row);

  resetAgg();
  syncSummaries();
}

// ---------------------------------------------------------------------------
// SERIAL COMMANDS
// ---------------------------------------------------------------------------
void dumpFile(const char* path) {
  flushRaw();
  if (!LittleFS.exists(path)) { Serial.println("(file not found)"); return; }
  File f = LittleFS.open(path, FILE_READ);
  if (!f) return;
  Serial.print("----- BEGIN ");
  Serial.println(path);
  while (f.available()) Serial.write(f.read());
  Serial.print("----- END ");
  Serial.println(path);
  f.close();
}

void printStatus() {
  Serial.println("----- STATUS -----");
  Serial.print("mode: ");
  Serial.println(EXPERIMENT_MODE ? "EXPERIMENT (relay held OFF)" : "NORMAL");
  Serial.print("time synced: ");  Serial.println(timeSynced ? "yes" : "no");
  Serial.print("WiFi: ");         Serial.println(WiFi.status() == WL_CONNECTED ? "connected" : "down");
  Serial.print("cloud push: ");   Serial.println(cloudConfigured() ? "MQTT configured" : "OFF (placeholder broker)");
  Serial.print("water added (mL): "); Serial.println(waterAddedMl, 0);
  Serial.print("flow pulses: ");  Serial.println(pulseCount);
  if (fsOk) {
    Serial.print("FS used/total (bytes): ");
    Serial.print(LittleFS.usedBytes()); Serial.print(" / "); Serial.println(LittleFS.totalBytes());
    Serial.print("raw.csv bytes: ");     Serial.println(fileSize(RAW_FILE) + rawBuf.length());
    Serial.print("summary.csv bytes: "); Serial.println(fileSize(SUMMARY_FILE));
    Serial.print("summary bytes not yet sent: ");
    size_t sz = fileSize(SUMMARY_FILE);
    Serial.println(sz > sentOffset ? sz - sentOffset : 0);
  } else {
    Serial.println("FILESYSTEM NOT AVAILABLE");
  }
  Serial.println("------------------");
}

void handleCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;
  String up = cmd; up.toUpperCase();

  if (up.startsWith("W")) {
    float ml = cmd.substring(1).toFloat();
    if (ml > 0) {
      waterAddedMl += ml;
      pendingEvent = "water_add_" + String(ml, 0) + "ml";
      Serial.print("[EVENT] ");
      Serial.print(pendingEvent);
      Serial.print("  total_added_mL=");
      Serial.println(waterAddedMl, 0);
    } else {
      Serial.println("Usage: W50  (millilitres added)");
    }
  } else if (up.startsWith("MARK")) {
    String t = cmd.substring(4); t.trim();
    t.replace(',', ' ');
    pendingEvent = t.length() ? t : String("mark");
    Serial.print("[EVENT] "); Serial.println(pendingEvent);
  } else if (up == "STATUS") {
    printStatus();
  } else if (up == "DUMP") {
    dumpFile(RAW_FILE);
  } else if (up == "DUMPPREV") {
    dumpFile(RAW_PREV_FILE);
  } else if (up == "DUMPS") {
    dumpFile(SUMMARY_FILE);
  } else if (up == "CLEAR") {
    rawBuf = "";
    LittleFS.remove(RAW_FILE);
    LittleFS.remove(RAW_PREV_FILE);
    LittleFS.remove(SUMMARY_FILE);
    LittleFS.remove(SENT_FILE);
    ensureHeader(RAW_FILE, RAW_HEADER);
    ensureHeader(SUMMARY_FILE, SUMMARY_HEADER);
    sentOffset = strlen(SUMMARY_HEADER) + 1;
    waterAddedMl = 0;
    Serial.println("[LOG] all logs cleared");
  } else {
    Serial.println("Commands: W<ml> | MARK <text> | STATUS | DUMP | DUMPPREV | DUMPS | CLEAR");
  }
}

void pollSerial() {
  static String line = "";
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') { handleCommand(line); line = ""; }
    else if (line.length() < 80) line += c;
  }
}

// ---------------------------------------------------------------------------
// SETUP
// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("Booting JalBhoomi logging firmware v2 ...");

  pinMode(RELAY_PIN, OUTPUT);
  setPump(false);                       // always start with the pump OFF
  pendingEvent = "";

  pinMode(FLOW_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), countPulse, FALLING);
  analogReadResolution(12);
  dht.begin();

  fsOk = LittleFS.begin(true);          // true = format if mount fails
  if (fsOk) {
    ensureHeader(RAW_FILE, RAW_HEADER);
    ensureHeader(SUMMARY_FILE, SUMMARY_HEADER);
    loadSentOffset();
    loadThresholds();
  } else {
    Serial.println("[FAULT] LittleFS failed - nothing can be logged to flash");
  }

  connectWiFi();
  if (!EXPERIMENT_MODE && WiFi.status() == WL_CONNECTED) checkWeatherForecast();

  resetAgg();
  Serial.println(EXPERIMENT_MODE
    ? "EXPERIMENT MODE: relay held OFF. Type HELP for commands."
    : "NORMAL MODE: predictive irrigation active.");
  Serial.println("Calibration values are still placeholders until measured.");
}

// ---------------------------------------------------------------------------
// LOOP (non-blocking)
// ---------------------------------------------------------------------------
void loop() {
  pollSerial();
  if (mqtt.connected()) mqtt.loop();

  if (WiFi.status() != WL_CONNECTED) {
    static unsigned long lastReconnect = 0;
    if (millis() - lastReconnect > 30000) {
      lastReconnect = millis();
      WiFi.reconnect();
    }
  }
  // Keep the MQTT session (and command subscription) alive.
  if (cloudConfigured() && WiFi.status() == WL_CONNECTED && !mqtt.connected()) {
    static unsigned long lastMqttTry = 0;
    if (millis() - lastMqttTry > 30000) {
      lastMqttTry = millis();
      mqttConnect();
    }
  }
  if (!EXPERIMENT_MODE && WiFi.status() == WL_CONNECTED &&
      millis() - lastWeatherCheckTime > WEATHER_CHECK_INTERVAL_MS) {
    checkWeatherForecast();
  }

  if (millis() - lastSample >= SENSOR_READ_INTERVAL_MS) {
    lastSample = millis();

    int raw = readSoilRaw();
    int pct = soilPercent(raw);
    lastSoilPct = pct;
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    bool dhtOk = !(isnan(t) || isnan(h));

    unsigned long pulses = pulseCount;
    float volL = pulsesToLitres(pulses);

    addToAgg(raw, pct, dhtOk, t, h);

    if (!EXPERIMENT_MODE) {
      checkPumpSafety();                       // interlocks always run first
      checkValveStuck();
      if (overrideActive) {
        if (!pumpOn) {
          overrideActive = false;              // a fault or limit already stopped it
        } else if ((long)(millis() - overrideUntilMs) >= 0) {
          overrideActive = false;
          setPump(false);
          noteEvent("override_end");
        }
      } else {
        decideIrrigation(pct);
      }
    }

    // The row carries any pending event (water added, mark, pump change, ...)
    sampleCounter++;
    if (pendingEvent.length() > 0 || sampleCounter % RAW_LOG_EVERY_N_SAMPLES == 0) {
      appendRaw(raw, pct, dhtOk, t, h, pulses, volL);
    }
    pendingEvent = "";

    Serial.print("raw=");   Serial.print(raw);
    Serial.print(" soil="); Serial.print(pct);
    Serial.print("% ");
    if (dhtOk) { Serial.print("T="); Serial.print(t, 1); Serial.print("C H="); Serial.print(h, 0); Serial.print("% "); }
    else       { Serial.print("DHT=FAULT "); }
    Serial.print("pulses="); Serial.print(pulses);
    Serial.print(" added_mL="); Serial.print(waterAddedMl, 0);
    Serial.print(" pump="); Serial.println(pumpOn ? "ON" : "OFF");
  }

  if (millis() - agg.startMs >= SUMMARY_PERIOD_MS) closeMinute();
}
