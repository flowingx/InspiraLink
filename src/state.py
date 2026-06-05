import json
import threading
import time
from collections import deque
from copy import deepcopy
from pathlib import Path

from .config import DEFAULT_CONFIG, GPIO_PINS

_SRC_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = _SRC_DIR / "runtime_logs"
EVENT_LOG_FILE = RUNTIME_DIR / "events.jsonl"
SENSOR_LOG_FILE = RUNTIME_DIR / "sensor_samples.jsonl"

state_lock = threading.Lock()
config_lock = threading.Lock()
pump_lock = threading.Lock()
monitor_thread_lock = threading.Lock()

config = deepcopy(DEFAULT_CONFIG)

system_state = {
    "system_mode": DEFAULT_CONFIG["system_mode"],
    "pressure_pa": None,
    "baseline_pressure_pa": None,
    "delta_pressure_pa": None,
    "noise_sigma_pa": 0.0,
    "calibration_noise_sigma_pa": 0.0,
    "effective_breath_threshold_pa": DEFAULT_CONFIG["min_breath_activity_pa"],
    "effective_inhale_threshold_pa": -DEFAULT_CONFIG["min_breath_activity_pa"],
    "breath_activity_amplitude_pa": 0.0,
    "humidity": None,
    "temperature_c": None,
    "humidity_activity": None,
    "temperature_activity": None,
    "environment_sensor_status": "not initialized",
    "adaptive_apnea_seconds": DEFAULT_CONFIG["apnea_min_seconds"],
    "apnea_hard_limit_seconds": 15.0,
    "breath_state": "starting",
    "last_breath_time": None,
    "last_breath_age_s": None,
    "resp_rate_est": None,
    "apnea_active": False,
    "activity_latched": False,
    "last_assist_time": None,
    "last_assist_request_time": None,
    "ignore_pressure_until": 0.0,
    "recovery_until": 0.0,
    "spo2": None,
    "heart_rate": None,
    "spo2_status": "not initialized",
    "spo2_trend": "unknown",
    "spo2_recovery_since": None,
    "spo2_recovered_stable": False,
    "apnea_pump_count": 0,
    "fall_detected": False,
    "fall_count": 0,
    "fall_status": "not initialized",
    "fall_angle": None,
    "servo_state": "idle",
    "mode": DEFAULT_CONFIG["mode"],
    "assist_mode": DEFAULT_CONFIG["assist_mode"],
    "hardware": {
        "simulation": False,
        "pressure_sensor": "not initialized",
        "environment_sensor": "not initialized",
        "pulse_oximeter": "not initialized",
        "fall_detector": "not initialized",
        "servo": "not initialized",
        "led": "not initialized",
        "gpio_pins": GPIO_PINS,
    },
    "alarms": [],
    "history": {"pressure": [], "events": []},
    "pump_test_count": 0,
    "started_at": time.time(),
    "last_pump_time": 0.0,
}

pressure_window = deque(maxlen=5)
baseline_window = deque(maxlen=200)
calibration_samples = []
activity_window = deque()
humidity_window = deque()
temperature_window = deque()
breath_times = deque(maxlen=8)
breath_amplitudes = deque(maxlen=12)
event_history = deque(maxlen=100)
pressure_history = deque(maxlen=400)
stop_event = threading.Event()
monitor_thread = None
last_sensor_log_time = 0.0

# Hardware device references (set during init)
servo_device = None
led_device = None
pressure_reader = None
environment_reader = None
spo2_monitor = None
fall_detector_instance = None


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


def add_event(kind, message, level="info"):
    event = {
        "ts": round(time.time(), 3),
        "kind": kind,
        "level": level,
        "message": message,
    }
    event_history.append(event)
    try:
        RUNTIME_DIR.mkdir(exist_ok=True)
        with EVENT_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_persistent_events(limit=200):
    if not EVENT_LOG_FILE.exists():
        return []
    try:
        lines = EVENT_LOG_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events
    except Exception:
        return []


def append_sensor_sample(sample, interval_s=0.5):
    global last_sensor_log_time
    now = sample["ts"]
    if now - last_sensor_log_time < interval_s:
        return
    last_sensor_log_time = now
    try:
        RUNTIME_DIR.mkdir(exist_ok=True)
        with SENSOR_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
    except Exception:
        pass


def refresh_derived_history():
    with state_lock:
        system_state["history"]["pressure"] = list(pressure_history)
        system_state["history"]["events"] = list(event_history)
