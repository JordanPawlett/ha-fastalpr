# FastALPR for Home Assistant

[![Validate](https://github.com/JordanPawlett/ha-fastalpr/actions/workflows/validate.yml/badge.svg)](https://github.com/JordanPawlett/ha-fastalpr/actions/workflows/validate.yml)

Local, on-demand automatic licence plate recognition using [FastALPR](https://github.com/ankandrew/fast-alpr). **Choose a Home Assistant camera directly from a dropdown**, optionally select a vehicle-detection binary sensor, and list the registrations you want to recognise. No NVR, MQTT, separate container, cloud recognition API, or snapshot-collection automation required.

> **Early preview.** Native ONNX dependency installation and real-camera inference have not yet been validated on every Home Assistant platform. FastALPR runs inside Home Assistant Core: a native library fault can affect Core. Do not use ALPR as the sole authentication mechanism for a lock, barrier, or other access control.

## How it works

```text
Vehicle binary sensor switches on (optional)
                    │
                    ▼
FastALPR captures stills from your chosen camera
                    │
                    ▼
Pretrained local detection + OCR (CPU, ONNX)
                    │
                    ▼
Normalised exact match against your configured plate list
                    │
                    ▼
HA binary sensor + last vehicle/plate sensors + fast_alpr_match event
```

When a detection occurs, FastALPR captures five stills by default, spaces them 750 ms apart, and requires agreement across at least two different images. Capture happens through Home Assistant's camera API, without specifying a snapshot directory or writing a persistent image dataset. Temporary inference files are deleted after processing. No constant video stream or directory watcher is needed. Cameras must support still-image retrieval; some stream-only cameras may need separate snapshot support.

Initial model download requires internet access; afterwards the inference itself runs locally.

## Installation

1. Install [HACS](https://www.hacs.xyz/) if needed.
2. Add `https://github.com/JordanPawlett/ha-fastalpr` in **HACS → Custom repositories**, category **Integration**.
3. Install **FastALPR**, then restart Home Assistant.
4. Under **Settings → Devices & services → Add integration**, choose **FastALPR**.
5. Select a camera from the entity dropdown; prefer its highest-resolution still-image source.
6. Optionally select a binary sensor that switches **on** when a vehicle is detected.
7. Enter known registrations and adjust capture/recognition settings as needed.

The integration supports one entry per camera and can be configured multiple times for different cameras. Without a detection sensor, initiate scans manually through the `fast_alpr.scan` action or an automation of your choice.

### Known registrations: spaces are supported

Use one `REGISTRATION=Vehicle name` per line:

```text
ZZ99 ZZZ=Example vehicle
AA00 AAA=Second example
```

**Whitespace, hyphens, and letter case in the registration are ignored** for matching, so `ZZ99 ZZZ`, `ZZ99ZZZ` and `zz99zzz` match the same plate. The friendly vehicle name is whatever follows the first `=`, including spaces. A duplicate registration in a different format is rejected. Matching is exact after normalisation (no fuzzy O/0 or I/1 substitutions). Configure real registrations **only in the private Home Assistant UI**, never in a public repository or issue.

## Running recognition

### Automatic

Set **Vehicle detection binary sensor** to an existing `binary_sensor` that transitions to `on` when a vehicle is detected. Every such transition starts one camera-capture burst. Repeated triggers during the active burst are coalesced. A per-plate cooldown suppresses repeated match notifications after recognition.

Any binary sensor can be selected in the UI; it need not be supplied by the same integration as the camera. The sensor must actually switch `off` and back `on` to cause a new scan.

### Manual or from your own automation

Leave the detection binary sensor empty and call the `fast_alpr.scan` action with the configured camera:

```yaml
action: fast_alpr.scan
data:
  camera: camera.example_camera
```

This also allows custom triggers (button press, motion event, time schedule, etc.), while keeping capture and OCR entirely within the integration. No `camera.snapshot` YAML is required.

## Configuration options

| Option | Default | Description |
| --- | --- | --- |
| Camera | Required | Camera entity selected from Home Assistant's dropdown |
| Vehicle detection binary sensor | Optional | State changing to `on` triggers a scan |
| Known plates | Required | Newline-separated registration-to-vehicle mappings |
| Match event | `fast_alpr_match` | Home Assistant event emitted for a confirmed match |
| Images per scan | 5 | Number of stills captured from the selected camera |
| Milliseconds between images | 750 | Time between captures |
| Minimum agreeing images | 2 | Number of distinct images reading the same registration |
| Minimum OCR confidence | 0.75 | Raw OCR confidence, not a calibrated probability |
| Repeat detection cooldown | 60 seconds | Suppression interval per registration |
| Signal duration | 30 seconds | Time the recognition binary sensor remains on |

Model execution is scheduled in Home Assistant's executor to avoid blocking the event loop. The integration has diagnostic sensors for processed scans and the last error; no independent process watchdog is available because it runs within Core.

## Home Assistant output

The integration creates a **Known vehicle detected** binary sensor, **Last recognised vehicle** and **Last recognised plate** sensors, plus diagnostic **Processed batches** and **Last error** sensors.

When a configured registration matches, it emits `fast_alpr_match` (or your configured event name) with:

```json
{
  "vehicle": "Example vehicle",
  "plate": "ZZ99ZZZ",
  "confidence": 0.94,
  "matching_frames": 3,
  "camera": "camera.example_camera",
  "capture_id": "example-unique-id",
  "timestamp": "2026-10-25T12:00:00+00:00"
}
```

These are fictional illustrative values. Use the event in your own automations:

```yaml
alias: Notify on a known vehicle
triggers:
  - trigger: event
    event_type: fast_alpr_match
actions:
  - action: notify.notify
    data:
      message: "Recognised {{ trigger.event.data.vehicle }}"
```

**Security:** number plates can be copied and OCR can be mistaken. Do not open gates or unlock doors using ALPR alone.

## Optional: keep snapshots for future training or testing

**This is entirely optional and independent of the recognition integration.** If you want to retain a dataset for evaluating models later, create a Home Assistant automation that saves camera stills on vehicle detections. Such an automation is **not** needed for FastALPR to run, and its images are not automatically used by the integration.

Create a Media folder such as `/media/fastalpr-samples` (or allow Home Assistant to create it when capturing), then create an automation and replace the example entity IDs:

```yaml
alias: Save vehicle sample images
mode: single
max_exceeded: silent
triggers:
  - trigger: state
    entity_id: binary_sensor.example_vehicle
    to: "on"
actions:
  - variables:
      sample_id: "{{ now().strftime('%Y%m%d_%H%M%S_%f') }}"
  - repeat:
      count: 5
      sequence:
        - action: camera.snapshot
          target:
            entity_id: camera.example_camera
          data:
            filename: "/media/fastalpr-samples/{{ sample_id }}_{{ '%02d' | format(repeat.index) }}.jpg"
        - delay:
            milliseconds: 750
  - delay:
      seconds: 60
```

The optional sample collector can run alongside FastALPR, although both will request images during detections. Samples remain on disk until you remove them, so consider retention, storage and privacy. There is no need to send a `fast_alpr_batch_ready` event: the camera-first integration no longer consumes it.

## Troubleshooting and limitations

- Camera stills must be available through Home Assistant's standard camera interface. Not every camera supports stills or offers the same resolution; try the manual scan action first.
- Ensure the camera has a sharp view of registration plates. Glare, distance, angle and motion blur can defeat any OCR engine.
- If no vehicle-detection binary sensor is available, use a manual/custom automation trigger.
- Check the **Last error** sensor and HA logs when inference fails.
- Native ONNX dependencies must be compatible with your Home Assistant Core Python/platform; this remains to be validated on a real installation.
- Recorded registrations are personal information. Avoid publishing screenshots, configuration dumps, machine addresses or actual registrations when reporting issues.
- Restarting Home Assistant resets in-memory cooldown and diagnostics; no automatic sample-image retention or upload occurs.

## Development

```sh
python -m compileall -q custom_components
python -m pytest -q tests
```

GitHub Actions runs unit tests, HACS validation and Hassfest. Tests cover plate normalisation, duplicate registration detection, OCR confidence filtering and independent-image consensus. Live-camera and ONNX model tests are still required.

## Licence

MIT — see [LICENSE](LICENSE). FastALPR and its models are separate dependencies with their own licences.
