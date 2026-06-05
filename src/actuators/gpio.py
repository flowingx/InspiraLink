import threading
import subprocess
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


def servo_value_from_angle(angle):
    return max(-1.0, min(1.0, (float(angle) / 90.0) - 1.0))


def clamp_servo_value(value):
    return max(-1.0, min(1.0, float(value)))


def set_servo_angle(servo, angle):
    servo.value = servo_value_from_angle(angle)


def set_servo_value(servo, value):
    servo.value = clamp_servo_value(value)


def release_servo():
    if _st.servo_device is not None:
        try:
            _st.servo_device.value = None
        except Exception:
            pass
    try:
        subprocess.run(
            ["pigs", "s", str(GPIO_PINS["servo"]), "0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1.0,
            check=False,
        )
    except Exception:
        pass


def init_gpio():
    from gpiozero import AngularServo, Device, LED

    try:
        from gpiozero.pins.pigpio import PiGPIOFactory

        Device.pin_factory = PiGPIOFactory()
    except Exception:
        from gpiozero.pins.lgpio import LGPIOFactory

        Device.pin_factory = LGPIOFactory()

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

    led = LED(GPIO_PINS["led"])
    servo_kwargs = {
        "min_angle": 0,
        "max_angle": 180,
        "min_pulse_width": 0.0005,
        "max_pulse_width": 0.0025,
    }
    try:
        servo = AngularServo(GPIO_PINS["servo"], initial_angle=None, **servo_kwargs)
    except TypeError:
        servo = AngularServo(GPIO_PINS["servo"], **servo_kwargs)
        try:
            servo.value = None
        except Exception:
            pass
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
            try:
                set_servo_value(_st.servo_device, cfg["servo_press_value"])
                time.sleep(cfg["pump_hold_seconds"])
                set_servo_value(_st.servo_device, cfg["servo_rest_value"])
                time.sleep(cfg["servo_rest_seconds"])
            finally:
                release_servo()
            update_state(
                servo_state="idle",
                last_assist_time=now,
                ignore_pressure_until=time.time() + cfg["pump_artifact_ignore_seconds"],
                recovery_until=time.time() + cfg["post_pump_recovery_seconds"],
            )
            add_event("pump", "Pump action completed.")
            return True, "pump completed"
        except Exception as exc:
            release_servo()
            add_alarm("servo_error")
            update_state(servo_state="error")
            set_led(True)
            add_event("servo_error", f"Servo action failed: {exc}", "error")
            return False, str(exc)
