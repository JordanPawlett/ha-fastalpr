# FastALPR Inference

A minimal Home Assistant Supervisor app providing local CPU inference to the FastALPR custom integration.

- Runs on Debian/glibc, avoiding Alpine/musllinux incompatibility with ONNX Runtime.
- No ports are exposed on the host network.
- Native Supervisor start-on-boot and health watchdog.
- `GET /health`: server liveness.
- `POST /recognize`: raw JPEG/PNG body (Home Assistant integration).
- `POST /scan-media`: JSON `{"paths":["anpr/example/sample_01.jpg"]}`, relative to `/media`, for testing saved frames.
- Read-only mount for Home Assistant Media. Captured images and registrations are not sent to any external API.

Use the Supervisor app slug with underscores replaced by hyphens as the internal hostname, such as `http://<repo-prefix>-fastalpr-backend:8099`. The hostname is discoverable in the Supervisor Apps listing after installation.

Models download on first inference and are cached within the app's container (may need to redownload on reinstall).
