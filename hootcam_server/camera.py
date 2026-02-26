"""
Dual camera capture for Raspberry Pi 5 using Picamera2.

Uses hardware-encoded video: main stream (full res) → H.264 encoder → CircularOutput2
for motion-triggered recording; lores stream (320×240) for motion detection and live preview.
When picamera2 is not available (e.g. development), capture is no-op and streams use placeholder.
"""

from __future__ import annotations

import io
import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Lores resolution used for motion detection and for building stream JPEG
LORES_SIZE = (320, 240)

# Optional: only on Pi with picamera2 installed
try:
    from picamera2 import Picamera2
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import CircularOutput2, PyavOutput
except ImportError:
    Picamera2 = None  # type: ignore
    H264Encoder = None  # type: ignore
    CircularOutput2 = None  # type: ignore
    PyavOutput = None  # type: ignore


def is_camera_available() -> bool:
    return Picamera2 is not None


class DualCameraService:
    """
    Manages two Pi cameras (CSI0 and CSI1) with HW video encode.
    main stream → H.264 encoder → [StreamingOutput, CircularOutput2]
    lores stream → capture_array("lores") for motion and preview.
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
        self._enc0: Optional[Any] = None
        self._enc1: Optional[Any] = None
        self._circular0: Optional[Any] = None
        self._circular1: Optional[Any] = None
        self._started = False
        self._config0: Optional[Tuple[int, int, int]] = None
        self._config1: Optional[Tuple[int, int, int]] = None

    def start(
        self,
        width0: int,
        height0: int,
        framerate0: int,
        width1: int,
        height1: int,
        framerate1: int,
        pre_capture_sec0: float = 2.0,
        pre_capture_sec1: float = 2.0,
        h264_bitrate: int = 6_000_000,
    ) -> Tuple[bool, bool]:
        """Start both cameras with video config and HW encode. Returns (cam0_ok, cam1_ok)."""
        if Picamera2 is None or H264Encoder is None or CircularOutput2 is None or PyavOutput is None:
            logger.warning("picamera2 or encoders not available; cameras disabled")
            return False, False

        cam0_ok, cam1_ok = False, False
        buffer_ms0 = max(1000, int(pre_capture_sec0 * 1000))
        buffer_ms1 = max(1000, int(pre_capture_sec1 * 1000))

        try:
            self._cam0 = Picamera2(0)
            main0 = {"size": (width0, height0), "format": "YUV420"}
            lores0 = {"size": LORES_SIZE, "format": "YUV420"}
            video_config0 = self._cam0.create_video_configuration(
                main=main0,
                lores=lores0,
                encode="main",
                controls={"FrameRate": framerate0},
            )
            self._cam0.configure(video_config0)
            self._circular0 = CircularOutput2(buffer_duration_ms=buffer_ms0)
            self._enc0 = H264Encoder(bitrate=h264_bitrate)
            self._enc0.output = [self._circular0]
            self._cam0.start_recording(self._enc0)
            self._config0 = (width0, height0, framerate0)
            cam0_ok = True
            logger.info("Camera 0 started %dx%d @ %d fps (HW encode)", width0, height0, framerate0)
        except Exception as e:
            logger.exception("Failed to start camera 0: %s", e)

        try:
            self._cam1 = Picamera2(1)
            main1 = {"size": (width1, height1), "format": "YUV420"}
            lores1 = {"size": LORES_SIZE, "format": "YUV420"}
            video_config1 = self._cam1.create_video_configuration(
                main=main1,
                lores=lores1,
                encode="main",
                controls={"FrameRate": framerate1},
            )
            self._cam1.configure(video_config1)
            self._circular1 = CircularOutput2(buffer_duration_ms=buffer_ms1)
            self._enc1 = H264Encoder(bitrate=h264_bitrate)
            self._enc1.output = [self._circular1]
            self._cam1.start_recording(self._enc1)
            self._config1 = (width1, height1, framerate1)
            cam1_ok = True
            logger.info("Camera 1 started %dx%d @ %d fps (HW encode)", width1, height1, framerate1)
        except Exception as e:
            logger.exception("Failed to start camera 1: %s", e)

        self._started = cam0_ok or cam1_ok
        return cam0_ok, cam1_ok

    def stop(self) -> None:
        """Stop both cameras and encoders."""
        for cam, enc in [(self._cam0, self._enc0), (self._cam1, self._enc1)]:
            if cam is not None and enc is not None:
                try:
                    cam.stop_recording()
                except Exception:
                    pass
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
        self._enc0 = self._enc1 = None
        self._circular0 = self._circular1 = None
        self._started = False
        logger.info("Cameras stopped")

    def restart_camera(self, camera_index: int) -> bool:
        """Restart one camera with same config. Returns True if succeeded."""
        if Picamera2 is None:
            return False
        cfg = self._config0 if camera_index == 0 else self._config1
        if cfg is None:
            logger.warning("Cannot restart camera %d: no stored config", camera_index)
            return False
        w, h, fps = cfg
        # Stop and clear
        if camera_index == 0:
            if self._cam0 is not None:
                try:
                    self._cam0.stop_recording()
                    self._cam0.stop()
                except Exception:
                    pass
                self._cam0 = self._enc0 = None
                self._circular0 = None
        else:
            if self._cam1 is not None:
                try:
                    self._cam1.stop_recording()
                    self._cam1.stop()
                except Exception:
                    pass
                self._cam1 = self._enc1 = None
                self._circular1 = None
        # Restart with same params (pre_capture_sec default)
        if camera_index == 0:
            return self.start(w, h, fps, self._config1[0] if self._config1 else 640, self._config1[1] if self._config1 else 480, self._config1[2] if self._config1 else 15)[0]
        else:
            return self.start(self._config0[0] if self._config0 else 640, self._config0[1] if self._config0 else 480, self._config0[2] if self._config0 else 15, w, h, fps)[1]

    def capture_array(self, camera_index: int, stream: str = "lores") -> Optional[Any]:
        """Capture from lores (for motion/preview) or main. Returns numpy array or None."""
        cam = self._cam0 if camera_index == 0 else self._cam1
        if cam is None:
            return None
        try:
            return cam.capture_array(stream)
        except Exception as e:
            logger.debug("Capture array %d failed: %s", camera_index, e)
            return None

    def start_event_recording(self, camera_index: int, path: str) -> None:
        """Start writing circular buffer to file (on motion start)."""
        circ = self._circular0 if camera_index == 0 else self._circular1
        if circ is not None and PyavOutput is not None:
            try:
                circ.open_output(PyavOutput(path))
            except Exception as e:
                logger.warning("start_event_recording %s: %s", path, e)

    def stop_event_recording(self, camera_index: int) -> None:
        """Stop writing to file and close (on motion end)."""
        circ = self._circular0 if camera_index == 0 else self._circular1
        if circ is not None:
            try:
                circ.close_output()
            except Exception as e:
                logger.debug("stop_event_recording: %s", e)

    def capture_frame(self, camera_index: int) -> Optional[bytes]:
        """
        Capture one frame as JPEG bytes from lores (for snapshot/fallback).
        Returns None if camera not available.
        """
        arr = self.capture_array(camera_index, "lores")
        if arr is None or arr.size == 0:
            return None
        try:
            from PIL import Image
            h, w = LORES_SIZE[1], LORES_SIZE[0]
            if arr.ndim == 2:
                y = arr[:h, :w]
            else:
                y = arr[:h, :w] if arr.shape[0] >= h else arr
            img = Image.fromarray(y) if y.ndim == 2 else Image.fromarray(y, mode="L")
            if img.mode != "L":
                img = img.convert("L")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception as e:
            logger.debug("Capture frame %d failed: %s", camera_index, e)
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
        """Return list of supported (width, height, fps) from the camera's sensor_modes."""
        cam = self._cam0 if camera_index == 0 else self._cam1
        if cam is None:
            return []
        modes = getattr(cam, "sensor_modes", None)
        if not modes:
            return []
        try:
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
