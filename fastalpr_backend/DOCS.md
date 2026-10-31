# FastALPR inference backend

The app provides the FastALPR Python/ONNX runtime in a separately managed Debian-based container. Its port is available on the Home Assistant internal network only. Do not expose the HTTP service to the public internet.

To test stored images, send `POST /scan-media` with a JSON list of relative paths beneath Home Assistant's `/media` directory.

The native Home Assistant integration calls `POST /recognize` with the binary content of a camera image. The app returns detections and confidence values, while Home Assistant stores the private known-registration list and performs matching.

The first inference may take additional time while weights are downloaded.

The backend uses the `yolo-v9-t-640-license-plate-end2end` detector. Its 640-pixel
input preserves substantially more detail than the 384-pixel variant for small,
glare-affected plates; no fixed crop is applied because camera framing differs.

## Correlated recognition diagnostics

`POST /recognize` accepts optional `X-Capture-ID` (32 lowercase hexadecimal
characters) and `X-Frame-Index` (zero-based index) headers. Invalid correlation
values are labelled unattributed/unknown, never echoed verbatim into logs.
Responses retain `detections` and add `timings`: `body_read_ms`, `queue_ms`,
`model_ms` and `total_ms`. Total runs from handler entry until response creation;
model time measures the prediction call and queue time measures model-lock wait.
One INFO log line records the correlation, outcome, counts, confidence values and
timings. Plate text is returned to HA but never written to backend logs.

Enable integration capture auditing to retain original frames and complete OCR
results in HA Media; the backend still uses temporary files only.
