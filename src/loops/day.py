import time

import src.state as _st
from ..config import GPIO_PINS
from ..state import (
    add_alarm,
    add_event,
    clear_alarm,
    refresh_derived_history,
    safe_config,
    state_lock,
    stop_event,
    system_state,
    update_state,
)
from ..actuators.gpio import set_led
from ..detectors.fall import FallDetector


def day_monitor_loop():
    """Day mode: fall detection via camera + YOLOv8n-pose."""
    cfg = safe_config()

    # Reuse existing led_device if available; only init if needed
    if _st.led_device is None:
        try:
            from gpiozero import Device, LED
            from gpiozero.pins.pigpio import PiGPIOFactory
            try:
                Device.pin_factory = PiGPIOFactory()
            except Exception:
                pass
            _st.led_device = LED(GPIO_PINS["led"])
        except Exception as exc:
            _st.led_device = None
            add_event("hardware", f"LED init failed in day mode: {exc}", "warning")

    # Initialize fall detector
    try:
        _st.fall_detector_instance = FallDetector(cfg)
        _st.fall_detector_instance.start()
        update_state(
            fall_status="active",
            hardware={
                "simulation": False,
                "pressure_sensor": "not active in day mode",
                "environment_sensor": "not active in day mode",
                "pulse_oximeter": "not active in day mode",
                "fall_detector": "YOLOv8n-pose camera active",
                "servo": "not active in day mode",
                "led": f"GPIO{GPIO_PINS['led']} LED" if _st.led_device else "unavailable",
                "gpio_pins": GPIO_PINS,
            },
        )
        add_event("system", "Day mode started: fall detection active.")
    except Exception as exc:
        _st.fall_detector_instance = None
        update_state(
            fall_status=f"init failed: {exc}",
            hardware={
                "simulation": False,
                "pressure_sensor": "not active in day mode",
                "environment_sensor": "not active in day mode",
                "pulse_oximeter": "not active in day mode",
                "fall_detector": f"init failed: {exc}",
                "servo": "not active in day mode",
                "led": f"GPIO{GPIO_PINS['led']} LED" if _st.led_device else "unavailable",
                "gpio_pins": GPIO_PINS,
            },
        )
        add_event("fall_error", f"Fall detector init failed: {exc}", "error")
        return

    while not stop_event.is_set():
        if _st.fall_detector_instance is None:
            time.sleep(1.0)
            continue

        is_fall = _st.fall_detector_instance.fall_detected
        angle = _st.fall_detector_instance.last_angle

        with state_lock:
            prev_fall = system_state["fall_detected"]

        update_state(
            fall_detected=is_fall,
            fall_count=_st.fall_detector_instance.fall_count,
            fall_angle=angle,
        )

        if is_fall and not prev_fall:
            add_alarm("fall_detected")
            set_led(True)
            add_event("fall", "FALL DETECTED! Alerting.", "error")
        elif not is_fall and prev_fall:
            clear_alarm("fall_detected")
            set_led(False)
            add_event("fall", "Fall state cleared; person appears upright.", "info")

        refresh_derived_history()
        time.sleep(0.5)

    # Cleanup
    if _st.fall_detector_instance is not None:
        _st.fall_detector_instance.stop()
        _st.fall_detector_instance = None
