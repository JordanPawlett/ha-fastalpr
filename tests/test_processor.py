"""Pure FastALPR logic tests; no Home Assistant/ONNX install needed."""
import importlib.util
from pathlib import Path
from sys import modules
from types import SimpleNamespace

import pytest

FILE = Path(__file__).resolve().parents[1] / "custom_components" / "fast_alpr" / "processor.py"
SPEC = importlib.util.spec_from_file_location("fast_alpr_processor", FILE)
processor = importlib.util.module_from_spec(SPEC)
modules[SPEC.name] = processor
SPEC.loader.exec_module(processor)


def ocr(plate, score):
    return SimpleNamespace(ocr=SimpleNamespace(text=plate, confidence=score))


class FakeModel:
    def __init__(self, per_filename):
        self.per_filename = per_filename

    def predict(self, path):
        return self.per_filename.get(Path(path).name, [])


@pytest.mark.parametrize("text", [
    "ZZ99 ZZZ", "zz99zzz", "ZZ99-ZZZ", " ZZ99\tZZZ ",
])
def test_registration_spaces_and_case(text):
    # These examples test formatting only, not any real vehicle allowlist.
    assert processor.normalize_plate(text) == "ZZ99ZZZ"


def test_known_plates_normalisation_and_names():
    assert processor.parse_known_plates("ZZ99 ZZZ=Example vehicle\nAA00-AAA=Another example") == {
        "ZZ99ZZZ": "Example vehicle", "AA00AAA": "Another example"}
    with pytest.raises(ValueError, match="Duplicate registration"):
        processor.parse_known_plates("ZZ99ZZZ=One;ZZ99 ZZZ=Two")
    with pytest.raises(ValueError):
        processor.parse_known_plates("invalid line without separator")


def test_consensus_one_vote_per_image(tmp_path):
    first = tmp_path / "frame_01.jpg"
    second = tmp_path / "frame_02.jpg"
    third = tmp_path / "frame_03.jpg"
    model = FakeModel({
        first.name: [ocr("zz99 zzz", .83), ocr("ZZ99ZZZ", .93)],
        second.name: [ocr("ZZ99ZZZ", .88), ocr("AA00AAA", .55)],
        third.name: [ocr("BB11BBB", .99)],
    })
    results = processor.read_batch(model, [first, second, third], {
        "ZZ99ZZZ": "Example vehicle", "AA00AAA": "Other vehicle"}, .75, 2)
    assert len(results) == 1
    assert results[0].plate == "ZZ99ZZZ"
    assert results[0].vehicle == "Example vehicle"
    assert results[0].matching_frames == 2
    assert results[0].confidence == .93


def test_single_image_cannot_satisfy_consensus(tmp_path):
    image = tmp_path / "frame_01.jpg"
    model = FakeModel({image.name: [ocr("ZZ99ZZZ", .99), ocr("ZZ99 ZZZ", .99)]})
    assert not processor.read_batch(model, [image], {"ZZ99ZZZ": "Car"}, .75, 2)


def test_low_confidence_is_rejected(tmp_path):
    frames = [tmp_path / "frame_01.jpg", tmp_path / "frame_02.jpg"]
    model = FakeModel({frame.name: [ocr("ZZ99ZZZ", .7)] for frame in frames})
    assert processor.read_batch(model, frames, {"ZZ99ZZZ": "Car"}, .75, 2) == []
