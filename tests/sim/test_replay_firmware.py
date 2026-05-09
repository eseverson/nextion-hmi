from pathlib import Path

import pytest

from sim.loader import load_hmi
from sim.parser import parse
from sim.exec import execute
from sim.renderer import Renderer
from sim import script as sim_script
from sim.state import ScriptContext


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


def _run_main_timer(state):
    """Manually fire the main page's Timer event handler once."""
    main = state.pages["main"]
    tm0 = next(c for c in main.components if c.attrs.get("type") == 51)
    code = tm0.events.get("codestimer", "")
    sim_script.run(code, ScriptContext(state))


def test_timer_reactivity_high_rpm_paints_x1_red(hmi_path):
    """The main-page Timer event should turn x1's bco red when RPM > 6800."""
    state = load_hmi(hmi_path)
    main = state.pages["main"]
    red_val = main.by_name("red").attrs["val"]
    main.by_name("x1").attrs["val"] = 7000
    _run_main_timer(state)
    assert main.by_name("x1").attrs["bco"] == red_val


def test_timer_reactivity_normal_values_dont_trip(hmi_path):
    state = load_hmi(hmi_path)
    main = state.pages["main"]
    bco_val = main.by_name("bco").attrs["val"]
    # Normal driving values
    main.by_name("x0").attrs["val"] = 60   # MAP kPa, not over 2000
    main.by_name("x1").attrs["val"] = 3500
    main.by_name("x2").attrs["val"] = 1700  # coolant in normal range
    main.by_name("x5").attrs["val"] = 90
    main.by_name("x6").attrs["val"] = 140
    main.by_name("x8").attrs["val"] = 0
    _run_main_timer(state)
    # x1 should NOT be red — RPM normal
    assert main.by_name("x1").attrs["bco"] == bco_val


def test_timer_reactivity_after_firmware_replay(hmi_path):
    """After applying the firmware frames, fire the timer; high RPM (4500)
    is below the redline (6000), so x1 stays at default bco."""
    state = load_hmi(hmi_path)
    for frame in FIRMWARE_FRAMES:
        execute(state, parse(frame))
    main = state.pages["main"]
    bco_val = main.by_name("bco").attrs["val"]
    _run_main_timer(state)
    # RPM = 4500 → no warning level
    assert main.by_name("x1").attrs["bco"] == bco_val
    # Coolant = 180 → far below 1400, x2 should be at blu (cold) per source
    blu_val = main.by_name("blu").attrs["val"]
    assert main.by_name("x2").attrs["bco"] == blu_val
