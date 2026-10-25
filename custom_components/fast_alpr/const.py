"""Constants for FastALPR."""
DOMAIN = "fast_alpr"
EVENT_MATCH = "fast_alpr_match"
EVENT_SCAN = "fast_alpr_scan"

CONF_BACKEND_URL = "backend_url"
CONF_CAMERA = "camera"
CONF_DETECTION_ENTITY = "detection_entity"
CONF_KNOWN_PLATES = "known_plates"
CONF_TRIGGER_MODE = "trigger_mode"
TRIGGER_AUTOMATIC = "automatic"
TRIGGER_MANUAL = "manual"
CONF_FRAMES = "frames"
CONF_INTERVAL_MS = "interval_ms"
CONF_MIN_MATCHING_FRAMES = "min_matching_frames"
CONF_MIN_CONFIDENCE = "min_confidence"
CONF_COOLDOWN_SECONDS = "cooldown_seconds"
CONF_SIGNAL_SECONDS = "signal_seconds"

DEFAULT_FRAMES = 4
DEFAULT_INTERVAL_MS = 350
DEFAULT_MIN_MATCHING_FRAMES = 2
DEFAULT_MIN_CONFIDENCE = 0.85
DEFAULT_COOLDOWN_SECONDS = 30
DEFAULT_SIGNAL_SECONDS = 30
DEFAULT_MATCH_EVENT = EVENT_MATCH  # Legacy entries may configure an additional event.
