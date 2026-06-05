import statistics
import threading
import time

from ..config import apnea_bounds
from ..state import (
    add_alarm,
    add_event,
    breath_amplitudes,
    breath_times,
    calibration_samples,
    clear_alarm,
    activity_window,
    baseline_window,
    humidity_window,
    pressure_window,
    safe_config,
    state_lock,
    system_state,
    temperature_window,
    update_state,
)
from ..actuators.gpio import execute_pump, set_led

import src.state as _st


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


def reset_calibration():
    calibration_samples.clear()
    pressure_window.clear()
    baseline_window.clear()
    activity_window.clear()
    humidity_window.clear()
    temperature_window.clear()
    breath_amplitudes.clear()
    with state_lock:
        system_state["baseline_pressure_pa"] = None
        system_state["noise_sigma_pa"] = 0.0
        system_state["calibration_noise_sigma_pa"] = 0.0
        system_state["breath_state"] = "calibrating"
        system_state["last_breath_time"] = None
        system_state["last_breath_age_s"] = None
        system_state["assist_mode"] = safe_config()["assist_mode"]
        system_state["apnea_active"] = False
        system_state["activity_latched"] = False
        system_state["last_assist_time"] = None
        system_state["last_assist_request_time"] = None
        system_state["ignore_pressure_until"] = 0.0
        system_state["recovery_until"] = 0.0
        system_state["spo2_recovery_since"] = None
        system_state["spo2_recovered_stable"] = False
    clear_alarm("apnea_watchdog")
    add_event("calibration", "Calibration restarted; keep the pressure tube still.")


def current_effective_threshold(cfg):
    with state_lock:
        sigma = system_state["calibration_noise_sigma_pa"]
        recovery_until = system_state["recovery_until"]
    adaptive_noise = abs(cfg["noise_sigma_multiplier"] * sigma)
    learned_amplitude = 0.0
    if breath_amplitudes:
        learned_amplitude = statistics.median(breath_amplitudes) * cfg["breath_amplitude_factor"]
    threshold = max(float(cfg["min_breath_activity_pa"]), adaptive_noise, learned_amplitude)
    if time.time() < recovery_until:
        threshold *= cfg["post_pump_threshold_factor"]
    return max(0.5, threshold)


def current_adaptive_apnea_seconds(cfg):
    apnea_min_seconds, apnea_max_seconds = apnea_bounds(cfg)
    if len(breath_times) < 3:
        return apnea_min_seconds
    intervals = [
        later - earlier for earlier, later in zip(list(breath_times), list(breath_times)[1:])
    ]
    median_interval = statistics.median(intervals)
    adaptive = median_interval * float(cfg["adaptive_apnea_factor"])
    return min(apnea_max_seconds, max(apnea_min_seconds, adaptive))


def spo2_blocks_assist(cfg):
    with state_lock:
        spo2_value = system_state["spo2"]
        spo2_recovered_stable = system_state["spo2_recovered_stable"]
    return (
        spo2_value is not None
        and (spo2_value < cfg["spo2_alarm_threshold"] or spo2_recovered_stable)
    )


