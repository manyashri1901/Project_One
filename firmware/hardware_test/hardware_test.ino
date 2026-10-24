/*
  ===========================================================================
  JalBhoomi - HARDWARE BENCH TEST (Arduino-ESP32, C++)
  ===========================================================================
  Purpose: check each part on its own BEFORE running the full firmware.
  No WiFi, no cloud, no automatic irrigation. Everything happens only when
  you type a command in the Serial Monitor (115200 baud, "Newline" ending).

  It answers the three open items from the team update:
    * does the soil sensor give sensible raw values?      -> command  s
    * does the flow sensor produce pulses / what is K?     -> commands f, g
    * which GPIO26 level switches the relay on?            -> command  r
  and lets you run the pump for a few seconds once the above are known.

  SAFETY
    * At boot GPIO26 is left as a high-impedance INPUT, so this sketch does
      not drive the relay until you ask it to.
    * For the relay polarity test (r) DISCONNECT THE PUMP from the relay's
      COM/NO terminals. Listen/look for the relay click and its LED instead.
    * The pump test (p) only runs after you set the polarity (pol low|high)
      and type YES. It runs at most 5 s. Confirm the pump's voltage rating
      before connecting it to any supply. Never power it from the ESP32.

  PINS (same as the main firmware)
    Soil  GPIO34 (ADC1)   DHT11 GPIO4   Flow GPIO27 (FALLING, pull-up)
    Relay GPIO26

  LIBRARY: "DHT sensor library" (Adafruit) + "Adafruit Unified Sensor"

  STATUS: written, syntax-checked only, not yet run on the board.
  ===========================================================================
*/

#include <DHT.h>

#define SOIL_PIN   34
#define DHT_PIN    4
#define DHT_TYPE   DHT11
#define FLOW_PIN   27
#define RELAY_PIN  26

DHT dht(DHT_PIN, DHT_TYPE);

volatile unsigned long pulseCount = 0;
void IRAM_ATTR countPulse() { pulseCount++; }

// Relay polarity: unknown until you test it (command r) and set it (pol ...).
bool polarityKnown = false;
bool relayActiveLow = true;          // only used once polarityKnown == true
bool relayDriven = false;            // false = GPIO26 still high-impedance

unsigned long flowMark = 0;          // pulse count at "f" start, for "g"

// ---------------------------------------------------------------------------
void printHelp() {
  Serial.println();
  Serial.println("==== JalBhoomi hardware test ====");
  Serial.println("s        soil sensor: raw ADC for 10 s (try air, then water)");
  Serial.println("d        DHT11: 5 readings, 2.2 s apart");
  Serial.println("f        flow sensor: count pulses for 20 s (blow/pour through it)");
  Serial.println("g <ml>   flow calibration: after 'f' with a MEASURED volume (mL),");
  Serial.println("         prints pulses per litre (K)");
  Serial.println("r        relay polarity test (DISCONNECT THE PUMP FIRST)");
  Serial.println("pol low  | pol high   record which GPIO26 level switched the relay ON");
  Serial.println("p <sec>  pump test, 1-5 s (needs polarity set + typing YES)");
  Serial.println("a        all sensors live every 2 s (send any key to stop)");
  Serial.println("o        release GPIO26 (back to high-impedance)");
  Serial.println("h        this help");
  Serial.println("=================================");
}

bool keyPressed() {
  if (Serial.available()) {
    while (Serial.available()) Serial.read();
    return true;
  }
  return false;
}

String readLineBlocking(unsigned long timeoutMs) {
  String line = "";
  unsigned long t0 = millis();
  while (millis() - t0 < timeoutMs) {
    while (Serial.available()) {
      char c = Serial.read();
      if (c == '\n' || c == '\r') { if (line.length()) return line; }
      else line += c;
    }
    delay(5);
  }
  return "";
}

// ---------------------------------------------------------------------------
void relayWrite(int level) {
  if (!relayDriven) { pinMode(RELAY_PIN, OUTPUT); relayDriven = true; }
  digitalWrite(RELAY_PIN, level);
}

void relayRelease() {
  pinMode(RELAY_PIN, INPUT);          // high impedance: no drive
  relayDriven = false;
}

