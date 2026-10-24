// secrets.example.h
// Copy this file to  secrets.h  (same folder) and fill in YOUR values.
// secrets.h is in .gitignore and must never be committed or sent to anyone.
// If secrets.h does not exist the sketch still compiles with these placeholders
// and cloud push stays OFF (summaries are only stored on the ESP32).
#pragma once

const char* WIFI_SSID     = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// MQTT broker (TLS, port set in config.h). Use per-device credentials.
const char* MQTT_HOST = "YOUR_MQTT_BROKER_HOST";
const char* MQTT_USER = "YOUR_DEVICE_USER";
const char* MQTT_PASS = "YOUR_DEVICE_PASSWORD";

const char* OPENWEATHER_API_KEY = "YOUR_API_KEY";
