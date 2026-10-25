# FastALPR for Home Assistant

[![Validate](https://github.com/JordanPawlett/ha-fastalpr/actions/workflows/validate.yml/badge.svg)](https://github.com/JordanPawlett/ha-fastalpr/actions/workflows/validate.yml)

Automatic licence plate recognition using any Home Assistant camera that supports still images and a local [FastALPR](https://github.com/ankandrew/fast-alpr) inference app. No cloud recognition service, MQTT, NVR, or mandatory snapshot collector.

> **Preview software.** Test with representative images before relying on recognition. Do not use plate recognition alone to open a barrier or authenticate a person: registrations are readily spoofed or misread.

## Architecture

```text
Vehicle detection binary sensor: on
  → Home Assistant camera snapshot
  → FastALPR Inference (Supervisor app, isolated ONNX/CPU)
  → OCR results returned to Home Assistant Core
  → multi-frame plate agreement (known or unknown)
  → classify against local configured known registrations
  → distinct Known / Unknown event entities + bus events
```

The Home Assistant integration contains **no native ONNX dependencies**, because Home Assistant Core's Alpine Linux environment cannot use ONNX Runtime's current glibc wheels. The companion Supervisor app uses Debian and provides an independent start-on-boot and HTTP watchdog.

Recognition runs only when triggered. The integration captures up to **four images, starting 350 ms apart**, sends each for inference as soon as it is available, and stops when **two different frames agree** on a plausible registration above the confidence threshold, whether known or unknown. No permanent image file is required.

## Installation

[![Open your Home Assistant instance and open the FastALPR repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=JordanPawlett&repository=ha-fastalpr&category=integration)

1. In Home Assistant OS, add this repository (`https://github.com/JordanPawlett/ha-fastalpr`) under **Settings → Apps → App store → Repositories**.
2. Install **FastALPR Inference**, enable **Start on boot** and **Watchdog**, and start the app. It preloads its two pretrained models at startup, which may require initial internet access.
3. Install the **FastALPR** integration from HACS, using this repository as a custom **Integration** source, and restart Home Assistant.
4. Under **Settings → Devices & services → Add integration → FastALPR**, choose **Automatic vehicle detection** (recommended). If the app announced itself, a discovered FastALPR integration may already be offered.
5. Configure the following, in order:
   - **Camera**: select any camera entity capable of still-image retrieval.
   - **Vehicle detection binary sensor**: select the binary sensor that turns `on` for a vehicle. It is required in automatic mode.
   - **Known registrations**: add one `REGISTRATION=Vehicle name` per line.

The inference app publishes its connection details through Home Assistant's **Supervisor discovery** mechanism. You should not need to type a hostname or port. If you do not see an inference app discovery, confirm it is running and restart that app. For a non-Supervisor external inference service, the backend URL can be overridden under advanced settings after configuration.

### Manual / externally triggered mode

When adding the integration, explicitly select **Manual or external automation** if you have no vehicle-detection sensor. That mode does *not* run recognition automatically: call the `fast_alpr.scan` Home Assistant action with the configured camera:

```yaml
action: fast_alpr.scan
data:
  camera: camera.example_camera
```

You can use any trigger in a normal Home Assistant automation to call this action. In automatic mode, a vehicle sensor transitioning from `off` to `on` starts the recognition pipeline automatically.

### Known registrations

Spaces, dashes and case in registrations are normalised for exact matching. These examples are fictional:

```text
ZZ99 ZZZ=Example vehicle
AA00 AAA=Another example
```

**All recognised plate values are output as UPPERCASE letters and digits only (A–Z, 0–9), with ALL spaces, punctuation and hyphens removed.** For example, an OCR read of `zz99 zzz` or `ZZ99-ZZZ` becomes `ZZ99ZZZ` in **both** native event entities, bus events and last-plate sensors. Automations should compare uppercase normalized values (e.g. `UNKNOWN2`, not `unknown2`). Both `ZZ99 ZZZ` and `ZZ99ZZZ` are treated as the same plate. Friendly names preserve their spaces. All registrations remain in your private Home Assistant config; they are **not sent to the inference backend** or to a third-party service.

## Advanced recognition settings

Use **FastALPR → Configure → Advanced recognition settings** to adjust the defaults:

| Setting | Default | Meaning |
| --- | ---: | --- |
| Maximum images per scan | **4** | Upper bound; stop early after matching |
| Time between image starts | **350 ms** | Target scheduling interval (camera latency may add delay) |
| Images required to agree | **2** | Two independent stills must agree on a plate, known or unknown |
| Minimum OCR confidence | **0.85** | Raw model confidence, not a calibrated correctness probability |
| Repeat match cooldown | **30 s** | Suppress repeated known or unknown plate events independently, per plate |
| Recognition signal duration | **30 s** | Duration of the binary sensor's `on` state |

The camera is selected once for each integration entry. To use a different camera, add another entry or replace the existing one.

The recognition event is **always `fast_alpr_match`**. It is no longer a user-configurable field. Legacy entries that previously used a custom event type continue to emit that event as well, preserving old automations.

## Latency requirement

**Goal:** publish a high-confidence, two-frame recognition event within **2–3 seconds** of a car entering the camera's view in representative operating conditions.

FastALPR emits a match immediately when enough frames agree; there is no wait for all four frames. The integration reports:

- `trigger_to_match_ms` — time from vehicle detection being delivered to Home Assistant until the match event;
- `capture_ms` — cumulative time awaiting snapshot retrieval;
- `inference_ms` — cumulative time awaiting local OCR results;
- diagnostic sensors **Last recognition latency**, **Last scan duration**, **Last capture time** and **Last inference time** (milliseconds).

The integration cannot measure when a vehicle *first enters the physical frame* before the camera recognises it. Overall arrival-to-event latency also depends on camera detection delay, image freshness/quality and inference speed. The target is **not guaranteed**; measure real arrivals and preferably review the p95 latency. Preloading models avoids cold-start inference delays after a normal app boot.

## Home Assistant automation triggers — known and unknown vehicles

FastALPR exposes **two distinct native Home Assistant event entities for each camera**:

| Event entity | When emitted | Native event type | Legacy/automation event |
| --- | --- | --- | --- |
| **Known vehicle recognised** | A confident plate matches the configured known registrations | `known_vehicle_recognised` | `fast_alpr_match` (unchanged) |
| **Unknown vehicle recognised** | A confident, plausible plate is **not** in the known list | `unknown_vehicle_recognised` | `fast_alpr_unknown_match` |

**Unknown does not mean unreadable.** If the model finds no plate, cannot reach the minimum OCR confidence, or cannot get agreement across at least two **different** frames, **neither** event fires. Both event types share the same OCR confidence setting, two-frame agreement and per-plate cooldown (default **30 seconds**).

Exactly one category is emitted per confirmed registration. Unknown matches **never** trigger the known-vehicle event entity, `fast_alpr_match`, any legacy known-match event, or the **Known vehicle detected** binary sensor. This avoids accidentally triggering existing automations that use the known event. **The integration does not decide which vehicles may open gates or need warnings**: these are access policies that belong in separate Home Assistant automations, preferably with an additional independent authorisation factor for gate access.

### Set up a trigger in the visual automation editor

1. In **Settings → Automations & scenes**, add a new automation.
2. Under **When**, use **Add trigger → By target → Entity** and select your camera's **Known vehicle recognised** or **Unknown vehicle recognised** event entity.
3. Choose **Event received**, then the corresponding recognition event type.
4. Under **Then do**, set your desired actions. To alert about **specific unknown plates**, add a condition matching the uppercase `plate` attribute to an explicit list. Never use the *absence* of a plate from a watchlist as permission to open gates.

For YAML users, an example native unknown event trigger is:

```yaml
triggers:
  - trigger: event.received
    target:
      entity_id: event.example_fastalpr_unknown_vehicle_recognised
    options:
      event_type:
        - unknown_vehicle_recognised
conditions:
  - condition: template
    value_template: >
      {{ trigger.to_state.attributes.get('plate', '') in ['UNKNOWN', 'UNKNOWN2'] }}
actions:
  - action: notify.notify
    data:
      message: "Unexpected vehicle {{ trigger.to_state.attributes.plate }}"
```

Replace the example entity ID with the actual event entity displayed in your Home Assistant instance. `UNKNOWN` and `UNKNOWN2` are illustration-only plate values; real registrations should be entered **only** in your private Home Assistant automations or config, never in a public GitHub issue.

The event's attributes include `plate` (always **UPPERCASE, no spaces or punctuation**), `confidence`, `matching_frames`, `camera`, `capture_id`, `timestamp`, `trigger_to_match_ms`, `capture_ms` and `inference_ms`. Known events additionally include the configured `vehicle` friendly name.

Existing automations using the prior **Known vehicle recognised** event entity, the `fast_alpr_match` bus event, or legacy configured event names remain compatible.

## Home Assistant entities and event

The integration exposes native **Known vehicle recognised** and **Unknown vehicle recognised** event entities, plus **Known vehicle detected** and **Inference engine ready** (binary), as well as **Last recognised vehicle**, **Last recognised plate**, **Processed batches**, **Last error**, and the latency diagnostic sensors.

When a **known** plate passes confidence and consensus, the integration publishes `fast_alpr_match` with `vehicle`, `plate`, `camera`, `confidence`, `matching_frames`, `capture_id`, `timestamp`, `trigger_to_match_ms`, `capture_ms`, and `inference_ms`.

Example notification automation:

```yaml
alias: Known vehicle notification
triggers:
  - trigger: event
    event_type: fast_alpr_match
actions:
  - action: notify.notify
    data:
      message: "Recognised {{ trigger.event.data.vehicle }}"
```

When a **not-configured** plate passes the same confidence and consensus, only `fast_alpr_unknown_match` and the native **Unknown vehicle recognised** event are emitted. The event `plate` field is always **UPPERCASE alphanumeric without whitespace**. A frame with no readable plate does **not** emit an unknown event.

The backend only processes local images; it does not store snapshots permanently or publicly expose a host port.

## Optional: Collect test/training snapshots

The snapshot collector is **not required** by FastALPR. It is useful if you want a separate local image dataset to compare recognition engines and improve quality in future.

For any camera and vehicle sensor, create a Media directory such as `/media/alpr-samples`, then create this example Home Assistant automation after substituting your own entity IDs:

```yaml
alias: Collect vehicle samples
mode: single
max_exceeded: silent
triggers:
  - trigger: state
    entity_id: binary_sensor.example_vehicle
    to: "on"
actions:
  - variables:
      batch: "{{ now().strftime('%Y%m%d_%H%M%S_%f') }}"
  - repeat:
      count: 4
      sequence:
        - action: camera.snapshot
          target:
            entity_id: camera.example_camera
          data:
            filename: "/media/alpr-samples/{{ batch }}_{{ '%02d' | format(repeat.index) }}.jpg"
        - delay:
            milliseconds: 350
  - delay:
      seconds: 30
```

Images are retained until you remove them. This dataset may contain personal data about passing vehicles: manage retention and do not commit it to GitHub. Camera integrations can support different snapshot resolutions and freshness.

## Troubleshooting

- If the integration says it cannot discover the backend, confirm **FastALPR Inference** is running and its health check passes, then restart that app.
- If automatic scans never start, verify the selected binary sensor actually transitions to `on`.
- If a scan yields no plate, examine camera angle, lighting, blur and the plate's size within the image. Try the manual scan action.
- Check the **Last error** sensor and Home Assistant logs for network or inference failures.
- Never publish private registrations, screenshots, host/IP details or configuration exports in issues.

## Development

```sh
python -m compileall -q custom_components fastalpr_backend
python -m pytest -q tests
```

CI runs HACS, Hassfest, Python tests, ONNX model import and backend Docker build checks. Live Home Assistant deployment and real-arrival latency validation are also important acceptance tests.

## Licence

MIT. FastALPR and its pretrained models are separate dependencies with their own licences.
