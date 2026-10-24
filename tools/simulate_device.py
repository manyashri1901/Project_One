#!/usr/bin/env python3
"""
JalBhoomi device SIMULATOR - plays the ESP32 so the backend and the app can be tested
without hardware.

  *** EVERYTHING THIS PRODUCES IS INVENTED. Never use it as a result in the report. ***

It speaks the same topics and JSON as firmware/irrigation_logging (see
docs/INTEGRATION_CONTRACT.md) and applies the same command arbitration and refusal reasons.
Limits mirror config.h; if you change config.h, change DEFAULTS below.

Usage
  python tools/simulate_device.py --print --minutes 5                 # just show the messages
  python tools/simulate_device.py --host localhost --port 1883 --no-tls --user dev --password pw
  python tools/simulate_device.py --scenario fault_no_flow --host ...
Scenarios: normal | rain_skip | fault_no_flow | stuck_open | experiment
Live mode also listens on jalbhoomi/<id>/cmd and answers on .../ack like the board.
"""
import argparse
import json
import math
import random
import ssl
import sys
import time

DEFAULTS = dict(            # keep in step with firmware/irrigation_logging/config.h
    MAX_PUMP_RUN_S=30, NO_FLOW_GRACE_S=5, FAULT_LOCKOUT_S=60,
    SOIL_DRY=30, SOIL_WET=60, RAIN_PROB=0.40, FLOW_PULSES_PER_L=450.0,
    STUCK_GRACE_S=5, STUCK_WINDOW_S=10, STUCK_PULSES=20,
)
PREFIX = "jalbhoomi"


