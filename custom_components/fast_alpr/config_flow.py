"""Camera-first FastALPR setup; the Supervisor app provides backend discovery."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import (
    CONF_BACKEND_URL, CONF_CAMERA, CONF_COOLDOWN_SECONDS,
    CONF_DETECTION_ENTITY, CONF_FRAMES, CONF_INTERVAL_MS, CONF_KNOWN_PLATES, CONF_WATCHLIST_PLATES,
    CONF_MATCH_EVENT, CONF_MIN_CONFIDENCE, CONF_MIN_MATCHING_FRAMES,
    CONF_SIGNAL_SECONDS, CONF_TRIGGER_MODE, DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_FRAMES, DEFAULT_INTERVAL_MS, DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MIN_MATCHING_FRAMES, DEFAULT_SIGNAL_SECONDS, DOMAIN,
    TRIGGER_AUTOMATIC, TRIGGER_MANUAL,
)
from .processor import parse_known_plates

# CONF_MATCH_EVENT is deliberately not user-editable; old values remain
# recognised for backwards-compatible notification automations.


def _backend_valid(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return bool(
            parsed.scheme in ("http", "https") and parsed.hostname
            and not parsed.username and not parsed.password
            and parsed.path in ("", "/") and not parsed.query and not parsed.fragment
        )
    except ValueError:
        return False


def _basic_schema(mode: str, existing: dict | None = None) -> vol.Schema:
    """Only the user-facing fields, in their visible order."""
    old = existing or {}
    camera_key = vol.Required(CONF_CAMERA, default=old[CONF_CAMERA]) if old.get(CONF_CAMERA) else vol.Required(CONF_CAMERA)
    plates_key = vol.Optional(CONF_KNOWN_PLATES, default=old.get(CONF_KNOWN_PLATES, ""))
    fields = {
        camera_key: selector.EntitySelector(selector.EntitySelectorConfig(domain="camera")),
    }
    if mode == TRIGGER_AUTOMATIC:
        sensor_key = (vol.Required(CONF_DETECTION_ENTITY, default=old[CONF_DETECTION_ENTITY])
                      if old.get(CONF_DETECTION_ENTITY) else vol.Required(CONF_DETECTION_ENTITY))
        fields[sensor_key] = selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))
    fields[plates_key] = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
    fields[vol.Optional(CONF_WATCHLIST_PLATES, default=old.get(CONF_WATCHLIST_PLATES, ""))] = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
    return vol.Schema(fields)


def _advanced_schema(current: dict) -> vol.Schema:
    """Separate, explicitly selected advanced options screen."""
    return vol.Schema({
        vol.Optional(CONF_BACKEND_URL, description={"suggested_value": current.get(CONF_BACKEND_URL, "")}): str,
        vol.Required(CONF_FRAMES, default=current.get(CONF_FRAMES, DEFAULT_FRAMES)):
            vol.All(vol.Coerce(int), vol.Range(min=2, max=12)),
        vol.Required(CONF_INTERVAL_MS, default=current.get(CONF_INTERVAL_MS, DEFAULT_INTERVAL_MS)):
            vol.All(vol.Coerce(int), vol.Range(min=100, max=5000)),
        vol.Required(CONF_MIN_MATCHING_FRAMES, default=current.get(CONF_MIN_MATCHING_FRAMES, DEFAULT_MIN_MATCHING_FRAMES)):
            vol.All(vol.Coerce(int), vol.Range(min=2, max=12)),
        vol.Required(CONF_MIN_CONFIDENCE, default=current.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE)):
            vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Required(CONF_COOLDOWN_SECONDS, default=current.get(CONF_COOLDOWN_SECONDS, DEFAULT_COOLDOWN_SECONDS)):
            vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
        vol.Required(CONF_SIGNAL_SECONDS, default=current.get(CONF_SIGNAL_SECONDS, DEFAULT_SIGNAL_SECONDS)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
    })


def _validate_basic(hass, values: dict, mode: str) -> None:
    camera = values.get(CONF_CAMERA)
    if not camera or hass.states.get(camera) is None or not camera.startswith("camera."):
        raise ValueError("invalid_camera")
    if mode == TRIGGER_AUTOMATIC:
        sensor = values.get(CONF_DETECTION_ENTITY)
        if not sensor or not sensor.startswith("binary_sensor.") or hass.states.get(sensor) is None:
            raise ValueError("invalid_detector")
    authorised = parse_known_plates(values.get(CONF_KNOWN_PLATES, ""))
    watchlist = parse_known_plates(values.get(CONF_WATCHLIST_PLATES, ""))
    if not authorised and not watchlist:
        raise ValueError("invalid_plates")
    if authorised.keys() & watchlist.keys():
        raise ValueError("plate_overlap")


def _validate_advanced(values: dict) -> None:
    if values.get(CONF_BACKEND_URL) and not _backend_valid(values[CONF_BACKEND_URL]):
        raise ValueError("invalid_backend")
    if values[CONF_MIN_MATCHING_FRAMES] > values[CONF_FRAMES]:
        raise ValueError("invalid_frames")


class FastAlprConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Hassio discovery supplies the backend; user chooses the camera."""
    VERSION = 3

    def __init__(self):
        self._discovered_url = None

    def _backend(self):
        return self._discovered_url or self.hass.data.get(DOMAIN, {}).get(CONF_BACKEND_URL)

    async def async_step_hassio(self, discovery_info: HassioServiceInfo):
        host = discovery_info.config.get("host")
        port = discovery_info.config.get("port")
        url = f"http://{host}:{port}" if host and port else None
        if not url or not _backend_valid(url):
            return self.async_abort(reason="invalid_backend")
        self.hass.data.setdefault(DOMAIN, {})[CONF_BACKEND_URL] = url
        self._discovered_url = url
        # Already-configured entries continue working; cache discovery for new ones.
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        return await self.async_step_automatic()

    async def async_step_user(self, user_input=None):
        """Explicit trigger choice, with automatic first and recommended."""
        return self.async_show_menu(step_id="user", menu_options=["automatic", "manual"])

    async def async_step_automatic(self, user_input=None):
        return await self._create(TRIGGER_AUTOMATIC, user_input)

    async def async_step_manual(self, user_input=None):
        return await self._create(TRIGGER_MANUAL, user_input)

    async def _create(self, mode: str, user_input: dict | None):
        errors = {}
        if user_input is not None:
            try:
                _validate_basic(self.hass, user_input, mode)
                backend = self._backend()
                if not backend:
                    raise ValueError("backend_unavailable")
            except ValueError as exc:
                errors["base"] = str(exc) if str(exc) in (
                    "invalid_camera", "invalid_detector", "invalid_plates", "plate_overlap", "plate_overlap", "backend_unavailable"
                ) else "invalid_plates"
            else:
                await self.async_set_unique_id(user_input[CONF_CAMERA])
                self._abort_if_unique_id_configured()
                data = {
                    **user_input, CONF_TRIGGER_MODE: mode,
                    CONF_BACKEND_URL: backend,
                    CONF_WATCHLIST_PLATES: user_input.get(CONF_WATCHLIST_PLATES, ""),
                    CONF_FRAMES: DEFAULT_FRAMES,
                    CONF_INTERVAL_MS: DEFAULT_INTERVAL_MS,
                    CONF_MIN_MATCHING_FRAMES: DEFAULT_MIN_MATCHING_FRAMES,
                    CONF_MIN_CONFIDENCE: DEFAULT_MIN_CONFIDENCE,
                    CONF_COOLDOWN_SECONDS: DEFAULT_COOLDOWN_SECONDS,
                    CONF_SIGNAL_SECONDS: DEFAULT_SIGNAL_SECONDS,
                }
                return self.async_create_entry(
                    title=f"FastALPR ({user_input[CONF_CAMERA]})", data=data
                )
        return self.async_show_form(
            step_id=mode, data_schema=_basic_schema(mode), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FastAlprOptionsFlow(config_entry)


class FastAlprOptionsFlow(config_entries.OptionsFlow):
    """Simple edits and a separate advanced recognition panel."""
    def __init__(self, config_entry):
        self._config_entry = config_entry

    @property
    def current(self):
        return {**self._config_entry.data, **self._config_entry.options}

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init", menu_options=["automatic", "manual", "advanced"]
        )

    async def async_step_automatic(self, user_input=None):
        return await self._edit_basic(TRIGGER_AUTOMATIC, user_input)

    async def async_step_manual(self, user_input=None):
        return await self._edit_basic(TRIGGER_MANUAL, user_input)

    async def _edit_basic(self, mode, user_input):
        errors = {}
        old = self.current
        # The camera is the entry unique ID. It is not editable after setup.
        if user_input is not None:
            payload = {**user_input, CONF_CAMERA: self._config_entry.data[CONF_CAMERA]}
            try:
                _validate_basic(self.hass, payload, mode)
            except ValueError as exc:
                errors["base"] = str(exc) if str(exc) in (
                    "invalid_camera", "invalid_detector", "invalid_plates"
                ) else "invalid_plates"
            else:
                updates = {
                    CONF_TRIGGER_MODE: mode,
                    CONF_KNOWN_PLATES: user_input.get(CONF_KNOWN_PLATES, ""),
                    CONF_WATCHLIST_PLATES: user_input.get(CONF_WATCHLIST_PLATES, ""),
                    CONF_DETECTION_ENTITY: user_input.get(CONF_DETECTION_ENTITY, "") if mode == TRIGGER_AUTOMATIC else "",
                }
                return self.async_create_entry(title="", data={**self._config_entry.options, **updates})
        # The camera was fixed at creation; show only mode-specific editable items.
        fields = {}
        if mode == TRIGGER_AUTOMATIC:
            detector_key = (vol.Required(CONF_DETECTION_ENTITY, default=old[CONF_DETECTION_ENTITY])
                            if old.get(CONF_DETECTION_ENTITY) else vol.Required(CONF_DETECTION_ENTITY))
            fields[detector_key] = selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor"))
        fields[vol.Optional(CONF_KNOWN_PLATES, default=old.get(CONF_KNOWN_PLATES, ""))] = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
        fields[vol.Optional(CONF_WATCHLIST_PLATES, default=old.get(CONF_WATCHLIST_PLATES, ""))] = selector.TextSelector(selector.TextSelectorConfig(multiline=True))
        return self.async_show_form(step_id=mode, data_schema=vol.Schema(fields), errors=errors)

    async def async_step_advanced(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                _validate_advanced(user_input)
            except ValueError as exc:
                errors["base"] = str(exc) if str(exc) in ("invalid_frames", "invalid_backend") else "invalid_frames"
            else:
                return self.async_create_entry(
                    title="", data={**self._config_entry.options, **user_input,
                                    CONF_BACKEND_URL: user_input.get(CONF_BACKEND_URL, "")}
                )
        return self.async_show_form(
            step_id="advanced", data_schema=_advanced_schema(self.current), errors=errors
        )
