import os


def env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./jalbhoomi_dev.db")

MQTT_ENABLED = env_bool("MQTT_ENABLED", False)
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASS", "")
MQTT_TLS = env_bool("MQTT_TLS", True)
MQTT_CA_CERT = os.getenv("MQTT_CA_CERT", "")
MQTT_TOPIC_PREFIX = os.getenv("MQTT_TOPIC_PREFIX", "jalbhoomi")

# Write endpoints (commands, thresholds, alert acknowledgement) need this key in
# the X-API-Key header. If it is empty, those endpoints refuse every request.
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "")

# Optional Firebase integration (report: Firebase Authentication + Realtime Database).
# Off by default. When on, the backend mirrors each zone's latest reading to the
# Realtime Database (live app screens) and sends FCM push alerts for faults and
# predictive skips. Needs a service-account JSON from YOUR Firebase project.
FIREBASE_ENABLED = env_bool("FIREBASE_ENABLED", False)
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS", "")      # path to service-account JSON
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "")                # https://<project>-default-rtdb.<region>.firebasedatabase.app

# Who may use the write endpoints with a Firebase login (the Android app).
# Comma-separated Firebase UIDs. Empty = nobody (safe default): a logged-in
# stranger must never be able to start a pump.
ALLOWED_FIREBASE_UIDS = [u.strip() for u in os.getenv("ALLOWED_FIREBASE_UIDS", "").split(",") if u.strip()]
# true = GET endpoints also need X-API-Key or a valid Firebase token.
REQUIRE_AUTH_FOR_READS = env_bool("REQUIRE_AUTH_FOR_READS", False)

# Scheduled prediction job (report: "Backend / API: Ingestion, REST, Scheduled Prediction Job").
# Every PREDICTION_INTERVAL_S seconds the backend runs the model for each device and stores the
# result. It is a no-op until a model trained on REAL data is installed (see ml/README.md).
# It only stores advisory predictions; it never sends commands by itself.
PREDICTION_JOB_ENABLED = env_bool("PREDICTION_JOB_ENABLED", True)
PREDICTION_INTERVAL_S = int(os.getenv("PREDICTION_INTERVAL_S", "900"))

# Folder with public-dataset benchmark results written by tools/train_decision_model.py
BENCHMARK_DIR = os.getenv("BENCHMARK_DIR", "")   # default: <repo>/ml/results


# Consolidated data in the cloud (faculty requirement: every timestamped reading stays in the LOCAL database;
# only consolidated per-cycle and per-day rows go to the cloud). Empty = no cloud database configured.
# Example: postgresql+psycopg2://USER:PASSWORD@HOST/DBNAME?sslmode=require   (treat it like a password)
CLOUD_DATABASE_URL = os.getenv("CLOUD_DATABASE_URL", "")

# A "soil dry for too long" alert is raised when the soil stays below the dry threshold, with the pump off,
# for this many minutes. 60 is a prototype default, not an agronomic value.
SOIL_ALERT_MINUTES = int(os.getenv("SOIL_ALERT_MINUTES", "60"))

# Live demo simulator (backend/app/livesim.py): a SIMULATED board that runs inside the backend so the dashboard and
# the app can be shown working end to end without hardware. Off by default; it has its own device id and never
# touches a real board. Every value it produces is invented.
LIVE_SIM_ENABLED = env_bool("LIVE_SIM_ENABLED", False)
LIVE_SIM_DEVICE_ID = os.getenv("LIVE_SIM_DEVICE_ID", "sim-live-01")
LIVE_SIM_SCENARIO = os.getenv("LIVE_SIM_SCENARIO", "normal")
LIVE_SIM_SPEED = int(os.getenv("LIVE_SIM_SPEED", "30"))
