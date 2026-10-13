"""Find which of the 13.3" panel's commands actually need the driver's 0.3 s pause.

The stock driver sleeps 0.3 s before each of the ~28 commands in an update, and shortening
every pause makes the panel silently skip the refresh. This shortens a subset at a time, splitting
it in half on failure, until it finds the pauses that matter. Each trial is one full refresh
(~30 s); expect 15-30 trials. A trial counts as refreshed when the panel goes busy within 3 s
of the refresh command and stays busy for a while (a real refresh takes ~18 s).

Run on the Pi with the calendar stopped (it holds the GPIO pins):

    sudo systemctl stop wallcal
    .venv/bin/python scripts/probe_panel_delays.py
    .venv/bin/python scripts/probe_panel_delays.py --slow 22,25   # just try one set
    sudo systemctl start wallcal
"""

from __future__ import annotations

import argparse
import sys
import time
import warnings
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from wallcal import palette  # noqa: E402

STOCK_DELAY = 0.3
REFRESHED_SECONDS = 5.0  # a refresh that returns sooner than this didn't happen


class Stuck(Exception):
    pass


def make_panel(fast_delay: float):
    from gpiod.line import Value
    from inky import inky_el133uf1 as drv

    names = {getattr(drv, n): n.removeprefix("EL133UF1_") for n in dir(drv) if n.startswith("EL133UF1_")
             and isinstance(getattr(drv, n), int)}

    class Panel(drv.Inky):
        slow: set[int] = set()  # pauses (by position in the update) kept at the stock 0.3 s

        def _update(self, buf_a, buf_b):
            self.sent = []  # (position, command name, time sent)
            self.refresh_seconds = None
            super()._update(buf_a, buf_b)

        def _send_command(self, command, cs_sel=None, data=None):
            # Same as the stock driver, with the pause chosen per position.
            if cs_sel is None:
                return
            if cs_sel & drv.CS0_SEL:
                self._gpio.set_value(self.cs0_pin, Value.INACTIVE)
            if cs_sel & drv.CS1_SEL:
                self._gpio.set_value(self.cs1_pin, Value.INACTIVE)
            self._gpio.set_value(self.dc_pin, Value.INACTIVE)
            i = len(self.sent)
            time.sleep(STOCK_DELAY if i in self.slow else fast_delay)
            self.sent.append((i, names.get(command, hex(command)), time.monotonic()))
            self._spi_bus.xfer3([command])
            if data is not None:
                self._gpio.set_value(self.dc_pin, Value.ACTIVE)
                self._spi_bus.xfer3(data)
            self._gpio.set_value(self.cs0_pin, Value.ACTIVE)
            self._gpio.set_value(self.cs1_pin, Value.ACTIVE)
            self._gpio.set_value(self.dc_pin, Value.INACTIVE)

        def _refresh_wait(self, timeout=65.0):
            # The stock wait gives the panel 65 s to start a skipped refresh; give up after 3 s.
            t = time.monotonic()
            while self._gpio.get_value(self.busy_pin) == Value.ACTIVE:
                if time.monotonic() - t > 3:
                    self.refresh_seconds = 0.0
                    return
                time.sleep(0.01)
            super()._refresh_wait(timeout)  # busy is asserted; this waits for it to clear
            self.refresh_seconds = time.monotonic() - t

    # Resolution passed explicitly: the EEPROM lookup crashes on the 13.3" (see wallcal/display.py).
    return Panel(resolution=(1600, 1200))


def card(text: str) -> Image.Image:
    img = Image.new("RGB", (1600, 1200), palette.WHITE)
    ImageDraw.Draw(img).multiline_text((800, 450), text, fill=palette.BLACK, anchor="ma", align="center",
                                       font=ImageFont.load_default(size=120))
    return palette.to_panel(img)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", type=float, default=0.0, help="the shortened pause (default 0)")
    ap.add_argument("--slow", help="comma-separated positions to keep at 0.3 s; runs one trial and exits")
    args = ap.parse_args()

    panel = make_panel(args.fast)
    every = set(range(64))  # more than the update sends; the extras are harmless
    trials = 0

    def trial(slow: set[int]) -> bool:
        nonlocal trials
        trials += 1
        panel.slow = slow
        label = "all" if len(slow) >= len(every) else ",".join(map(str, sorted(slow))) or "none"
        panel.set_image(card(f"trial {trials}\nslow: {label}"))
        t = time.monotonic()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            panel.show()
        drf = next(ts for _, name, ts in panel.sent if name == "DRF")
        ok = (panel.refresh_seconds or 0) >= REFRESHED_SECONDS
        print(f"trial {trials:2}: slow={label}  refresh started after {drf - t:4.1f}s, "
              f"busy {panel.refresh_seconds or 0:4.1f}s -> {'REFRESHED' if ok else 'no refresh'}", flush=True)
        if any("Timed out" in str(w.message) for w in caught):
            raise Stuck("panel timed out; shut down, unplug it for 10 s, and rerun")
        return ok

    if args.slow is not None:
        trial({int(s) for s in args.slow.split(",") if s.strip()})
        return

    if not trial(every):
        sys.exit("The panel didn't refresh even with every pause at 0.3 s; nothing to probe.")
    positions = list(range(len(panel.sent)))
    print("Commands in an update:", ", ".join(f"{i}={name}" for i, name, _ in panel.sent), flush=True)

    fast: set[int] = set()

    def needed(group: list[int]) -> list[int]:
        """The positions in group that must stay slow, given `fast` is already known to be OK."""
        nonlocal fast
        if trial(set(positions) - fast - set(group)):
            fast |= set(group)
            return []
        if len(group) == 1:
            return group
        mid = len(group) // 2
        return needed(group[:mid]) + needed(group[mid:])

    slow = needed(positions)
    print(f"\nPauses that seem to need 0.3 s: {slow} "
          f"({', '.join(name for i, name, _ in panel.sent if i in slow)})")
    print("Confirming twice...")
    confirmed = trial(set(slow)) and trial(set(slow))
    print("Confirmed." if confirmed else "Not reliable: the result is borderline; rerun or use 0.3 s.")
    print(f"{trials} trials.")


if __name__ == "__main__":
    try:
        main()
    except Stuck as e:
        sys.exit(str(e))
