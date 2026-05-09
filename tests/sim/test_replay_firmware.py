from pathlib import Path

import pytest

from sim.loader import load_hmi
from sim.parser import parse
from sim.exec import execute
from sim.renderer import Renderer


# Exact byte shapes the firmware sends during one update cycle, taken
# from src/main.cpp in the parent miata-dash repo.
FIRMWARE_FRAMES = [
    b"j0.val=42",
    b"x0.val=98",
    b"x1.val=4500",
    b"x2.val=180",
    b"x3.val=33",
    b"x4.val=145",
    b"x5.val=120",
    b"x6.val=132",
    b"x7.val=145",
    b"x8.val=0",
    b's0.txt=""',
]


def test_firmware_replay_does_not_crash(hmi_path):
    state = load_hmi(hmi_path)
    for frame in FIRMWARE_FRAMES:
        execute(state, parse(frame))
    main = state.pages["main"]
    assert main.by_name("x0").attrs["val"] == 98
    assert main.by_name("x1").attrs["val"] == 4500
    assert main.by_name("j0").attrs["val"] == 42
    assert main.by_name("s0").attrs["txt"] == ""


def test_firmware_replay_renders_to_committed_reference(hmi_path):
    state = load_hmi(hmi_path)
    for frame in FIRMWARE_FRAMES:
        execute(state, parse(frame))
    img = Renderer().render(state)

    fixtures = Path(__file__).parent / "fixtures"
    reference = fixtures / "firmware_replay.png"
    if not reference.exists():
        fixtures.mkdir(parents=True, exist_ok=True)
        img.save(reference)
        pytest.skip(f"reference written to {reference}; commit and rerun")

    from PIL import Image
    ref_img = Image.open(reference)
    assert img.size == ref_img.size
    assert list(img.getdata()) == list(ref_img.getdata())
