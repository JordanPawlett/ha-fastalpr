"""Exercise real scan scheduling and auditing with fake HA and HTTP boundaries."""
import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

BASE = Path(__file__).resolve().parents[1] / 'custom_components' / 'fast_alpr'


@pytest.fixture
def runtime_module(monkeypatch):
    def module(name, **attrs):
        value = types.ModuleType(name)
        value.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, value)
        return value
    module('audit_test_pkg', __path__=[str(BASE)])
    module('homeassistant', __path__=[])
    module('homeassistant.components', __path__=[])
    module('homeassistant.components.camera', async_get_image=None)
    module('homeassistant.core', HomeAssistant=object, callback=lambda f: f)
    module('homeassistant.helpers', __path__=[])
    module('homeassistant.helpers.aiohttp_client', async_get_clientsession=None)
    module('homeassistant.helpers.dispatcher', async_dispatcher_send=lambda *a: None)
    module('homeassistant.helpers.event', async_call_later=lambda *a: lambda: None)
    for name in ['const', 'processor', 'audit', 'runtime']:
        spec = importlib.util.spec_from_file_location('audit_test_pkg.' + name, BASE / (name + '.py'))
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, mod)
        spec.loader.exec_module(mod)
    return mod


def setup_scan(mod, monkeypatch, tmp_path, detections, audit=True, save_error=False):
    captured, submitted, saved, events = [], [], [], []
    async def camera(*args, **kwargs):
        await asyncio.sleep(.005)
        image = b'\xff\xd8\xffexact-frame-' + str(len(captured)).encode()
        captured.append(image)
        return SimpleNamespace(content=image, content_type='image/jpeg')
    class Response:
        def __init__(self, index): self.index = index
        async def __aenter__(self):
            await asyncio.sleep(.001)
            return self
        async def __aexit__(self, *args): pass
        def raise_for_status(self): pass
        async def json(self):
            return {'detections': detections[self.index], 'timings': {'model_ms': 1}}
    class Session:
        def post(self, endpoint, *, data, headers, timeout):
            submitted.append((data, headers))
            return Response(len(submitted) - 1)
    async def executor(fn, root, report, images, keep):
        # The audit must run only after capture/recognition, using original bytes.
        saved.append((report, images))
        if save_error:
            raise OSError('disk full')
        return await asyncio.to_thread(fn, tmp_path, report, images, keep)
    monkeypatch.setattr(mod, 'async_get_image', camera)
    monkeypatch.setattr(mod, 'async_get_clientsession', lambda hass: Session())
    hass = SimpleNamespace(data={}, async_create_task=asyncio.create_task,
                           async_add_executor_job=executor,
                           bus=SimpleNamespace(async_fire=lambda *args: events.append(args)))
    entry = SimpleNamespace(entry_id='example', data={
        'camera': 'camera.example', 'backend_url': 'http://backend',
        'known_plates': 'ZZ99ZZZ=Example', 'min_confidence': .85,
        'min_matching_frames': 2, 'frames': len(detections), 'interval_ms': 0,
        'cooldown_seconds': 30, 'signal_seconds': 30,
        'capture_audit': audit,
    }, options={})
    return mod.FastAlprRuntime(hass, entry), captured, submitted, saved, events


@pytest.mark.parametrize('detections,outcome', [
    ([[], [], []], 'no_detections'),
    ([[{'plate': 'ZZ99ZZZ', 'confidence': .95}], [], []], 'insufficient_agreement'),
    ([[{'plate': 'ZZ99ZZZ', 'confidence': .5}], [], []], 'rejected_detections'),
    ([[{'plate': 'ZZ99ZZZ', 'confidence': .95}]] * 3, 'known_match'),
    ([[{'plate': 'AA00AAA', 'confidence': .95}]] * 3, 'unknown_match'),
])
def test_scan_exact_bytes_timing_and_outcomes(runtime_module, monkeypatch, tmp_path, caplog, detections, outcome):
    caplog.set_level(logging.DEBUG)
    runtime, captured, submitted, saved, events = setup_scan(runtime_module, monkeypatch, tmp_path, detections)
    asyncio.run(runtime.capture_and_recognise())
    assert runtime.last_scan_outcome == outcome
    assert len(captured) == len(submitted) == len(saved[0][1])
    report = json.loads((Path(runtime.last_audit_path) / 'scan.json').read_text())
    for index, (content, headers) in enumerate(submitted):
        assert content == captured[index] == (Path(runtime.last_audit_path) / f'frame_{index+1:02d}.jpg').read_bytes()
        assert headers['X-Capture-ID'] == report['capture_id']
        assert headers['X-Frame-Index'] == str(index)
        frame = report['frames'][index]
        assert frame['snapshot_requested_at'] <= frame['snapshot_received_at']
        assert frame['capture_ms'] >= 4
    assert 'ZZ99ZZZ' not in json.dumps(runtime.last_scan_report)
    assert 'ZZ99ZZZ' not in caplog.text
    assert 'AA00AAA' not in caplog.text
    assert bool(events) == outcome.endswith('_match')


