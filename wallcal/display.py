"""Output backends: the real Inky panel, or a PNG file for laptop development."""

from __future__ import annotations

import logging
import time
import warnings
from pathlib import Path
from typing import Protocol

from PIL import Image

from . import palette

log = logging.getLogger(__name__)


class Display(Protocol):
    def show(self, img: Image.Image) -> None:
        """Show a 1600x1200 RGB image. Blocks until the refresh finishes."""
        ...


def to_panel_orientation(img: Image.Image, orientation: str) -> Image.Image:
    """Rotate the logical (as-seen-on-the-wall) image onto the 1600x1200 panel."""
    angle = {
        "landscape": 0,
        "landscape-flipped": 180,
        "portrait": -90,
        "portrait-flipped": 90,
    }[orientation]
    return img.rotate(angle, expand=True) if angle else img


def make_display(backend: str, data_dir: Path, command_delay: float | None = None,
                 power_command_delay: float = 0.3) -> Display:
    if backend == "inky":
        return InkyDisplay(command_delay, power_command_delay)
    if backend == "preview":
        return PreviewDisplay(data_dir / "panel.png")
    raise ValueError(f"Unknown display backend {backend!r} (expected 'inky' or 'preview')")


class InkyDisplay:
    def __init__(self, command_delay: float | None = None, power_command_delay: float = 0.3):
        try:
            from inky.auto import auto

            self.inky = auto(ask_user=False, verbose=True)
        except Exception as e:  # EEPROM not readable (e.g. I2C disabled)
            log.warning("Inky auto-detect failed (%s); assuming Impression 13.3\"", e)
            self.inky = None
        if self.inky is None:
            from inky.inky_el133uf1 import Inky

            self.inky = Inky(resolution=(1600, 1200))
        if command_delay is not None and type(self.inky).__module__ == "inky.inky_el133uf1":
            self.inky = _el133uf1_with_delays(command_delay, power_command_delay, self.inky.resolution)
            log.info("Panel command delay: %ss (%ss after power-on)", command_delay, power_command_delay)
        log.info("Using %s at %sx%s", type(self.inky).__module__, *self.inky.resolution)

    def show(self, img: Image.Image) -> None:
        # A 'P' image with exactly the 6 panel colors is sent as-is (no dithering).
        self.inky.set_image(palette.to_panel(img))
        # The driver only warns when the panel never signals a refresh, then returns as if it
        # worked. Turn that into an error so it isn't logged as "Panel refreshed".
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.inky.show()
        for w in caught:
            if "Timed out" in str(w.message):
                raise RuntimeError(f"Panel didn't respond ({w.message}). If it keeps happening, "
                                   "shut down and unplug the power for 10 seconds.")


class PreviewDisplay:
    """Stands in for the panel on a laptop: writes what the panel would show."""

    def __init__(self, path: Path):
        self.path = path

    def show(self, img: Image.Image) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        palette.simulate(img).save(self.path)
        log.info("Preview display updated: %s", self.path)


def _el133uf1_with_delays(delay: float, power_on_delay: float, resolution: tuple[int, int]):
    """The 13.3" driver without most of its pauses between SPI commands.

    The stock driver sleeps 0.3 s before every command, and ~25 commands go out before each
    refresh starts, so the panel sat still for ~9 s after a button press. Probing the panel
    (scripts/probe_panel_delays.py) showed only one pause matters: between power-on (PON) and the
    refresh (DRF), as in Waveshare's driver for the same panel. Without it the panel ignores
    the refresh. That one waits `power_on_delay`, every other command `delay`.
    Otherwise identical to inky_el133uf1.Inky._send_command.
    """
    from gpiod.line import Value
    from inky import inky_el133uf1 as drv

    class Inky(drv.Inky):
        _after_power_on = False

        def _send_command(self, command, cs_sel=None, data=None):
            if cs_sel is None:
                return
            if cs_sel & drv.CS0_SEL:
                self._gpio.set_value(self.cs0_pin, Value.INACTIVE)
            if cs_sel & drv.CS1_SEL:
                self._gpio.set_value(self.cs1_pin, Value.INACTIVE)
            self._gpio.set_value(self.dc_pin, Value.INACTIVE)
            time.sleep(power_on_delay if self._after_power_on else delay)
            self._after_power_on = command == drv.EL133UF1_PON
            self._spi_bus.xfer3([command])
            if data is not None:
                self._gpio.set_value(self.dc_pin, Value.ACTIVE)
                self._spi_bus.xfer3(data)
            self._gpio.set_value(self.cs0_pin, Value.ACTIVE)
            self._gpio.set_value(self.cs1_pin, Value.ACTIVE)
            self._gpio.set_value(self.dc_pin, Value.INACTIVE)

    # Pass the resolution: without it the driver looks it up from the EEPROM and crashes on
    # display variant 21 (the 13.3"), indexing a 3-item list with 21. inky.auto does the same.
    return Inky(resolution=tuple(resolution))