def maybe_detect_breath_activity(delta_pa, now, cfg):
    with state_lock:
        ignore_pressure_until = system_state["ignore_pressure_until"]
        activity_latched = system_state["activity_latched"]
    if now < ignore_pressure_until:
        update_state(breath_state="pump_artifact_ignored")
        return

    activity_window.append((now, delta_pa))
    while activity_window and now - activity_window[0][0] > cfg["breath_window_seconds"]:
        activity_window.popleft()
    while humidity_window and now - humidity_window[0][0] > cfg["breath_window_seconds"]:
        humidity_window.popleft()
    while temperature_window and now - temperature_window[0][0] > cfg["breath_window_seconds"]:
        temperature_window.popleft()

    values = [item[1] for item in activity_window]
    amplitude = max(values) - min(values) if values else 0.0
    humidity_values = [item[1] for item in humidity_window]
    temperature_values = [item[1] for item in temperature_window]
    humidity_activity = None
    humidity_recent_rise = 0.0
    if len(humidity_window) >= 2:
        humidity_activity = max(0.0, humidity_values[-1] - min(humidity_values[:-1]))
        recent_humidity_values = [
            value for ts, value in humidity_window if now - ts <= 1.5
        ]
        if len(recent_humidity_values) >= 2:
            humidity_recent_rise = humidity_values[-1] - min(recent_humidity_values[:-1])

    temperature_activity = None
    temperature_recent_rise = 0.0
    if len(temperature_window) >= 2:
        temperature_activity = max(0.0, temperature_values[-1] - min(temperature_values[:-1]))
        recent_temperature_values = [
            value for ts, value in temperature_window if now - ts <= 1.5
        ]
        if len(recent_temperature_values) >= 2:
            temperature_recent_rise = temperature_values[-1] - min(recent_temperature_values[:-1])

    threshold = current_effective_threshold(cfg)
    with state_lock:
        last_breath = system_state["last_breath_time"]
        breath_state = system_state["breath_state"]

    cooldown_ok = last_breath is None or now - last_breath >= cfg["cooldown_seconds"]
    pressure_active = False
    pressure_drift = False
    pressure_direction = "stable"
    if len(values) >= 5 and amplitude >= threshold:
        max_value = max(values)
        min_value = min(values)
        max_index = values.index(max_value)
        min_index = values.index(min_value)
        drift_limit = max(1.0, threshold * 0.75)
        edge_drift = abs(values[-1] - values[0])
        positive_pulse = (
            0 < max_index < len(values) - 1
            and max_value - min(values[: max_index + 1]) >= threshold * 0.45
            and max_value - min(values[max_index:]) >= threshold * 0.45
            and edge_drift <= drift_limit
        )
        negative_pulse = (
            0 < min_index < len(values) - 1
            and max(values[: min_index + 1]) - min_value >= threshold * 0.45
            and max(values[min_index:]) - min_value >= threshold * 0.45
            and edge_drift <= drift_limit
        )
        pressure_active = positive_pulse or negative_pulse
        pressure_drift = not pressure_active
        if pressure_active:
            pressure_direction = "positive" if positive_pulse else "negative"

    humidity_slope_threshold = max(0.15, cfg["humidity_activity_threshold"] * 0.2)
    humidity_active = (
        humidity_activity is not None
        and humidity_activity >= cfg["humidity_activity_threshold"]
        and humidity_recent_rise >= humidity_slope_threshold
    )
    temperature_slope_threshold = max(0.03, cfg["temperature_activity_threshold"] * 0.2)
    temperature_active = (
        temperature_activity is not None
        and temperature_activity >= cfg["temperature_activity_threshold"]
        and temperature_recent_rise >= temperature_slope_threshold
    )
    active_breath = (
        (pressure_active or humidity_active or temperature_active)
        and cooldown_ok
        and not activity_latched
    )
    recovered = (
        not humidity_active
        and not temperature_active
        and (amplitude < threshold * 0.55 or pressure_drift)
    )

    if active_breath:
        breath_times.append(now)
        breath_amplitudes.append(amplitude)
        direction = pressure_direction
        if not pressure_active:
            direction = "humidity_rise" if humidity_active else "temperature_rise"
        features = []
        if pressure_active:
            features.append(f"pressure pulse {amplitude:.1f} Pa")
        if humidity_active:
            features.append(f"humidity rise {humidity_activity:.1f}%")
        if temperature_active:
            features.append(f"temperature rise {temperature_activity:.2f}C")
        update_state(
            last_breath_time=now,
            last_breath_age_s=0.0,
            resp_rate_est=calculate_resp_rate(),
            breath_state="breath_activity",
            apnea_active=False,
            activity_latched=True,
        )
        clear_alarm("apnea_watchdog")
        set_led(True)
        add_event(
            "breath",
            f"Breath activity detected ({direction}, {', '.join(features)}); apnea alarm cleared.",
        )
        if cfg["assist_mode"] == "sync":
            if spo2_blocks_assist(cfg):
                add_event(
                    "assist",
                    "Sync pump suppressed: SpO2 is low/unreliable or has recovered stably.",
                    "warning",
                )
            else:
                threading.Thread(target=execute_pump, args=("breath_sync",), daemon=True).start()
    elif (breath_state == "breath_activity" or activity_latched) and recovered:
        set_led(False)
        update_state(breath_state="monitoring", activity_latched=False)
    elif pressure_drift and breath_state == "monitoring":
        update_state(breath_state="pressure_drift_ignored", activity_latched=False)
    elif breath_state == "pressure_drift_ignored" and not pressure_drift:
        update_state(breath_state="monitoring", activity_latched=False)

    update_state(
        breath_activity_amplitude_pa=round(amplitude, 2),
        humidity_activity=round(humidity_activity, 2) if humidity_activity is not None else None,
        temperature_activity=round(temperature_activity, 3)
        if temperature_activity is not None
        else None,
        effective_breath_threshold_pa=round(threshold, 2),
        effective_inhale_threshold_pa=round(-threshold, 2),
        adaptive_apnea_seconds=round(current_adaptive_apnea_seconds(cfg), 1),
    )


