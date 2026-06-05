import argparse
import atexit
import signal
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path

# Ensure the src parent directory is on sys.path so that `import src.*` works
# when running as `python src/app.py`.
_SRC_DIR = Path(__file__).resolve().parent
_PROJECT_DIR = _SRC_DIR.parent
if str(_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIR))

from flask import Flask, Response, jsonify, render_template, request

from src.config import GPIO_PINS, clamp_config_values
from src.state import (
    add_event,
    config,
    config_lock,
    event_history,
    load_persistent_events,
    monitor_thread_lock,
    safe_config,
    state_lock,
    stop_event,
    system_state,
    update_state,
)
from src.actuators.gpio import execute_pump, release_servo, set_led
from src.detectors.breath import reset_calibration
from src.loops.night import monitor_loop
from src.loops.day import day_monitor_loop

import src.state as _st

app = Flask(
    "src",
    root_path=str(_SRC_DIR),
    template_folder=str(_SRC_DIR / "templates"),
    instance_path=str(_PROJECT_DIR / "instance"),
)


def release_outputs():
    release_servo()
    set_led(False)


def handle_shutdown(signum, frame):
    stop_event.set()
    release_outputs()
    raise SystemExit(0)


atexit.register(release_outputs)
if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)


def start_background_threads():
    cfg = safe_config()
    mode = cfg.get("system_mode", "night")
    update_state(system_mode=mode)

    with monitor_thread_lock:
        if _st.monitor_thread is None or not _st.monitor_thread.is_alive():
            stop_event.clear()
            target = monitor_loop if mode == "night" else day_monitor_loop
            _st.monitor_thread = threading.Thread(target=target, daemon=True)
            _st.monitor_thread.start()
        return _st.monitor_thread


def switch_mode(new_mode):
    """Switch between 'night' and 'day' mode. Stops current thread and starts new one."""
    if new_mode not in ("night", "day"):
        return False, f"Invalid mode: {new_mode}"

    with config_lock:
        config["system_mode"] = new_mode

    # Stop current thread
    stop_event.set()
    with monitor_thread_lock:
        if _st.monitor_thread is not None and _st.monitor_thread.is_alive():
            _st.monitor_thread.join(timeout=5.0)
        _st.monitor_thread = None

    # Release GPIO pins before re-init in new mode
    if _st.led_device is not None:
        try:
            _st.led_device.close()
        except Exception:
            pass
        _st.led_device = None
    if _st.servo_device is not None:
        try:
            release_servo()
            _st.servo_device.close()
        except Exception:
            pass
        _st.servo_device = None
    time.sleep(0.3)

    update_state(system_mode=new_mode)
    add_event("mode", f"System mode switched to: {new_mode}")

    # Start new thread
    stop_event.clear()
    with monitor_thread_lock:
        target = monitor_loop if new_mode == "night" else day_monitor_loop
        _st.monitor_thread = threading.Thread(target=target, daemon=True)
        _st.monitor_thread.start()

    return True, f"Switched to {new_mode} mode"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/data")
def data():
    with state_lock:
        snapshot = deepcopy(system_state)
    return jsonify(snapshot)


@app.route("/camera_feed")
def camera_feed():
    def generate():
        while True:
            detector = _st.fall_detector_instance
            frame = detector.get_jpeg_frame() if detector is not None else None
            if frame is not None:
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            time.sleep(0.1)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/config", methods=["GET", "POST"])
def config_route():
    if request.method == "GET":
        return jsonify(safe_config())

    updates = request.get_json(silent=True) or {}
    allowed = {
        "min_breath_activity_pa": float,
        "humidity_activity_threshold": float,
        "temperature_activity_threshold": float,
        "assist_mode": str,
        "breath_window_seconds": float,
        "noise_sigma_multiplier": float,
        "breath_amplitude_factor": float,
        "cooldown_seconds": float,
        "apnea_min_seconds": float,
        "apnea_max_seconds": float,
        "adaptive_apnea_factor": float,
        "assist_interval_seconds": float,
        "pump_artifact_ignore_seconds": float,
        "post_pump_recovery_seconds": float,
        "post_pump_threshold_factor": float,
        "servo_rest_angle": int,
        "servo_press_angle": int,
        "servo_rest_value": float,
        "servo_press_value": float,
        "servo_rest_seconds": float,
        "pump_hold_seconds": float,
        "pump_cooldown_seconds": float,
        "spo2_alarm_threshold": float,
        "spo2_recovery_hold_seconds": float,
        "spo2_pump_max_count": int,
        "fall_angle_threshold": float,
        "fall_detect_frames": int,
    }

    with config_lock:
        for key, caster in allowed.items():
            if key in updates:
                config[key] = caster(updates[key])
        clamp_config_values(config)
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
    persisted = load_persistent_events()
    memory = list(event_history)
    if not persisted:
        return jsonify(memory)

    merged = persisted + [
        event
        for event in memory
        if not persisted or event.get("ts") > persisted[-1].get("ts", 0)
    ]
    return jsonify(merged[-200:])


@app.route("/mode", methods=["GET", "POST"])
def mode_route():
    if request.method == "GET":
        with state_lock:
            return jsonify({"system_mode": system_state["system_mode"]})
    body = request.get_json(silent=True) or {}
    new_mode = body.get("system_mode", "").strip().lower()
    ok, msg = switch_mode(new_mode)
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": msg, "system_mode": new_mode}), status


def parse_args():
    parser = argparse.ArgumentParser(description="InspiraLink hardware runner")
    parser.add_argument(
        "--test",
        choices=["pressure", "environment", "spo2", "fall", "led", "servo", "all", "routes", "apnea"],
        help="run one hardware/module test and exit",
    )
    parser.add_argument("--mode", choices=["night", "day"], default="night",
                        help="startup mode: night (breathing+spo2) or day (fall detection)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.test:
        from src.tests import (
            test_pressure_sensor,
            test_environment_sensor,
            test_spo2,
            test_fall,
            test_led,
            test_servo,
            test_routes,
            test_apnea_bounds,
        )

    if args.test == "pressure":
        test_pressure_sensor()
    elif args.test == "environment":
        test_environment_sensor()
    elif args.test == "spo2":
        test_spo2()
    elif args.test == "fall":
        test_fall()
    elif args.test == "led":
        test_led()
    elif args.test == "servo":
        test_servo()
    elif args.test == "all":
        test_pressure_sensor()
        test_environment_sensor()
        test_led()
        test_servo()
    elif args.test == "routes":
        test_routes()
    elif args.test == "apnea":
        test_apnea_bounds()
    else:
        with config_lock:
            config["system_mode"] = args.mode
        start_background_threads()
        app.run(host=args.host, port=args.port, debug=False)
