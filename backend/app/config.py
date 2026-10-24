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