def update_apnea_control(now, cfg):
    apnea_seconds = current_adaptive_apnea_seconds(cfg)
    if apnea_seconds <= 0:
        clear_alarm("apnea_watchdog")
        return

    with state_lock:
        last_breath = system_state["last_breath_time"]
        last_assist = system_state["last_assist_time"]
        last_request = system_state["last_assist_request_time"]
        apnea_active = system_state["apnea_active"]
        servo_state = system_state["servo_state"]

    if last_breath is not None and now - last_breath > apnea_seconds:
        add_alarm("apnea_watchdog")
        set_led(True)
        if not apnea_active:
            update_state(apnea_active=True, breath_state="apnea")
            add_event("apnea", "No valid breath detected; apnea alarm is active.", "warning")

        last_activity = max(
            item for item in (last_assist, last_request) if item is not None
        ) if (last_assist is not None or last_request is not None) else None
        interval_ok = (
            servo_state != "pumping"
            and (last_activity is None or now - last_activity >= cfg["assist_interval_seconds"])
        )
        if interval_ok:
            with state_lock:
                pump_count = system_state["apnea_pump_count"]
            if spo2_blocks_assist(cfg):
                update_state(last_assist_request_time=now)
                add_event(
                    "assist",
                    "Pump suppressed: SpO2 is low/unreliable or has recovered stably.",
                    "warning",
                )
            else:
                update_state(last_assist_request_time=now, apnea_pump_count=pump_count + 1)
                add_event("assist", "Apnea assist pump requested.", "warning")
                threading.Thread(target=execute_pump, args=("apnea_assist",), daemon=True).start()
    else:
        clear_alarm("apnea_watchdog")
        if apnea_active:
            update_state(apnea_active=False)


def update_spo2_state(cfg):
    if _st.spo2_monitor is None:
        return

    monitor_error = getattr(_st.spo2_monitor, "error", None)
    if monitor_error:
        try:
            _st.spo2_monitor.stop()
        except Exception:
            pass
        _st.spo2_monitor = None
        update_state(
            spo2=None,
            heart_rate=None,
            spo2_status=f"MAX30102 unavailable: {monitor_error}",
            spo2_trend="unknown",
            spo2_recovery_since=None,
            spo2_recovered_stable=False,
        )
        add_event("hardware", f"MAX30102 unavailable: {monitor_error}", "warning")
        return

    current_bpm = _st.spo2_monitor.bpm
    current_spo2 = _st.spo2_monitor.spo2

    hr_value = round(current_bpm, 1) if current_bpm and current_bpm > 0 else None
    spo2_value = round(current_spo2, 1) if current_spo2 and current_spo2 > 0 else None

    with state_lock:
        system_state["heart_rate"] = hr_value
        system_state["spo2"] = spo2_value

    now = time.time()

    if spo2_value is None:
        update_state(spo2_trend="unknown", spo2_recovery_since=None, spo2_recovered_stable=False)
        return

    if spo2_value < cfg["spo2_alarm_threshold"]:
        add_alarm("spo2_low")
        clear_alarm("spo2_pump_ineffective")
        update_state(spo2_trend="low_untrusted", spo2_recovery_since=None, spo2_recovered_stable=False)
    else:
        clear_alarm("spo2_low")
        clear_alarm("spo2_pump_ineffective")
        with state_lock:
            recovery_since = system_state["spo2_recovery_since"]
            was_stable = system_state["spo2_recovered_stable"]
        if recovery_since is None:
            update_state(
                spo2_trend="recovering",
                spo2_recovery_since=now,
                spo2_recovered_stable=False,
            )
        elif now - recovery_since >= cfg["spo2_recovery_hold_seconds"]:
            update_state(spo2_trend="recovered", spo2_recovered_stable=True, apnea_pump_count=0)
            if not was_stable:
                add_event(
                    "spo2",
                    f"SpO2 stayed above {cfg['spo2_alarm_threshold']}% for "
                    f"{cfg['spo2_recovery_hold_seconds']}s; pausing assist pump.",
                    "info",
                )
        else:
            update_state(spo2_trend="recovering", spo2_recovered_stable=False)
