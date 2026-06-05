import statistics
import time

import src.state as _st
from ..config import GPIO_PINS
from ..state import (
    add_alarm,
    add_event,
    append_sensor_sample,
    baseline_window,
    calibration_samples,
    clear_alarm,
    humidity_window,
    pressure_history,
    pressure_window,
    refresh_derived_history,
    safe_config,
    state_lock,
    stop_event,
    system_state,
    temperature_window,
    update_state,
)
from ..sensors import HardwarePressureReader, OptionalAHT20Reader, OptionalMAX30102Reader
from ..actuators.gpio import init_gpio, release_servo, set_led
from ..detectors.breath import (
    maybe_detect_breath_activity,
    update_apnea_control,
    update_spo2_state,
)


def init_hardware():
    cfg = safe_config()

    # --- GPIO (LED + Servo) ---
    gpio_status_led = "not initialized"
    gpio_status_servo = "not initialized"
    try:
        _st.led_device, _st.servo_device = init_gpio()
        release_servo()
        gpio_status_led = f"GPIO{GPIO_PINS['led']} LED"
        gpio_status_servo = f"GPIO{GPIO_PINS['servo']} AngularServo"
        add_event("hardware", "GPIO initialized (LED + Servo released).")
    except Exception as exc:
        _st.led_device = None
        _st.servo_device = None
        gpio_status_led = f"init failed: {exc}"
        gpio_status_servo = f"init failed: {exc}"
        add_event("hardware", f"GPIO init failed: {exc}", "warning")

    # --- BMP280 pressure sensor (optional) ---
    pressure_status = "BMP280 not available"
    try:
        _st.pressure_reader = HardwarePressureReader()
        pressure_status = (
            f"BMP280 on I2C bus 1, address 0x{_st.pressure_reader.address:02x}, "
            f"driver {_st.pressure_reader.driver}"
        )
        add_event("hardware", "BMP280 pressure sensor initialized.")
    except Exception as exc:
        _st.pressure_reader = None
        pressure_status = f"BMP280 unavailable: {exc}"
        add_event("hardware", f"BMP280 unavailable: {exc}", "warning")

    # --- AHT20 environment sensor (optional) ---
    environment_status = "AHT20 not available"
    try:
        _st.environment_reader = OptionalAHT20Reader()
        environment_status = "AHT20 on I2C"
        add_event("hardware", "AHT20 environment sensor initialized.")
    except Exception as exc:
        _st.environment_reader = None
        add_event("hardware", f"AHT20 unavailable: {exc}", "warning")

    # --- MAX30102 pulse oximeter (optional) ---
    spo2_status = "MAX30102 not available"
    try:
        _st.spo2_monitor = OptionalMAX30102Reader()
        spo2_status = "MAX30102 on I2C (0x57)"
        add_event("hardware", "MAX30102 pulse oximeter initialized.")
    except Exception as exc:
        _st.spo2_monitor = None
        spo2_status = f"MAX30102 unavailable: {exc}"
        add_event("hardware", f"MAX30102 unavailable: {exc}", "warning")

    # --- Determine if we have enough sensors to run ---
    has_any_sensor = (
        _st.pressure_reader is not None
        or _st.environment_reader is not None
        or _st.spo2_monitor is not None
    )
    has_actuator = _st.servo_device is not None

    if not has_any_sensor and not has_actuator:
        add_alarm("hardware_init_error")
        update_state(
            breath_state="hardware_error",
            hardware={
                "simulation": False,
                "pressure_sensor": pressure_status,
                "environment_sensor": environment_status,
                "pulse_oximeter": spo2_status,
                "fall_detector": "not active in night mode",
                "servo": gpio_status_servo,
                "led": gpio_status_led,
                "gpio_pins": GPIO_PINS,
            },
        )
        add_event("hardware_error", "No sensors or actuators available.", "error")
        return False

    initial_breath_state = "calibrating" if _st.pressure_reader is not None else "monitoring"

    update_state(
        hardware={
            "simulation": False,
            "pressure_sensor": pressure_status,
            "environment_sensor": environment_status,
            "pulse_oximeter": spo2_status,
            "fall_detector": "not active in night mode",
            "servo": gpio_status_servo,
            "led": gpio_status_led,
            "gpio_pins": GPIO_PINS,
        },
        breath_state=initial_breath_state,
        environment_sensor_status=environment_status,
        spo2_status=spo2_status,
    )
    clear_alarm("hardware_init_error")
    add_event("hardware", "Night mode hardware initialized.")
    return True


