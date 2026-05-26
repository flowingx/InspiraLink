import time


class HardwarePressureReader:
    def __init__(self, address=None):
        self.driver = None
        self.sensor = None
        self.address = None
        self.bus = None

        errors = []
        for candidate in self._candidate_addresses(address):
            try:
                self._init_pimoroni_bmp280(candidate)
                return
            except Exception as exc:
                errors.append(f"bmp280@0x{candidate:02x}: {exc}")

        for candidate in self._candidate_addresses(address):
            try:
                self._init_bme280_compat(candidate)
                return
            except Exception as exc:
                errors.append(f"bme280@0x{candidate:02x}: {exc}")

        raise RuntimeError("BMP280 init failed; " + " | ".join(errors))

    def _candidate_addresses(self, address):
        if address is not None:
            return [address]
        return [0x76, 0x77]

    def _init_pimoroni_bmp280(self, address):
        from bmp280 import BMP280
        from smbus2 import SMBus

        bus = SMBus(1)
        try:
            sensor = BMP280(i2c_addr=address, i2c_dev=bus)
            self._warmup_pimoroni_sensor(sensor)
        except TypeError:
            sensor = BMP280(i2c_dev=bus)
            self._warmup_pimoroni_sensor(sensor)

        self.driver = "bmp280"
        self.bus = bus
        self.sensor = sensor
        self.address = address

    def _init_bme280_compat(self, address):
        import bme280
        import smbus2

        bus = smbus2.SMBus(1)
        calibration = bme280.load_calibration_params(bus, address)
        self._warmup_bme280_sensor(bme280, bus, address, calibration)

        self.driver = "bme280"
        self.bus = bus
        self.sensor = (bme280, calibration)
        self.address = address

    def _warmup_pimoroni_sensor(self, sensor):
        last_pressure = None
        for _ in range(8):
            last_pressure = float(sensor.get_pressure())
            time.sleep(0.05)
        if last_pressure is None or not 800.0 <= last_pressure <= 1100.0:
            raise RuntimeError(f"implausible pressure after warmup: {last_pressure} hPa")

    def _warmup_bme280_sensor(self, bme280, bus, address, calibration):
        last_pressure = None
        for _ in range(8):
            last_pressure = float(bme280.sample(bus, address, calibration).pressure)
            time.sleep(0.05)
        if last_pressure is None or not 800.0 <= last_pressure <= 1100.0:
            raise RuntimeError(f"implausible pressure after warmup: {last_pressure} hPa")

    def read_pa(self):
        if self.driver == "bmp280":
            return float(self.sensor.get_pressure()) * 100.0
        if self.driver == "bme280":
            bme280, calibration = self.sensor
            data = bme280.sample(self.bus, self.address, calibration)
            return float(data.pressure) * 100.0
        raise RuntimeError("BMP280 reader is not initialized")
