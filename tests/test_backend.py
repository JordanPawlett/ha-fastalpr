"""Tests for isolated inference server pure helpers and media path confinement."""
import importlib.util
from pathlib import Path
from sys import modules

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
    from types import SimpleNamespace
    assert server._confidence(SimpleNamespace(confidence=[0.5, 1.0])) == 0.75