def test_audit_off_still_has_diagnostics(runtime_module, monkeypatch, tmp_path):
    runtime, _, _, saved, _ = setup_scan(runtime_module, monkeypatch, tmp_path, [[], []], audit=False)
    asyncio.run(runtime.capture_and_recognise())
    assert not saved
    assert runtime.last_scan_report['outcome'] == 'no_detections'
    assert runtime.last_audit_path is None


def test_failed_audit_does_not_undo_recognition(runtime_module, monkeypatch, tmp_path):
    runtime, _, _, _, events = setup_scan(runtime_module, monkeypatch, tmp_path,
        [[{'plate': 'ZZ99ZZZ', 'confidence': .95}]] * 3, save_error=True)
    asyncio.run(runtime.capture_and_recognise())
    assert runtime.last_scan_outcome == 'known_match'
    assert runtime.last_audit_error == 'OSError'
    assert len(events) == 1


def test_capture_failure_retains_partial_audit(runtime_module, monkeypatch, tmp_path):
    runtime, _, _, _, _ = setup_scan(runtime_module, monkeypatch, tmp_path, [[], []])
    async def broken_camera(*args, **kwargs): raise RuntimeError('camera offline')
    monkeypatch.setattr(runtime_module, 'async_get_image', broken_camera)
    asyncio.run(runtime.capture_and_recognise())
    assert runtime.last_scan_outcome == 'error'
    report = json.loads((Path(runtime.last_audit_path) / 'scan.json').read_text())
    assert report['error_type'] == 'RuntimeError'
    assert report['frames'][0]['status'] == 'capture_failed'


def test_retention_only_deletes_owned_scan_directories(runtime_module, tmp_path):
    root = tmp_path / 'audits'
    root.mkdir()
    unrelated = root / 'old_samples'
    unrelated.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / ('f' * 32)).symlink_to(outside, target_is_directory=True)
    for i in range(3):
        runtime_module.save_scan(root, {'capture_id': f'{i:032x}'}, [(0, 'image/png', b'bytes')], 2)
    assert len([p for p in root.iterdir() if p.name.isdecimal()]) == 2
    assert unrelated.exists() and outside.exists()
    assert (root / ('f' * 32)).is_symlink()


def test_cooldown_is_reported(runtime_module, monkeypatch, tmp_path):
    runtime, _, _, _, events = setup_scan(runtime_module, monkeypatch, tmp_path,
        [[{'plate': 'ZZ99ZZZ', 'confidence': .95}]] * 3)
    runtime.last_sent['ZZ99ZZZ'] = runtime_module.time.monotonic()
    asyncio.run(runtime.capture_and_recognise())
    assert runtime.last_scan_outcome == 'cooldown_suppressed'
    assert not events


def test_cancelled_scan_retains_audit(runtime_module, monkeypatch, tmp_path):
    runtime, _, _, _, _ = setup_scan(runtime_module, monkeypatch, tmp_path, [[], []])
    async def run():
        started = asyncio.Event()
        async def blocked_camera(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(runtime_module, 'async_get_image', blocked_camera)
        runtime.start_capture()
        task = runtime.task
        await started.wait()
        runtime.start_capture()
        assert runtime.task is task  # Repeated triggers do not create another capture loop.
        await runtime.close()
    asyncio.run(run())
    assert runtime.last_scan_outcome == 'cancelled'
    assert (Path(runtime.last_audit_path) / 'scan.json').exists()


def test_backend_failure_is_distinct_from_no_detection(runtime_module, monkeypatch, tmp_path):
    runtime, _, _, _, _ = setup_scan(runtime_module, monkeypatch, tmp_path, [[], []])
    class FailedSession:
        def post(self, *args, **kwargs):
            raise ConnectionError('offline')
    monkeypatch.setattr(runtime_module, 'async_get_clientsession', lambda hass: FailedSession())
    asyncio.run(runtime.capture_and_recognise())
    assert runtime.last_scan_outcome == 'error'
    assert any(f['status'] == 'inference_failed' for f in runtime.last_scan_report['frames'])
    assert runtime.last_scan_report['error_type'] == 'ConnectionError'
