"""Static configuration loaded from config.toml (things you set once per device)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from .palette import ALL_COLOR_NAMES, default_colors

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CalendarConfig:
    id: str
    name: str = ""
    color: str = ""  # empty = next color from palette.DEFAULT_PALETTE
    # "events" draws normal events; "holidays" draws the event title as small
    # italic text next to the date number, like a printed calendar.
    kind: str = "events"


@dataclass
class Config:
    timezone: str = ""
    data_dir: Path = PROJECT_ROOT / "data"

    # display
    backend: str = "preview"  # "inky" on the Pi, "preview" on a laptop
    min_refresh_seconds: int = 60  # never refresh the panel more often than this
    debounce_seconds: float = 1.0  # wait for button/web clicks to settle before refreshing
    buttons: bool = False
    button_pins: list[int] = field(default_factory=lambda: [5, 6, 25, 24])
    frame_button_pins: list[int] = field(default_factory=list)  # [left, right]; empty = none
    # Pause between commands sent to the 13.3" panel. None = the driver's 0.3 s before every
    # one (set it negative for that), which adds ~7.5 s before each refresh starts. Only the pause between power-on and
    # the refresh is needed (scripts/probe_panel_delays.py); that one is the power delay.
    panel_command_delay: float | None = 0.0
    panel_power_command_delay: float = 0.3
    hold_seconds: float = 1.0
    power_off_hold_seconds: float = 3.0  # hold both frame buttons this long to shut down; 0 = off  # how long to hold a frame button for its second action

    # calendar source
    source: str = "sample"  # "google" or "sample"
    auth: str = "service_account"  # "service_account" or "oauth"
    service_account_file: Path = PROJECT_ROOT / "secrets" / "service_account.json"
    oauth_client_file: Path = PROJECT_ROOT / "secrets" / "client_secret.json"
    oauth_token_file: Path = PROJECT_ROOT / "secrets" / "token.json"
    poll_seconds: int = 60
    calendars: list[CalendarConfig] = field(default_factory=list)

    # web server
    host: str = "0.0.0.0"
    port: int = 8080

    @property
    def tz(self) -> ZoneInfo | None:
        return ZoneInfo(self.timezone) if self.timezone else None


def _path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    p = Path(value).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_config(path: Path | None) -> Config:
    raw: dict = {}
    if path is not None:
        with open(path, "rb") as f:
            raw = tomllib.load(f)

    display = raw.get("display", {})
    google = raw.get("google", {})
    server = raw.get("server", {})
    d = Config()

    calendars = []
    for c in raw.get("calendars", []):
        cal = CalendarConfig(
            id=c["id"],
            name=c.get("name", c["id"]),
            color=c.get("color", ""),
            kind=c.get("kind", "events"),
        )
        if cal.color and cal.color not in ALL_COLOR_NAMES:
            raise ValueError(f"Calendar {cal.id!r}: color must be one of {ALL_COLOR_NAMES}")
        calendars.append(cal)
    defaults = default_colors(calendars)
    for cal in calendars:
        cal.color = cal.color or defaults[cal.id]

    return Config(
        timezone=raw.get("timezone", d.timezone),
        data_dir=_path(raw.get("data_dir"), d.data_dir),
        backend=display.get("backend", d.backend),
        min_refresh_seconds=display.get("min_refresh_seconds", d.min_refresh_seconds),
        debounce_seconds=display.get("debounce_seconds", d.debounce_seconds),
        buttons=display.get("buttons", d.buttons),
        button_pins=display.get("button_pins", d.button_pins),
        frame_button_pins=display.get("frame_button_pins", d.frame_button_pins),
        hold_seconds=display.get("hold_seconds", d.hold_seconds),
        power_off_hold_seconds=display.get("power_off_hold_seconds", d.power_off_hold_seconds),
        # Negative = the stock driver (TOML has no None).
        panel_command_delay=(None if (cd := display.get("panel_command_delay", d.panel_command_delay)) is not None
                             and cd < 0 else cd),
        panel_power_command_delay=display.get("panel_power_command_delay", d.panel_power_command_delay),
        source=google.get("source", d.source),
        auth=google.get("auth", d.auth),
        service_account_file=_path(google.get("service_account_file"), d.service_account_file),
        oauth_client_file=_path(google.get("oauth_client_file"), d.oauth_client_file),
        oauth_token_file=_path(google.get("oauth_token_file"), d.oauth_token_file),
        poll_seconds=google.get("poll_seconds", d.poll_seconds),
        calendars=calendars,
        host=server.get("host", d.host),
        port=server.get("port", d.port),
    )
