from unittest.mock import MagicMock

from sim.state import Component, DisplayState, Page
from sim.timer import TimerScheduler, TIMER_TYPE


def _make_state(*pages: Page) -> DisplayState:
    return DisplayState(pages={p.name: p for p in pages})


def _timer(name: str, cid: int, *, tim: int = 100, en: int = 1, codestimer: str = "x") -> Component:
    return Component(
        name=name,
        id=cid,
        type=TIMER_TYPE,
        attrs={"type": TIMER_TYPE, "tim": tim, "en": en},
        events={"codestimer": codestimer},
    )


def _label(name: str, cid: int) -> Component:
    return Component(name=name, id=cid, type=54, attrs={"type": 54})


# ---------------------------------------------------------------------------
# basic shape
# ---------------------------------------------------------------------------


def test_tick_does_nothing_without_timers():
    page = Page(name="main", id=0, attrs={}, components=[_label("t0", 1)])
    state = _make_state(page)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()
    sched.tick(10_000, run_event)
    run_event.assert_not_called()


# ---------------------------------------------------------------------------
# enabled timer fires after tim ms
# ---------------------------------------------------------------------------


def test_enabled_timer_fires_after_tim_ms():
    t = _timer("tm0", 1, tim=100, en=1)
    page = Page(name="main", id=0, attrs={}, components=[t])
    state = _make_state(page)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()

    # Just before the first interval — must not fire.
    sched.tick(99, run_event)
    run_event.assert_not_called()

    # At the interval — fires once.
    sched.tick(100, run_event)
    assert run_event.call_count == 1
    run_event.assert_called_with(t, "codestimer")

    # Same now_ms — idempotent (still 1 call total).
    sched.tick(100, run_event)
    assert run_event.call_count == 1

    # Next interval — fires again.
    sched.tick(200, run_event)
    assert run_event.call_count == 2


def test_run_event_receives_component_so_caller_can_lookup_handler():
    t = _timer("tm0", 1, tim=50, en=1, codestimer="page0.bco=63488")
    page = Page(name="main", id=0, attrs={}, components=[t])
    state = _make_state(page)
    sched = TimerScheduler(state)
    sched.reset(0)

    received: list[Component] = []

    def run(c, evt):
        received.append(c)
        # Caller-side: look up the handler text from the component.
        assert c.events.get("codestimer") == "page0.bco=63488"

    sched.tick(50, run)
    assert received == [t]


# ---------------------------------------------------------------------------
# disabled timer never fires
# ---------------------------------------------------------------------------


def test_disabled_timer_never_fires():
    t = _timer("tm0", 1, tim=100, en=0)
    page = Page(name="main", id=0, attrs={}, components=[t])
    state = _make_state(page)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()
    for now in (50, 100, 200, 500, 1000):
        sched.tick(now, run_event)
    run_event.assert_not_called()


# ---------------------------------------------------------------------------
# re-enabled timer waits a full tim before firing
# ---------------------------------------------------------------------------


def test_reenabled_timer_fires_after_full_interval_from_reenable():
    t = _timer("tm0", 1, tim=100, en=0)
    page = Page(name="main", id=0, attrs={}, components=[t])
    state = _make_state(page)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()

    # Disabled — no fires while time advances.
    sched.tick(500, run_event)
    sched.tick(1000, run_event)
    run_event.assert_not_called()

    # Re-enable at t=1000.
    t.attrs["en"] = 1

    # First post-enable tick re-arms but must not fire immediately.
    sched.tick(1000, run_event)
    run_event.assert_not_called()

    # Just before the new interval — still no fire.
    sched.tick(1099, run_event)
    run_event.assert_not_called()

    # tim ms after re-enabling — fires once.
    sched.tick(1100, run_event)
    assert run_event.call_count == 1


# ---------------------------------------------------------------------------
# reset re-derives timers and re-arms (page switch)
# ---------------------------------------------------------------------------


def test_reset_after_page_switch_rederives_and_rearms():
    t_main = _timer("tm_main", 1, tim=100, en=1)
    t_other = _timer("tm_other", 2, tim=100, en=1)
    main = Page(name="main", id=0, attrs={}, components=[t_main])
    other = Page(name="settings", id=1, attrs={}, components=[t_other])
    state = _make_state(main, other)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()

    # Main page timer fires.
    sched.tick(100, run_event)
    assert run_event.call_count == 1
    run_event.assert_called_with(t_main, "codestimer")

    # Switch active page and reset at t=100.
    state.set_active(other)
    sched.reset(100)

    # The other-page timer should fire 100 ms after reset, not immediately.
    sched.tick(100, run_event)
    assert run_event.call_count == 1  # unchanged

    sched.tick(199, run_event)
    assert run_event.call_count == 1

    sched.tick(200, run_event)
    assert run_event.call_count == 2
    # Most recent call should be for the other page's timer.
    assert run_event.call_args == ((t_other, "codestimer"),)


def test_inactive_page_timers_do_not_fire():
    t_main = _timer("tm_main", 1, tim=100, en=1)
    t_other = _timer("tm_other", 2, tim=50, en=1)
    main = Page(name="main", id=0, attrs={}, components=[t_main])
    other = Page(name="settings", id=1, attrs={}, components=[t_other])
    state = _make_state(main, other)  # active_page is main (lowest id)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()
    sched.tick(500, run_event)

    # Only the main-page timer should have been called.
    for call in run_event.call_args_list:
        assert call.args[0] is t_main


# ---------------------------------------------------------------------------
# slow caller — fire once, not N times
# ---------------------------------------------------------------------------


def test_slow_caller_fires_once_not_burst():
    t = _timer("tm0", 1, tim=100, en=1)
    page = Page(name="main", id=0, attrs={}, components=[t])
    state = _make_state(page)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()

    # 5 intervals overdue in a single tick.
    sched.tick(500, run_event)
    assert run_event.call_count == 1

    # Re-arm should be relative to now (500), so next fire at 600.
    sched.tick(599, run_event)
    assert run_event.call_count == 1
    sched.tick(600, run_event)
    assert run_event.call_count == 2


# ---------------------------------------------------------------------------
# default tim of 400 when missing
# ---------------------------------------------------------------------------


def test_missing_tim_attr_defaults_to_400_ms():
    c = Component(
        name="tm0", id=1, type=TIMER_TYPE,
        attrs={"type": TIMER_TYPE, "en": 1},  # no `tim`
        events={"codestimer": ""},
    )
    page = Page(name="main", id=0, attrs={}, components=[c])
    state = _make_state(page)
    sched = TimerScheduler(state)
    sched.reset(0)

    run_event = MagicMock()
    sched.tick(399, run_event)
    run_event.assert_not_called()
    sched.tick(400, run_event)
    assert run_event.call_count == 1
