import math
import random
import statistics
import threading
import time
from collections import deque
from copy import deepcopy

from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


DEFAULT_CONFIG = {
    "mode": "bench",
    "simulate_hardware": True,
    "sample_hz": 20,
    "spo2_sample_hz": 5,
    "calibration_seconds": 10,
    "min_inhale_delta_pa": -5.0,
    "noise_sigma_multiplier": 3.0,
    "cooldown_seconds": 1.5,
    "watchdog_seconds": 15.0,
    "servo_rest_angle": 30,
    "servo_press_angle": 105,
    "pump_hold_seconds": 0.55,
    "pump_cooldown_seconds": 3.0,
    "pump_test_limit": 5,
    "spo2_alarm_threshold": 92,
}

GPIO_PINS = {
    "led": 27,
    "servo": 18,
    "i2c_sda": 2,
    "i2c_scl": 3,
}

state_lock = threading.Lock()
config_lock = threading.Lock()
pump_lock = threading.Lock()

config = deepcopy(DEFAULT_CONFIG)

system_state = {
    "pressure_pa": None,
    "baseline_pressure_pa": None,
    "delta_pressure_pa": 0.0,
    "noise_sigma_pa": 0.0,
    "effective_inhale_threshold_pa": DEFAULT_CONFIG["min_inhale_delta_pa"],
    "breath_state": "calibrating",
    "last_breath_time": None,
    "last_breath_age_s": None,
    "resp_rate_est": None,
    "spo2": None,
    "heart_rate": None,
    "servo_state": "idle",
    "mode": DEFAULT_CONFIG["mode"],
    "hardware": {
        "simulation": True,
        "pressure_sensor": "simulated",
        "pulse_oximeter": "simulated",
        "servo": "simulated",
        "led": "simulated",
        "gpio_pins": GPIO_PINS,
    },
    "alarms": [],
    "history": {
        "pressure": [],
        "events": [],
    },
    "pump_test_count": 0,
    "started_at": time.time(),
    "last_pump_time": 0.0,
}

pressure_window = deque(maxlen=5)
baseline_window = deque(maxlen=200)
calibration_samples = []
breath_times = deque(maxlen=8)
event_history = deque(maxlen=80)
pressure_history = deque(maxlen=400)
stop_event = threading.Event()
monitor_thread = None
monitor_thread_lock = threading.Lock()

servo_device = None
led_device = None
pressure_reader = None
pulse_reader = None


class SimulatedPressureReader:
    def __init__(self):
        self.start_time = time.time()
        self.last_breath_center = time.time()
        self.breath_interval = 5.0

    def read_pa(self):
        now = time.time()
        base = 101_325.0 + 1.2 * math.sin((now - self.start_time) / 18.0)
        noise = random.gauss(0.0, 0.45)

        if now - self.last_breath_center >= self.breath_interval:
            self.last_breath_center = now
            self.breath_interval = random.uniform(4.0, 6.5)

        phase = now - self.last_breath_center
        inhale = -8.5 * math.exp(-((phase - 0.32) ** 2) / 0.028)

        return base + noise + inhale


class SimulatedPulseReader:
    def __init__(self):
        self.start_time = time.time()

    def read(self):
        elapsed = time.time() - self.start_time
        spo2 = 97 + 0.8 * math.sin(elapsed / 14.0) + random.gauss(0, 0.25)
        heart_rate = 76 + 4.0 * math.sin(elapsed / 10.0) + random.gauss(0, 0.8)
        return round(max(88, min(100, spo2)), 1), int(max(45, min(140, heart_rate)))


class HardwarePressureReader:
    def __init__(self):
        import bme280
        import smbus2

        self.bme280 = bme280
        self.bus = smbus2.SMBus(1)
        self.address = 0x76
        self.calibration = bme280.load_calibration_params(self.bus, self.address)

    def read_pa(self):
        data = self.bme280.sample(self.bus, self.address, self.calibration)
        return float(data.pressure) * 100.0


class HardwarePulseReader:
    def read(self):
        # MAX30102 Python libraries vary by module. Keep this adapter explicit so
        # teams can swap in their chosen library without changing Flask routes.
        raise RuntimeError("MAX30102 adapter is not configured")


