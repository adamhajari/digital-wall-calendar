"""Where events come from: Google Calendar, or made-up sample data for development."""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .config import CalendarConfig, Config
from .models import Event

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
API = "https://www.googleapis.com/calendar/v3"


class EventSource(Protocol):
    calendars: list[CalendarConfig]

    def fetch(self, start: date, end: date) -> list[Event]:
        """Events touching [start, end). Raises on failure."""
        ...


def make_source(cfg: Config) -> EventSource:
    if cfg.source == "google":
        return GoogleSource(cfg)
    if cfg.source == "sample":
        return SampleSource(cfg.calendars)
    raise ValueError(f"Unknown source {cfg.source!r} (expected 'google' or 'sample')")


# --------------------------------------------------------------------------- Google


def load_credentials(cfg: Config):
    if cfg.auth == "service_account":
        from google.oauth2 import service_account

        if not cfg.service_account_file.exists():
            raise FileNotFoundError(f"Service account key not found: {cfg.service_account_file}")
        return service_account.Credentials.from_service_account_file(
            str(cfg.service_account_file), scopes=SCOPES
        )
    if cfg.auth == "oauth":
        from google.oauth2.credentials import Credentials

        if not cfg.oauth_token_file.exists():
            raise FileNotFoundError(
                f"OAuth token not found: {cfg.oauth_token_file}. Run `python -m wallcal auth` first."
            )
        return Credentials.from_authorized_user_file(str(cfg.oauth_token_file), SCOPES)
    raise ValueError(f"Unknown auth {cfg.auth!r} (expected 'service_account' or 'oauth')")


def run_oauth_flow(cfg: Config) -> None:
    """Interactive browser login; writes the token file. Run on a machine with a browser."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(cfg.oauth_client_file), SCOPES)
    creds = flow.run_local_server(port=0)
    cfg.oauth_token_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.oauth_token_file.write_text(creds.to_json())
    print(f"Saved token to {cfg.oauth_token_file}")


def _session(cfg: Config):
    from google.auth.transport.requests import AuthorizedSession

    return AuthorizedSession(load_credentials(cfg))


def list_calendars(cfg: Config) -> list[dict]:
    """Calendars visible in the account's calendar list (OAuth only, really:
    a service account's list is empty until calendars are added to it)."""
    resp = _session(cfg).get(f"{API}/users/me/calendarList", timeout=30)
    resp.raise_for_status()
    return resp.json().get("items", [])


def _local(dt: datetime, tz: ZoneInfo | None) -> datetime:
    """Aware datetime -> naive wall-clock time in the display's timezone."""
    return dt.astimezone(tz).replace(tzinfo=None)


def _parse_event(item: dict, calendar_id: str, tz: ZoneInfo | None) -> Event | None:
    if item.get("status") == "cancelled":
        return None
    for attendee in item.get("attendees", []):
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return None

    start, end = item["start"], item.get("end", item["start"])
    if "date" in start:
        s = datetime.fromisoformat(start["date"])
        e = datetime.fromisoformat(end["date"])
        all_day = True
    else:
        s = _local(datetime.fromisoformat(start["dateTime"]), tz)
        e = _local(datetime.fromisoformat(end["dateTime"]), tz)
        all_day = False

    # Events from calendars shared as "free/busy only" have no title.
    title = (item.get("summary") or "Busy").strip()
    return Event(title=title, calendar_id=calendar_id, all_day=all_day, start=s, end=e)


