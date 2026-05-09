"""Periodic Timer-component event scheduler.

Nextion Timer components (type 51) fire their `codestimer` event at a
fixed `tim` interval (ms) while their `en` attribute is 1. The host
app drives this from its main loop by calling `tick(now_ms, run_event)`
on every frame; the scheduler decides which Timer components are due
and asks the caller to run their handler.

This module is deliberately I/O-free: it owns no clock and no script
runner — just bookkeeping over the active page's Timer components.
"""
from __future__ import annotations
from typing import Callable, Dict

from sim.state import DisplayState, Component, Page


TIMER_TYPE = 51
DEFAULT_TIM_MS = 400


def _is_enabled(c: Component) -> bool:
    return int(c.attrs.get("en", 0)) == 1


def _tim_ms(c: Component) -> int:
    v = c.attrs.get("tim", DEFAULT_TIM_MS)
    try:
        return int(v)
    except (TypeError, ValueError):
        return DEFAULT_TIM_MS


class TimerScheduler:
    def __init__(self, state: DisplayState):
        self.state = state
        # Map id(Component) -> next-fire timestamp (ms). Using id() rather
        # than the component object itself sidesteps the unhashable-dict
        # attrs issue and keeps lookup O(1).
        self._next_fire: Dict[int, int] = {}
        # Track which page's timers are currently armed so we can detect
        # silent page switches (a caller that forgets to call reset()).
        self._armed_page: Page | None = None

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _active_timers(self) -> list[Component]:
        page = self.state.active_page
        if page is None:
            return []
        return [c for c in page.components if c.attrs.get("type") == TIMER_TYPE]

    def _arm(self, c: Component, now_ms: int) -> None:
        self._next_fire[id(c)] = now_ms + _tim_ms(c)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def reset(self, now_ms: int) -> None:
        """Re-derive Timer components from the current state and reset
        next-fire timestamps to now + tim. Call on page switch.
        """
        self._next_fire.clear()
        self._armed_page = self.state.active_page
        for c in self._active_timers():
            self._arm(c, now_ms)

    def tick(self, now_ms: int, run_event: Callable[[Component, str], None]) -> None:
        """Fire any due Timer components.

        For each Timer on the active page whose `en == 1` and whose
        next-fire timestamp has elapsed, call `run_event(component,
        "codestimer")` exactly once and re-arm to `now_ms + tim`. A
        single overdue timer fires only once per tick regardless of how
        many intervals have elapsed (matches the real device, which
        does not burst-catch-up).

        Timers with `en != 1` are skipped and have their schedule
        cleared, so a re-enable starts a fresh `tim`-ms interval.
        """
        # If the active page changed without an explicit reset, treat
        # this tick as the re-arm point. Don't fire on the first tick
        # after a page switch.
        if self._armed_page is not self.state.active_page:
            self.reset(now_ms)
            return

        for c in self._active_timers():
            key = id(c)
            if not _is_enabled(c):
                # Drop the schedule so re-enabling restarts cleanly.
                self._next_fire.pop(key, None)
                continue
            if key not in self._next_fire:
                # Newly enabled (or newly visible) — arm without firing.
                self._arm(c, now_ms)
                continue
            if now_ms >= self._next_fire[key]:
                run_event(c, "codestimer")
                # Re-arm relative to now, not to the missed deadline,
                # so a slow caller doesn't trigger a burst of catch-up
                # events.
                self._arm(c, now_ms)
