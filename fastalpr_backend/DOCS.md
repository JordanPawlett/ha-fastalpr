# FastALPR inference backend

The app provides the FastALPR Python/ONNX runtime in a separately managed Debian-based container. Its port is available on the Home Assistant internal network only. Do not expose the HTTP service to the public internet.

To test stored images, send `POST /scan-media` with a JSON list of relative paths beneath Home Assistant's `/media` directory.

The native Home Assistant integration calls `POST /recognize` with the binary content of a camera image. The app returns detections and confidence values, while Home Assistant stores the private known-registration list and performs matching.

The first inference may take additional time while weights are downloaded.
