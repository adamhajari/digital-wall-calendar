"""GPIO push buttons: the four on the side of the Inky Impression, plus optional ones on the frame.

Every button connects its pin to ground when pressed (the internal pull-up holds it high otherwise).
A button with a hold action fires its press action on release, or its hold action once it has been
held down for `hold_seconds`. A button without one fires as soon as it goes down. A combo fires when
all of its buttons are held together for its own time; their single actions are then skipped.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

log = logging.getLogger(__name__)


@dataclass
class Button:
    pin: int
    name: str
    on_press: Callable[[], None]
    on_hold: Callable[[], None] | None = None


@dataclass
class Combo:
    """Hold several buttons (indexes into the button list, each with an on_hold) together."""
    buttons: tuple[int, ...]
    name: str
    seconds: float
    action: Callable[[], None]


def _call(button: Button, action: str) -> None:
    log.info("Button %s %s", button.name, "held" if action == "hold" else "pressed")
    try:
        (button.on_hold if action == "hold" else button.on_press)()
    except Exception:
        log.exception("Button handler failed")


class _PressTracker:
    """Turns down/up edges into press and hold actions. Separate from gpiod so it can be tested."""

    def __init__(self, buttons: list[Button], hold_seconds: float, combos: list[Combo] = ()):
        self.buttons = buttons
        self.hold_seconds = hold_seconds
        self.combos = list(combos)
        self._lock = threading.Lock()
        self._timers: dict[int, threading.Timer] = {}
        self._held: set[int] = set()
        self._down: set[int] = set()
        self._combo_timers: dict[int, threading.Timer] = {}

    def down(self, i: int) -> None:
        b = self.buttons[i]
        if b.on_hold is None:
            _call(b, "press")
            return
        with self._lock:
            if i in self._timers or i in self._down:  # already down (missed release)
                return
            self._down.add(i)
            for ci, combo in enumerate(self.combos):
                if i in combo.buttons and self._down.issuperset(combo.buttons):
                    # All of the combo's buttons are down: drop their own press/hold actions.
                    for j in combo.buttons:
                        t = self._timers.pop(j, None)
                        if t:
                            t.cancel()
                    timer = threading.Timer(combo.seconds, self._combo, args=(ci,))
                    timer.daemon = True
                    self._combo_timers[ci] = timer
                    timer.start()
                    return
            self._held.discard(i)
            timer = threading.Timer(self.hold_seconds, self._hold, args=(i,))
            timer.daemon = True
            self._timers[i] = timer
            timer.start()

    def up(self, i: int) -> None:
        b = self.buttons[i]
        if b.on_hold is None:
            return
        with self._lock:
            self._down.discard(i)
            for ci, combo in enumerate(self.combos):
                if i in combo.buttons and ci in self._combo_timers:
                    self._combo_timers.pop(ci).cancel()
            timer = self._timers.pop(i, None)
            if timer is None:
                return
            timer.cancel()
            was_held = i in self._held
            self._held.discard(i)
        if not was_held:
            _call(b, "press")

    def _hold(self, i: int) -> None:
        with self._lock:
            if i not in self._timers:  # released just as the timer fired
                return
            self._held.add(i)
        _call(self.buttons[i], "hold")

    def _combo(self, ci: int) -> None:
        with self._lock:
            if self._combo_timers.pop(ci, None) is None:  # released just as the timer fired
                return
        combo = self.combos[ci]
        log.info("Buttons %s held together", combo.name)
        try:
            combo.action()
        except Exception:
            log.exception("Button combo handler failed")


def start_buttons(buttons: list[Button], hold_seconds: float = 1.0, combos: list[Combo] = ()) -> None:
    """Listen for the buttons in a daemon thread.

    The Impression 13.3" onboard buttons are on GPIO 5, 6, 25, 24 (other Impressions use 16 for C).
    """
    if not buttons:
        return
    try:
        import gpiod
        import gpiodevice
        from gpiod import EdgeEvent
        from gpiod.line import Bias, Direction, Edge
    except ImportError:
        log.warning("gpiod not installed; buttons disabled")
        return

    try:
        settings = gpiod.LineSettings(
            direction=Direction.INPUT,
            bias=Bias.PULL_UP,
            edge_detection=Edge.BOTH,
            debounce_period=timedelta(milliseconds=50),
        )
        chip = gpiodevice.find_chip_by_platform()
        offsets = [chip.line_offset_from_id(b.pin) for b in buttons]
        request = chip.request_lines(consumer="wallcal-buttons", config=dict.fromkeys(offsets, settings))
    except Exception:
        log.exception("Could not set up buttons; continuing without them")
        return

    tracker = _PressTracker(buttons, hold_seconds, combos)

    def loop():
        while True:
            for event in request.read_edge_events():
                i = offsets.index(event.line_offset)
                if event.event_type == EdgeEvent.Type.FALLING_EDGE:
                    tracker.down(i)
                else:
                    tracker.up(i)

    threading.Thread(target=loop, name="buttons", daemon=True).start()
    log.info("Listening for buttons on GPIO %s", [b.pin for b in buttons])
