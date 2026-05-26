import threading
import time

from ..config import GPIO_PINS
from ..state import (
    add_alarm,
    add_event,
    clear_alarm,
    config_lock,
    pump_lock,
    safe_config,
    state_lock,
    system_state,
    update_state,
)

# These are set by init_gpio() and referenced by other modules via state
import src.state as _st


def init_gpio():
    from gpiozero import AngularServo, Device, LED
    from gpiozero.pins.pigpio import PiGPIOFactory

    Device.pin_factory = PiGPIOFactory()

    if _st.led_device is not None:
        try:
            _st.led_device.close()
        except Exception:
            pass
        _st.led_device = None
    if _st.servo_device is not None:
        try:
            _st.servo_device.close()
        except Exception:
            pass
        _st.servo_device = None

    led = LED(GPIO_PINS["led"])
    servo = AngularServo(
        GPIO_PINS["servo"],
        min_angle=0,
        max_angle=180,
        min_pulse_width=0.0005,
        max_pulse_width=0.0025,
    )
    return led, servo


def set_led(active):
    if _st.led_device is None:
        return
    try:
        _st.led_device.on() if active else _st.led_device.off()
    except Exception:
        pass


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
            if _st.servo_device is None:
                raise RuntimeError("servo is not initialized")
            _st.servo_device.angle = cfg["servo_press_angle"]
            time.sleep(cfg["pump_hold_seconds"])
            _st.servo_device.angle = cfg["servo_rest_angle"]
            update_state(
                servo_state="idle",
                last_assist_time=now,
                ignore_pressure_until=time.time() + cfg["pump_artifact_ignore_seconds"],
                recovery_until=time.time() + cfg["post_pump_recovery_seconds"],
            )
            add_event("pump", "Pump action completed.")
            return True, "pump completed"
        except Exception as exc:
            add_alarm("servo_error")
            update_state(servo_state="error")
            set_led(True)
            add_event("servo_error", f"Servo action failed: {exc}", "error")
            return False, str(exc)