def add_event(kind, message, level="info"):
    item = {
        "ts": round(time.time(), 3),
        "kind": kind,
        "level": level,
        "message": message,
    }
    event_history.append(item)


def set_led(active):
    if led_device is None:
        return
    try:
        led_device.on() if active else led_device.off()
    except Exception as exc:
        add_event("led_error", f"LED control failed: {exc}", "warning")


def safe_config():
    with config_lock:
        return deepcopy(config)


def update_state(**kwargs):
    with state_lock:
        system_state.update(kwargs)


def refresh_derived_history():
    with state_lock:
        system_state["history"]["pressure"] = list(pressure_history)
        system_state["history"]["events"] = list(event_history)


def init_hardware():
    global led_device, pressure_reader, pulse_reader, servo_device

    cfg = safe_config()
    if cfg["simulate_hardware"]:
        pressure_reader = SimulatedPressureReader()
        pulse_reader = SimulatedPulseReader()
        add_event("hardware", "Simulation mode enabled; hardware adapters are bypassed.")
        return

    try:
        from gpiozero import AngularServo, Device, LED
        from gpiozero.pins.pigpio import PiGPIOFactory

        Device.pin_factory = PiGPIOFactory()
        led_device = LED(GPIO_PINS["led"])
        servo_device = AngularServo(
            GPIO_PINS["servo"],
            min_angle=0,
            max_angle=180,
            min_pulse_width=0.0005,
            max_pulse_width=0.0025,
        )
        servo_device.angle = cfg["servo_rest_angle"]
        pressure_reader = HardwarePressureReader()
        pulse_reader = HardwarePulseReader()

        with state_lock:
            system_state["hardware"].update(
                {
                    "simulation": False,
                    "pressure_sensor": "BMP280/AHT20 on I2C",
                    "pulse_oximeter": "MAX30102 adapter placeholder",
                    "servo": "GPIO18 AngularServo",
                    "led": "GPIO27 LED",
                }
            )
        add_event("hardware", "Hardware mode initialized.")
    except Exception as exc:
        pressure_reader = SimulatedPressureReader()
        pulse_reader = SimulatedPulseReader()
        with state_lock:
            system_state["hardware"].update(
                {
                    "simulation": True,
                    "pressure_sensor": "simulated fallback",
                    "pulse_oximeter": "simulated fallback",
                    "servo": "simulated fallback",
                    "led": "simulated fallback",
                }
            )
        with config_lock:
            config["simulate_hardware"] = True
        add_event("hardware_error", f"Hardware init failed; using simulation: {exc}", "warning")


def current_effective_threshold(cfg):
    with state_lock:
        sigma = system_state["noise_sigma_pa"]
    adaptive = -abs(cfg["noise_sigma_multiplier"] * sigma)
    return min(float(cfg["min_inhale_delta_pa"]), adaptive)


def reset_calibration():
    calibration_samples.clear()
    pressure_window.clear()
    baseline_window.clear()
    with state_lock:
        system_state["baseline_pressure_pa"] = None
        system_state["noise_sigma_pa"] = 0.0
        system_state["breath_state"] = "calibrating"
        system_state["alarms"] = []
    add_event("calibration", "Calibration restarted; keep the sampling tube still.")


def calculate_resp_rate():
    if len(breath_times) < 2:
        return None
    intervals = [
        later - earlier for earlier, later in zip(list(breath_times), list(breath_times)[1:])
    ]
    avg_interval = sum(intervals) / len(intervals)
    if avg_interval <= 0:
        return None
    return round(60.0 / avg_interval, 1)


