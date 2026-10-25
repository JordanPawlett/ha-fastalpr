"""Config and options flows for camera-based FastALPR."""
from __future__ import annotations

import re

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CAMERA, CONF_COOLDOWN_SECONDS, CONF_DETECTION_ENTITY, CONF_FRAMES,
    CONF_INTERVAL_MS, CONF_KNOWN_PLATES, CONF_MATCH_EVENT, CONF_MIN_CONFIDENCE,
    CONF_MIN_MATCHING_FRAMES, CONF_SIGNAL_SECONDS, DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_FRAMES, DEFAULT_INTERVAL_MS, DEFAULT_MATCH_EVENT,
    DEFAULT_MIN_CONFIDENCE, DEFAULT_MIN_MATCHING_FRAMES,
    DEFAULT_SIGNAL_SECONDS, DOMAIN,
)
from .processor import parse_known_plates


def schema(options: dict | None = None, *, include_camera: bool = True) -> vol.Schema:
    o = options or {}
    camera_key = (
        vol.Required(CONF_CAMERA, default=o[CONF_CAMERA])
        if CONF_CAMERA in o else vol.Required(CONF_CAMERA)
    )
    # A schema default would restore the old detector when the user clears it.
    detector_key = vol.Optional(
        CONF_DETECTION_ENTITY,
        description={"suggested_value": o.get(CONF_DETECTION_ENTITY, "")},
    )
    fields = {
        detector_key: selector.EntitySelector(selector.EntitySelectorConfig(domain="binary_sensor")),
        vol.Required(CONF_KNOWN_PLATES, default=o.get(CONF_KNOWN_PLATES, "")):
            selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        vol.Required(CONF_MATCH_EVENT, default=o.get(CONF_MATCH_EVENT, DEFAULT_MATCH_EVENT)): str,
        vol.Required(CONF_FRAMES, default=o.get(CONF_FRAMES, DEFAULT_FRAMES)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
        vol.Required(CONF_INTERVAL_MS, default=o.get(CONF_INTERVAL_MS, DEFAULT_INTERVAL_MS)):
            vol.All(vol.Coerce(int), vol.Range(min=200, max=5000)),
        vol.Required(CONF_MIN_MATCHING_FRAMES, default=o.get(CONF_MIN_MATCHING_FRAMES, DEFAULT_MIN_MATCHING_FRAMES)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=20)),
        vol.Required(CONF_MIN_CONFIDENCE, default=o.get(CONF_MIN_CONFIDENCE, DEFAULT_MIN_CONFIDENCE)):
            vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.0)),
        vol.Required(CONF_COOLDOWN_SECONDS, default=o.get(CONF_COOLDOWN_SECONDS, DEFAULT_COOLDOWN_SECONDS)):
            vol.All(vol.Coerce(int), vol.Range(min=0, max=3600)),
        vol.Required(CONF_SIGNAL_SECONDS, default=o.get(CONF_SIGNAL_SECONDS, DEFAULT_SIGNAL_SECONDS)):
            vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
    }
    if include_camera:
        fields[camera_key] = selector.EntitySelector(selector.EntitySelectorConfig(domain="camera"))
    return vol.Schema(fields)


def validate(hass, values: dict) -> dict:
    camera = values[CONF_CAMERA]
    if not isinstance(camera, str) or not camera.startswith("camera.") or hass.states.get(camera) is None:
        raise ValueError("invalid_camera")
    detector = values.get(CONF_DETECTION_ENTITY)
    if detector not in (None, "") and (
        not isinstance(detector, str)
        or not detector.startswith("binary_sensor.")
        or hass.states.get(detector) is None
    ):
        raise ValueError("invalid_detector")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,99}", values[CONF_MATCH_EVENT]):
        raise ValueError("invalid_event")
    if values[CONF_MIN_MATCHING_FRAMES] > values[CONF_FRAMES]:
        raise ValueError("invalid_frames")
    if not parse_known_plates(values[CONF_KNOWN_PLATES]):
        raise ValueError("invalid_plates")
    return {**values, CONF_DETECTION_ENTITY: detector or ""}


def flow_error(exc: ValueError) -> str:
    error = str(exc)
    return error if error in {
        "invalid_camera", "invalid_detector", "invalid_event",
        "invalid_frames", "invalid_plates"
    } else "invalid_plates"


class FastAlprConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a camera and optionally its vehicle-detection entity."""
    VERSION = 2

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                values = validate(self.hass, user_input)
            except ValueError as exc:
                errors["base"] = flow_error(exc)
            else:
                await self.async_set_unique_id(values[CONF_CAMERA])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=f"FastALPR ({values[CONF_CAMERA]})", data=values)
        return self.async_show_form(step_id="user", data_schema=schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return FastAlprOptionsFlow(config_entry)


class FastAlprOptionsFlow(config_entries.OptionsFlow):
    """Editable camera, detection entity, registrations and thresholds."""
    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                # Keep the original camera: it is this entry's unique ID.
                values = validate(self.hass, {**user_input, CONF_CAMERA: self._config_entry.data[CONF_CAMERA]})
            except ValueError as exc:
                errors["base"] = flow_error(exc)
            else:
                # The camera cannot be changed without replacing the entry.
                return self.async_create_entry(title="", data=values)
        initial = {**self._config_entry.data, **self._config_entry.options}
        return self.async_show_form(step_id="init", data_schema=schema(initial, include_camera=False), errors=errors)
