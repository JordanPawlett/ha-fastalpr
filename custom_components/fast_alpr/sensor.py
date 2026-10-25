"""FastALPR native Home Assistant sensors."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([
        FastAlprSensor(entry, "last_vehicle", "Last recognised vehicle", "mdi:car"),
        FastAlprSensor(entry, "last_plate", "Last recognised plate", "mdi:card-text-outline"),
        FastAlprSensor(entry, "processed_batches", "Processed batches", "mdi:image-multiple", diagnostic=True),
        FastAlprSensor(entry, "last_error", "Last error", "mdi:alert-circle-outline", diagnostic=True),
    ])


class FastAlprSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, key, name, icon, diagnostic=False):
        self.runtime = entry.runtime_data
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_icon = icon
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return getattr(self.runtime, self.key)

    @property
    def extra_state_attributes(self):
        if self.key != "last_vehicle":
            return None
        r = self.runtime
        return {
            "last_seen": r.last_seen.isoformat() if r.last_seen else None,
            "confidence": r.last_confidence,
            "matching_frames": r.last_matching_frames,
            "processed_batch": r.last_processed,
        }

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, f"fast_alpr_updated_{self.runtime.entry.entry_id}", self.async_write_ha_state))
