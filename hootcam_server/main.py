"""
Hootcam Server: FastAPI application for dual camera, motion detection, recording, streams, API.

Run with: uvicorn hootcam_server.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from . import auth
from . import database
from . import recording
from .api.routes import router
from .api.schemas import CameraConfig, GlobalConfig
from .config import (
    ensure_config_dir_and_db,
    get_bootstrap_target_dir,
    load_camera_config,
    load_global_config,
    save_camera_config,
    save_global_config,
)
from .motion import MotionDetector

# Optional camera service (picamera2 on Pi only)
try:
    from .camera import LORES_SIZE, DualCameraService
except ImportError:
    DualCameraService = None  # type: ignore
    LORES_SIZE = (320, 240)  # fallback when camera module not available

# Global app state (set in lifespan)
app_state: dict[str, Any] = {}


def _setup_logging(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


# Max time to wait for one frame before treating as camera timeout (e.g. V4L frontend timeout)
CAPTURE_TIMEOUT_SEC = 15.0
# Consecutive timeouts before we mark the camera as failed and stop calling it
CAPTURE_TIMEOUT_FAILURE_THRESHOLD = 3


async def _capture_array_with_timeout(
    loop: asyncio.AbstractEventLoop,
    camera_service: Any,
    camera_index: int,
    timeout: float,
) -> Optional[Any]:
    """Run blocking capture_array() in a thread; return None on timeout or error."""
    if camera_service is None:
        return None

    def _capture() -> Optional[Any]:
        return camera_service.capture_array(camera_index, "lores")

    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _capture),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        return None
    except Exception:
        return None


async def _capture_loop(
    camera_index: int,
    camera_service: Any,
    motion_detector: MotionDetector,
    config: CameraConfig,
    global_config: GlobalConfig,
    target_dir: Path,
    db_path: Path,
    state: dict,
) -> None:
    """One loop per camera: capture -> motion -> record; update latest_jpeg.
    Capture runs in a thread with a timeout so a stuck camera (e.g. V4L timeout) doesn't lock the app.
    On timeout we try to restart the camera once; after several consecutive timeouts we mark it failed.
    """
    from PIL import Image
    import io

    log = logging.getLogger(__name__)
    event_gap_sec = config.event_gap if config.event_gap is not None and config.event_gap >= 0 else 60
    post_capture = config.post_capture or 0
    last_motion_at: Optional[datetime] = None
    post_frames_left = 0
    framerate = config.framerate or 15
    interval = 1.0 / framerate
    consecutive_timeouts = 0
    restarted_after_timeout = False

    loop = asyncio.get_event_loop()

    while True:
        try:
            if state.get("camera_failed") and state["camera_failed"][camera_index]:
                await asyncio.sleep(interval)
                continue

            # Lores Y plane size (YUV420: first H rows are Y)
            lh, lw = LORES_SIZE[1], LORES_SIZE[0]

            if state["detection_paused"][camera_index]:
                arr = await _capture_array_with_timeout(
                    loop, camera_service, camera_index, CAPTURE_TIMEOUT_SEC
                )
                if arr is not None:
                    consecutive_timeouts = 0
                    y = arr[:lh, :lw] if arr.ndim >= 2 else arr
                    buf = io.BytesIO()
                    Image.fromarray(y).convert("L").save(buf, format="JPEG", quality=global_config.stream_quality or 50)
                    if state.get("latest_jpeg") is not None and camera_index < len(state["latest_jpeg"]):
                        state["latest_jpeg"][camera_index] = buf.getvalue()
                else:
                    consecutive_timeouts += 1
                    if camera_service and consecutive_timeouts == 1 and not restarted_after_timeout and getattr(camera_service, "restart_camera", None):
                        try:
                            ok = await loop.run_in_executor(None, lambda: camera_service.restart_camera(camera_index))
                            restarted_after_timeout = True
                            if ok:
                                consecutive_timeouts = 0
                        except Exception:
                            pass
                    if consecutive_timeouts >= CAPTURE_TIMEOUT_FAILURE_THRESHOLD and state.get("camera_failed") is not None:
                        state["camera_failed"][camera_index] = True
                        log.error("Camera %d failed after %d consecutive timeouts; disabling.", camera_index, consecutive_timeouts)
                await asyncio.sleep(interval)
                continue

            arr = await _capture_array_with_timeout(
                loop, camera_service, camera_index, CAPTURE_TIMEOUT_SEC
            )
            if arr is None:
                consecutive_timeouts += 1
                if camera_service is not None and consecutive_timeouts == 1 and not restarted_after_timeout:
                    if getattr(camera_service, "restart_camera", None):
                        log.warning(
                            "Camera %d capture timed out (e.g. sensor disconnected). Attempting restart.",
                            camera_index,
                        )
                        try:
                            ok = await loop.run_in_executor(
                                None,
                                lambda: camera_service.restart_camera(camera_index),
                            )
                            restarted_after_timeout = True
                            if ok:
                                consecutive_timeouts = 0
                                log.info("Camera %d restarted successfully.", camera_index)
                        except Exception as e:
                            log.exception("Camera %d restart failed: %s", camera_index, e)
                if consecutive_timeouts >= CAPTURE_TIMEOUT_FAILURE_THRESHOLD:
                    if state.get("camera_failed") is not None:
                        state["camera_failed"][camera_index] = True
                    log.error(
                        "Camera %d failed after %d consecutive timeouts; disabling capture for this camera. "
                        "Check cable/sensor. Restart the server to retry.",
                        camera_index,
                        consecutive_timeouts,
                    )
                await asyncio.sleep(interval)
                continue
            consecutive_timeouts = 0

            # Extract Y plane from lores (YUV420: shape (h*3/2, w))
            y = arr[:lh, :lw] if arr.ndim >= 2 else arr
            jpeg_bytes = None
            try:
                buf = io.BytesIO()
                Image.fromarray(y).convert("L").save(buf, format="JPEG", quality=85)
                jpeg_bytes = buf.getvalue()
                if state.get("latest_jpeg") is not None and camera_index < len(state["latest_jpeg"]):
                    state["latest_jpeg"][camera_index] = jpeg_bytes
            except Exception:
                pass

            motion_detected, changed = motion_detector.update(y)
            now = datetime.utcnow()

            # On-demand snapshot (from UI "Take snapshot")
            requests = state.get("snapshot_requests") or {}
            if requests.get(camera_index) and requests[camera_index] and jpeg_bytes:
                requests[camera_index].pop()
                try:
                    name = recording._expand_filename(
                        config.snapshot_filename or "%v-%Y%m%d%H%M%S-snapshot",
                        0,
                        config.camera_id,
                        config.camera_name,
                    )
                    snap_path = target_dir / f"{name}.jpg"
                    snap_path.parent.mkdir(parents=True, exist_ok=True)
                    snap_path.write_bytes(jpeg_bytes)
                    try:
                        rel = str(snap_path.relative_to(target_dir))
                    except ValueError:
                        rel = str(snap_path)
                    if config.sql_log_snapshot:
                        database.log_file(
                            db_path,
                            None,
                            camera_index,
                            "snapshot",
                            rel,
                            now.strftime("%Y-%m-%d %H:%M:%S"),
                            None,
                        )
                except Exception as e:
                    log.warning("Snapshot save failed: %s", e)

            # HW recording path: camera_service.start_event_recording / stop_event_recording
            recording_session = state.get("current_event_id") and state["current_event_id"][camera_index] is not None
            if recording_session:
                if motion_detected:
                    last_motion_at = now
                    post_frames_left = post_capture
                elif post_frames_left > 0:
                    post_frames_left -= 1
                else:
                    # End event after event_gap with no motion
                    if last_motion_at and (now - last_motion_at).total_seconds() >= event_gap_sec:
                        event_path = (state.get("current_event_path") or [None, None])[camera_index]
                        if event_path and config.sql_log_movie:
                            try:
                                rel = str(Path(event_path).relative_to(target_dir))
                            except ValueError:
                                rel = event_path
                            database.log_file(
                                db_path,
                                state["current_event_id"][camera_index],
                                camera_index,
                                "movie",
                                rel,
                                now.strftime("%Y-%m-%d %H:%M:%S"),
                                None,
                            )
                        database.log_event_end(db_path, state["current_event_id"][camera_index], now.strftime("%Y-%m-%d %H:%M:%S"))
                        if (state.get("current_event_path") and state["current_event_path"][camera_index]
                                and getattr(camera_service, "stop_event_recording", None)):
                            camera_service.stop_event_recording(camera_index)
                        if config.on_event_end:
                            recording.run_script_sync(config.on_event_end)
                        state["current_event_id"][camera_index] = None
                        if state.get("current_event_path") is not None:
                            state["current_event_path"][camera_index] = None
                        last_motion_at = None

            elif motion_detected:
                # Start new event: open HW recording to file (if movie_output enabled)
                started_at_str = now.strftime("%Y-%m-%d %H:%M:%S")
                event_id = database.log_event_start(db_path, camera_index, config.camera_id, started_at_str)
                state["current_event_id"][camera_index] = event_id
                if config.movie_output:
                    name = recording._expand_filename(
                        config.movie_filename or "%v-%Y%m%d%H%M%S",
                        event_id,
                        config.camera_id,
                        config.camera_name,
                    )
                    out_path = target_dir / f"{name}.mp4"
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    if getattr(camera_service, "start_event_recording", None):
                        camera_service.start_event_recording(camera_index, str(out_path))
                    if state.get("current_event_path") is not None:
                        state["current_event_path"][camera_index] = str(out_path)
                last_motion_at = now
                post_frames_left = post_capture
                if config.on_event_start:
                    recording.run_script_sync(config.on_event_start)
                if config.on_motion_detected:
                    recording.run_script_sync(config.on_motion_detected)

            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.getLogger(__name__).exception("Capture loop %d error: %s", camera_index, e)
            await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start cameras, load config, run capture loops; on exit stop cameras."""
    global app_state
    _setup_logging(logging.INFO)
    log = logging.getLogger(__name__)

    # Resolve config DB path first so we load and save from the same place.
    bootstrap_target = get_bootstrap_target_dir()
    db_path = ensure_config_dir_and_db(None, bootstrap_target)
    global_config = load_global_config(db_path=db_path)
    auth.ensure_default_user(db_path)
    global_config.target_dir = global_config.target_dir or str(Path.cwd() / "data")
    target_dir = Path(global_config.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    camera_configs = [load_camera_config(0, db_path=db_path), load_camera_config(1, db_path=db_path)]
    detection_paused = [camera_configs[0].pause or False, camera_configs[1].pause or False]
    current_event_id: list[Optional[int]] = [None, None]
    current_event_path: list[Optional[str]] = [None, None]  # path opened for HW recording (for DB log)
    latest_jpeg: list[Optional[bytes]] = [None, None]
    camera_failed = [False, False]  # set True when a camera times out repeatedly; that camera is then skipped

    camera_service = None
    camera_started = [False, False]  # which cameras actually started (e.g. only 0 if one camera)
    if DualCameraService is not None:
        camera_service = DualCameraService()
        w0, h0 = camera_configs[0].width or 640, camera_configs[0].height or 480
        w1, h1 = camera_configs[1].width or 640, camera_configs[1].height or 480
        fps0 = camera_configs[0].framerate or 15
        fps1 = camera_configs[1].framerate or 15
        pre0 = (camera_configs[0].pre_capture or 0) / max(1, fps0)
        pre1 = (camera_configs[1].pre_capture or 0) / max(1, fps1)
        cam0_ok, cam1_ok = camera_service.start(
            w0, h0, fps0,
            w1, h1, fps1,
            pre_capture_sec0=max(1.0, pre0) if pre0 > 0 else 2.0,
            pre_capture_sec1=max(1.0, pre1) if pre1 > 0 else 2.0,
        )
        camera_started[0], camera_started[1] = cam0_ok, cam1_ok
        if not cam0_ok and not cam1_ok:
            log.warning("No cameras started; check connections.")
        elif not cam1_ok:
            log.info("Camera 1 not available (single-camera setup or not connected).")
    else:
        log.warning("DualCameraService not available; capture disabled")

    motion_detectors = [
        MotionDetector(
            threshold=camera_configs[0].threshold or 1500,
            threshold_maximum=camera_configs[0].threshold_maximum or 0,
            noise_level=camera_configs[0].noise_level or 32,
            despeckle_filter=camera_configs[0].despeckle_filter,
            minimum_motion_frames=camera_configs[0].minimum_motion_frames or 1,
        ),
        MotionDetector(
            threshold=camera_configs[1].threshold or 1500,
            threshold_maximum=camera_configs[1].threshold_maximum or 0,
            noise_level=camera_configs[1].noise_level or 32,
            despeckle_filter=camera_configs[1].despeckle_filter,
            minimum_motion_frames=camera_configs[1].minimum_motion_frames or 1,
        ),
    ]

    def save_global(c: GlobalConfig) -> None:
        save_global_config(c, db_path=db_path)

    def save_camera(i: int, c: CameraConfig) -> None:
        save_camera_config(i, c, db_path=db_path)

    app_state.update({
        "global_config": global_config,
        "camera_configs": camera_configs,
        "db_path": db_path,
        "target_dir": target_dir,
        "camera_service": camera_service,
        "detection_paused": detection_paused,
        "current_event_id": current_event_id,
        "current_event_path": current_event_path,
        "latest_jpeg": latest_jpeg,
        "camera_failed": camera_failed,
        "snapshot_requests": {},  # camera_index -> list of pending on-demand snapshots
        "save_global_config": save_global,
        "save_camera_config": save_camera,
        "base_url": "http://localhost:8080",
    })

    tasks = []
    if camera_service is not None:
        for i in range(2):
            if camera_started[i]:
                t = asyncio.create_task(
                    _capture_loop(
                        i,
                        camera_service,
                        motion_detectors[i],
                        camera_configs[i],
                        global_config,
                        target_dir,
                        db_path,
                        app_state,
                    )
                )
                tasks.append(t)
    log.info("Hootcam Server started; API at /docs")

    yield

    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    if camera_service is not None:
        camera_service.stop()
    log.info("Hootcam Server stopped")


app = FastAPI(
    title="Hootcam Server",
    description="""
Owl box camera server for Raspberry Pi 5: dual CSI cameras, motion detection,
and motion-triggered recording to SSD.

Use **global config** for target directory, logging, and stream settings;
use **per-camera config** for resolution, framerate, motion detection (threshold, noise_level,
event_gap, pre_capture, post_capture), picture/movie output, and script hooks.
    """,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# CORS: reflect the request's Origin so credentials work from any UI origin (browsers forbid * with credentials).
class CORSReflectMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        origin = request.headers.get("origin")
        if request.method == "OPTIONS":
            response = Response(status_code=200)
        else:
            response = await call_next(request)
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Expose-Headers"] = "*"
        if request.method == "OPTIONS":
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
            response.headers["Access-Control-Max-Age"] = "600"
        return response


app.add_middleware(CORSReflectMiddleware)

app.include_router(router)
