"""Tests for isolated inference server pure helpers and media path confinement."""
import importlib.util
from pathlib import Path
from sys import modules
from types import SimpleNamespace

import pytest

FILE = Path(__file__).resolve().parents[1] / "fastalpr_backend" / "server.py"
SPEC = importlib.util.spec_from_file_location("fastalpr_backend_server", FILE)
server = importlib.util.module_from_spec(SPEC)
modules[SPEC.name] = server
SPEC.loader.exec_module(server)


def test_plate_normalization():
    assert server._normalize_plate(" zz99 zzz ") == "ZZ99ZZZ"


def test_media_path_confinement(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "MEDIA_DIR", tmp_path.resolve())
    good = tmp_path / "training" / "sample_01.jpg"
    good.parent.mkdir()
    good.write_bytes(b"jpg")
    assert server._media_file("training/sample_01.jpg") == good.resolve()
    outside = tmp_path.parent / "escaped.jpg"
    outside.write_bytes(b"jpg")
    with pytest.raises(ValueError):
        server._media_file("../escaped.jpg")
    (tmp_path / "link.jpg").symlink_to(outside)
    with pytest.raises(ValueError):
        server._media_file("link.jpg")
    with pytest.raises(ValueError):
        server._media_file("/etc/passwd")


def test_confidence_mean():
    assert server._confidence(SimpleNamespace(confidence=[0.5, 1.0])) == 0.75


def test_model_uses_high_resolution_detector(monkeypatch):
    calls = {}

    class ALPR:
        def __init__(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setitem(modules, "fast_alpr", SimpleNamespace(ALPR=ALPR))
    monkeypatch.setattr(server, "_MODEL", None)
    model = server._model()

    assert isinstance(model, ALPR)
    assert calls == {
        "detector_model": "yolo-v9-t-640-license-plate-end2end",
        "ocr_model": "cct-s-v2-global-model",
    }


def test_recognition_http_reports_correlated_timings_without_plates(monkeypatch, caplog):
    import json
    import logging
    import threading
    import urllib.request
    class Model:
        def predict(self, path):
            assert Path(path).read_bytes() == b'\xff\xd8\xffimage'
            return [SimpleNamespace(ocr=SimpleNamespace(text='ZZ99ZZZ', confidence=.95))]
    monkeypatch.setattr(server, '_MODEL', Model())
    caplog.set_level(logging.INFO)
    httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}/recognize',
            data=b'\xff\xd8\xffimage', headers={'X-Capture-ID': 'a'*32, 'X-Frame-Index': '0'})
        with urllib.request.urlopen(req) as response:
            payload = json.load(response)
        assert payload['detections'][0]['plate'] == 'ZZ99ZZZ'
        assert {'queue_ms', 'model_ms', 'body_read_ms', 'total_ms'} <= payload['timings'].keys()
        assert 'capture_id=' + 'a'*32 in caplog.text
        assert 'ZZ99ZZZ' not in caplog.text
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
