"""Progressive camera recognition, with early-exit consensus and latency diagnostics."""
from __future__ import annotations

import asyncio
import logging
import hashlib
import json
from pathlib import Path
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
    EVENT_MATCH, EVENT_UNKNOWN_MATCH,
)
from .processor import PlateConsensus, parse_known_plates, normalize_plate, PLATE_PATTERN
from .audit import save_scan
from .const import CONF_CAPTURE_AUDIT, CONF_AUDIT_KEEP_SCANS, DEFAULT_AUDIT_KEEP_SCANS

_LOGGER = logging.getLogger(__name__)


class FastAlprRuntime:
    """One camera. The ONNX model remains in the separate Supervisor app."""

    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
        self.task: asyncio.Task | None = None
        self._closed = False
        self.last_sent: dict[str, float] = {}
        self.last_scan_outcome = None
        self.last_audit_path = None
        self.last_audit_error = None
        self.last_scan_report = None
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
            _LOGGER.debug("Scan trigger coalesced entry=%s closed=%s", self.entry.entry_id, self._closed)
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

    async def _recognise_frame(self, index: int, content: bytes, mime: str, endpoint: str, capture_id: str, frame: dict,
                               candidate_ids: dict, min_confidence: float):
        began = time.monotonic()
        frame["inference_requested_at"] = datetime.now(timezone.utc).isoformat()
        frame["status"] = "inference_pending"
        try:
            session = async_get_clientsession(self.hass)
            async with session.post(
                endpoint, data=content,
                headers={"Content-Type": mime, "X-Capture-ID": capture_id, "X-Frame-Index": str(index)},
                timeout=ClientTimeout(total=8),
            ) as response:
                response.raise_for_status()
                data = await response.json()
            frame["inference_received_at"] = datetime.now(timezone.utc).isoformat()
            frame["backend_timings"] = data.get("timings", {})
            detections = data.get("detections")
            if not isinstance(detections, list):
                raise ValueError("Inference backend returned invalid detections")
            # Record evidence even if consensus never consumes this task.
            frame["detections"] = []
            for detection in detections:
                plate = normalize_plate(detection.get("plate", ""))
                score = float(detection.get("confidence", 0))
                candidate = candidate_ids.setdefault(plate, len(candidate_ids) + 1)
                reason = ("invalid_plate" if not PLATE_PATTERN.fullmatch(plate) else
                          "below_confidence" if score < min_confidence else "accepted")
                frame["detections"].append({"plate": plate, "candidate": candidate,
                                            "confidence": score, "reason": reason})
            frame["status"] = "inference_completed"
            return index, detections, int((time.monotonic() - began) * 1000)
        except asyncio.CancelledError:
            frame["status"] = "inference_cancelled"
            raise
        except Exception as exc:
            frame["status"] = "inference_failed"
            frame["error_type"] = type(exc).__name__
            raise
        finally:
            frame["inference_finished_at"] = datetime.now(timezone.utc).isoformat()
            frame["inference_ms"] = int((time.monotonic() - began) * 1000)

    def _publish(self, match, capture_id: str, options: dict, started: float,
                 capture_ms: int, inference_ms: int):
        """Route validated plate to exactly ONE native event type.

        Known registrations retain the legacy event + binary sensor behaviour.
        Unknown registrations NEVER emit the known event or set known-vehicle
        sensors. Both kinds share per-registration cooldowns.
        """
        now = time.monotonic()
        if now - self.last_sent.get(match.plate, float("-inf")) < int(options[CONF_COOLDOWN_SECONDS]):
            return False
        self.last_sent[match.plate] = now
        self.last_error = None
        when = datetime.now(timezone.utc)
        latency_ms = round((now - started) * 1000)
        self.last_latency_ms = latency_ms
        self.last_capture_ms = capture_ms
        self.last_inference_ms = inference_ms
        event_data = {
            "plate": match.plate,  # ALWAYS normalized to uppercase A-Z0-9.
            "confidence": round(match.confidence, 4),
            "matching_frames": match.matching_frames,
            "camera": options[CONF_CAMERA],
            "capture_id": capture_id,
            "timestamp": when.isoformat(),
            "trigger_to_match_ms": latency_ms,
            "capture_ms": capture_ms,
            "inference_ms": inference_ms,
        }
        if match.vehicle:
            self.last_vehicle = match.vehicle
            self.last_plate = match.plate
            self.last_confidence = match.confidence
            self.last_matching_frames = match.matching_frames
            self.last_seen = when
            self.detected = True
            if self._cancel_reset:
                self._cancel_reset()
            self._cancel_reset = async_call_later(
                self.hass, int(options[CONF_SIGNAL_SECONDS]), self.reset_detected
            )
            event_data["vehicle"] = match.vehicle
            self.hass.bus.async_fire(EVENT_MATCH, event_data)
            async_dispatcher_send(
                self.hass, f"fast_alpr_match_{self.entry.entry_id}", event_data
            )
            legacy = options.get(CONF_MATCH_EVENT)
            if legacy and legacy != EVENT_MATCH:
                self.hass.bus.async_fire(legacy, event_data)
        else:
            # This path must never send a known event, even for a watchlisted
            # plate used by a downstream automation.
            self.hass.bus.async_fire(EVENT_UNKNOWN_MATCH, event_data)
            async_dispatcher_send(
                self.hass, f"fast_alpr_unknown_match_{self.entry.entry_id}", event_data
            )
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
        capture_task: asyncio.Task | None = None
        accumulated_capture_ms = 0
        accumulated_inference_ms = 0
        images = []
        report = {
            "capture_id": capture_id, "camera": options[CONF_CAMERA],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "frames": [], "outcome": "no_detections",
            "settings": {key: options[key] for key in (
                CONF_FRAMES, CONF_INTERVAL_MS, CONF_MIN_CONFIDENCE, CONF_MIN_MATCHING_FRAMES)},
        }
        self.last_audit_path = None
        self.last_audit_error = None
        candidate_ids = {}
        _LOGGER.info("Scan started capture_id=%s camera=%s settings=%s", capture_id, options[CONF_CAMERA], report["settings"])
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
                    # Prefer known registrations when several plates agree.
                    frame = report["frames"][index]
                    frame["status"] = "processed"
                    matches = consensus.add(index, detections)
                    frame["votes"] = {str(candidate_ids[p]): len(scores) for p, scores in consensus.observations.items()}
                    if report["outcome"] != "cooldown_suppressed":
                        if consensus.observations:
                            report["outcome"] = "insufficient_agreement"
                        elif detections:
                            report["outcome"] = "rejected_detections"
                    _LOGGER.debug("Frame result capture_id=%s frame=%s inference_ms=%s backend=%s candidates=%s votes=%s",
                                  capture_id, index + 1, spent, frame.get("backend_timings"),
                                  [{k: v for k, v in d.items() if k != "plate"} for d in frame["detections"]], frame["votes"])
                    for match in sorted(
                        matches, key=lambda candidate: not bool(candidate.vehicle)
                    ):
                        if self._publish(
                            match, capture_id, options, started,
                            accumulated_capture_ms, accumulated_inference_ms,
                        ):
                            report["outcome"] = "known_match" if match.vehicle else "unknown_match"
                            return True
                        report["outcome"] = "cooldown_suppressed"
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
                frame = {"index": index + 1, "snapshot_requested_at": datetime.now(timezone.utc).isoformat(),
                         "request_offset_ms": round((began - started) * 1000), "status": "capture_pending"}
                report["frames"].append(frame)
                _LOGGER.debug("Snapshot requested capture_id=%s frame=%s at=%s", capture_id, index + 1, frame["snapshot_requested_at"])
                async def capture_frame():
                    nonlocal accumulated_capture_ms
                    try:
                        return await async_get_image(self.hass, options[CONF_CAMERA], timeout=4)
                    except asyncio.CancelledError:
                        frame["status"] = "capture_cancelled"
                        raise
                    except Exception as exc:
                        frame["status"] = "capture_failed"
                        frame["error_type"] = type(exc).__name__
                        raise
                    finally:
                        frame["snapshot_finished_at"] = datetime.now(timezone.utc).isoformat()
                        frame["capture_ms"] = int((time.monotonic() - began) * 1000)
                        accumulated_capture_ms += frame["capture_ms"]

                capture_task = self.hass.async_create_task(capture_frame())
                while not capture_task.done():
                    await asyncio.wait(
                        pending | {capture_task}, return_when=asyncio.FIRST_COMPLETED,
                    )
                    # Consume recognition before reading the next snapshot result,
                    # even when both finish together (or that snapshot fails).
                    if await consume(0):
                        return
                image = capture_task.result()
                capture_task = None
                frame["snapshot_received_at"] = datetime.now(timezone.utc).isoformat()
                frame["bytes"] = len(image.content)
                frame["sha256"] = hashlib.sha256(image.content).hexdigest()
                frame["status"] = "captured"
                _LOGGER.debug("Snapshot received capture_id=%s frame=%s capture_ms=%s bytes=%s", capture_id, index + 1, frame["capture_ms"], frame["bytes"])
                if image.content_type not in ("image/jpeg", "image/png") or not image.content:
                    raise ValueError("Camera did not return a valid still image")
                if options.get(CONF_CAPTURE_AUDIT, False):
                    images.append((index, image.content_type, image.content))
                pending.add(self.hass.async_create_task(
                    self._recognise_frame(index, image.content, image.content_type, endpoint, capture_id, frame,
                                          candidate_ids, consensus.min_confidence)
                ))
                if await consume(0):
                    return

            while pending:
                if await consume(None):
                    return
        except asyncio.CancelledError:
            report["outcome"] = "cancelled"
            raise
        except Exception as exc:
            report["outcome"] = "error"
            report["error_type"] = type(exc).__name__
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            _LOGGER.exception("FastALPR scan failed capture_id=%s", capture_id)
        else:
            self.last_error = None
        finally:
            # Publish happens before cleanup; no unused snapshot may delay a match.
            cleanup_tasks = pending | ({capture_task} if capture_task is not None else set())
            for task in cleanup_tasks:
                task.cancel()
            if cleanup_tasks:
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
            # Include every finished request, including failures and cancellations.
            accumulated_inference_ms = sum(
                frame.get("inference_ms", 0) for frame in report["frames"])
            # Completed scans count even when no known registration was present.
            self.last_processed = capture_id
            self.last_scan_ms = round((time.monotonic() - started) * 1000)
            self.last_capture_ms = accumulated_capture_ms
            self.last_inference_ms = accumulated_inference_ms
            self.processed_batches += 1
            report.update(finished_at=datetime.now(timezone.utc).isoformat(), scan_ms=self.last_scan_ms,
                          capture_ms=accumulated_capture_ms, inference_ms=accumulated_inference_ms)
            for frame in report["frames"]:
                if frame["status"] in ("capture_pending", "captured", "inference_pending"):
                    frame["status"] = "unfinished_" + frame["status"]
            report["detection_count"] = sum(len(f.get("detections", [])) for f in report["frames"])
            report["max_agreeing_frames"] = max(
                (v for f in report["frames"] for v in f.get("votes", {}).values()), default=0)
            self.last_scan_outcome = report["outcome"]
            _LOGGER.info("Scan finished capture_id=%s outcome=%s frames=%s scan_ms=%s capture_ms=%s inference_ms=%s detections=%s max_agreeing_frames=%s",
                         capture_id, report["outcome"], len(report["frames"]), self.last_scan_ms,
                         accumulated_capture_ms, accumulated_inference_ms,
                         report["detection_count"], report["max_agreeing_frames"])
            # Retain a redacted in-memory report even when image auditing is off.
            self.last_scan_report = json.loads(json.dumps(report))
            for frame in self.last_scan_report["frames"]:
                for detection in frame.get("detections", []):
                    detection.pop("plate", None)
            if options.get(CONF_CAPTURE_AUDIT, False):
                # Off the event loop, after all capture and recognition has finished.
                root = Path("/media/fast_alpr") / self.entry.entry_id
                try:
                    self.last_audit_path = await self.hass.async_add_executor_job(
                        save_scan, root, report, images,
                        int(options.get(CONF_AUDIT_KEEP_SCANS, DEFAULT_AUDIT_KEEP_SCANS)))
                except Exception as exc:
                    self.last_audit_error = type(exc).__name__
                    _LOGGER.exception("Audit write failed capture_id=%s", capture_id)
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
