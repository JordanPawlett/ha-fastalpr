"""Local ALPR from any Home Assistant camera supporting still images."""
from __future__ import annotations

from datetime import timedelta

import voluptuous as vol
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    CONF_BACKEND_URL, CONF_CAMERA, CONF_DETECTION_ENTITY,
    CONF_TRIGGER_MODE, DOMAIN, TRIGGER_AUTOMATIC,
)
from .runtime import FastAlprRuntime

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
SCAN_SERVICE = "scan"


async def async_setup(hass, config):
    """Register manual scans and cache Supervisor backend discovery."""
    hass.data.setdefault(DOMAIN, {})
    async def handle_scan(call):
        camera = call.data[CONF_CAMERA]
        for entry in hass.config_entries.async_entries(DOMAIN):
            if not hasattr(entry, "runtime_data"):
                continue
            runtime = entry.runtime_data
            if runtime.options.get(CONF_CAMERA) == camera:
                runtime.start_capture()
                return
        raise ValueError("FastALPR has no configured entry for that camera")

    hass.services.async_register(
        DOMAIN, SCAN_SERVICE, handle_scan,
        schema=vol.Schema({vol.Required(CONF_CAMERA): cv.entity_id}),
    )
    return True


async def async_setup_entry(hass, entry):
    runtime = FastAlprRuntime(hass, entry)
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(async_track_time_interval(
        hass, lambda now: hass.async_create_task(runtime.refresh_backend_status()),
        timedelta(seconds=30),
    ))
    hass.async_create_task(runtime.refresh_backend_status())

    detector = (runtime.options.get(CONF_DETECTION_ENTITY)
                if runtime.options.get(CONF_TRIGGER_MODE, TRIGGER_AUTOMATIC) == TRIGGER_AUTOMATIC else None)
    if detector:
        @callback
        def on_vehicle_detection(event):
            old = event.data.get("old_state")
            new = event.data.get("new_state")
            if new is not None and new.state == "on" and (old is None or old.state != "on"):
                runtime.start_capture()

        entry.async_on_unload(
            async_track_state_change_event(hass, detector, on_vehicle_detection)
        )

    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.close()
    return ok
