from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Event:
    title: str
    calendar_id: str
    all_day: bool
    # For all-day events these are midnight-local datetimes and `end` is
    # exclusive (Google's convention): a one-day event on Oct 7 ends Oct 8 00:00.
    start: datetime
    end: datetime

    @property
    def first_day(self) -> date:
        return self.start.date()

    @property
    def last_day(self) -> date:
        """Last calendar day the event touches (inclusive)."""
        end = self.end - timedelta(microseconds=1) if self.end > self.start else self.end
        return end.date()

    @property
    def multi_day(self) -> bool:
        return self.last_day > self.first_day

    def to_json(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d

    @classmethod
    def from_json(cls, d: dict) -> Event:
        return cls(
            title=d["title"],
            calendar_id=d["calendar_id"],
            all_day=d["all_day"],
            start=datetime.fromisoformat(d["start"]),
            end=datetime.fromisoformat(d["end"]),
        )
