"""Event-driven camera snapshots and isolated FastALPR inference."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone
from uuid import uuid4

from aiohttp import ClientTimeout
from homeassistant.components.camera import async_get_image
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .const import (
    CONF_BACKEND_URL, CONF_CAMERA, CONF_COOLDOWN_SECONDS, CONF_FRAMES,
    CONF_INTERVAL_MS, CONF_KNOWN_PLATES, CONF_MATCH_EVENT,
    CONF_MIN_CONFIDENCE, CONF_MIN_MATCHING_FRAMES, CONF_SIGNAL_SECONDS,
)
from .processor import match_frames, parse_known_plates

_LOGGER = logging.getLogger(__name__)


class FastAlprRuntime:
    """One Home Assistant entry, one selected camera, no ONNX in Core."""

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
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
        """Drop overlapping triggers while capture or inference is active."""
        if self._closed or (self.task is not None and not self.task.done()):
            return
        self.task = self.hass.async_create_task(self.capture_and_recognise())

    async def capture_and_recognise(self):
        """Capture directly from the configured camera, without permanent files."""
        options = self.options
        capture_id = uuid4().hex
        try:
            known = parse_known_plates(options[CONF_KNOWN_PLATES])
            session = async_get_clientsession(self.hass)
            endpoint = options[CONF_BACKEND_URL].rstrip("/") + "/recognize"
            images = []
            frames = int(options[CONF_FRAMES])
            for index in range(frames):
                frame = await async_get_image(self.hass, options[CONF_CAMERA], timeout=15)
                if frame.content_type not in ("image/jpeg", "image/png"):
                    raise ValueError("Camera returned unsupported image format")
                if not frame.content:
                    raise ValueError("Camera returned an empty image")
                images.append(frame)
                if index + 1 < frames:
                    await asyncio.sleep(int(options[CONF_INTERVAL_MS]) / 1000)

            results = []
            for frame in images:
                async with session.post(
                    endpoint,
                    data=frame.content,
                    headers={"Content-Type": frame.content_type},
                    timeout=ClientTimeout(total=180),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                detections = payload.get("detections")
                if not isinstance(detections, list):
                    raise ValueError("Inference backend returned invalid response")
                results.append(detections)

            matches = match_frames(
                results, known, float(options[CONF_MIN_CONFIDENCE]),
                int(options[CONF_MIN_MATCHING_FRAMES]),
            )
            self.last_error = None
            self.last_processed = capture_id
            self.processed_batches += 1
            now = time.monotonic()
            for match in matches:
                if now - self.last_sent.get(match.plate, float("-inf")) < int(options[CONF_COOLDOWN_SECONDS]):
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
                    self.hass, int(options[CONF_SIGNAL_SECONDS]), self.reset_detected
                )
                self.hass.bus.async_fire(options[CONF_MATCH_EVENT], {
                    "vehicle": match.vehicle,
                    "plate": match.plate,
                    "confidence": round(match.confidence, 4),
                    "matching_frames": match.matching_frames,
                    "camera": options[CONF_CAMERA],
                    "capture_id": capture_id,
                    "timestamp": self.last_seen.isoformat(),
                })
            self.notify()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            _LOGGER.exception("FastALPR capture/inference failed")
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
