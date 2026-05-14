import argparse
import json
import statistics
import threading
import time
from collections import deque
from copy import deepcopy

from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


DEFAULT_CONFIG = {
    "mode": "pressure_servo_led",
    "assist_mode": "apnea_only",
    "sample_hz": 20,
    "calibration_seconds": 10,
    "min_breath_activity_pa": 3.0,
    "noise_sigma_multiplier": 3.0,
    "cooldown_seconds": 1.5,
    "apnea_detect_seconds": 5.0,
    "assist_interval_seconds": 3.0,
    "pump_artifact_ignore_seconds": 2.0,
    "servo_rest_angle": 30,
    "servo_press_angle": 105,
    "pump_hold_seconds": 0.55,
    "pump_cooldown_seconds": 3.0,
    "pump_test_limit": 20,
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
monitor_thread_lock = threading.Lock()

config = deepcopy(DEFAULT_CONFIG)

system_state = {
    "pressure_pa": None,
    "baseline_pressure_pa": None,
    "delta_pressure_pa": None,
    "noise_sigma_pa": 0.0,
    "calibration_noise_sigma_pa": 0.0,
    "effective_breath_threshold_pa": DEFAULT_CONFIG["min_breath_activity_pa"],
    "effective_inhale_threshold_pa": -DEFAULT_CONFIG["min_breath_activity_pa"],
    "breath_state": "starting",
    "last_breath_time": None,
    "last_breath_age_s": None,
    "resp_rate_est": None,
    "apnea_active": False,
    "last_assist_time": None,
    "ignore_pressure_until": 0.0,
    # Phase 1 only tests BMP280 pressure + servo + LED.
    # MAX30102/SpO2 is intentionally disabled until the sensor is available.
    "spo2": None,
    "heart_rate": None,
    "spo2_status": "disabled_not_connected",
    "servo_state": "idle",
    "mode": DEFAULT_CONFIG["mode"],
    "assist_mode": DEFAULT_CONFIG["assist_mode"],
    "hardware": {
        "simulation": False,
        "pressure_sensor": "not initialized",
        "pulse_oximeter": "disabled: MAX30102 not connected in Phase 1",
        "servo": "not initialized",
        "led": "not initialized",
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
event_history = deque(maxlen=100)
pressure_history = deque(maxlen=400)
stop_event = threading.Event()
monitor_thread = None

servo_device = None
led_device = None
pressure_reader = None


class HardwarePressureReader:
    def __init__(self, address=None):
        self.driver = None
        self.sensor = None
        self.address = None
        self.bus = None

        errors = []
        for candidate in self._candidate_addresses(address):
            try:
                self._init_pimoroni_bmp280(candidate)
                return
            except Exception as exc:
                errors.append(f"bmp280@0x{candidate:02x}: {exc}")

        for candidate in self._candidate_addresses(address):
            try:
                self._init_bme280_compat(candidate)
                return
            except Exception as exc:
                errors.append(f"bme280@0x{candidate:02x}: {exc}")

        raise RuntimeError("BMP280 init failed; " + " | ".join(errors))

    def _candidate_addresses(self, address):
        if address is not None:
            return [address]
        return [0x76, 0x77]

    def _init_pimoroni_bmp280(self, address):
        from bmp280 import BMP280
        from smbus2 import SMBus

        bus = SMBus(1)
        try:
            sensor = BMP280(i2c_addr=address, i2c_dev=bus)
            self._warmup_pimoroni_sensor(sensor)
        except TypeError:
            sensor = BMP280(i2c_dev=bus)
            self._warmup_pimoroni_sensor(sensor)

        self.driver = "bmp280"
        self.bus = bus
        self.sensor = sensor
        self.address = address

    def _init_bme280_compat(self, address):
        import bme280
        import smbus2

        bus = smbus2.SMBus(1)
        calibration = bme280.load_calibration_params(bus, address)
        self._warmup_bme280_sensor(bme280, bus, address, calibration)

        self.driver = "bme280"
        self.bus = bus
        self.sensor = (bme280, calibration)
        self.address = address

    def _warmup_pimoroni_sensor(self, sensor):
        last_pressure = None
        for _ in range(8):
            last_pressure = float(sensor.get_pressure())
            time.sleep(0.05)
        if last_pressure is None or not 800.0 <= last_pressure <= 1100.0:
            raise RuntimeError(f"implausible pressure after warmup: {last_pressure} hPa")

    def _warmup_bme280_sensor(self, bme280, bus, address, calibration):
        last_pressure = None
        for _ in range(8):
            last_pressure = float(bme280.sample(bus, address, calibration).pressure)
            time.sleep(0.05)
        if last_pressure is None or not 800.0 <= last_pressure <= 1100.0:
            raise RuntimeError(f"implausible pressure after warmup: {last_pressure} hPa")

    def read_pa(self):
        if self.driver == "bmp280":
            # Pimoroni bmp280.get_pressure() returns hPa.
            return float(self.sensor.get_pressure()) * 100.0

        if self.driver == "bme280":
            bme280, calibration = self.sensor
            data = bme280.sample(self.bus, self.address, calibration)
            return float(data.pressure) * 100.0

        raise RuntimeError("BMP280 reader is not initialized")


def add_event(kind, message, level="info"):
    event_history.append(
        {
            "ts": round(time.time(), 3),
            "kind": kind,
            "level": level,
            "message": message,
        }
    )


def safe_config():
    with config_lock:
        return deepcopy(config)


def update_state(**kwargs):
    with state_lock:
        system_state.update(kwargs)


def add_alarm(alarm):
    with state_lock:
        alarms = set(system_state["alarms"])
        alarms.add(alarm)
        system_state["alarms"] = sorted(alarms)


def clear_alarm(alarm):
    with state_lock:
        alarms = set(system_state["alarms"])
        alarms.discard(alarm)
        system_state["alarms"] = sorted(alarms)


def refresh_derived_history():
    with state_lock:
        system_state["history"]["pressure"] = list(pressure_history)
        system_state["history"]["events"] = list(event_history)


def init_gpio():
    from gpiozero import AngularServo, Device, LED
    from gpiozero.pins.pigpio import PiGPIOFactory

    Device.pin_factory = PiGPIOFactory()
    led = LED(GPIO_PINS["led"])
    servo = AngularServo(
        GPIO_PINS["servo"],
        min_angle=0,
        max_angle=180,
        min_pulse_width=0.0005,
        max_pulse_width=0.0025,
    )
    return led, servo


def init_hardware():
    global led_device, pressure_reader, servo_device

    cfg = safe_config()
    try:
        led_device, servo_device = init_gpio()
        servo_device.angle = cfg["servo_rest_angle"]
        pressure_reader = HardwarePressureReader()
    except Exception as exc:
        add_alarm("hardware_init_error")
        update_state(
            breath_state="hardware_error",
            hardware={
                "simulation": False,
                "pressure_sensor": f"init failed: {exc}",
                "pulse_oximeter": "disabled: MAX30102 not connected in Phase 1",
                "servo": f"init failed: {exc}",
                "led": f"init failed: {exc}",
                "gpio_pins": GPIO_PINS,
            },
        )
        add_event("hardware_error", f"Hardware init failed: {exc}", "error")
        return False

    update_state(
        hardware={
            "simulation": False,
            "pressure_sensor": (
                f"BMP280 on I2C bus 1, address 0x{pressure_reader.address:02x}, "
                f"driver {pressure_reader.driver}"
            ),
            "pulse_oximeter": "disabled: MAX30102 not connected in Phase 1",
            "servo": f"GPIO{GPIO_PINS['servo']} AngularServo",
            "led": f"GPIO{GPIO_PINS['led']} LED",
            "gpio_pins": GPIO_PINS,
        },
        breath_state="calibrating",
    )
    clear_alarm("hardware_init_error")
    add_event("hardware", "BMP280, servo, and LED initialized. SpO2 is disabled.")
    return True


def set_led(active):
    if led_device is None:
        return
    try:
        led_device.on() if active else led_device.off()
    except Exception as exc:
        add_alarm("led_error")
        add_event("led_error", f"LED control failed: {exc}", "warning")


def current_effective_threshold(cfg):
    with state_lock:
        sigma = system_state["calibration_noise_sigma_pa"]
    adaptive = abs(cfg["noise_sigma_multiplier"] * sigma)
    return max(float(cfg["min_breath_activity_pa"]), adaptive)


def reset_calibration():
    calibration_samples.clear()
    pressure_window.clear()
    baseline_window.clear()
    with state_lock:
        system_state["baseline_pressure_pa"] = None
        system_state["noise_sigma_pa"] = 0.0
        system_state["calibration_noise_sigma_pa"] = 0.0
        system_state["breath_state"] = "calibrating"
        system_state["last_breath_time"] = None
        system_state["last_breath_age_s"] = None
        system_state["assist_mode"] = safe_config()["assist_mode"]
        system_state["apnea_active"] = False
        system_state["last_assist_time"] = None
        system_state["ignore_pressure_until"] = 0.0
    clear_alarm("apnea_watchdog")
    add_event("calibration", "Calibration restarted; keep the pressure tube still.")


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
        if "servo_error" in alarms or "hardware_init_error" in alarms:
            return False, "servo or hardware alarm blocks pumping"

        update_state(servo_state="pumping", last_pump_time=now)
        add_event("pump", f"Pump action started ({reason}).", "warning")

        try:
            if servo_device is None:
                raise RuntimeError("servo is not initialized")
            servo_device.angle = cfg["servo_press_angle"]
            time.sleep(cfg["pump_hold_seconds"])
            servo_device.angle = cfg["servo_rest_angle"]
            update_state(
                servo_state="idle",
                last_assist_time=now,
                ignore_pressure_until=time.time() + cfg["pump_artifact_ignore_seconds"],
            )
            add_event("pump", "Pump action completed.")
            return True, "pump completed"
        except Exception as exc:
            add_alarm("servo_error")
            update_state(servo_state="error")
            set_led(True)
            add_event("servo_error", f"Servo action failed: {exc}", "error")
            return False, str(exc)


def maybe_detect_breath_activity(delta_pa, now, cfg):
    with state_lock:
        ignore_pressure_until = system_state["ignore_pressure_until"]
    if now < ignore_pressure_until:
        update_state(breath_state="pump_artifact_ignored")
        return

    threshold = current_effective_threshold(cfg)
    with state_lock:
        last_breath = system_state["last_breath_time"]
        breath_state = system_state["breath_state"]

    cooldown_ok = last_breath is None or now - last_breath >= cfg["cooldown_seconds"]
    active_breath = abs(delta_pa) >= threshold and cooldown_ok
    recovered = abs(delta_pa) < threshold * 0.45

    if active_breath:
        breath_times.append(now)
        direction = "negative" if delta_pa < 0 else "positive"
        update_state(
            last_breath_time=now,
            last_breath_age_s=0.0,
            resp_rate_est=calculate_resp_rate(),
            breath_state="breath_activity",
            apnea_active=False,
        )
        clear_alarm("apnea_watchdog")
        set_led(True)
        add_event(
            "breath",
            f"Breath activity detected ({direction}, {delta_pa:.1f} Pa); apnea alarm cleared.",
        )
        if cfg["assist_mode"] == "sync":
            threading.Thread(target=execute_pump, args=("breath_sync",), daemon=True).start()
    elif breath_state == "breath_activity" and recovered:
        set_led(False)
        update_state(breath_state="monitoring")

    update_state(
        effective_breath_threshold_pa=round(threshold, 2),
        effective_inhale_threshold_pa=round(-threshold, 2),
    )


def update_apnea_control(now, cfg):
    if cfg["apnea_detect_seconds"] <= 0:
        clear_alarm("apnea_watchdog")
        return

    with state_lock:
        last_breath = system_state["last_breath_time"]
        last_assist = system_state["last_assist_time"]
        apnea_active = system_state["apnea_active"]

    if last_breath is not None and now - last_breath > cfg["apnea_detect_seconds"]:
        add_alarm("apnea_watchdog")
        set_led(True)
        if not apnea_active:
            update_state(apnea_active=True, breath_state="apnea")
            add_event("apnea", "No valid breath detected; apnea alarm is active.", "warning")

        interval_ok = last_assist is None or now - last_assist >= cfg["assist_interval_seconds"]
        if interval_ok:
            add_event("assist", "Apnea assist pump requested.", "warning")
            threading.Thread(target=execute_pump, args=("apnea_assist",), daemon=True).start()
    else:
        clear_alarm("apnea_watchdog")
        if apnea_active:
            update_state(apnea_active=False)


def monitor_loop():
    if not init_hardware():
        refresh_derived_history()
        return

    add_event("system", "Monitor thread started for BMP280 + servo + LED.")

    while not stop_event.is_set():
        cfg = safe_config()
        start = time.time()
        now = start

        try:
            raw_pressure = pressure_reader.read_pa()
            pressure_window.append(raw_pressure)
            filtered_pressure = sum(pressure_window) / len(pressure_window)
            clear_alarm("pressure_sensor_error")

            if len(calibration_samples) < int(cfg["calibration_seconds"] * cfg["sample_hz"]):
                baseline_window.append(filtered_pressure)
                calibration_samples.append(filtered_pressure)
                baseline = sum(calibration_samples) / len(calibration_samples)
                sigma = (
                    statistics.pstdev(calibration_samples)
                    if len(calibration_samples) > 1
                    else 0.0
                )
                breath_state = "calibrating"
            else:
                if len(calibration_samples) == int(cfg["calibration_seconds"] * cfg["sample_hz"]):
                    add_event("calibration", "Calibration completed.")
                    calibration_sigma = (
                        statistics.pstdev(calibration_samples)
                        if len(calibration_samples) > 1
                        else 0.0
                    )
                    calibration_samples.append(filtered_pressure)
                    with state_lock:
                        system_state["last_breath_time"] = now
                        system_state["last_breath_age_s"] = 0.0
                        system_state["calibration_noise_sigma_pa"] = round(calibration_sigma, 2)
                with state_lock:
                    ignore_pressure_until = system_state["ignore_pressure_until"]
                if now >= ignore_pressure_until:
                    baseline_window.append(filtered_pressure)
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
                        "assist_mode": cfg["assist_mode"],
                        "last_breath_age_s": round(now - last_breath, 1)
                        if last_breath is not None
                        else None,
                    }
                )
                if system_state["breath_state"] in {"starting", "calibrating"}:
                    system_state["breath_state"] = breath_state

            if breath_state == "monitoring":
                maybe_detect_breath_activity(delta, now, cfg)
                update_apnea_control(now, cfg)

            refresh_derived_history()
        except Exception as exc:
            add_alarm("pressure_sensor_error")
            update_state(breath_state="pressure_sensor_error")
            set_led(True)
            add_event("pressure_sensor_error", f"BMP280 read failed: {exc}", "error")
            refresh_derived_history()
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
        "min_breath_activity_pa": float,
        "assist_mode": str,
        "noise_sigma_multiplier": float,
        "cooldown_seconds": float,
        "apnea_detect_seconds": float,
        "assist_interval_seconds": float,
        "pump_artifact_ignore_seconds": float,
        "servo_rest_angle": int,
        "servo_press_angle": int,
        "pump_hold_seconds": float,
        "pump_cooldown_seconds": float,
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

    if mode != "pressure_servo_led":
        return jsonify({"ok": False, "message": "pump test only allowed in Phase 1 mode"}), 403
    if count >= cfg["pump_test_limit"]:
        return jsonify({"ok": False, "message": "pump test limit reached"}), 429

    ok, message = execute_pump("manual_test")
    if ok:
        with state_lock:
            system_state["pump_test_count"] += 1
    return jsonify({"ok": ok, "message": message})


@app.route("/led_test", methods=["POST"])
def led_test():
    seconds = float((request.get_json(silent=True) or {}).get("seconds", 1.0))
    try:
        set_led(True)
        time.sleep(max(0.1, min(seconds, 5.0)))
        set_led(False)
        add_event("led", "LED test completed.")
        return jsonify({"ok": True, "message": "led test completed"})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 500


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


def test_pressure_sensor(samples=20, delay=0.1):
    reader = HardwarePressureReader()
    values = []
    for _ in range(samples):
        pressure = reader.read_pa()
        values.append(pressure)
        print(f"pressure_pa={pressure:.2f}")
        time.sleep(delay)
    print(
        json.dumps(
            {
                "samples": len(values),
                "min_pa": round(min(values), 2),
                "max_pa": round(max(values), 2),
                "mean_pa": round(sum(values) / len(values), 2),
                "sigma_pa": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def test_led(cycles=3, on_seconds=0.5):
    led, _ = init_gpio()
    try:
        for idx in range(cycles):
            print(f"LED on ({idx + 1}/{cycles})")
            led.on()
            time.sleep(on_seconds)
            print(f"LED off ({idx + 1}/{cycles})")
            led.off()
            time.sleep(on_seconds)
    finally:
        led.off()


def test_servo(rest_angle=30, press_angle=105, hold_seconds=0.6, cycles=3):
    _, servo = init_gpio()
    try:
        servo.angle = rest_angle
        time.sleep(0.5)
        for idx in range(cycles):
            print(f"servo press {press_angle} deg ({idx + 1}/{cycles})")
            servo.angle = press_angle
            time.sleep(hold_seconds)
            print(f"servo rest {rest_angle} deg ({idx + 1}/{cycles})")
            servo.angle = rest_angle
            time.sleep(1.0)
    finally:
        servo.angle = rest_angle


def test_routes():
    client = app.test_client()
    print("GET /config", client.get("/config").status_code)
    print("GET /data", client.get("/data").status_code)
    print("GET /logs", client.get("/logs").status_code)


def parse_args():
    parser = argparse.ArgumentParser(description="InspiraLink Phase 1 hardware runner")
    parser.add_argument(
        "--test",
        choices=["pressure", "led", "servo", "all", "routes"],
        help="run one hardware/module test and exit",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.test == "pressure":
        test_pressure_sensor()
    elif args.test == "led":
        test_led()
    elif args.test == "servo":
        test_servo()
    elif args.test == "all":
        test_pressure_sensor()
        test_led()
        test_servo()
    elif args.test == "routes":
        test_routes()
    else:
        start_background_threads()
        app.run(host=args.host, port=args.port, debug=False)
