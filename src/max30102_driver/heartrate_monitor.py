from max30102 import MAX30102
import hrcalc
import threading
import time
import numpy as np


class HeartRateMonitor(object):
    """
    A class that encapsulates the max30102 device into a thread
    """

    LOOP_TIME = 0.01
    MAX_CONSECUTIVE_IO_ERRORS = 3

    def __init__(self, print_raw=False, print_result=False):
        self.bpm = 0
        self.error = None
        if print_raw is True:
            print('IR, Red')
        self.print_raw = print_raw
        self.print_result = print_result

    def run_sensor(self):
        sensor = None
        ir_data = []
        red_data = []
        bpms = []

        # data for show
        self.spos = []
        self.bpms = []

        try:
            sensor = MAX30102()
            io_errors = 0

            # run until told to stop
            while not self._thread.stopped:
                # check if any data is available
                try:
                    num_bytes = sensor.get_data_present()
                    io_errors = 0
                except OSError as exc:
                    io_errors += 1
                    if io_errors >= self.MAX_CONSECUTIVE_IO_ERRORS:
                        raise
                    if self.print_result:
                        print("MAX30102 transient I2C error: {0}".format(exc))
                    time.sleep(0.1)
                    continue

                if num_bytes > 0:
                    # grab all the data and stash it into arrays
                    while num_bytes > 0:
                        try:
                            red, ir = sensor.read_fifo()
                            io_errors = 0
                        except OSError as exc:
                            io_errors += 1
                            if io_errors >= self.MAX_CONSECUTIVE_IO_ERRORS:
                                raise
                            if self.print_result:
                                print("MAX30102 transient I2C error: {0}".format(exc))
                            time.sleep(0.1)
                            break
                        num_bytes -= 1
                        ir_data.append(ir)
                        red_data.append(red)
                        if self.print_raw:
                            print("{0}, {1}".format(ir, red))

                    while len(ir_data) > 100:
                        ir_data.pop(0)
                        red_data.pop(0)

                    if len(ir_data) == 100:
                        bpm, valid_bpm, spo2, valid_spo2 = hrcalc.calc_hr_and_spo2(ir_data, red_data)
                        if valid_bpm:
                            bpms.append(bpm)
                            while len(bpms) > 4:
                                bpms.pop(0)
                            self.bpm = np.mean(bpms)
                            if (np.mean(ir_data) < 50000 and np.mean(red_data) < 50000):
                                self.bpm = 0
                                if self.print_result:
                                    print("Finger not detected")
                            if self.print_result:
                                print("BPM: {0}, SpO2: {1}".format(self.bpm, spo2))

                            if spo2 > 0:
                                self.bpms.append(self.bpm)
                                self.spos.append(spo2)

                time.sleep(self.LOOP_TIME)
        except Exception as exc:
            self.error = str(exc)
            self.bpm = 0
            if self.print_result:
                print("MAX30102 stopped: {0}".format(exc))
        finally:
            if sensor is not None:
                try:
                    sensor.shutdown()
                except Exception:
                    pass

    def start_sensor(self):
        self.error = None
        self._thread = threading.Thread(target=self.run_sensor)
        self._thread.stopped = False
        self._thread.start()

    def stop_sensor(self, timeout=2.0):
        self._thread.stopped = True
        self.bpm = 0
        self._thread.join(timeout)

    def show(self):
        import matplotlib.pyplot as plt
        from scipy.signal import savgol_filter

        x = np.arange(len(self.spos))
        y = np.array(self.spos)

        yhat = savgol_filter(y, 51, 3)

        plt.plot(x, yhat)
        plt.show()
