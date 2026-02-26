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
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from . import database
from . import recording
from .api.routes import router
from .api.schemas import CameraConfig, GlobalConfig
from .config import (
    ensure_config_dir_and_db,
    load_camera_config,
    load_global_config,
    save_camera_config,
    save_global_config,
)
from .motion import MotionDetector

# Optional camera service (picamera2 on Pi only)
try:
    from .camera import DualCameraService
except ImportError:
    DualCameraService = None  # type: ignore

# Global app state (set in lifespan)
app_state: dict[str, Any] = {}


def _setup_logging(level: int) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


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
    """One loop per camera: capture -> motion -> record; update latest_jpeg."""
    from PIL import Image
    import io

    event_gap_sec = config.event_gap if config.event_gap is not None and config.event_gap >= 0 else 60
    post_capture = config.post_capture or 0
    pre_capture = config.pre_capture or 0
    pre_buffer: list[tuple[bytes, datetime]] = []
    recording_session: Optional[recording.RecordingSession] = None
    last_motion_at: Optional[datetime] = None
    post_frames_left = 0
    framerate = config.framerate or 15
    interval = 1.0 / framerate

    while True:
        try:
            if state["detection_paused"][camera_index]:
                arr = camera_service.capture_array(camera_index) if camera_service else None
                if arr is not None:
                    buf = io.BytesIO()
                    Image.fromarray(arr).save(buf, format="JPEG", quality=global_config.stream_quality or 50)
                    if state.get("latest_jpeg") is not None and camera_index < len(state["latest_jpeg"]):
                        state["latest_jpeg"][camera_index] = buf.getvalue()
                await asyncio.sleep(interval)
                continue

            arr = camera_service.capture_array(camera_index) if camera_service else None
            if arr is None:
                await asyncio.sleep(interval)
                continue

            jpeg_bytes = None
            try:
                buf = io.BytesIO()
                Image.fromarray(arr).save(buf, format="JPEG", quality=85)
                jpeg_bytes = buf.getvalue()
                if state.get("latest_jpeg") is not None and camera_index < len(state["latest_jpeg"]):
                    state["latest_jpeg"][camera_index] = jpeg_bytes
            except Exception:
                pass

            motion_detected, changed = motion_detector.update(arr)
            now = datetime.utcnow()

            if recording_session is not None:
                if motion_detected:
                    last_motion_at = now
                    post_frames_left = post_capture
                    recording_session.record_frame(jpeg_bytes or b"", now)
                elif post_frames_left > 0:
                    post_frames_left -= 1
                    recording_session.record_frame(jpeg_bytes or b"", now)
                else:
                    # End event after event_gap with no motion
                    if last_motion_at and (now - last_motion_at).total_seconds() >= event_gap_sec:
                        database.log_event_end(db_path, state["current_event_id"][camera_index], now.strftime("%Y-%m-%d %H:%M:%S"))
                        recording_session.end_event(now)
                        if config.on_event_end:
                            recording.run_script_sync(config.on_event_end)
                        state["current_event_id"][camera_index] = None
                        recording_session = None
                        last_motion_at = None

            elif motion_detected:
                # Start new event
                started_at_str = now.strftime("%Y-%m-%d %H:%M:%S")
                event_id = database.log_event_start(db_path, camera_index, config.camera_id, started_at_str)
                state["current_event_id"][camera_index] = event_id
                recording_session = recording.RecordingSession(
                    camera_index,
                    config,
                    target_dir,
                    db_path,
                    event_id,
                    on_movie_end_script=config.on_movie_end,
                    on_picture_save_script=config.on_picture_save,
                )
                for jb, ts in pre_buffer:
                    recording_session.add_pre_capture_frame(jb, ts)
                pre_buffer.clear()
                recording_session.start_event(now)
                recording_session.record_frame(jpeg_bytes or b"", now)
                last_motion_at = now
                post_frames_left = post_capture
                if config.on_event_start:
                    recording.run_script_sync(config.on_event_start)
                if config.on_motion_detected:
                    recording.run_script_sync(config.on_motion_detected)
            else:
                # Not in event: keep pre_capture buffer
                if pre_capture > 0 and jpeg_bytes:
                    pre_buffer.append((jpeg_bytes, now))
                    if len(pre_buffer) > pre_capture:
                        pre_buffer.pop(0)

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

    global_config = load_global_config()
    db_path = ensure_config_dir_and_db(None, global_config.target_dir)
    auth.ensure_default_user(db_path)
    global_config.target_dir = global_config.target_dir or str(Path.cwd() / "data")
    target_dir = Path(global_config.target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    camera_configs = [load_camera_config(0, db_path=db_path), load_camera_config(1, db_path=db_path)]
    detection_paused = [camera_configs[0].pause or False, camera_configs[1].pause or False]
    current_event_id: list[Optional[int]] = [None, None]
    latest_jpeg: list[Optional[bytes]] = [None, None]

    camera_service = None
    if DualCameraService is not None:
        camera_service = DualCameraService()
        w0, h0 = camera_configs[0].width or 640, camera_configs[0].height or 480
        w1, h1 = camera_configs[1].width or 640, camera_configs[1].height or 480
        camera_service.start(
            w0, h0, camera_configs[0].framerate or 15,
            w1, h1, camera_configs[1].framerate or 15,
        )
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
        "latest_jpeg": latest_jpeg,
        "save_global_config": save_global,
        "save_camera_config": save_camera,
        "base_url": "http://localhost:8080",
    })

    tasks = []
    if camera_service is not None:
        for i in range(2):
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