class GoogleSource:
    def __init__(self, cfg: Config):
        if not cfg.calendars:
            raise ValueError("No [[calendars]] configured in config.toml")
        self.cfg = cfg
        self.calendars = cfg.calendars
        self._session = None

    def fetch(self, start: date, end: date) -> list[Event]:
        if self._session is None:
            self._session = _session(self.cfg)
        tz = self.cfg.tz
        time_min = datetime.combine(start, time()).astimezone(tz).isoformat()
        time_max = datetime.combine(end, time()).astimezone(tz).isoformat()

        events: list[Event] = []
        for cal in self.calendars:
            params = {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",  # expand recurring events
                "orderBy": "startTime",
                "maxResults": 2500,
            }
            while True:
                resp = self._session.get(
                    f"{API}/calendars/{_quote(cal.id)}/events", params=params, timeout=30
                )
                if resp.status_code == 404:
                    raise RuntimeError(
                        f"Calendar {cal.id!r} not found. Is it shared with the service account?"
                    )
                resp.raise_for_status()
                body = resp.json()
                for item in body.get("items", []):
                    ev = _parse_event(item, cal.id, tz)
                    if ev is not None:
                        events.append(ev)
                if "nextPageToken" not in body:
                    break
                params["pageToken"] = body["nextPageToken"]
        log.info("Fetched %d events from %d calendars", len(events), len(self.calendars))
        return events


def _quote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")


# --------------------------------------------------------------------------- Sample data

SAMPLE_CALENDARS = [
    CalendarConfig(id="family", name="Family", color="blue"),
    CalendarConfig(id="work", name="Work", color="green"),
    CalendarConfig(id="kids", name="Kids", color="red"),
    CalendarConfig(id="holidays", name="Holidays", color="red", kind="holidays"),
]


class SampleSource:
    """Plausible fake events so the layout can be developed without Google."""

    def __init__(self, calendars: list[CalendarConfig] | None = None):
        self.calendars = calendars or SAMPLE_CALENDARS

    def fetch(self, start: date, end: date) -> list[Event]:
        ids = [c.id for c in self.calendars if c.kind == "events"] or ["family"]
        holiday_ids = [c.id for c in self.calendars if c.kind == "holidays"]
        events: list[Event] = []

        def timed(day: date, h: int, m: int, mins: int, title: str, cal: str):
            s = datetime.combine(day, time(h, m))
            events.append(Event(title, cal, False, s, s + timedelta(minutes=mins)))

        def all_day(day: date, days: int, title: str, cal: str):
            s = datetime.combine(day, time())
            events.append(Event(title, cal, True, s, s + timedelta(days=days)))

        cal = lambda i: ids[i % len(ids)]  # noqa: E731
        day = start
        while day < end:
            rng = random.Random(day.toordinal())
            wd = day.weekday()
            if wd == 0:
                timed(day, 9, 0, 30, "Team standup", cal(1))
            if wd == 1:
                timed(day, 16, 30, 60, "Soccer practice", cal(2))
            if wd == 3:
                timed(day, 18, 0, 90, "Book club", cal(0))
            if wd == 5 and rng.random() < 0.6:
                timed(day, 10, 0, 120, rng.choice(["Farmers market", "Hike", "Brunch w/ Sam & Alex"]), cal(0))
            if rng.random() < 0.25:
                timed(day, rng.choice([8, 11, 13, 15]), rng.choice([0, 15, 30, 45]), 60,
                      rng.choice(["Dentist", "Call with Jordan", "Haircut", "Vet: Biscuit",
                                  "Parent-teacher conference", "Plumber"]), cal(rng.randrange(3)))
            if day.day == 3:
                all_day(day, 1, "Grandma's birthday", cal(0))
            if day.day == 12:
                all_day(day, 4, "Trip to Portland", cal(0))
            if day.day == 21:
                all_day(day, 2, "Offsite", cal(1))
                timed(day, 19, 0, 120, "Dinner reservation at Lupa", cal(0))
            if day.day == 26:
                all_day(day, 1, "Field trip", cal(2))
                all_day(day, 1, "Recycling", cal(0))
                timed(day, 7, 30, 30, "Drop-off early", cal(2))
            if holiday_ids and day.day == 11:
                all_day(day, 1, "Example Holiday", holiday_ids[0])
            day += timedelta(days=1)
        return events
