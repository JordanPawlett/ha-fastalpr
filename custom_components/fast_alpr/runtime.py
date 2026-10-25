"""Event-driven camera captures and local FastALPR inference."""
from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from homeassistant.components.camera import async_get_image
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_CAMERA, CONF_COOLDOWN_SECONDS, CONF_FRAMES, CONF_INTERVAL_MS,
    CONF_KNOWN_PLATES, CONF_MATCH_EVENT, CONF_MIN_CONFIDENCE,
    CONF_MIN_MATCHING_FRAMES, CONF_SIGNAL_SECONDS, DEFAULT_MODEL, DEFAULT_OCR,
)
from .processor import parse_known_plates, read_batch

_LOGGER = logging.getLogger(__name__)


class FastAlprRuntime:
    """Scoped to one camera/configuration entry."""

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
        self.model = None
        self.task: asyncio.Task | None = None
        self._closed = False
        self.last_sent: dict[str, float] = {}
        self.last_error: str | None = None
        self.last_vehicle: str | None = None
        self.last_plate: str | None = None
        self.last_processed: str | None = None
        self.last_seen: datetime | None = None
        self.last_confidence: float | None = None
        self.last_matching_frames: int | None = None
        self.detected = False
        self.processed_batches = 0
        self._cancel_reset = None

    @property
    def options(self):
        return {**self.entry.data, **self.entry.options}

    @callback
    def notify(self):
        async_dispatcher_send(self.hass, f"fast_alpr_updated_{self.entry.entry_id}")

    @callback
    def reset_detected(self, now=None):
        self.detected = False
        self._cancel_reset = None
        self.notify()

    @callback
    def start_capture(self):
        """Coalesce triggers while a capture or model inference is active."""
        if self._closed or (self.task is not None and not self.task.done()):
            return
        self.task = self.hass.async_create_task(self.capture_and_recognise())

    def _sync_recognise(self, images, options):
        """Execute blocking model inference using short-lived local files."""
        if self.model is None:
            from fast_alpr import ALPR
            self.model = ALPR(detector_model=DEFAULT_MODEL, ocr_model=DEFAULT_OCR)
        known = parse_known_plates(options[CONF_KNOWN_PLATES])
        with tempfile.TemporaryDirectory(prefix="ha_fastalpr_") as tmp:
            paths = []
            for index, (mime, content) in enumerate(images, start=1):
                suffix = ".png" if mime == "image/png" else ".jpg"
                path = Path(tmp) / f"frame_{index:02d}{suffix}"
                path.write_bytes(content)
                paths.append(path)
            return read_batch(
                self.model, paths, known,
                float(options[CONF_MIN_CONFIDENCE]),
                int(options[CONF_MIN_MATCHING_FRAMES]),
            )

    async def capture_and_recognise(self):
        """Capture from the configured camera without persistent sample storage."""
        options = self.options
        capture_id = uuid4().hex
        try:
            images = []
            frames = int(options[CONF_FRAMES])
            for index in range(frames):
                image = await async_get_image(
                    self.hass, options[CONF_CAMERA], timeout=15
                )
                if image.content_type not in ("image/jpeg", "image/png"):
                    raise ValueError("Camera returned an unsupported image format")
                if not image.content:
                    raise ValueError("Camera returned an empty image")
                images.append((image.content_type, image.content))
                if index + 1 < frames:
                    await asyncio.sleep(int(options[CONF_INTERVAL_MS]) / 1000)

            matches = await self.hass.async_add_executor_job(
                self._sync_recognise, images, options
            )
            self.last_error = None
            self.last_processed = capture_id
            self.processed_batches += 1
            now = time.monotonic()
            for match in matches:
                if now - self.last_sent.get(match.plate, float("-inf")) < int(
                    options[CONF_COOLDOWN_SECONDS]
                ):
                    continue
                self.last_sent[match.plate] = now
                self.last_vehicle = match.vehicle
                self.last_plate = match.plate
                self.last_confidence = match.confidence
                self.last_matching_frames = match.matching_frames
                self.last_seen = datetime.now(timezone.utc)
                self.detected = True
                if self._cancel_reset:
                    self._cancel_reset()
                self._cancel_reset = async_call_later(
                    self.hass, int(options[CONF_SIGNAL_SECONDS]),
                    self.reset_detected,
                )
                self.hass.bus.async_fire(
                    options[CONF_MATCH_EVENT],
                    {
                        "vehicle": match.vehicle,
                        "plate": match.plate,
                        "confidence": round(match.confidence, 4),
                        "matching_frames": match.matching_frames,
                        "camera": options[CONF_CAMERA],
                        "capture_id": capture_id,
                        "timestamp": self.last_seen.isoformat(),
                    },
                )
            self.notify()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            _LOGGER.exception("FastALPR capture or inference failed")
            self.notify()

    async def close(self):
        self._closed = True
        if self._cancel_reset:
            self._cancel_reset()
            self._cancel_reset = None
        if self.task and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
