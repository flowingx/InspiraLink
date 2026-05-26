import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent.parent
_DRIVER_DIR = _SRC_DIR / "max30102_driver"
if str(_DRIVER_DIR) not in sys.path:
    sys.path.insert(0, str(_DRIVER_DIR))


class OptionalMAX30102Reader:
    """Wraps heartrate_monitor.HeartRateMonitor for optional MAX30102 usage."""

    def __init__(self):
        from heartrate_monitor import HeartRateMonitor

        self._hrm = HeartRateMonitor(print_raw=False, print_result=False)
        self._hrm.start_sensor()
        self._started = True

    @property
    def bpm(self):
        return self._hrm.bpm if self._started else 0

    @property
    def spo2(self):
        if not self._started:
            return None
        spos = getattr(self._hrm, "spos", [])
        if spos:
            return spos[-1]
        return None

    def stop(self):
        if self._started:
            try:
                self._hrm.stop_sensor()
            except Exception:
                pass
            self._started = False
