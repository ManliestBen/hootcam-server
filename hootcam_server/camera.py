"""
Dual camera capture for Raspberry Pi 5 using Picamera2.

On Pi: use Picamera2(0) and Picamera2(1) for the two CSI ports.
When picamera2 is not available (e.g. development), capture is no-op and
streams can serve a placeholder.
"""

from __future__ import annotations

import io
import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Optional: only on Pi with picamera2 installed
try:
    from picamera2 import Picamera2
except ImportError:
    Picamera2 = None  # type: ignore


def is_camera_available() -> bool:
    return Picamera2 is not None


class DualCameraService:
    """
    Manages two Pi cameras (CSI0 and CSI1).
    Single process; both cameras run in same process for stability.
    """

    def __init__(
        self,
        width: int = 640,
        height: int = 480,
        framerate: int = 15,
    ) -> None:
        self.width = width
        self.height = height
        self.framerate = framerate
        self._cam0: Optional[Any] = None
        self._cam1: Optional[Any] = None
        self._started = False
        self._config0: Optional[Tuple[int, int, int]] = None  # (width, height, framerate)
        self._config1: Optional[Tuple[int, int, int]] = None

    def start(
        self,
        width0: int,
        height0: int,
        framerate0: int,
        width1: int,
        height1: int,
        framerate1: int,
    ) -> Tuple[bool, bool]:
        """Start both cameras. Returns (cam0_ok, cam1_ok)."""
        if Picamera2 is None:
            logger.warning("picamera2 not available; cameras disabled")
            return False, False

        cam0_ok, cam1_ok = False, False

        try:
            self._cam0 = Picamera2(0)
            self._cam0.configure(
                self._cam0.create_preview_configuration(
                    main={"size": (width0, height0), "format": "RGB888"},
                    controls={"FrameRate": framerate0},
                )
            )
            self._cam0.start()
            self._config0 = (width0, height0, framerate0)
            cam0_ok = True
            logger.info("Camera 0 started %dx%d @ %d fps", width0, height0, framerate0)
        except Exception as e:
            logger.exception("Failed to start camera 0: %s", e)

        try:
            self._cam1 = Picamera2(1)
            self._cam1.configure(
                self._cam1.create_preview_configuration(
                    main={"size": (width1, height1), "format": "RGB888"},
                    controls={"FrameRate": framerate1},
                )
            )
            self._cam1.start()
            self._config1 = (width1, height1, framerate1)
            cam1_ok = True
            logger.info("Camera 1 started %dx%d @ %d fps", width1, height1, framerate1)
        except Exception as e:
            logger.exception("Failed to start camera 1: %s", e)

        self._started = cam0_ok or cam1_ok
        return cam0_ok, cam1_ok

    def restart_camera(self, camera_index: int) -> bool:
        """
        Stop and start one camera (e.g. after a timeout). Returns True if restart succeeded.
        Uses the same width/height/framerate stored from start().
        """
        if Picamera2 is None:
            return False
        cfg = self._config0 if camera_index == 0 else self._config1
        if cfg is None:
            logger.warning("Cannot restart camera %d: no stored config", camera_index)
            return False
        w, h, fps = cfg
        cam = self._cam0 if camera_index == 0 else self._cam1
        if cam is not None:
            try:
                cam.stop()
            except Exception as e:
                logger.warning("Stop camera %d during restart: %s", camera_index, e)
        if camera_index == 0:
            self._cam0 = None
        else:
            self._cam1 = None

        try:
            if camera_index == 0:
                self._cam0 = Picamera2(0)
                self._cam0.configure(
                    self._cam0.create_preview_configuration(
                        main={"size": (w, h), "format": "RGB888"},
                        controls={"FrameRate": fps},
                    )
                )
                self._cam0.start()
                logger.info("Camera 0 restarted %dx%d @ %d fps", w, h, fps)
                return True
            else:
                self._cam1 = Picamera2(1)
                self._cam1.configure(
                    self._cam1.create_preview_configuration(
                        main={"size": (w, h), "format": "RGB888"},
                        controls={"FrameRate": fps},
                    )
                )
                self._cam1.start()
                logger.info("Camera 1 restarted %dx%d @ %d fps", w, h, fps)
                return True
        except Exception as e:
            logger.exception("Failed to restart camera %d: %s", camera_index, e)
            return False

    def stop(self) -> None:
        """Stop both cameras."""
        if self._cam0 is not None:
            try:
                self._cam0.stop()
            except Exception:
                pass
            self._cam0 = None
        if self._cam1 is not None:
            try:
                self._cam1.stop()
            except Exception:
                pass
            self._cam1 = None
        self._started = False
        logger.info("Cameras stopped")

    def capture_frame(self, camera_index: int) -> Optional[bytes]:
        """
        Capture one frame as JPEG bytes, or None if camera not available.
        camera_index: 0 or 1.
        """
        cam = self._cam0 if camera_index == 0 else self._cam1
        if cam is None:
            return None
        try:
            from PIL import Image
            arr = cam.capture_array()
            if arr is None or arr.size == 0:
                return None
            img = Image.fromarray(arr)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception as e:
            logger.debug("Capture frame %d failed: %s", camera_index, e)
            return None

    def capture_array(self, camera_index: int) -> Optional[Any]:
        """Capture raw array (for motion detection). Returns numpy array or None."""
        cam = self._cam0 if camera_index == 0 else self._cam1
        if cam is None:
            return None
        try:
            return cam.capture_array()
        except Exception as e:
            logger.debug("Capture array %d failed: %s", camera_index, e)
            return None

    @property
    def started(self) -> bool:
        return self._started

    def camera_available(self, camera_index: int) -> bool:
        if camera_index == 0:
            return self._cam0 is not None
        if camera_index == 1:
            return self._cam1 is not None
        return False

    def get_sensor_modes(self, camera_index: int) -> List[dict]:
        """
        Return list of supported (width, height, fps) from the camera's sensor_modes.
        Each item is {"width": int, "height": int, "fps": float}.
        Returns [] if camera is not available or sensor_modes cannot be read.
        """
        cam = self._cam0 if camera_index == 0 else self._cam1
        if cam is None:
            return []
        try:
            modes = getattr(cam, "sensor_modes", None)
            if not modes:
                return []
            by_size: dict[Tuple[int, int], int] = {}
            for m in modes:
                size = m.get("size")
                fps = m.get("fps")
                if not size or len(size) != 2:
                    continue
                w, h = int(size[0]), int(size[1])
                fps_val = int(fps) if isinstance(fps, (int, float)) and fps is not None else 0
                key = (w, h)
                by_size[key] = max(by_size.get(key, 0), fps_val)
            result = [{"width": w, "height": h, "fps": f} for (w, h), f in by_size.items()]
            result.sort(key=lambda x: (-x["width"] * x["height"], -x["fps"]))
            return result
        except Exception as e:
            logger.debug("get_sensor_modes %d: %s", camera_index, e)
            return []