def execute_pump(reason="manual"):
    cfg = safe_config()
    now = time.time()

    with pump_lock:
        with state_lock:
            last_pump = system_state["last_pump_time"]
            servo_state = system_state["servo_state"]
            alarms = list(system_state["alarms"])

        if servo_state == "pumping":
            return False, "pump already running"
        if now - last_pump < cfg["pump_cooldown_seconds"]:
            return False, "pump cooldown active"
        if any(alarm in alarms for alarm in ["sensor_error", "servo_error", "power_error"]):
            return False, "safety alarm blocks automatic pumping"

        update_state(servo_state="pumping", last_pump_time=now)
        add_event("pump", f"Pump action started ({reason}).", "warning")

        try:
            if servo_device is not None:
                servo_device.angle = cfg["servo_press_angle"]
            time.sleep(cfg["pump_hold_seconds"])
            if servo_device is not None:
                servo_device.angle = cfg["servo_rest_angle"]
            update_state(servo_state="idle")
            add_event("pump", "Pump action completed.")
            return True, "pump completed"
        except Exception as exc:
            with state_lock:
                alarms = set(system_state["alarms"])
                alarms.add("servo_error")
                system_state["alarms"] = sorted(alarms)
                system_state["servo_state"] = "error"
            set_led(True)
            add_event("servo_error", f"Servo action failed: {exc}", "error")
            return False, str(exc)


def maybe_trigger_breath(delta_pa, now, cfg):
    threshold = current_effective_threshold(cfg)
    with state_lock:
        last_breath = system_state["last_breath_time"]
        breath_state = system_state["breath_state"]

    cooldown_ok = last_breath is None or now - last_breath >= cfg["cooldown_seconds"]
    inhaling = delta_pa <= threshold and cooldown_ok
    recovered = delta_pa > threshold * 0.45

    if inhaling:
        breath_times.append(now)
        with state_lock:
            system_state["last_breath_time"] = now
            system_state["last_breath_age_s"] = 0.0
            system_state["resp_rate_est"] = calculate_resp_rate()
            system_state["breath_state"] = "inhale_detected"
        add_event("breath", f"Inhale detected at {delta_pa:.1f} Pa.")
        threading.Thread(target=execute_pump, args=("breath_sync",), daemon=True).start()
    elif breath_state == "inhale_detected" and recovered:
        update_state(breath_state="monitoring")

    with state_lock:
        system_state["effective_inhale_threshold_pa"] = round(threshold, 2)


def update_alarms(now, cfg):
    alarms = set()
    with state_lock:
        last_breath = system_state["last_breath_time"]
        spo2 = system_state["spo2"]

    if last_breath is not None and now - last_breath > cfg["watchdog_seconds"]:
        alarms.add("apnea_watchdog")
    if spo2 is not None and spo2 < cfg["spo2_alarm_threshold"]:
        alarms.add("low_spo2")

    with state_lock:
        existing_blocking = {
            alarm
            for alarm in system_state["alarms"]
            if alarm in {"sensor_error", "servo_error", "power_error"}
        }
        system_state["alarms"] = sorted(alarms | existing_blocking)
        active_alarms = list(system_state["alarms"])

    set_led(bool(active_alarms))

    if "apnea_watchdog" in alarms:
        add_event("watchdog", "No valid breath detected; bench pump demonstration requested.", "warning")
        threading.Thread(target=execute_pump, args=("watchdog",), daemon=True).start()
        with state_lock:
            system_state["last_breath_time"] = now