// ---------------------------------------------------------------------------
void testSoil() {
  Serial.println("[soil] raw ADC (0-4095) for 10 s. Note the value in AIR (dry) and in WATER (wet).");
  unsigned long t0 = millis();
  long sum = 0; int n = 0; int mn = 4095, mx = 0;
  while (millis() - t0 < 10000) {
    int raw = analogRead(SOIL_PIN);
    sum += raw; n++;
    if (raw < mn) mn = raw;
    if (raw > mx) mx = raw;
    Serial.print("raw="); Serial.println(raw);
    delay(500);
    if (keyPressed()) break;
  }
  if (n) {
    Serial.print("[soil] mean="); Serial.print(sum / n);
    Serial.print(" min="); Serial.print(mn);
    Serial.print(" max="); Serial.println(mx);
    if (mx >= 4090 || mn <= 5)
      Serial.println("[soil] WARNING: reading is stuck at the ADC limit - check wiring (3V3, GND, signal on GPIO34).");
    else if (mx - mn > 300)
      Serial.println("[soil] NOTE: large spread while stationary - noisy wiring or sensor moved.");
  }
}

void testDht() {
  Serial.println("[dht] 5 readings (DHT11 needs >= 1-2 s between reads)");
  for (int i = 0; i < 5; i++) {
    float t = dht.readTemperature();
    float h = dht.readHumidity();
    if (isnan(t) || isnan(h)) {
      Serial.println("[dht] FAIL: no valid reading - check 3V3/GND/data on GPIO4 (and the pull-up on a bare sensor).");
    } else {
      Serial.print("[dht] T="); Serial.print(t, 1);
      Serial.print(" C  H="); Serial.print(h, 0); Serial.println(" %");
    }
    delay(2200);
  }
}

void testFlow() {
  Serial.println("[flow] counting for 20 s. Blow or pour water through the sensor now.");
  noInterrupts(); pulseCount = 0; interrupts();
  unsigned long t0 = millis(), lastPrint = 0;
  while (millis() - t0 < 20000) {
    if (millis() - lastPrint >= 1000) {
      lastPrint = millis();
      Serial.print("[flow] t="); Serial.print((millis() - t0) / 1000);
      Serial.print("s pulses="); Serial.println(pulseCount);
    }
    if (keyPressed()) break;
  }
  flowMark = pulseCount;
  Serial.print("[flow] total pulses="); Serial.println(flowMark);
  if (flowMark == 0)
    Serial.println("[flow] NO PULSES: check the sensor's 5V/GND, the signal wire on GPIO27, flow direction arrow, and that water is actually moving.");
  else
    Serial.println("[flow] pulses seen. For calibration, pour a MEASURED volume and type: g <ml>");
}

void flowCalibration(const String& arg) {
  float ml = arg.toFloat();
  if (ml <= 0) { Serial.println("[flow] usage: g <mL poured>   (run 'f' first, pour a measured volume)"); return; }
  if (flowMark == 0) { Serial.println("[flow] no pulses recorded yet - run 'f' while pouring the measured volume."); return; }
  float k = (float)flowMark / (ml / 1000.0);
  Serial.print("[flow] "); Serial.print(flowMark);
  Serial.print(" pulses for "); Serial.print(ml, 0);
  Serial.print(" mL  ->  K = "); Serial.print(k, 1);
  Serial.println(" pulses per litre");
  Serial.println("[flow] repeat 3-5 times and average; use that as FLOW_CALIBRATION_PULSES_PER_L.");
}

void testRelayPolarity() {
  Serial.println("[relay] POLARITY TEST. The pump must be DISCONNECTED from the relay output.");
  Serial.println("[relay] Starting in 5 s. Watch the relay's LED and listen for a click.");
  delay(5000);
  for (int i = 1; i <= 3; i++) {
    Serial.print("[relay] round "); Serial.print(i); Serial.println("/3");
    Serial.println("[relay] GPIO26 = LOW  for 4 s  -> did the relay click / LED turn ON?");
    relayWrite(LOW);
    delay(4000);
    Serial.println("[relay] GPIO26 = HIGH for 4 s  -> did the relay click / LED turn ON?");
    relayWrite(HIGH);
    delay(4000);
    if (keyPressed()) break;
  }
  relayRelease();
  Serial.println("[relay] Done; GPIO26 released.");
  Serial.println("[relay] If the relay was ON while GPIO26 = LOW   -> type: pol low   (active-LOW)");
  Serial.println("[relay] If the relay was ON while GPIO26 = HIGH  -> type: pol high  (active-HIGH)");
}

