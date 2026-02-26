"""
MJPEG streaming for live camera feeds.

Serves multipart/x-mixed-replace streams per camera so a frontend can
embed GET /cameras/0/stream and GET /cameras/1/stream.
When no frame is available (no camera or not yet), yields a placeholder
so the client always receives a valid stream and does not hang on "Connecting...".
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import AsyncIterator, Callable, Optional

logger = logging.getLogger(__name__)

BOUNDARY = "frame"

_placeholder_jpeg: Optional[bytes] = None


def _get_placeholder_jpeg() -> bytes:
    """Return a small grey placeholder JPEG (320x240) when no camera frame is available."""
    global _placeholder_jpeg
    if _placeholder_jpeg is not None:
        return _placeholder_jpeg
    try:
        from PIL import Image
        img = Image.new("L", (320, 240), color=80)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50)
        _placeholder_jpeg = buf.getvalue()
        return _placeholder_jpeg
    except Exception as e:
        logger.warning("Could not create stream placeholder: %s", e)
        # Minimal 1x1 grey JPEG fallback (valid JPEG bytes)
        _placeholder_jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' \",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xfa\x9f\xff\xd9"
        )
        return _placeholder_jpeg


async def mjpeg_stream(
    frame_provider: Callable[[int], Optional[bytes]],
    camera_index: int,
    quality: int = 50,
    max_fps: float = 10.0,
) -> AsyncIterator[bytes]:
    """
    Yield MJPEG multipart response body chunks.
    frame_provider(camera_index) -> bytes | None (JPEG).
    When None, yields a placeholder JPEG so the client always gets a valid stream.
    """
    interval = 1.0 / max_fps if max_fps > 0 else 0.1
    while True:
        try:
            jpeg = frame_provider(camera_index)
            if jpeg is None:
                jpeg = _get_placeholder_jpeg()
            part = (
                f"--{BOUNDARY}\r\n"
                "Content-Type: image/jpeg\r\n"
                f"Content-Length: {len(jpeg)}\r\n\r\n"
            )
            yield part.encode("ascii") + (bytes(jpeg) if not isinstance(jpeg, bytes) else jpeg) + b"\r\n"
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Stream frame error: %s", e)
            await asyncio.sleep(interval)
