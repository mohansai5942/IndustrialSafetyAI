"""Fail the image build if OpenCV/YOLO native dependencies are broken."""
import ctypes
from importlib.metadata import distributions

for library in ("libGL.so.1", "libglib-2.0.so.0", "libxcb.so.1", "libXext.so.6"):
    ctypes.CDLL(library)

opencv_packages = sorted({dist.metadata["Name"].lower() for dist in distributions()
                          if dist.metadata.get("Name", "").lower().startswith("opencv-")})
if opencv_packages != ["opencv-python"]:
    raise RuntimeError(f"Expected only opencv-python; found {opencv_packages}. Rebuild without cache.")

import cv2
import numpy as np
from ultralytics import YOLO
import aiortc

ok, encoded = cv2.imencode(".jpg", np.zeros((32, 32, 3), dtype=np.uint8))
assert ok and encoded.size > 0, "OpenCV image encoding failed"
print(f"Runtime imports OK: OpenCV {cv2.__version__}, YOLO, aiortc {aiortc.__version__}")