def monitor_loop():
    init_hardware()
    add_event("system", "Monitor thread started.")
    next_pulse_read = 0.0

    while not stop_event.is_set():
        cfg = safe_config()
        start = time.time()
        now = start

        try:
            raw_pressure = pressure_reader.read_pa()
            pressure_window.append(raw_pressure)
            filtered_pressure = sum(pressure_window) / len(pressure_window)
            baseline_window.append(filtered_pressure)

            if len(calibration_samples) < int(cfg["calibration_seconds"] * cfg["sample_hz"]):
                calibration_samples.append(filtered_pressure)
                baseline = sum(calibration_samples) / len(calibration_samples)
                sigma = statistics.pstdev(calibration_samples) if len(calibration_samples) > 1 else 0.0
                breath_state = "calibrating"
            else:
                if len(calibration_samples) == int(cfg["calibration_seconds"] * cfg["sample_hz"]):
                    add_event("calibration", "Calibration completed.")
                    calibration_samples.append(filtered_pressure)
                baseline = sum(baseline_window) / len(baseline_window)
                sigma = statistics.pstdev(list(baseline_window)) if len(baseline_window) > 1 else 0.0
                breath_state = "monitoring"

            delta = filtered_pressure - baseline
            pressure_history.append(
                {
                    "ts": round(now, 3),
                    "pressure_pa": round(filtered_pressure, 2),
                    "baseline_pressure_pa": round(baseline, 2),
                    "delta_pressure_pa": round(delta, 2),
                }
            )

            with state_lock:
                last_breath = system_state["last_breath_time"]
                system_state.update(
                    {
                        "pressure_pa": round(filtered_pressure, 2),
                        "baseline_pressure_pa": round(baseline, 2),
                        "delta_pressure_pa": round(delta, 2),
                        "noise_sigma_pa": round(sigma, 2),
                        "mode": cfg["mode"],
                        "last_breath_age_s": round(now - last_breath, 1)
                        if last_breath is not None
                        else None,
                    }
                )
                if system_state["breath_state"] == "calibrating":
                    system_state["breath_state"] = breath_state

            if breath_state == "monitoring":
                maybe_trigger_breath(delta, now, cfg)
                update_alarms(now, cfg)

            if now >= next_pulse_read:
                try:
                    spo2, heart_rate = pulse_reader.read()
                    update_state(spo2=spo2, heart_rate=heart_rate)
                except Exception as exc:
                    with state_lock:
                        alarms = set(system_state["alarms"])
                        alarms.add("sensor_error")
                        system_state["alarms"] = sorted(alarms)
                    add_event("sensor_error", f"Pulse reader failed: {exc}", "warning")
                next_pulse_read = now + 1.0 / max(1, cfg["spo2_sample_hz"])

            refresh_derived_history()
        except Exception as exc:
            with state_lock:
                alarms = set(system_state["alarms"])
                alarms.add("sensor_error")
                system_state["alarms"] = sorted(alarms)
                system_state["breath_state"] = "sensor_error"
            set_led(True)
            add_event("sensor_error", f"Pressure monitor failed: {exc}", "error")
            time.sleep(1.0)

        elapsed = time.time() - start
        time.sleep(max(0.0, 1.0 / cfg["sample_hz"] - elapsed))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data")
def data():
    with state_lock:
        snapshot = deepcopy(system_state)
    return jsonify(snapshot)


@app.route("/config", methods=["GET", "POST"])
def config_route():
    if request.method == "GET":
        return jsonify(safe_config())

    updates = request.get_json(silent=True) or {}
    allowed = {
        "min_inhale_delta_pa": float,
        "noise_sigma_multiplier": float,
        "cooldown_seconds": float,
        "watchdog_seconds": float,
        "servo_rest_angle": int,
        "servo_press_angle": int,
        "pump_hold_seconds": float,
        "pump_cooldown_seconds": float,
        "spo2_alarm_threshold": int,
    }

    with config_lock:
        for key, caster in allowed.items():
            if key in updates:
                config[key] = caster(updates[key])
        new_config = deepcopy(config)

    add_event("config", "Configuration updated from dashboard.")
    return jsonify(new_config)


@app.route("/calibrate", methods=["POST"])
def calibrate():
    reset_calibration()
    return jsonify({"ok": True, "message": "calibration restarted"})


@app.route("/pump_test", methods=["POST"])
def pump_test():
    cfg = safe_config()
    with state_lock:
        count = system_state["pump_test_count"]
        mode = system_state["mode"]

    if mode not in {"bench", "debug"}:
        return jsonify({"ok": False, "message": "pump test only allowed in bench/debug mode"}), 403
    if count >= cfg["pump_test_limit"]:
        return jsonify({"ok": False, "message": "pump test limit reached"}), 429

    ok, message = execute_pump("manual_test")
    if ok:
        with state_lock:
            system_state["pump_test_count"] += 1
    return jsonify({"ok": ok, "message": message})


@app.route("/logs")
def logs():
    return jsonify(list(event_history))


def start_background_threads():
    global monitor_thread

    with monitor_thread_lock:
        if monitor_thread is None or not monitor_thread.is_alive():
            monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
            monitor_thread.start()
        return monitor_thread


if __name__ == "__main__":
    start_background_threads()
    app.run(host="0.0.0.0", port=5000, debug=False)
