"""
MJPEG streaming for live camera feeds.

Serves multipart/x-mixed-replace streams per camera so a frontend can
embed GET /cameras/0/stream and GET /cameras/1/stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

BOUNDARY = "frame"

# Minimal valid 1x1 grey JPEG so we always send a frame when provider returns None
# (otherwise the client never receives any data and stays on "Connecting…")
_MINIMAL_JPEG = (
    b"\xff\xd8\xff\xdb\x00C\x00\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01"
    b"\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01"
    b"\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01"
    b"\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\x01\xff\xc2\x00\x0b"
    b"\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x14\x00\x01\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x03\xff\xda\x00\x08"
    b"\x01\x01\x00\x00\x00\x01?\xff\xd9"
)


async def mjpeg_stream(
    frame_provider: Callable[[int], Optional[bytes]],
    camera_index: int,
    quality: int = 50,
    max_fps: float = 10.0,
) -> AsyncIterator[bytes]:
    """
    Yield MJPEG multipart response body chunks.
    frame_provider(camera_index) -> bytes | None. When None, yield minimal placeholder so client always receives data.
    """
    interval = 1.0 / max_fps if max_fps > 0 else 0.1
    while True:
        try:
            jpeg = frame_provider(camera_index)
            if not jpeg:
                jpeg = _MINIMAL_JPEG
            part = (
                f"--{BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg)}\r\n\r\n"
            )
            yield part.encode("ascii") + jpeg + b"\r\n"
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Stream frame error: %s", e)
            await asyncio.sleep(interval)
