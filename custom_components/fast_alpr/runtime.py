"""Progressive camera recognition, with early-exit consensus and latency diagnostics."""
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
    EVENT_MATCH,
)
from .processor import PlateConsensus, parse_known_plates

_LOGGER = logging.getLogger(__name__)


class FastAlprRuntime:
    """One camera. The ONNX model remains in the separate Supervisor app."""

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
        self.last_latency_ms: int | None = None
        self.last_scan_ms: int | None = None
        self.last_capture_ms: int | None = None
        self.last_inference_ms: int | None = None
        self.detected = False
        self.backend_ready = False
        self.processed_batches = 0
        self._cancel_reset = None

    @property
    def options(self):
        value = {**self.entry.data, **self.entry.options}
        # Prefer the running, discovered Supervisor app, without exposing its URL
        # to users; legacy entries retain their existing URL as a safe fallback.
        discovered = self.hass.data.get("fast_alpr", {}).get(CONF_BACKEND_URL)
        override = self.entry.options.get(CONF_BACKEND_URL)
        if override:
            value[CONF_BACKEND_URL] = override
        elif discovered:
            value[CONF_BACKEND_URL] = discovered
        return value

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
        """Coalesce triggers while the previous scan remains active."""
        if self._closed or (self.task is not None and not self.task.done()):
            return
        self.task = self.hass.async_create_task(self.capture_and_recognise())

    async def refresh_backend_status(self):
        """Report health and model readiness without exposing backend details."""
        backend = self.options.get(CONF_BACKEND_URL)
        ready = False
        if backend:
            try:
                session = async_get_clientsession(self.hass)
                async with session.get(
                    backend.rstrip("/") + "/health",
                    timeout=ClientTimeout(total=4),
                ) as response:
                    if response.status == 200:
                        payload = await response.json()
                        ready = payload.get("model_ready") is True
            except Exception:
                pass
        if self.backend_ready != ready:
            self.backend_ready = ready
            self.notify()

    async def _recognise_frame(self, index: int, content: bytes, mime: str, endpoint: str):
        began = time.monotonic()
        session = async_get_clientsession(self.hass)
        async with session.post(
            endpoint, data=content,
            headers={"Content-Type": mime},
            timeout=ClientTimeout(total=8),
        ) as response:
            response.raise_for_status()
            data = await response.json()
        detections = data.get("detections")
        if not isinstance(detections, list):
            raise ValueError("Inference backend returned invalid detections")
        return index, detections, int((time.monotonic() - began) * 1000)

    def _publish(self, match, capture_id: str, options: dict, started: float,
                 capture_ms: int, inference_ms: int):
        """Publish a canonical event immediately after the required consensus."""
        now = time.monotonic()
        if now - self.last_sent.get(match.plate, float("-inf")) < int(options[CONF_COOLDOWN_SECONDS]):
            return False
        self.last_error = None
        self.last_sent[match.plate] = now
        self.last_vehicle = match.vehicle
        self.last_plate = match.plate
        self.last_confidence = match.confidence
        self.last_matching_frames = match.matching_frames
        self.last_seen = datetime.now(timezone.utc)
        self.detected = True
        self.last_latency_ms = round((now - started) * 1000)
        self.last_capture_ms = capture_ms
        self.last_inference_ms = inference_ms
        if self._cancel_reset:
            self._cancel_reset()
        self._cancel_reset = async_call_later(
            self.hass, int(options[CONF_SIGNAL_SECONDS]), self.reset_detected
        )
        event_data = {
            "vehicle": match.vehicle,
            "plate": match.plate,
            "confidence": round(match.confidence, 4),
            "matching_frames": match.matching_frames,
            "camera": options[CONF_CAMERA],
            "capture_id": capture_id,
            "timestamp": self.last_seen.isoformat(),
            "trigger_to_match_ms": self.last_latency_ms,
            "capture_ms": capture_ms,
            "inference_ms": inference_ms,
        }
        self.hass.bus.async_fire(EVENT_MATCH, event_data)
        # Preserve prior private notification automations without making
        # the custom event name a public configuration setting.
        legacy = options.get(CONF_MATCH_EVENT)
        if legacy and legacy != EVENT_MATCH:
            self.hass.bus.async_fire(legacy, event_data)
        self.notify()
        return True

    async def capture_and_recognise(self):
        """Capture and infer concurrently; stop immediately on two-frame consensus.

        trigger_to_match_ms starts when this callback is scheduled by the
        vehicle binary sensor. The camera's internal detection latency is not
        measurable here, so end-to-end 'car enters view' time is not claimed.
        """
        started = time.monotonic()
        options = self.options
        capture_id = uuid4().hex
        pending: set[asyncio.Task] = set()
        accumulated_capture_ms = 0
        accumulated_inference_ms = 0
        try:
            known = parse_known_plates(options[CONF_KNOWN_PLATES])
            backend = options.get(CONF_BACKEND_URL)
            if not backend:
                raise ValueError("FastALPR Inference app not discovered")
            consensus = PlateConsensus(
                known, float(options[CONF_MIN_CONFIDENCE]),
                int(options[CONF_MIN_MATCHING_FRAMES]),
            )
            endpoint = backend.rstrip("/") + "/recognize"

            async def consume(wait: float | None) -> bool:
                nonlocal accumulated_inference_ms
                if not pending:
                    if wait is not None and wait > 0:
                        await asyncio.sleep(wait)
                    return False
                done, _ = await asyncio.wait(
                    pending, timeout=wait,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for job in done:
                    pending.discard(job)
                    index, detections, spent = job.result()
                    accumulated_inference_ms += spent
                    for match in consensus.add(index, detections):
                        if self._publish(
                            match, capture_id, options, started,
                            accumulated_capture_ms, accumulated_inference_ms,
                        ):
                            return True
                return False

            count = int(options[CONF_FRAMES])
            spacing = int(options[CONF_INTERVAL_MS]) / 1000.0
            first_capture_started = None
            for index in range(count):
                if index > 0:
                    due = first_capture_started + spacing * index
                    while time.monotonic() < due:
                        if await consume(max(0, due - time.monotonic())):
                            return
                    if await consume(0):
                        return
                began = time.monotonic()
                if first_capture_started is None:
                    first_capture_started = began
                image = await async_get_image(self.hass, options[CONF_CAMERA], timeout=4)
                accumulated_capture_ms += int((time.monotonic() - began) * 1000)
                if image.content_type not in ("image/jpeg", "image/png") or not image.content:
                    raise ValueError("Camera did not return a valid still image")
                pending.add(self.hass.async_create_task(
                    self._recognise_frame(index, image.content, image.content_type, endpoint)
                ))
                if await consume(0):
                    return

            while pending:
                if await consume(None):
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            _LOGGER.exception("FastALPR scan failed")
        else:
            self.last_error = None
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            # Completed scans count even when no known registration was present.
            self.last_processed = capture_id
            self.last_scan_ms = round((time.monotonic() - started) * 1000)
            self.last_capture_ms = accumulated_capture_ms
            self.last_inference_ms = accumulated_inference_ms
            self.processed_batches += 1
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
