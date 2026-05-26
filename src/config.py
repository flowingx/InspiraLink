APNEA_HARD_LIMIT_SECONDS = 15.0

DEFAULT_CONFIG = {
    "system_mode": "night",
    "mode": "pressure_servo_led",
    "assist_mode": "apnea_only",
    "sample_hz": 20,
    "calibration_seconds": 10,
    "breath_window_seconds": 4.0,
    "min_breath_activity_pa": 1.5,
    "humidity_activity_threshold": 1.0,
    "temperature_activity_threshold": 0.15,
    "noise_sigma_multiplier": 3.0,
    "breath_amplitude_factor": 0.35,
    "cooldown_seconds": 2.0,
    "apnea_min_seconds": 12.0,
    "apnea_max_seconds": 15.0,
    "adaptive_apnea_factor": 2.0,
    "assist_interval_seconds": 5.0,
    "pump_artifact_ignore_seconds": 2.0,
    "post_pump_recovery_seconds": 8.0,
    "post_pump_threshold_factor": 0.65,
    "servo_rest_angle": 30,
    "servo_press_angle": 105,
    "pump_hold_seconds": 0.55,
    "pump_cooldown_seconds": 3.0,
    "pump_test_limit": 20,
    "spo2_alarm_threshold": 90,
    "spo2_pump_max_count": 5,
    "fall_angle_threshold": 45,
    "fall_detect_frames": 5,
    "camera_index": 0,
    "camera_width": 640,
    "camera_height": 480,
    "fall_inference_interval": 3,
    "onnx_input_size": 320,
}

GPIO_PINS = {
    "led": 27,
    "servo": 18,
    "i2c_sda": 2,
    "i2c_scl": 3,
}


def apnea_bounds(cfg):
    minimum = max(
        5.0,
        min(float(cfg.get("apnea_min_seconds", DEFAULT_CONFIG["apnea_min_seconds"])), APNEA_HARD_LIMIT_SECONDS),
    )
    configured_max = max(minimum, float(cfg.get("apnea_max_seconds", DEFAULT_CONFIG["apnea_max_seconds"])))
    maximum = min(configured_max, APNEA_HARD_LIMIT_SECONDS)
    return minimum, maximum


def clamp_config_values(cfg):
    minimum, maximum = apnea_bounds(cfg)
    cfg["apnea_min_seconds"] = minimum
    cfg["apnea_max_seconds"] = maximum
    cfg["adaptive_apnea_factor"] = max(
        1.2,
        min(float(cfg.get("adaptive_apnea_factor", DEFAULT_CONFIG["adaptive_apnea_factor"])), 2.0),
    )
