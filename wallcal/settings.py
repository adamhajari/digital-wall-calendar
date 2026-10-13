"""Display settings that can be changed from the web UI (persisted to data/settings.json)."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .palette import ACCENT_NAMES, ALL_COLOR_NAMES

log = logging.getLogger(__name__)

CHOICES = {
    "week_start": ["sunday", "monday"],
    "orientation": ["landscape", "portrait", "landscape-flipped", "portrait-flipped"],
    "time_format": ["12h", "24h"],
    "event_text_size": ["small", "medium-small", "medium", "large"],
    "accent_color": ACCENT_NAMES,
}


@dataclass
class Settings:
    title: str = ""  # small line above the month name, e.g. "The Smith Family"
    week_start: str = "sunday"
    orientation: str = "landscape"  # how the panel hangs on the wall
    time_format: str = "12h"
    accent_color: str = "red"
    show_event_times: bool = True
    event_text_size: str = "medium"
    wrap_event_titles: bool = True  # use a second line for long titles when the day has room
    show_mini_months: bool = True
    show_adjacent_events: bool = True  # events on the grayed days of prev/next month
    # When you browse to another month, jump back to the current one after this
    # many minutes (0 = stay until you press "Today").
    return_to_today_minutes: int = 30
    # Extra blank space around the calendar (pixels), e.g. so a picture frame's
    # mat doesn't cover the edges. Added to the built-in 32px margin.
    margin_sides: int = 0
    margin_top_bottom: int = 0
    hidden_calendars: list[str] = field(default_factory=list)
    calendar_colors: dict[str, str] = field(default_factory=dict)  # overrides config.toml

    def validate(self) -> None:
        for key, options in CHOICES.items():
            if getattr(self, key) not in options:
                raise ValueError(f"{key} must be one of {options}")
        for cal_id, color in self.calendar_colors.items():
            if color not in ALL_COLOR_NAMES:
                raise ValueError(f"Color for {cal_id} must be one of {ALL_COLOR_NAMES}")
        if self.return_to_today_minutes < 0:
            raise ValueError("return_to_today_minutes must be >= 0")
        for key in ("margin_sides", "margin_top_bottom"):
            if not 0 <= getattr(self, key) <= 300:
                raise ValueError(f"{key} must be between 0 and 300")


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text())
            known = {f.name for f in fields(Settings)}
            s = Settings(**{k: v for k, v in raw.items() if k in known})
            s.validate()
            return s
        except (ValueError, TypeError) as e:
            log.warning("Ignoring invalid %s: %s", self.path, e)
            return Settings()

    def get(self) -> Settings:
        with self._lock:
            return Settings(**asdict(self._settings))

    def update(self, changes: dict) -> Settings:
        with self._lock:
            known = {f.name: f for f in fields(Settings)}
            data = asdict(self._settings)
            for key, value in changes.items():
                if key not in known:
                    raise ValueError(f"Unknown setting {key!r}")
                default = getattr(Settings(), key)
                if isinstance(default, bool):
                    value = bool(value)
                elif isinstance(default, int):
                    value = int(value)
                data[key] = value
            new = Settings(**data)
            new.validate()
            self._settings = new
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(asdict(new), indent=2))
            return Settings(**asdict(new))