void setPolarity(const String& arg) {
  if (arg == "low")       { relayActiveLow = true;  polarityKnown = true; }
  else if (arg == "high") { relayActiveLow = false; polarityKnown = true; }
  else { Serial.println("[relay] usage: pol low | pol high"); return; }
  Serial.print("[relay] recorded: relay is ON when GPIO26 is ");
  Serial.println(relayActiveLow ? "LOW  (set RELAY_ACTIVE_LOW = true in the main firmware)"
                                : "HIGH (set RELAY_ACTIVE_LOW = false in the main firmware)");
}

void pumpTest(const String& arg) {
  int sec = arg.toInt();
  if (sec < 1 || sec > 5) { Serial.println("[pump] usage: p <1-5>  seconds"); return; }
  if (!polarityKnown) { Serial.println("[pump] REFUSED: set the relay polarity first (run r, then pol low|high)."); return; }
  Serial.println("[pump] Confirm: pump has its OWN supply within its rated voltage, grounds are joined,");
  Serial.println("[pump] tubing is in water and the outlet is drained. Type YES within 15 s to run.");
  String ans = readLineBlocking(15000);
  ans.trim();
  if (ans != "YES") { Serial.println("[pump] cancelled."); return; }

  noInterrupts(); pulseCount = 0; interrupts();
  int onLevel  = relayActiveLow ? LOW : HIGH;
  int offLevel = relayActiveLow ? HIGH : LOW;
  Serial.print("[pump] ON for "); Serial.print(sec); Serial.println(" s");
  relayWrite(onLevel);
  unsigned long t0 = millis();
  while (millis() - t0 < (unsigned long)sec * 1000UL) {
    if (keyPressed()) break;          // any key = emergency stop
    delay(20);
  }
  relayWrite(offLevel);
  Serial.println("[pump] OFF");
  Serial.print("[pump] flow pulses during run = "); Serial.println(pulseCount);
  if (pulseCount == 0)
    Serial.println("[pump] no flow pulses: pump not moving water, tubing/priming problem, or flow sensor wiring.");
  relayRelease();
}

void liveAll() {
  Serial.println("[live] every 2 s; send any key to stop");
  while (true) {
    int raw = analogRead(SOIL_PIN);
    float t = dht.readTemperature(), h = dht.readHumidity();
    Serial.print("soil_raw="); Serial.print(raw);
    if (isnan(t) || isnan(h)) Serial.print(" dht=FAIL");
    else { Serial.print(" T="); Serial.print(t, 1); Serial.print("C H="); Serial.print(h, 0); Serial.print("%"); }
    Serial.print(" flow_pulses="); Serial.println(pulseCount);
    unsigned long t0 = millis();
    while (millis() - t0 < 2000) { if (keyPressed()) { Serial.println("[live] stopped"); return; } delay(10); }
  }
}

// ---------------------------------------------------------------------------
void handle(String line) {
  line.trim();
  if (line.length() == 0) return;
  String cmd = line, arg = "";
  int sp = line.indexOf(' ');
  if (sp > 0) { cmd = line.substring(0, sp); arg = line.substring(sp + 1); arg.trim(); }
  cmd.toLowerCase();
  arg.toLowerCase();

  if (cmd == "s")        testSoil();
  else if (cmd == "d")   testDht();
  else if (cmd == "f")   testFlow();
  else if (cmd == "g")   flowCalibration(arg);
  else if (cmd == "r")   testRelayPolarity();
  else if (cmd == "pol") setPolarity(arg);
  else if (cmd == "p")   pumpTest(arg);
  else if (cmd == "a")   liveAll();
  else if (cmd == "o")   { relayRelease(); Serial.println("[relay] GPIO26 released"); }
  else                   printHelp();
}

void setup() {
  Serial.begin(115200);
  delay(500);
  analogReadResolution(12);
  pinMode(FLOW_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), countPulse, FALLING);
  dht.begin();
  // GPIO26 intentionally NOT configured: relay stays undriven until you ask.
  printHelp();
}

void loop() {
  static String line = "";
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') { handle(line); line = ""; }
    else if (line.length() < 40) line += c;
  }
}
