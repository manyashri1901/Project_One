"""Optional Firebase side channel: live status (Realtime Database) and push
alerts (FCM). Everything here is best-effort and must never break ingestion.

STATUS: written against the firebase-admin API but NOT tested against a real
Firebase project (needs your own service account). Unit tests use a fake sink.
"""
import logging

from . import config

log = logging.getLogger("jalbhoomi.firebase")

sink = None   # set by init(); tests replace it with a fake

ALERT_TEXT = {
    "predictive_skip": ("Irrigation skipped", "Rain is forecast, so watering was withheld."),
    "advisory_skip": ("Irrigation skipped", "Watering was withheld on advice."),
    "fault_no_flow": ("Pump stopped: no flow", "The pump ran but no water flow was detected. Check tubing, pump and water level."),
    "fault_max_time": ("Pump stopped: time limit", "The pump reached its maximum run-time and was stopped."),
    "fault_valve_stuck_open": ("Water flowing while pump is off", "Flow was detected with the pump off. The valve may be stuck open."),
    "command_rejected": ("Command refused", "The controller refused a command for safety reasons."),
}


class FirebaseSink:
    def __init__(self, credentials_path: str, db_url: str):
        import firebase_admin
        from firebase_admin import credentials, db, messaging
        self._db, self._messaging = db, messaging
        firebase_admin.initialize_app(credentials.Certificate(credentials_path), {"databaseURL": db_url})

    def set_latest(self, device_id: str, data: dict) -> None:
        self._db.reference(f"zones/{device_id}/latest").set(data)

    def verify_token(self, token: str) -> dict | None:
        from firebase_admin import auth
        try:
            return auth.verify_id_token(token)
        except Exception:   # noqa: BLE001 - any failure = not authenticated
            return None

    def send_alert(self, device_id: str, title: str, body: str, data: dict) -> None:
        msg = self._messaging.Message(
            topic=f"zone_{device_id}",
            notification=self._messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in data.items()},
        )
        self._messaging.send(msg)


def init() -> None:
    """Create the real sink if enabled and configured."""
    global sink
    if not config.FIREBASE_ENABLED:
        return
    if not (config.FIREBASE_CREDENTIALS and config.FIREBASE_DB_URL):
        log.error("FIREBASE_ENABLED but FIREBASE_CREDENTIALS / FIREBASE_DB_URL are not set; Firebase stays off")
        return
    try:
        sink = FirebaseSink(config.FIREBASE_CREDENTIALS, config.FIREBASE_DB_URL)
        log.info("Firebase sink ready")
    except Exception:   # noqa: BLE001 - never stop the backend because of Firebase
        log.exception("Firebase initialisation failed; continuing without it")
        sink = None


def push_status(device_id: str, summary: dict) -> None:
    if sink is None:
        return
    try:
        sink.set_latest(device_id, summary)
    except Exception:   # noqa: BLE001
        log.exception("Firebase status push failed")


def notify_alert(device_id: str, alert_type: str, severity: str, detail: str | None) -> None:
    """Push only faults and notices (predictive skips, refused commands)."""
    if sink is None or severity not in ("fault", "notice"):
        return
    title, body = ALERT_TEXT.get(alert_type, (alert_type.replace("_", " ").capitalize(), detail or ""))
    try:
        sink.send_alert(device_id, title, body, {"device_id": device_id, "type": alert_type, "severity": severity})
    except Exception:   # noqa: BLE001
        log.exception("Firebase push failed")


def verify_token(token: str) -> dict | None:
    """Firebase ID-token check. None if Firebase is off or the token is invalid."""
    if sink is None or not hasattr(sink, "verify_token"):
        return None
    try:
        return sink.verify_token(token)
    except Exception:   # noqa: BLE001
        log.exception("Firebase token verification failed")
        return None
