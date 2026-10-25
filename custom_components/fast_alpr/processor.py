"""Pure ALPR recognition evaluation; testable without Home Assistant."""
from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLATE_PATTERN = re.compile(r"^[A-Z0-9]{3,12}$")


@dataclass(frozen=True)
class Match:
    plate: str
    vehicle: str
    confidence: float
    matching_frames: int


def normalize_plate(value: str) -> str:
    """Normalise human-entered and OCR plates identically."""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def parse_known_plates(raw: str) -> dict[str, str]:
    """Accept REGISTRATION=Name; spaces and hyphens in plates are ignored."""
    vehicles: dict[str, str] = {}
    for line in raw.replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError("Use REGISTRATION=Vehicle name (one per line)")
        plate_raw, name = line.split("=", 1)
        plate = normalize_plate(plate_raw)
        name = name.strip()
        if not PLATE_PATTERN.fullmatch(plate) or not 1 <= len(name) <= 80:
            raise ValueError("Registrations must be 3–12 letters/digits and names 1–80 characters")
        if plate in vehicles:
            raise ValueError("Duplicate registration after normalisation")
        vehicles[plate] = name
    return vehicles


def confidence_of(result: Any) -> float:
    confidence = getattr(result, "confidence", 0.0)
    if isinstance(confidence, (tuple, list)):
        confidence = statistics.mean(confidence) if confidence else 0.0
    return float(confidence or 0.0)


def read_batch(model: Any, paths: list[Path], known: dict[str, str],
               min_confidence: float, min_matching_frames: int) -> list[Match]:
    """Require independent source frames to agree before reporting a vehicle."""
    observations: dict[str, list[float]] = defaultdict(list)
    for path in paths:
        per_image: dict[str, float] = {}
        for result in model.predict(str(path)):
            ocr = getattr(result, "ocr", None)
            if ocr is None:
                continue
            plate = normalize_plate(getattr(ocr, "text", ""))
            if plate not in known:
                continue
            score = confidence_of(ocr)
            if score >= min_confidence:
                per_image[plate] = max(score, per_image.get(plate, 0.0))
        for plate, score in per_image.items():
            observations[plate].append(score)
    return [
        Match(plate=plate, vehicle=known[plate],
              confidence=max(scores), matching_frames=len(scores))
        for plate, scores in observations.items()
        if len(scores) >= min_matching_frames
    ]


def match_frames(
    frames: list[list[dict]],
    known: dict[str, str],
    min_confidence: float,
    min_matching_frames: int,
) -> list[Match]:
    """Match raw recogniser results across independent frames.

    The private known-registration list never leaves Home Assistant Core.
    """
    observations: dict[str, list[float]] = defaultdict(list)
    for detections in frames:
        per_frame: dict[str, float] = {}
        for detection in detections:
            plate = normalize_plate(detection.get("plate", ""))
            if plate not in known:
                continue
            confidence = float(detection.get("confidence", 0))
            if confidence >= min_confidence:
                per_frame[plate] = max(confidence, per_frame.get(plate, 0.0))
        for plate, confidence in per_frame.items():
            observations[plate].append(confidence)
    return [
        Match(plate=plate, vehicle=known[plate], confidence=max(scores),
              matching_frames=len(scores))
        for plate, scores in observations.items()
        if len(scores) >= min_matching_frames
    ]
