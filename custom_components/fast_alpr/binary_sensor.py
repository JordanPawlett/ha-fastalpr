"""Known-vehicle detection signal."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FastAlprKnownVehicle(entry), FastAlprBackendReady(entry)])


class FastAlprKnownVehicle(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Known vehicle detected"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:car-connected"

    def __init__(self, entry):
        self.runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_known_vehicle"

    @property
    def is_on(self):
        return self.runtime.detected

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, f"fast_alpr_updated_{self.runtime.entry.entry_id}", self.async_write_ha_state))


class FastAlprBackendReady(BinarySensorEntity):
    """Indicates that the isolated inference model is loaded and responsive."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Inference engine ready"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, entry):
        self.runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_backend_ready"

    @property
    def is_on(self):
        return self.runtime.backend_ready

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"fast_alpr_updated_{self.runtime.entry.entry_id}",
            self.async_write_ha_state,
        ))
