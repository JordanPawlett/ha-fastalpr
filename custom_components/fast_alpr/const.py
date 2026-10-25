"""Constants for FastALPR integration."""
DOMAIN = "fast_alpr"
EVENT_SCAN = "fast_alpr_scan"
DEFAULT_MATCH_EVENT = "fast_alpr_match"
DEFAULT_MODEL = "yolo-v9-t-384-license-plate-end2end"
DEFAULT_OCR = "cct-s-v2-global-model"

CONF_CAMERA = "camera"
CONF_DETECTION_ENTITY = "detection_entity"
CONF_KNOWN_PLATES = "known_plates"
CONF_MATCH_EVENT = "match_event"
CONF_FRAMES = "frames"
CONF_INTERVAL_MS = "interval_ms"
CONF_MIN_MATCHING_FRAMES = "min_matching_frames"
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_COOLDOWN_SECONDS = "cooldown_seconds"
CONF_SIGNAL_SECONDS = "signal_seconds"

DEFAULT_FRAMES = 5
DEFAULT_INTERVAL_MS = 750
DEFAULT_MIN_MATCHING_FRAMES = 2
DEFAULT_MIN_CONFIDENCE = 0.75
DEFAULT_COOLDOWN_SECONDS = 60
DEFAULT_SIGNAL_SECONDS = 30
