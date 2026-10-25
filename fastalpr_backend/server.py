"""Local, isolated FastALPR inference API. No network ports exposed on the host."""
from __future__ import annotations

import json
import logging
import os
import re
import statistics
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8099
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_BATCH_IMAGES = 20
MEDIA_DIR = Path("/media").resolve()
LOGGER = logging.getLogger("fastalpr_backend")
_MODEL = None
_MODEL_LOCK = threading.Lock()


def _model():
    global _MODEL
    if _MODEL is None:
        from fast_alpr import ALPR
        _MODEL = ALPR(
            detector_model="yolo-v9-t-384-license-plate-end2end",
            ocr_model="cct-s-v2-global-model",
        )
    return _MODEL


def _normalize_plate(raw):
    return re.sub(r"[^A-Z0-9]", "", str(raw).upper())


def _confidence(ocr):
    score = getattr(ocr, "confidence", 0.0)
    if isinstance(score, (list, tuple)):
        score = statistics.mean(score) if score else 0.0
    return float(score or 0.0)


def _recognize_file(file_path: Path):
    """Serialise predictions to bound CPU and avoid model thread-safety assumptions."""
    with _MODEL_LOCK:
        predictions = _model().predict(str(file_path))
    detections = []
    for prediction in predictions:
        ocr = getattr(prediction, "ocr", None)
        if ocr is None:
            continue
        plate = _normalize_plate(getattr(ocr, "text", ""))
        if plate:
            detections.append({
                "plate": plate,
                "confidence": round(_confidence(ocr), 5),
            })
    return detections


def _media_file(relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or relative_path.startswith("/"):
        raise ValueError("Supply a relative media path")
    location = (MEDIA_DIR / relative_path).resolve(strict=True)
    if not location.is_relative_to(MEDIA_DIR) or not location.is_file():
        raise ValueError("File must be inside the read-only media directory")
    if location.suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise ValueError("Only JPEG and PNG images are accepted")
    if location.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds size limit")
    return location


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        try:
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdigit():
                raise ValueError("Missing Content-Length")
            length = int(raw_length)
            if self.path == "/recognize":
                if not 1 <= length <= MAX_IMAGE_BYTES:
                    raise ValueError("Image size outside supported range")
                data = self.rfile.read(length)
                jpeg = data.startswith(b"\\xff\\xd8\\xff")
                png = data.startswith(b"\\x89PNG\\r\\n\\x1a\\n")
                if not (jpeg or png):
                    raise ValueError("Expected a JPEG or PNG image")
                with tempfile.TemporaryDirectory(prefix="alpr_") as root:
                    path = Path(root) / ("frame.jpg" if jpeg else "frame.png")
                    path.write_bytes(data)
                    detections = _recognize_file(path)
                self._json(200, {"detections": detections})
            elif self.path == "/scan-media":
                if not 1 <= length <= MAX_JSON_BYTES:
                    raise ValueError("Request size outside supported range")
                obj = json.loads(self.rfile.read(length))
                paths = obj.get("paths")
                if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_BATCH_IMAGES:
                    raise ValueError("Supply between 1 and 20 media paths")
                # Validate every path before starting any model predictions.
                safe_paths = [_media_file(p) for p in paths]
                frames = [_recognize_file(p) for p in safe_paths]
                self._json(200, {"frames": frames})
            else:
                self._json(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError, FileNotFoundError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception:
            LOGGER.exception("Inference request failed")
            self._json(503, {"error": "inference unavailable"})

    def log_message(self, fmt, *args):
        # Never log raw payloads or recognised registrations.
        LOGGER.info("%s %s", self.command, self.path.split("?")[0])


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs("/data/cache", exist_ok=True)
    LOGGER.info("Starting FastALPR backend on internal port %s", PORT)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
