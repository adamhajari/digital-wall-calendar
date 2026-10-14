"""Photo mode: which photo is on the panel, and drawing it in the panel's six inks."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict

from PIL import Image, ImageEnhance, ImageOps

from .. import palette
from ..render import logical_size
from ..settings import Settings
from .store import FITS, PhotoStore

log = logging.getLogger(__name__)

# Photos are dithered against what the inks really look like (palette.SIMULATED), so the
# dot pattern reproduces the photo's colors as closely as the panel can. (Dithering against
# the pure colors instead looks more washed out: it scatters in extra white.) The inks are
# much duller than a screen, so color, contrast and brightness are pushed up first. More
# boost is punchier but starts to posterize soft backgrounds.
# boost -> (saturation, contrast, brightness)
BOOSTS = {
    "natural": (1.25, 1.1, 1.0),
    "vivid": (1.6, 1.2, 1.05),
    "extra": (2.0, 1.3, 1.1),
}


def to_inks(img: Image.Image, boost: str = "vivid") -> Image.Image:
    """Dither a photo to the panel's six inks, drawn with the pure colors the panel expects."""
    saturation, contrast, brightness = BOOSTS[boost]
    img = ImageEnhance.Color(img).enhance(saturation)
    img = ImageEnhance.Contrast(img).enhance(contrast)
    img = ImageEnhance.Brightness(img).enhance(brightness)
    real = Image.new("P", (1, 1))
    real.putpalette([v for name in palette.COLOR_NAMES for v in palette.SIMULATED[name]])
    out = img.quantize(palette=real, dither=Image.Dither.FLOYDSTEINBERG)
    out.putpalette([v for name in palette.COLOR_NAMES for v in palette.PURE[name]])
    return out.convert("RGB")


class PhotoScreen:
    """Implements wallcal.controller.AltScreen. Off at startup, so the calendar shows first."""

    name = "photos"

    def __init__(self, store: PhotoStore):
        self.store = store
        self._lock = threading.Lock()
        self._active = False
        self._current: str | None = None
        self._shown_at = 0.0  # monotonic time the current photo went up

    # ---------------------------------------------------------- AltScreen

    def active(self) -> bool:
        with self._lock:
            return self._active

    def render_key(self, settings: Settings):
        with self._lock:
            interval = self.store.settings().interval_minutes
            if interval and time.monotonic() - self._shown_at >= interval * 60:
                self._step(1)
                log.info("Next photo: %s", self._current)
            photo_settings = self.store.settings()
            return self._current, photo_settings.fit, photo_settings.boost

    def render(self, settings: Settings) -> Image.Image:
        with self._lock:
            photo_id = self._current
        size = logical_size(settings)
        if photo_id is None:
            return Image.new("RGB", size, palette.WHITE)
        img = self.store.open(photo_id)
        photo_settings = self.store.settings()
        if photo_settings.fit == "fit":
            img = ImageOps.pad(img, size, Image.Resampling.LANCZOS, color=palette.SIMULATED["white"])
        else:
            img = ImageOps.fit(img, size, Image.Resampling.LANCZOS)
        return to_inks(img, photo_settings.boost)

    def navigate(self, action: str) -> bool:
        if action in ("prev", "next"):
            self.step(-1 if action == "prev" else 1)
        elif action == "today":  # the calendar's "home" button takes you back to it
            self.set_active(False)
        else:
            return False
        return True

    def status(self) -> dict:
        with self._lock:
            active, current = self._active, self._current
            settings = self.store.settings()
            next_in = None
            if active and settings.interval_minutes:
                next_in = max(0, int(self._shown_at + settings.interval_minutes * 60 - time.monotonic()))
        return {
            "active": active,
            "current": current,
            "next_in": next_in,
            "settings": asdict(settings),
            "fits": FITS,
            "photos": [asdict(p) for p in self.store.photos()],
        }

    # ------------------------------------------------------------ control
    # These change what's shown; the caller tells the controller (via the web routes,
    # or Controller.navigate) so the panel redraws.

    def set_active(self, on: bool) -> None:
        with self._lock:
            if on and not self._selected():
                raise ValueError("Choose at least one photo to show first")
            if on and not self._active:
                if self._current not in self._selected():
                    self._step(1)
                self._shown_at = time.monotonic()
            self._active = on

    def show(self, photo_id: str) -> None:
        """Put this photo up now (adding it to the rotation) and switch to photo mode."""
        self.store.set_selected(photo_id, True)
        with self._lock:
            self._current = photo_id
            self._shown_at = time.monotonic()
            self._active = True

    def step(self, delta: int) -> None:
        with self._lock:
            self._step(delta)

    def selection_changed(self) -> None:
        """After photos are deselected or deleted: move off a photo that's no longer in the rotation."""
        with self._lock:
            selected = self._selected()
            if not selected:
                self._active = False
                self._current = None
            elif self._current not in selected:
                self._step(1)

    # ----------------------------------------------------------- internal

    def _selected(self) -> list[str]:
        return [p.id for p in self.store.photos() if p.selected]

    def _step(self, delta: int) -> None:
        """Move through the selected photos in upload order (call with the lock held)."""
        self._shown_at = time.monotonic()
        order = {p.id: k for k, p in enumerate(self.store.photos())}
        selected = self._selected()
        if not selected:
            self._current = None
            return
        if self._current in selected:
            i = selected.index(self._current) + delta
        else:
            # It left the rotation (or nothing was up yet): carry on from where it sat.
            here = order.get(self._current, -1)
            after = [k for k, pid in enumerate(selected) if order[pid] > here]
            i = (after[0] if after else len(selected)) - (delta < 0)
        self._current = selected[i % len(selected)]
