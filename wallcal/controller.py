"""Keeps the panel in sync with Google Calendar and the web UI.

Two background threads:
  * the main loop polls the calendar, re-renders when anything that affects the
    picture changes (events, the date, settings, the month being viewed) and
    hands new images to...
  * the display worker, which debounces and rate-limits panel refreshes (a full
    refresh of the 13.3" Spectra 6 panel takes ~30-40 seconds). Changes someone asked
    for (buttons, the web UI) skip the rate limit so the panel reacts right away.
"""

from __future__ import annotations

import calendar
import hashlib
import importlib
import io
import json
import logging
import threading
import time
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image

from . import palette
from . import render as render_mod
from .config import Config
from .display import Display, to_panel_orientation
from .models import Event
from .settings import CHOICES, SettingsStore
from .sources import EventSource

log = logging.getLogger(__name__)

# Events are fetched for a window of months around the one shown, so paging
# through nearby months draws straight away instead of waiting on Google.
PREFETCH_MONTHS_BACK = 2
PREFETCH_MONTHS_AHEAD = 6


def add_months(year: int, month: int, n: int) -> tuple[int, int]:
    i = year * 12 + (month - 1) + n
    return i // 12, i % 12 + 1


def _covers(outer: tuple[date, date] | None, inner: tuple[date, date]) -> bool:
    return outer is not None and outer[0] <= inner[0] and inner[1] <= outer[1]


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _fmt_when(dt: datetime) -> str:
    return f"{dt:%b} {dt.day}, {dt.hour % 12 or 12}:{dt.minute:02d}{'a' if dt.hour < 12 else 'p'}"


