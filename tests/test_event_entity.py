"""Unit coverage for the native event entity without requiring HA Core in CI."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def event_platform(monkeypatch):
    """Load the event platform using minimal HA event/dispatcher fakes."""
    callbacks = {}
    fake_package = types.ModuleType("fast_alpr")
    fake_package.__path__ = []
    fake_constants = types.ModuleType("fast_alpr.const")
    fake_constants.CONF_CAMERA = "camera"
    fake_constants.DOMAIN = "fast_alpr"

    class EventEntity:
        async def async_added_to_hass(self):
            pass

        def async_on_remove(self, cleanup):
            self.cleanup = cleanup

        def _trigger_event(self, event_type, attributes=None):
            self.emitted.append((event_type, attributes))

        def async_write_ha_state(self):
            self.writes += 1

    def connect(hass, name, callback):
        callbacks[name] = callback
        return lambda: callbacks.pop(name, None)

    def register_module(name, **attributes):
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)

    monkeypatch.setitem(sys.modules, "fast_alpr", fake_package)
    monkeypatch.setitem(sys.modules, "fast_alpr.const", fake_constants)
    register_module("homeassistant", __path__=[])
    register_module("homeassistant.components", __path__=[])
    register_module("homeassistant.components.event", EventEntity=EventEntity)
    register_module("homeassistant.core", callback=lambda method: method)
    register_module("homeassistant.helpers", __path__=[])
    register_module("homeassistant.helpers.device_registry", DeviceInfo=dict)
    register_module(
        "homeassistant.helpers.dispatcher",
        async_dispatcher_connect=connect,
    )
    path = (
        Path(__file__).resolve().parents[1]
        / "custom_components" / "fast_alpr" / "event.py"
    )
    spec = importlib.util.spec_from_file_location("fast_alpr.event", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "fast_alpr.event", module)
    spec.loader.exec_module(module)
    return module, callbacks


def test_event_entity_publishes_recognition_details(event_platform):
    module, callbacks = event_platform
    entry = SimpleNamespace(entry_id="test-entry-1", data={"camera": "camera.demo"})
    entity = module.FastAlprRecognitionEvent(entry)
    entity.hass = object()
    entity.emitted = []
    entity.writes = 0
    import asyncio
    asyncio.run(entity.async_added_to_hass())
    assert entity._attr_event_types == ["known_vehicle_recognised"]
    assert entity._attr_unique_id == "test-entry-1_known_vehicle_event"

    match = {
        "vehicle": "Example vehicle",
        "plate": "ZZ99ZZZ",
        "camera": "camera.demo",
        "confidence": 0.96,
        "matching_frames": 2,
    }
    callbacks["fast_alpr_match_test-entry-1"](match)
    assert entity.emitted == [("known_vehicle_recognised", match)]
    assert entity.writes == 1
    entity.cleanup()
    assert not callbacks


def test_setup_adds_a_single_event_entity(event_platform):
    module, _ = event_platform
    entry = SimpleNamespace(entry_id="other-entry", data={"camera": "camera.demo"})
    added = []
    import asyncio
    asyncio.run(module.async_setup_entry(None, entry, added.extend))
    assert len(added) == 1
    assert added[0]._attr_unique_id == "other-entry_known_vehicle_event"
