"""Native Home Assistant event entity for confirmed FastALPR matches.

This supports the built-in 'Event received' automation trigger so users do
not have to manually enter the raw Home Assistant bus event name.
"""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import CONF_CAMERA, DOMAIN

EVENT_KNOWN_VEHICLE = "known_vehicle_recognised"


async def async_setup_entry(hass, entry, async_add_entities):
    """Expose one independently addressable recognition trigger per camera."""
    async_add_entities([FastAlprRecognitionEvent(entry)])


class FastAlprRecognitionEvent(EventEntity):
    """Stateless event fired once for each confirmed, non-suppressed match."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Known vehicle recognised"
    _attr_icon = "mdi:car-key"
    _attr_event_types = [EVENT_KNOWN_VEHICLE]

    def __init__(self, entry):
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_known_vehicle_event"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"FastALPR ({entry.data[CONF_CAMERA]})",
            manufacturer="FastALPR",
            model="Local licence plate recognition",
        )

    @callback
    def _on_match(self, event_data: dict):
        """Provide recognition details as attributes on the event entity."""
        self._trigger_event(EVENT_KNOWN_VEHICLE, event_data)
        self.async_write_ha_state()

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass,
            f"fast_alpr_match_{self.entry.entry_id}",
            self._on_match,
        ))