class Controller:
    def __init__(self, cfg: Config, source: EventSource, display: Display,
                 settings: SettingsStore, dev: bool = False):
        self.cfg = cfg
        self.source = source
        self.display = display
        self.settings = settings
        self.dev = dev

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        # Lets the web UI wait for the redraw a change causes, so its response
        # already has the new preview (no waiting for the next status poll).
        self._render_cond = threading.Condition()
        self._render_asked = 0
        self._render_done = 0

        # Which month is shown. None means "the current month" (the default).
        self.view: tuple[int, int] | None = None
        self._view_set_at = 0.0

        # Calendar data
        self.events: list[Event] = []
        self.events_range: tuple[date, date] | None = None
        self.last_sync_ok: datetime | None = None
        self.last_sync_attempt: datetime | None = None
        self.sync_error: str | None = None
        self._attempted_range: tuple[date, date] | None = None
        self._next_sync = 0.0

        # Rendering
        self._render_key = None
        self._force_next = False
        self._user_change = False  # the next render was asked for by a person
        # Every button press / web change bumps _input_seq; the main loop sets _rendered_seq
        # once it has drawn the result, so the panel never starts on a month you're tapping past.
        self._input_seq = 0
        self._rendered_seq = 0
        self._last_input = -1e9  # monotonic time of the last press: the debounce runs from here
        self._last_request = -1e9  # ...or from the last automatic change
        self._render_mtime = 0.0
        self.render_error: str | None = None
        self.preview_png = b""
        self.preview_raw_png = b""
        self.image_version = 0

        # Panel
        self._display_wake = threading.Event()
        self._pending: tuple[Image.Image, str, bool, bool] | None = None  # img, digest, force, urgent
        self.display_state = "idle"  # idle | pending | refreshing
        self.display_error: str | None = None
        self.last_refresh: datetime | None = None
        self._last_refresh_mono = -1e9
        self._displayed_file = cfg.data_dir / "displayed.sha256"
        self.displayed_hash = self._read(self._displayed_file)
        self._cache_file = cfg.data_dir / "events_cache.json"
        self._load_cache()

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        threading.Thread(target=self._loop, name="main-loop", daemon=True).start()
        threading.Thread(target=self._display_loop, name="display", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._display_wake.set()

    def today(self) -> date:
        return datetime.now(self.cfg.tz).date()

    def shown_month(self) -> tuple[int, int]:
        t = self.today()
        return self.view or (t.year, t.month)

    def navigate(self, action: str, month: str | None = None) -> None:
        """action: prev | next | today | set (with month='YYYY-MM')."""
        with self._lock:
            t = self.today()
            current = (t.year, t.month)
            y, m = self.view or current
            if action == "prev":
                target = add_months(y, m, -1)
            elif action == "next":
                target = add_months(y, m, 1)
            elif action == "today":
                target = current
            elif action == "set" and month:
                yy, mm = (int(p) for p in month.split("-")[:2])
                if not 1 <= mm <= 12:
                    raise ValueError("month must be YYYY-MM")
                target = (yy, mm)
            else:
                raise ValueError(f"Unknown action {action!r}")
            self.view = None if target == current else target
            self._view_set_at = time.monotonic()
            self._note_input()
        self._wake.set()

    def request_sync(self) -> None:
        with self._lock:
            self._next_sync = 0.0
            self._attempted_range = None
        self._wake.set()

    def force_refresh(self) -> None:
        """Redraw the panel even if nothing changed (clears any ghosting)."""
        with self._lock:
            self._render_key = None
            self._force_next = True
            self._note_input()
        self._wake.set()

    def settings_changed(self) -> None:
        with self._lock:
            self._note_input()
        self._wake.set()

    def _note_input(self) -> None:
        """A person asked for a change (call with the lock held)."""
        self._user_change = True
        self._input_seq += 1
        self._last_input = time.monotonic()

    def wait_for_render(self, timeout: float = 5.0) -> None:
        """Block until the main loop has handled every change made so far (or timeout)."""
        with self._render_cond:
            self._render_asked += 1
            target = self._render_asked
        self._wake.set()
        with self._render_cond:
            self._render_cond.wait_for(lambda: self._render_done >= target, timeout)

    def status(self) -> dict:
        with self._lock:
            y, m = self.shown_month()
            s = self.settings.get()
            returns_in = None
            if self.view and s.return_to_today_minutes:
                returns_in = max(0, int(self._view_set_at + s.return_to_today_minutes * 60 - time.monotonic()))
            return {
                "month": f"{y:04d}-{m:02d}",
                "month_label": f"{calendar.month_name[m]} {y}",
                "is_current": self.view is None,
                "returns_to_today_in": returns_in,
                "today": self.today().isoformat(),
                "source": self.cfg.source,
                "backend": self.cfg.backend,
                "dev": self.dev,
                "last_sync": self.last_sync_ok.isoformat(timespec="seconds") if self.last_sync_ok else None,
                "sync_error": self.sync_error,
                "event_count": len(self.events),
                "display_state": self.display_state,
                "display_error": self.display_error,
                "last_refresh": self.last_refresh.isoformat(timespec="seconds") if self.last_refresh else None,
                "render_error": self.render_error,
                "image_version": self.image_version,
                "settings": asdict(s),
                "calendars": [asdict(c) for c in self.source.calendars],
                "choices": CHOICES,
                "colors": palette.PICKER_NAMES,
                "swatches": palette.swatches(),
                "color_aliases": palette.ALIASES,
                "color_groups": palette.picker_groups(),
                "holiday_color_groups": palette.picker_groups(palette.HOLIDAY_NAMES),
                "palettes": [
                    {"id": key, "label": label, "colors": palette.default_colors(self.source.calendars, key)}
                    for key, (label, _) in palette.PALETTES.items()
                ],
            }

    # --------------------------------------------------------------- main loop

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._render_cond:
                asked = self._render_asked
            try:
                self._tick()
            except Exception:
                log.exception("Main loop error")
            with self._render_cond:
                self._render_done = asked
                self._render_cond.notify_all()
            self._wake.wait(1.0)
            self._wake.clear()

    def _tick(self) -> None:
        s = self.settings.get()
        with self._lock:
            if self.view and s.return_to_today_minutes:
                if time.monotonic() - self._view_set_at > s.return_to_today_minutes * 60:
                    log.info("Returning to the current month")
                    self.view = None
            need = self._needed_range()
            due = time.monotonic() >= self._next_sync
            new_range = not _covers(self.events_range, need) and not _covers(self._attempted_range, need)
        if new_range:
            self._sync(self._fetch_range())  # a month outside the fetched window needs its events first
        else:
            self._maybe_render()  # draw changes first so a routine sync doesn't delay them
            if due:
                self._sync(self._fetch_range())
        self._maybe_render()

    def _needed_range(self) -> tuple[date, date]:
        """Every day that can appear in the month grid (with some slack)."""
        y, m = self.shown_month()
        first = date(y, m, 1)
        ny, nm = add_months(y, m, 1)
        return first - timedelta(days=7), date(ny, nm, 1) + timedelta(days=14)

    def _fetch_range(self) -> tuple[date, date]:
        """The shown month plus the prefetch window around it."""
        y, m = self.shown_month()
        start = date(*add_months(y, m, -PREFETCH_MONTHS_BACK), 1)
        end = date(*add_months(y, m, PREFETCH_MONTHS_AHEAD + 1), 1)
        return start - timedelta(days=7), end + timedelta(days=14)

    def _sync(self, rng: tuple[date, date]) -> None:
        with self._lock:
            self._attempted_range = rng
            self._next_sync = time.monotonic() + self.cfg.poll_seconds
        try:
            events = self.source.fetch(*rng)
        except Exception as e:
            log.warning("Calendar sync failed: %s", e)
            with self._lock:
                self.sync_error = str(e) or type(e).__name__
                self.last_sync_attempt = datetime.now()
            return
        with self._lock:
            self.events = sorted(events, key=lambda e: (e.start, e.title))
            self.events_range = rng
            self.last_sync_ok = self.last_sync_attempt = datetime.now()
            self.sync_error = None
        self._save_cache()

    def _maybe_render(self) -> None:
        with self._lock:
            seq = self._input_seq
        try:
            self._render_if_changed()
        finally:
            with self._lock:
                caught_up = seq > self._rendered_seq
                self._rendered_seq = max(self._rendered_seq, seq)
            if caught_up:
                self._display_wake.set()

    def _render_if_changed(self) -> None:
        if self.dev:
            self._reload_renderer_if_changed()
        s = self.settings.get()
        with self._lock:
            if self.last_sync_attempt is None:
                return  # don't overwrite the panel with an empty month before the first sync
            y, m = self.shown_month()
            today = self.today()
            hidden = set(s.hidden_calendars)
            calendars = [
                replace(c, color=s.calendar_colors.get(c.id, c.color))
                for c in self.source.calendars if c.id not in hidden
            ]
            warning = self._stale_warning()
            lo, hi = self._needed_range()
            events = tuple(e for e in self.events if e.last_day >= lo and e.first_day <= hi)
            key = (y, m, today, events, json.dumps(asdict(s), sort_keys=True),
                   repr(calendars), warning, self._render_mtime)
            urgent = self._user_change
            self._user_change = False
            if key == self._render_key:
                return
            self._render_key = key
            force = self._force_next
            self._force_next = False

        try:
            img = render_mod.render(render_mod.RenderInput(
                year=y, month=m, today=today, events=list(events),
                calendars=calendars, settings=s, warning=warning,
            ))
        except Exception as e:
            log.exception("Render failed")
            with self._lock:
                self.render_error = f"{type(e).__name__}: {e}"
            return

        panel_img = to_panel_orientation(img, s.orientation)
        digest = hashlib.sha256(panel_img.tobytes()).hexdigest()
        log.info("Rendered %04d-%02d (%d events)", y, m, len(events))
        if force or digest != self.displayed_hash:
            self._request_display(panel_img, digest, force, urgent)
        # The web previews come after the panel request: on a Pi Zero they take ~half a second.
        preview, raw = _png(palette.simulate(img)), _png(img)
        with self._lock:
            self.render_error = None
            self.preview_png, self.preview_raw_png = preview, raw
            self.image_version += 1

    def _stale_warning(self) -> str:
        if self.cfg.source != "google" or self.sync_error is None:
            return ""
        if self.last_sync_ok is None:
            return "Can't reach Google Calendar"
        if datetime.now() - self.last_sync_ok < timedelta(minutes=30):
            return ""  # ride out short blips quietly
        return f"Can't reach Google Calendar · last synced {_fmt_when(self.last_sync_ok)}"

    def _reload_renderer_if_changed(self) -> None:
        mtime = max(p.stat().st_mtime for p in Path(render_mod.__file__).parent.glob("*.py"))
        if mtime != self._render_mtime:
            if self._render_mtime:
                log.info("Renderer changed on disk; reloading")
                try:
                    importlib.reload(palette)
                    importlib.reload(render_mod)
                except Exception as e:
                    log.exception("Reload failed")
                    self.render_error = f"{type(e).__name__}: {e}"
            self._render_mtime = mtime

    # ----------------------------------------------------------- display worker

    def _request_display(self, img: Image.Image, digest: str, force: bool, urgent: bool) -> None:
        with self._lock:
            _, _, prev_force, prev_urgent = self._pending or (None, None, False, False)
            self._pending = (img, digest, force or prev_force, urgent or prev_urgent)
            if not urgent:
                self._last_request = time.monotonic()
            if self.display_state != "refreshing":
                self.display_state = "pending"
        self._display_wake.set()

    def _display_loop(self) -> None:
        while not self._stop.is_set():
            self._display_wake.wait()
            # Debounce: wait until presses stop (e.g. tapping "next" 3 times) and the main
            # loop has drawn the last one. The wait runs from the press, so it overlaps
            # the render instead of adding to it.
            while not self._stop.is_set():
                self._display_wake.clear()
                with self._lock:
                    if self._pending is None:
                        break
                    caught_up = self._rendered_seq >= self._input_seq
                    wait = max(self._last_input, self._last_request) + self.cfg.debounce_seconds - time.monotonic()
                if caught_up and wait <= 0:
                    break
                self._display_wake.wait(min(wait, 1.0) if wait > 0 else 1.0)
            # Rate limit automatic refreshes to give the panel a rest. Ones a person
            # asked for go now, or they'd look like the button didn't work.
            with self._lock:
                if self._pending is None:
                    continue
                urgent = self._pending[3]
            remaining = self._last_refresh_mono + self.cfg.min_refresh_seconds - time.monotonic()
            if remaining > 0 and not urgent:
                log.info("Waiting %.0fs before next panel refresh", remaining)
                if self._display_wake.wait(remaining) or self._stop.is_set():
                    continue  # a new request (maybe a button press): debounce it, then check again
            with self._lock:
                item, self._pending = self._pending, None
                if item is None:
                    continue
                img, digest, force, _ = item
                if digest == self.displayed_hash and not force:
                    self.display_state = "idle"
                    continue
                self.display_state = "refreshing"
            log.info("Refreshing panel...")
            t0 = time.monotonic()
            try:
                self.display.show(img)
            except Exception as e:
                log.exception("Panel refresh failed")
                with self._lock:
                    self.display_error = f"{type(e).__name__}: {e}"
                    self.display_state = "pending" if self._pending else "idle"
                continue
            log.info("Panel refreshed in %.1fs", time.monotonic() - t0)
            with self._lock:
                self.displayed_hash = digest
                self.display_error = None
                self.last_refresh = datetime.now()
                self._last_refresh_mono = time.monotonic()
                self.display_state = "pending" if self._pending else "idle"
            self._write(self._displayed_file, digest)

    # ----------------------------------------------------------------- storage

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            return path.read_text().strip() or None
        except OSError:
            return None

    @staticmethod
    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(path)

    def _load_cache(self) -> None:
        """Last fetched events, so a reboot without Wi-Fi still shows the calendar."""
        try:
            raw = json.loads(self._cache_file.read_text())
            if raw.get("source") != self.cfg.source:
                return
            self.events = [Event.from_json(e) for e in raw["events"]]
            self.events_range = (date.fromisoformat(raw["range"][0]), date.fromisoformat(raw["range"][1]))
            self.last_sync_ok = datetime.fromisoformat(raw["synced_at"])
        except (OSError, ValueError, KeyError) as e:
            if not isinstance(e, FileNotFoundError):
                log.warning("Ignoring events cache: %s", e)

    def _save_cache(self) -> None:
        with self._lock:
            data = {
                "source": self.cfg.source,
                "range": [d.isoformat() for d in self.events_range],
                "synced_at": self.last_sync_ok.isoformat(),
                "events": [e.to_json() for e in self.events],
            }
        self._write(self._cache_file, json.dumps(data))
