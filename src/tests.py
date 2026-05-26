import json
import statistics
import time
from copy import deepcopy

from .config import APNEA_HARD_LIMIT_SECONDS, DEFAULT_CONFIG, clamp_config_values
from .state import safe_config, breath_times
from .sensors import HardwarePressureReader, OptionalAHT20Reader, OptionalMAX30102Reader
from .actuators.gpio import init_gpio
from .detectors.fall import FallDetector
from .detectors.breath import current_adaptive_apnea_seconds


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


def test_environment_sensor(samples=20, delay=0.5):
    reader = OptionalAHT20Reader()
    humidities = []
    temperatures = []
    for _ in range(samples):
        humidity, temperature_c = reader.read()
        humidities.append(humidity)
        temperatures.append(temperature_c)
        print(f"humidity={humidity:.2f}% temperature_c={temperature_c:.2f}")
        time.sleep(delay)
    print(
        json.dumps(
            {
                "samples": len(humidities),
                "humidity_min": round(min(humidities), 2),
                "humidity_max": round(max(humidities), 2),
                "humidity_delta": round(max(humidities) - min(humidities), 2),
                "temperature_min": round(min(temperatures), 2),
                "temperature_max": round(max(temperatures), 2),
                "temperature_delta": round(max(temperatures) - min(temperatures), 3),
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
    from .app import app
    client = app.test_client()
    print("GET /config", client.get("/config").status_code)
    print("GET /data", client.get("/data").status_code)
    print("GET /logs", client.get("/logs").status_code)
    print("GET /mode", client.get("/mode").status_code)


def test_spo2(duration=15):
    """Test MAX30102 sensor for `duration` seconds, print BPM and SpO2."""
    print("Initializing MAX30102...")
    try:
        reader = OptionalMAX30102Reader()
    except Exception as exc:
        print(f"MAX30102 init failed: {exc}")
        return

    print(f"Reading for {duration} seconds (keep finger on sensor)...")
    start = time.time()
    while time.time() - start < duration:
        bpm = reader.bpm
        spo2 = reader.spo2
        print(f"  BPM={bpm:.1f}  SpO2={spo2:.1f}" if spo2 else f"  BPM={bpm:.1f}  SpO2=--")
        time.sleep(1.0)

    reader.stop()
    print("MAX30102 test complete.")


def test_fall(duration=15):
    """Test fall detection for `duration` seconds."""
    print("Initializing fall detector...")
    cfg = safe_config()
    try:
        detector = FallDetector(cfg)
        detector.start()
    except Exception as exc:
        print(f"Fall detector init failed: {exc}")
        return

    print(f"Detecting for {duration} seconds...")
    start = time.time()
    while time.time() - start < duration:
        angle = detector.last_angle
        is_fall = detector.fall_detected
        angle_str = f"{angle}" if angle is not None else "--"
        status = "FALL!" if is_fall else "OK"
        print(f"  angle={angle_str}  status={status}  fall_count={detector.fall_count}")
        time.sleep(1.0)

    detector.stop()
    print("Fall detection test complete.")


def test_apnea_bounds():
    test_cfg = deepcopy(DEFAULT_CONFIG)
    test_cfg["apnea_max_seconds"] = 25.0
    test_cfg["adaptive_apnea_factor"] = 2.5
    clamp_config_values(test_cfg)

    breath_times.clear()
    now = time.time()
    breath_times.extend([now - 50.0, now - 25.0, now])

    print(
        json.dumps(
            {
                "apnea_hard_limit_seconds": APNEA_HARD_LIMIT_SECONDS,
                "clamped_apnea_min_seconds": test_cfg["apnea_min_seconds"],
                "clamped_apnea_max_seconds": test_cfg["apnea_max_seconds"],
                "clamped_adaptive_apnea_factor": test_cfg["adaptive_apnea_factor"],
                "computed_adaptive_apnea_seconds": current_adaptive_apnea_seconds(test_cfg),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

