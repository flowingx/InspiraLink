import time


class OptionalAHT20Reader:
    ADDRESS = 0x38
    INIT_COMMAND = [0xBE, 0x08, 0x00]
    SOFT_RESET_COMMAND = [0xBA]
    MEASURE_COMMAND = [0xAC, 0x33, 0x00]
    BUSY_BIT = 0x80
    CALIBRATED_BIT = 0x08

    def __init__(self, bus_number=1, address=ADDRESS):
        from smbus2 import SMBus, i2c_msg

        self.bus = SMBus(bus_number)
        self.i2c_msg = i2c_msg
        self.address = address
        self._initialize()
        self.read()

    def _write(self, payload):
        try:
            if len(payload) == 1:
                self.bus.write_byte(self.address, payload[0])
            else:
                self.bus.write_i2c_block_data(self.address, payload[0], payload[1:])
        except OSError:
            self.bus.i2c_rdwr(self.i2c_msg.write(self.address, payload))

    def _read_raw(self, length):
        read_msg = self.i2c_msg.read(self.address, length)
        self.bus.i2c_rdwr(read_msg)
        return list(read_msg)

    def _read_register_zero(self, length):
        try:
            return list(self.bus.read_i2c_block_data(self.address, 0x00, length))
        except OSError:
            return self._read_raw(length)

    def _read_status(self):
        try:
            return int(self.bus.read_byte(self.address))
        except OSError:
            return self._read_raw(1)[0]

    def _is_calibrated(self, status):
        return (status & 0x68) == self.CALIBRATED_BIT

    def _soft_reset(self):
        self._write(self.SOFT_RESET_COMMAND)
        time.sleep(0.2)

    def _initialize(self):
        time.sleep(0.04)
        self._write(self.INIT_COMMAND)
        time.sleep(0.5)
        for attempt in range(10):
            status = self._read_status()
            if self._is_calibrated(status):
                return
            self._soft_reset()
            self._write(self.INIT_COMMAND)
            time.sleep(0.5)
        raise RuntimeError("AHT20 calibration bit did not become ready after init")

    def _read_measurement_frame(self, timeout_s=0.2):
        errors = []
        start = time.time()
        while time.time() - start < timeout_s:
            for label, reader, length in (
                ("raw I2C, 7 bytes", self._read_raw, 7),
                ("raw I2C, 6 bytes", self._read_raw, 6),
                ("register 0x00, 7 bytes", self._read_register_zero, 7),
                ("register 0x00, 6 bytes", self._read_register_zero, 6),
            ):
                try:
                    data = reader(length)
                    if data and data[0] & self.BUSY_BIT:
                        errors.append(f"{label}: busy status 0x{data[0]:02x}")
                        continue
                    return data
                except Exception as exc:
                    errors.append(f"{label}: {exc}")
            time.sleep(0.005)
        raise RuntimeError("AHT20 measurement not ready; " + " | ".join(errors[-8:]))

    def _crc8(self, payload):
        crc = 0xFF
        for value in payload:
            crc ^= value
            for _ in range(8):
                if crc & 0x80:
                    crc = ((crc << 1) ^ 0x31) & 0xFF
                else:
                    crc = (crc << 1) & 0xFF
        return crc

    def _decode_measurement(self, data):
        if len(data) not in (6, 7):
            raise RuntimeError(f"AHT20 returned {len(data)} bytes, expected 6 or 7")
        if data[0] & self.BUSY_BIT:
            raise RuntimeError("AHT20 measurement data still marked busy")
        if len(data) == 7 and self._crc8(data[:6]) != data[6]:
            raise RuntimeError(
                f"AHT20 CRC mismatch: got 0x{data[6]:02x}, expected 0x{self._crc8(data[:6]):02x}"
            )
        raw_humidity = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
        raw_temperature = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]
        humidity = raw_humidity * 100.0 / (1 << 20)
        temperature_c = raw_temperature * 200.0 / (1 << 20) - 50.0
        if not -5.0 <= humidity <= 105.0:
            raise RuntimeError(f"AHT20 humidity out of plausible range: {humidity:.2f}%")
        if not -40.0 <= temperature_c <= 85.0:
            raise RuntimeError(f"AHT20 temperature out of plausible range: {temperature_c:.2f}C")
        return float(humidity), float(temperature_c)

    def read(self):
        self._write(self.MEASURE_COMMAND)
        time.sleep(0.08)
        data = self._read_measurement_frame()
        errors = []
        try:
            return self._decode_measurement(data)
        except Exception as exc:
            errors.append(str(exc))
        raise RuntimeError("AHT20 read failed; " + " | ".join(errors))
