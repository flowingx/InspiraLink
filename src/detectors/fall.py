import threading
import time
from pathlib import Path


class FallDetector:
    """YOLOv8n-pose based fall detection running in a background thread.

    Uses the exact same algorithm from falldown/test.py:
    - Compute angle between shoulder-center and hip-center.
    - If angle < threshold for N consecutive inference frames -> fall detected.
    """

    SKELETON = [(5, 6), (5, 11), (6, 12), (11, 12)]

    def __init__(self, cfg):
        import cv2
        import numpy as np
        import onnxruntime as ort

        self._cv2 = cv2
        self._np = np

        model_path = str(
            Path(__file__).resolve().parent.parent / "models" / "yolov8n-pose2.onnx"
        )
        self._session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self._input_name = self._session.get_inputs()[0].name

        cam_idx = int(cfg.get("camera_index", 0))
        self._cap = cv2.VideoCapture(cam_idx)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cfg.get("camera_width", 640)))
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cfg.get("camera_height", 480)))

        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera index {cam_idx}")

        self._angle_threshold = float(cfg.get("fall_angle_threshold", 45))
        self._detect_frames = int(cfg.get("fall_detect_frames", 5))
        self._inference_interval = int(cfg.get("fall_inference_interval", 3))
        self._input_size = int(cfg.get("onnx_input_size", 320))
        self._fall_count = 0
        self._frame_count = 0
        self._is_fall = False
        self._last_angle = None
        self._stopped = False
        self._thread = None

    def start(self):
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopped = True
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        if self._cap is not None:
            self._cap.release()

    @property
    def fall_detected(self):
        return self._is_fall

    @property
    def fall_count(self):
        return self._fall_count

    @property
    def last_angle(self):
        return self._last_angle

    def _loop(self):
        cv2 = self._cv2
        np = self._np
        while not self._stopped:
            ret, frame = self._cap.read()
            if not ret:
                time.sleep(0.05)
                continue

            self._frame_count += 1
            if self._frame_count % self._inference_interval != 0:
                time.sleep(0.01)
                continue

            original_h, original_w = frame.shape[:2]
            sz = self._input_size

            img = cv2.resize(frame, (sz, sz))
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            img = img.astype(np.float32) / 255.0
            img = np.transpose(img, (2, 0, 1))
            img = np.expand_dims(img, axis=0)

            outputs = self._session.run(None, {self._input_name: img})
            output = np.squeeze(outputs[0]).T

            detected_fall_this_frame = False
            for detection in output:
                x, y, w, h = detection[:4]
                confidence = detection[4]
                if confidence < 0.7:
                    continue

                keypoints = []
                for i in range(17):
                    kp_x = detection[5 + i * 3]
                    kp_y = detection[5 + i * 3 + 1]
                    kp_conf = detection[5 + i * 3 + 2]
                    if kp_conf > 0.5:
                        px = int(kp_x * original_w / sz)
                        py = int(kp_y * original_h / sz)
                        keypoints.append((px, py))
                    else:
                        keypoints.append(None)

                if (
                    keypoints[5]
                    and keypoints[6]
                    and keypoints[11]
                    and keypoints[12]
                ):
                    shoulder_x = (keypoints[5][0] + keypoints[6][0]) / 2
                    shoulder_y = (keypoints[5][1] + keypoints[6][1]) / 2
                    hip_x = (keypoints[11][0] + keypoints[12][0]) / 2
                    hip_y = (keypoints[11][1] + keypoints[12][1]) / 2
                    dx = hip_x - shoulder_x
                    dy = hip_y - shoulder_y
                    angle = abs(np.degrees(np.arctan2(dy, dx)))
                    self._last_angle = round(float(angle), 1)

                    if angle < self._angle_threshold:
                        self._fall_count += 1
                    else:
                        self._fall_count = 0

                    if self._fall_count > self._detect_frames:
                        detected_fall_this_frame = True

            self._is_fall = detected_fall_this_frame
            time.sleep(0.01)