class SimDevice:
    """Deterministic fake ESP32. Call tick(dt) to advance time; it returns (topic, json_str) messages."""

    def __init__(self, device_id="sim-esp32-01", scenario="normal", epoch0=1_800_000_000, seed=1,
                 experiment_mode=None, **limits):
        self.id = device_id
        self.sc = scenario
        self.cfg = {**DEFAULTS, **limits}
        self.experiment_mode = (scenario == "experiment") if experiment_mode is None else experiment_mode
        self.rng = random.Random(seed)
        self.t = 0.0                      # uptime seconds
        self.epoch0 = epoch0
        self.soil = 45.0 if scenario == "normal" else 25.0      # percent; other scenarios start dry
        self.dry, self.wet = self.cfg["SOIL_DRY"], self.cfg["SOIL_WET"]
        self.rain_prob = self.cfg["RAIN_PROB"]
        self.auto = True
        self.pump = False
        self.pump_t0 = 0.0
        self.override_until = None
        self.adv_skip_until = 0.0
        self.last_fault = None
        self.pulses_total = 0
        self.stopped_at = None
        self.stuck_win = (0.0, 0)
        self.last_skip_event = -10**9
        self.last_stuck_event = -10**9
        self.last_adv_skip_event = -10**9
        self.agg = self._new_agg()
        self.backlog = []                 # summaries not yet "sent" (offline simulation)
        self.online = True
        self.rain_forecast = 1 if scenario == "rain_skip" else 0

    # ------------------------------------------------------------ helpers
    def _new_agg(self):
        return dict(n=0, soil=[], temp=[], hum=[], pulses=0, pump_s=0, faults=0)

    def topic(self, leaf):
        return f"{PREFIX}/{self.id}/{leaf}"

    def _event(self, typ, detail=None):
        m = {"device_id": self.id, "epoch": int(self.epoch0 + self.t), "uptime_s": int(self.t), "type": typ}
        if detail:
            m["detail"] = detail
        return (self.topic("event"), json.dumps(m))

    def _ack(self, cid, cmd, accepted, reason):
        return (self.topic("ack"), json.dumps({"device_id": self.id, "id": cid, "cmd": cmd,
                                               "accepted": accepted, "reason": reason}))

    def _set_pump(self, on, out):
        if on and not self.pump:
            self.pump, self.pump_t0 = True, self.t
            out.append(self._event("irrigation_start"))
        elif not on and self.pump:
            self.pump, self.stopped_at = False, self.t
            out.append(self._event("irrigation_stop"))

    def _fault(self, typ, out):
        self.agg["faults"] += 1
        self.last_fault = self.t
        self.override_until = None
        self._set_pump(False, out)
        out.append(self._event(typ))

    # --------------------------------------------------- arbitration (mirrors tryStartIrrigation)
    def try_start(self, duration_s, out):
        if self.experiment_mode:
            return "experiment_mode"
        if self.last_fault is not None and self.t - self.last_fault < self.cfg["FAULT_LOCKOUT_S"]:
            return "fault_lockout"
        if self.soil >= self.wet:
            return "soil_already_wet"
        if duration_s <= 0:
            return "bad_duration"
        duration_s = min(duration_s, self.cfg["MAX_PUMP_RUN_S"])
        self.override_until = self.t + duration_s
        self._set_pump(True, out)
        return "ok"

    def on_command(self, text):
        """Handle a message from jalbhoomi/<id>/cmd. Returns the messages the board would publish."""
        out = []
        try:
            doc = json.loads(text)
            assert isinstance(doc, dict)
        except Exception:
            return [self._ack("", "invalid", False, "bad_json")]
        cid, cmd = str(doc.get("id", ""))[:40], str(doc.get("cmd", ""))[:40]
        reason = "unknown_command"
        if cmd == "override_on":
            reason = self.try_start(int(doc.get("duration_s", 20)), out)
        elif cmd == "override_off":
            self.override_until = None
            self._set_pump(False, out)
            reason = "ok"
        elif cmd == "set_thresholds":
            dry = int(doc.get("soil_dry_pct", self.dry))
            wet = int(doc.get("soil_wet_pct", self.wet))
            pop = float(doc.get("rain_probability", self.rain_prob))
            if dry < 0 or wet > 100 or dry >= wet or pop < 0 or pop > 1:
                reason = "bad_thresholds"
            else:
                self.dry, self.wet, self.rain_prob, reason = dry, wet, pop, "ok"
        elif cmd == "set_auto":
            self.auto = bool(doc.get("enabled", True))
            if not self.auto and self.pump and self.override_until is None:
                self._set_pump(False, out)
            out.append(self._event("auto_on" if self.auto else "auto_off"))
            reason = "ok"
        elif cmd == "advise":
            action = str(doc.get("action", ""))
            hours = max(1, min(24, int(doc.get("hours", 6))))
            if action == "skip":
                self.adv_skip_until = self.t + hours * 3600
                reason = "ok"
            elif action == "irrigate":
                reason = self.try_start(int(doc.get("duration_s", 20)), out)
            else:
                reason = "bad_action"
        accepted = reason == "ok"
        out.append(self._ack(cid, cmd, accepted, reason))
        out.append(self._event("command_accepted" if accepted else "command_rejected", f"{cmd}:{reason}"))
        return out

    # ----------------------------------------------------------------- time
    def tick(self, dt=1.0):
        """Advance dt seconds (use 1). Returns messages to publish (empty list if offline and queuing)."""
        out = []
        self.t += dt
        a = self.agg
        # automatic decision (EXPERIMENT_MODE never starts the pump)
        if (not self.experiment_mode and not self.pump and self.auto and self.soil < self.dry
                and (self.last_fault is None or self.t - self.last_fault >= self.cfg["FAULT_LOCKOUT_S"])):
            if self.t < self.adv_skip_until and self.soil >= self.dry - 10:
                if self.t - self.last_adv_skip_event > 1800 or self.last_adv_skip_event < 0:
                    self.last_adv_skip_event = self.t                # advised skip, honoured unless critically dry
                    out.append(self._event("advisory_skip"))
            elif self.rain_forecast:
                if self.t - self.last_skip_event > 1800 or self.last_skip_event < 0:
                    self.last_skip_event = self.t                   # event is rate-limited like the firmware
                    out.append(self._event("predictive_skip"))
            else:
                self._set_pump(True, out)
        # physics
        flowing = self.pump and self.sc != "fault_no_flow"
        pulses = int(7.5 * dt) if flowing else 0                        # ~450 pulses/min while running
        if self.sc == "stuck_open" and not self.pump and self.t > 20:
            pulses = int(7.5 * dt)                                      # water flows with pump off
        self.pulses_total += pulses
        a["pulses"] += pulses
        if self.pump:
            if flowing:
                self.soil = min(100.0, self.soil + 1.5 * dt)         # water only reaches the soil if it flows
            a["pump_s"] += dt
        else:
            self.soil = max(0.0, self.soil - 0.01 * dt * (1 + 0.5 * math.sin(self.t / 600)))
        # interlocks
        if self.pump:
            if self.t - self.pump_t0 > self.cfg["MAX_PUMP_RUN_S"]:
                self._fault("fault_max_time", out)
            elif self.t - self.pump_t0 > self.cfg["NO_FLOW_GRACE_S"] and pulses == 0:
                self._fault("fault_no_flow", out)
            elif self.override_until is not None and self.t >= self.override_until:
                self.override_until = None
                self._set_pump(False, out)
                out.append(self._event("override_end"))
            elif self.override_until is None and self.soil >= self.wet:
                self._set_pump(False, out)
        elif self.stopped_at is None or self.t - self.stopped_at > self.cfg["STUCK_GRACE_S"]:
            w0, c0 = self.stuck_win
            if self.t - w0 > self.cfg["STUCK_WINDOW_S"]:
                self.stuck_win = (self.t, self.pulses_total)
            elif self.pulses_total - c0 >= self.cfg["STUCK_PULSES"]:
                self.stuck_win = (0.0, 0)
                self.last_fault = self.t
                self.agg["faults"] += 1
                if self.t - self.last_stuck_event > 600:            # event is rate-limited like the firmware
                    self.last_stuck_event = self.t
                    out.append(self._event("fault_valve_stuck_open"))
        # sensors
        a["n"] += 1
        a["soil"].append(self.soil + self.rng.uniform(-0.3, 0.3))
        a["temp"].append(round(29 + 2 * math.sin(self.t / 3600) + self.rng.uniform(-0.5, 0.5)))
        a["hum"].append(round(60 + self.rng.uniform(-2, 2)))
        # one summary per minute
        if int(self.t) % 60 == 0:
            self.backlog.append(self._summary())
            self.agg = self._new_agg()
        if self.online:
            while self.backlog:
                out.append(self.backlog.pop(0))
        return out

    def _summary(self):
        a = self.agg
        mean = lambda v: sum(v) / len(v) if v else None
        s = mean(a["soil"])
        raw = None if s is None else 3000 - (3000 - 1200) * s / 100          # inverse of the soil mapping
        m = {"device_id": self.id, "epoch": int(self.epoch0 + self.t), "uptime_s": int(self.t),
             "samples": a["n"], "soil_raw_mean": raw and round(raw, 1),
             "soil_raw_min": raw and int(raw - 8), "soil_raw_max": raw and int(raw + 8),
             "soil_pct_mean": s and round(s, 1), "temp_c_mean": round(mean(a["temp"]), 1) if a["temp"] else None,
             "hum_pct_mean": round(mean(a["hum"]), 1) if a["hum"] else None,
             "flow_pulses": a["pulses"], "flow_L": round(a["pulses"] / self.cfg["FLOW_PULSES_PER_L"], 3),
             "pump_on_s": int(a["pump_s"]), "faults": a["faults"]}
        if self.sc == "rain_skip":
            m["rain"] = self.rain_forecast
        m = {k: v for k, v in m.items() if v is not None}                    # unknown fields are omitted
        return (self.topic("summary"), json.dumps(m))


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device-id", default="sim-esp32-01")
    ap.add_argument("--scenario", default="normal", choices=["normal", "rain_skip", "fault_no_flow", "stuck_open", "experiment"])
    ap.add_argument("--print", action="store_true", help="print messages instead of connecting to a broker")
    ap.add_argument("--minutes", type=float, default=0, help="stop after N simulated minutes (0 = run forever)")
    ap.add_argument("--speed", type=float, default=60.0, help="simulated seconds per real second (live mode)")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8883)
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--ca", default="")
    ap.add_argument("--user", default="")
    ap.add_argument("--password", default="")
    args = ap.parse_args()

    dev = SimDevice(args.device_id, args.scenario)
    sys.stderr.write("SIMULATOR: all data is invented. Do not use it as results.\n")

    if args.print:
        steps = int((args.minutes or 5) * 60)
        for _ in range(steps):
            for topic, payload in dev.tick(1.0):
                print(topic, payload)
        return

    import paho.mqtt.client as mqtt
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=args.device_id)
    if args.user:
        c.username_pw_set(args.user, args.password)
    if not args.no_tls:
        c.tls_set(ca_certs=args.ca or None, cert_reqs=ssl.CERT_REQUIRED)
    pending = []

    def on_connect(cl, _u, _f, rc, _p):
        sys.stderr.write(f"connected ({rc})\n")
        cl.subscribe(dev.topic("cmd"), qos=1)

    def on_message(_cl, _u, msg):
        pending.append(msg.payload.decode("utf-8", "replace"))

    c.on_connect, c.on_message = on_connect, on_message
    c.connect(args.host, args.port, 30)
    c.loop_start()
    n = 0
    try:
        while not args.minutes or dev.t < args.minutes * 60:
            batch = []
            while pending:
                batch += dev.on_command(pending.pop(0))
            for _ in range(max(1, int(args.speed))):
                batch += dev.tick(1.0)
            for topic, payload in batch:
                c.publish(topic, payload)
                n += 1
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        c.loop_stop()
        c.disconnect()
        sys.stderr.write(f"published {n} messages\n")


if __name__ == "__main__":
    main()