def monitor_loop():
    if not init_hardware():
        refresh_derived_history()
        return

    add_event("system", "Night mode monitor started.")

    # If no pressure sensor, set last_breath_time so apnea timer doesn't fire immediately
    if _st.pressure_reader is None:
        with state_lock:
            system_state["last_breath_time"] = time.time()
            system_state["breath_state"] = "monitoring"

    while not stop_event.is_set():
        cfg = safe_config()
        start = time.time()
        now = start

        try:
            # --- Pressure sensing (optional) ---
            if _st.pressure_reader is not None:
                raw_pressure = _st.pressure_reader.read_pa()
                pressure_window.append(raw_pressure)
                filtered_pressure = sum(pressure_window) / len(pressure_window)
                clear_alarm("pressure_sensor_error")
            else:
                filtered_pressure = None

            # --- Environment sensing (optional) ---
            humidity = None
            temperature_c = None
            if _st.environment_reader is not None:
                try:
                    humidity, temperature_c = _st.environment_reader.read()
                    humidity_window.append((now, humidity))
                    temperature_window.append((now, temperature_c))
                    update_state(
                        humidity=round(humidity, 2),
                        temperature_c=round(temperature_c, 2),
                        environment_sensor_status="AHT20 active",
                    )
                except Exception as exc:
                    update_state(environment_sensor_status=f"AHT20 read failed: {exc}")
                    add_event("environment_error", f"AHT20 read failed: {exc}", "warning")

            # --- Pressure calibration and breath detection ---
            delta = 0.0
            baseline = 0.0
            sigma = 0.0
            breath_state = "monitoring"

            if filtered_pressure is not None:
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
                        "pressure_pa": round(filtered_pressure, 2) if filtered_pressure is not None else None,
                        "baseline_pressure_pa": round(baseline, 2) if filtered_pressure is not None else None,
                        "delta_pressure_pa": round(delta, 2) if filtered_pressure is not None else None,
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

            # Poll SpO2 / heart rate from MAX30102
            update_spo2_state(cfg)

            append_sensor_sample(
                {
                    "ts": round(now, 3),
                    "pressure_pa": round(filtered_pressure, 2) if filtered_pressure is not None else None,
                    "baseline_pressure_pa": round(baseline, 2) if filtered_pressure is not None else None,
                    "delta_pressure_pa": round(delta, 2) if filtered_pressure is not None else None,
                    "humidity": round(humidity, 2) if humidity is not None else None,
                    "temperature_c": round(temperature_c, 2)
                    if temperature_c is not None
                    else None,
                    "spo2": system_state.get("spo2"),
                    "heart_rate": system_state.get("heart_rate"),
                    "breath_state": system_state["breath_state"],
                    "alarms": list(system_state["alarms"]),
                }
            )

            if breath_state == "monitoring" and filtered_pressure is not None:
                maybe_detect_breath_activity(delta, now, cfg)
                update_apnea_control(now, cfg)

            refresh_derived_history()
        except Exception as exc:
            if _st.pressure_reader is not None:
                add_alarm("pressure_sensor_error")
                update_state(breath_state="pressure_sensor_error")
                set_led(True)
                add_event("pressure_sensor_error", f"Sensor read failed: {exc}", "error")
            else:
                add_event("sensor_error", f"Sensor read failed: {exc}", "error")
            refresh_derived_history()
            time.sleep(1.0)

        elapsed = time.time() - start
        time.sleep(max(0.0, 1.0 / cfg["sample_hz"] - elapsed))

    # Cleanup MAX30102 when night loop stops
    if _st.spo2_monitor is not None:
        _st.spo2_monitor.stop()
