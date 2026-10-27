"""Draws a month as a printed wall calendar.

Everything is drawn with the six pure panel colors and with font
anti-aliasing off, so the result can go to the e-ink panel as-is.
The rolling week view starts at the current week and spans this month
and the next, without the small previous/next month calendars.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from itertools import accumulate
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import CalendarConfig
from .models import Event
from . import palette
from .palette import BLACK, PURE, WHITE
from .settings import Settings

PANEL_SIZE = (1600, 1200)
FONT_DIR = Path(__file__).parent / "fonts"

TITLE_FONT = "WallcalSerif-Black.ttf"
SERIF_BOLD = "WallcalSerif-Bold.ttf"
SERIF_ITALIC = "CrimsonText-Italic.ttf"
SANS = "Lato-Regular.ttf"
SANS_BOLD = "Lato-Bold.ttf"
SANS_ITALIC = "Lato-Italic.ttf"


@dataclass
class RenderInput:
    year: int
    month: int
    today: date
    events: list[Event]
    calendars: list[CalendarConfig]  # visible calendars, with effective colors
    settings: Settings
    warning: str = ""  # shown small in the footer, e.g. when sync is failing
    rolling: bool = False  # current week on top, spanning this month and the next


def logical_size(settings: Settings) -> tuple[int, int]:
    w, h = PANEL_SIZE
    return (h, w) if settings.orientation.startswith("portrait") else (w, h)


@lru_cache(maxsize=64)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size)


def fit(draw: ImageDraw.ImageDraw, text: str, f, max_w: float) -> str:
    """Truncate text with an ellipsis so it fits in max_w pixels."""
    if draw.textlength(text, font=f) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # longest prefix that fits with the ellipsis
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid].rstrip() + "…", font=f) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…" if lo else ""


def spaced(draw, xy, text, f, fill, tracking: int, anchor_center=True):
    """Draw letter-spaced text (centered on xy, or starting at xy)."""
    widths = [draw.textlength(ch, font=f) for ch in text]
    total = sum(widths) + tracking * (len(text) - 1)
    x = xy[0] - total / 2 if anchor_center else xy[0]
    for ch, w in zip(text, widths):
        draw.text((x, xy[1]), ch, font=f, fill=fill, anchor="lm")
        x += w + tracking
    return total


@lru_cache(maxsize=2)
def _dot_mask(size: tuple[int, int]) -> Image.Image:
    """Sparse dot pattern that reads as light gray on the panel."""
    tile = Image.new("1", (6, 6), 0)
    tile.putpixel((0, 0), 1)
    tile.putpixel((3, 3), 1)
    mask = Image.new("1", size, 0)
    for x in range(0, size[0], 6):
        for y in range(0, size[1], 6):
            mask.paste(tile, (x, y))
    return mask


def shade(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    img.paste(BLACK, box, mask=_dot_mask(img.size).crop(box))


def wrap2(draw, text: str, f, max_w: float, second_w: float | None = None) -> list[str]:
    """Split text into at most two lines: the first fits max_w, the second
    second_w (default max_w) and gets an ellipsis if it's still too long."""
    if draw.textlength(text, font=f) <= max_w:
        return [text]
    words = text.split()
    first = ""
    taken = 0
    for word in words:
        trial = f"{first} {word}".strip()
        if draw.textlength(trial, font=f) > max_w:
            break
        first, taken = trial, taken + 1
    if not first:  # a single word longer than the line: break it mid-word
        first = fit(draw, text, f, max_w).rstrip("…")
        rest = text[len(first):]
    else:
        rest = " ".join(words[taken:])
    return [first, fit(draw, rest.strip(), f, second_w or max_w)]


EVENT_SIZES = {"small": (16, 23), "medium-small": (17, 25), "medium": (19, 27), "large": (22, 31)}  # font px, line height


def fmt_time(t, fmt: str) -> str:
    if fmt == "24h":
        return f"{t.hour}:{t.minute:02d}"
    h = t.hour % 12 or 12
    suffix = "a" if t.hour < 12 else "p"
    return f"{h}{suffix}" if t.minute == 0 else f"{h}:{t.minute:02d}{suffix}"


ink_on = palette.text_on


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def rolling_weeks(today: date, week_start: str) -> list[list[date]]:
    """Weeks for the rolling view: the week containing today on top, then the
    weeks after it. Five rows, or six when that's what it takes to reach the next month.

    Called with the current date whenever the calendar draws, so the top row
    moves to the new week on the morning that week starts."""
    first_wd = 6 if week_start == "sunday" else 0
    start = today - timedelta(days=(today.weekday() - first_wd) % 7)
    ny, nm = _next_month(today.year, today.month)
    n = 5
    while start + timedelta(days=n * 7 - 1) < date(ny, nm, 1):
        n += 1
    return [[start + timedelta(days=r * 7 + c) for c in range(7)] for r in range(n)]


def rolling_title(year: int, month: int) -> tuple[str, str]:
    """Month line and year line for a view that spans this month and the next."""
    ny, nm = _next_month(year, month)
    name = f"{calendar.month_name[month]} – {calendar.month_name[nm]}"
    years = str(year) if ny == year else f"{year} – {ny}"
    return name, years


def rolling_label(year: int, month: int) -> str:
    name, years = rolling_title(year, month)
    return f"{name} {years}"


# --------------------------------------------------------------------------- layout


def render(inp: RenderInput) -> Image.Image:
    s = inp.settings
    W, H = logical_size(s)
    portrait = H > W
    img = Image.new("RGB", (W, H), WHITE)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"  # no anti-aliasing: crisp pixels on e-ink

    accent, accent_ink = palette.accent(s.accent_color)
    colors = {c.id: palette.color(c.color) for c in inp.calendars}
    kinds = {c.id: c.kind for c in inp.calendars}

    margin = 32
    header_h = 220 if portrait else 176
    weekday_h = 42
    footer_h = 34
    left, right = margin + s.margin_sides, W - margin - s.margin_sides

    # ----------------------------------------------------------------- header
    top = margin + s.margin_top_bottom
    has_title = bool(s.title.strip())
    if has_title:
        spaced(draw, (W / 2, top + 12), s.title.strip().upper(), font(SANS_BOLD, 22), BLACK, 5)

    # The rolling view names both months in the title, so the little
    # previous/next month calendars would only repeat that.
    mini_w = 7 * 27
    if s.show_mini_months and not inp.rolling:
        prev_m = date(inp.year, inp.month, 1) - timedelta(days=1)
        next_m = date(inp.year, inp.month, 28) + timedelta(days=7)
        mini_y = top + header_h - 162
        _mini_month(draw, left, mini_y, prev_m.year, prev_m.month, s, accent)
        _mini_month(draw, right - mini_w, mini_y, next_m.year, next_m.month, s, accent)
        name_space = right - left - 2 * (mini_w + 30)
    else:
        name_space = right - left

    if inp.rolling:
        focus_year, focus_month = inp.today.year, inp.today.month
        focus = {(focus_year, focus_month), _next_month(focus_year, focus_month)}
        month_name, year_txt = rolling_title(focus_year, focus_month)
    else:
        focus = {(inp.year, inp.month)}
        month_name = calendar.month_name[inp.month]
        year_txt = str(inp.year)
    size = 150 if portrait else 112
    while True:
        f_month, f_year = font(TITLE_FONT, size), font(SERIF_BOLD, int(size * 0.55))
        gap = size * 0.22
        mw = draw.textlength(month_name, font=f_month)
        yw = draw.textlength(year_txt, font=f_year)
        if mw + gap + yw <= name_space or size <= 40:
            break
        size -= 4
    baseline = top + header_h - (40 if portrait else 34)
    if not has_title and not portrait:
        baseline -= 6
    x0 = (W - (mw + gap + yw)) / 2
    draw.text((x0, baseline), month_name, font=f_month, fill=accent, anchor="ls")
    draw.text((x0 + mw + gap, baseline), year_txt, font=f_year, fill=BLACK, anchor="ls")

    rule_y = top + header_h
    draw.rectangle((left, rule_y, right, rule_y + 3), fill=BLACK)
    draw.rectangle((left, rule_y + 8, right, rule_y + 8), fill=BLACK)

    # --------------------------------------------------------------- weekdays
    first_wd = 6 if s.week_start == "sunday" else 0
    if inp.rolling:
        weeks = rolling_weeks(inp.today, s.week_start)
    else:
        weeks = calendar.Calendar(firstweekday=first_wd).monthdatescalendar(inp.year, inp.month)
    col_w = (right - left) / 7
    xs = [round(left + i * col_w) for i in range(8)]

    wd_y = rule_y + 9 + weekday_h / 2
    names = [calendar.day_name[(first_wd + i) % 7].upper() for i in range(7)]
    f_wd = font(SANS_BOLD, 19)
    tracking = 3
    if max(draw.textlength(n, font=f_wd) + tracking * len(n) for n in names) > col_w - 16:
        names = [n[:3] for n in names]
    for i, n in enumerate(names):
        is_sun = (first_wd + i) % 7 == 6
        spaced(draw, ((xs[i] + xs[i + 1]) / 2, wd_y), n, f_wd, accent if is_sun else BLACK, tracking)

    # ----------------------------------------------------------------- events
    holidays: dict[date, list[str]] = {}
    holiday_ink: dict[date, tuple[int, int, int]] = {}
    cal_colors = {c.id: c.color for c in inp.calendars}
    spanning: list[Event] = []
    timed: dict[date, list[Event]] = {}
    for ev in inp.events:
        if ev.calendar_id not in colors:
            continue  # hidden calendar
        if kinds.get(ev.calendar_id) == "holidays":
            d = ev.first_day
            while d <= ev.last_day:
                holidays.setdefault(d, []).append(ev.title)
                holiday_ink.setdefault(d, palette.solid(cal_colors[ev.calendar_id]))
                d += timedelta(days=1)
        elif ev.all_day or ev.multi_day:
            spanning.append(ev)
        else:
            timed.setdefault(ev.first_day, []).append(ev)
    for lst in timed.values():
        lst.sort(key=lambda e: (e.start, e.title))
    spanning.sort(key=lambda e: (e.first_day, -(e.last_day - e.first_day).days, e.title))

    def shows_events(day: date) -> bool:
        return (day.year, day.month) in focus or s.show_adjacent_events

    # ------------------------------------------------------------------- grid
    grid_top = round(rule_y + 9 + weekday_h)
    grid_bottom = H - footer_h - s.margin_top_bottom
    row_h = (grid_bottom - grid_top) / len(weeks)

    num_size = max(26, min(40, round(row_h * 0.2)))
    f_num = font(SERIF_BOLD, num_size)
    f_hol = font(SERIF_ITALIC, 21)
    ev_px, line_h = EVENT_SIZES[s.event_text_size]
    f_ev = font(SANS, ev_px)
    f_ev_b = font(SANS_BOLD, ev_px)
    pad = 9
    cell_head = num_size + 16

    def capacity(h: float) -> int:
        return int((h - 4 - cell_head) // line_h)

    heights = [row_h] * len(weeks)
    if inp.rolling:
        # Make the current week tall enough to show all its events on one line
        # each: first with a line of the footer when there's no warning, then a
        # line at a time from the weeks furthest away (which may then show "+N").
        placed = _lanes(spanning, weeks[0])
        need = 0
        for c, day in enumerate(weeks[0]):
            if shows_events(day):
                lane_rows = {lane for ev, lane, a, b in placed if a <= c <= b}
                need = max(need, _stack([1] * len(timed.get(day, [])), lane_rows)[2])
        extra = need - capacity(row_h)
        if extra > 0 and not inp.warning:
            grid_bottom += line_h
            heights[0] += line_h
            extra -= 1
        while extra > 0:
            donors = [r for r in range(len(weeks) - 1, 0, -1) if capacity(heights[r] - line_h) >= 1]
            if not donors:
                break
            for r in donors[:extra]:
                heights[r] -= line_h
                heights[0] += line_h
                extra -= 1
    ys = [round(y) for y in accumulate(heights, initial=grid_top)]

    for r, week in enumerate(weeks):
        for c, day in enumerate(week):
            if (day.year, day.month) not in focus:
                shade(img, (xs[c] + 1, ys[r] + 1, xs[c + 1], ys[r + 1]))
    for x in xs[1:-1]:
        draw.line((x, grid_top, x, grid_bottom), fill=BLACK, width=2)
    for y in ys[1:-1]:
        draw.line((left, y, right, y), fill=BLACK, width=2)
    draw.rectangle((left, grid_top, right, grid_bottom), outline=BLACK, width=3)

    # ------------------------------------------------------- events in the grid
    for r, week in enumerate(weeks):
        y_top, y_bot = ys[r], ys[r + 1]
        ev_top = y_top + cell_head
        placed = _lanes(spanning, week)

        # Per day: which rows are taken by bars, where timed events go, and how
        # many events didn't fit (counted as "+N" in the cell's top-right corner).
        limit = [max(1, capacity(y_bot - y_top))] * 7
        for c, day in enumerate(week):
            x_l, x_r = xs[c], xs[c + 1]
            if not shows_events(day):
                limit[c] = 0
                _day_number(draw, x_l, y_top, x_r, day, inp, accent, accent_ink, f_num, pad,
                            holidays.get(day), holiday_ink.get(day), f_hol,
                            in_month=(day.year, day.month) in focus)
                continue
            lane_rows = {lane for ev, lane, a, b in placed if a <= c <= b}
            day_timed = timed.get(day, [])
            texts = [_timed_text(draw, ev, x_r - x_l - 2 * pad, s, f_ev, f_ev_b) for ev in day_timed]
            want = [len(lines) for _, lines in texts]

            starts, sizes, used = _stack(want, lane_rows)
            # Too crowded: un-wrap titles, latest first, before hiding anything.
            # If even all-single-line doesn't fit, every title ends up on one line.
            while used > limit[c] and 2 in want:
                want[len(want) - 1 - want[::-1].index(2)] = 1
                starts, sizes, used = _stack(want, lane_rows)
            hidden = sum(1 for row in lane_rows if row >= limit[c])
            hidden += sum(1 for row in starts if row >= limit[c])
            _day_number(draw, x_l, y_top, x_r, day, inp, accent, accent_ink, f_num, pad,
                        holidays.get(day), holiday_ink.get(day), f_hol,
                        in_month=(day.year, day.month) in focus,
                        more=f"+{hidden}" if hidden else "", f_more=f_ev_b)

            for ev, (time_txt, lines), row, h in zip(day_timed, texts, starts, sizes):
                if row >= limit[c]:
                    continue
                if h == 1 or row + 1 >= limit[c]:
                    lines = _timed_text(draw, ev, x_r - x_l - 2 * pad, s, f_ev, f_ev_b, wrap=False)[1]
                _timed_line(draw, time_txt, lines, x_l + pad, ev_top + row * line_h, line_h,
                            colors[ev.calendar_id], f_ev, f_ev_b)

        # Bars are drawn last so they run continuously over the grid lines.
        for ev, lane, a, b in placed:
            c = a
            while c <= b:
                if lane >= limit[c]:
                    c += 1
                    continue
                run_start = c
                while c + 1 <= b and lane < limit[c + 1]:
                    c += 1
                _bar(draw, ev, week, xs, run_start, c, ev_top + lane * line_h, line_h,
                     colors[ev.calendar_id], f_ev_b)
                c += 1

    # ----------------------------------------------------------------- footer
    if inp.warning:
        draw.text((right, grid_bottom + footer_h / 2), inp.warning, font=font(SANS_ITALIC, 18),
                  fill=PURE["red"], anchor="rm")
    palette.apply_mixes(img)
    return img


def _lanes(spanning: list[Event], week: list[date]) -> list[tuple[Event, int, int, int]]:
    """Assign each all-day / multi-day event in this week to a lane so its bar
    stays on one row across the days it covers: (event, lane, first col, last col)."""
    w0, w6 = week[0], week[-1]
    lanes_end: list[date] = []
    placed = []
    for ev in spanning:
        if ev.last_day < w0 or ev.first_day > w6:
            continue
        a, b = max(ev.first_day, w0), min(ev.last_day, w6)
        for lane, end in enumerate(lanes_end):
            if end < a:
                lanes_end[lane] = b
                break
        else:
            lane = len(lanes_end)
            lanes_end.append(b)
        placed.append((ev, lane, (a - w0).days, (b - w0).days))
    return placed


def _stack(heights: list[int], lane_rows: set[int]) -> tuple[list[int], list[int], int]:
    """Stack timed events (1 or 2 lines each) into the rows not taken by bars:
    (first row of each, lines of each, rows used in all)."""
    starts, sizes, row = [], [], 0
    for h in heights:
        while row in lane_rows:
            row += 1
        if h == 2 and row + 1 in lane_rows:
            h = 1
        starts.append(row)
        sizes.append(h)
        row += h
    used = max([*(r + 1 for r in lane_rows), *(a + h for a, h in zip(starts, sizes))], default=0)
    return starts, sizes, used


def _day_number(draw, x_l, y_top, x_r, day, inp, accent, accent_ink, f_num, pad, holiday_names, holiday_ink, f_hol,
                in_month=True, more="", f_more=None):
    text = str(day.day)
    x, y = x_l + pad + 2, y_top + 8
    color = holiday_ink if holiday_names else accent if day.weekday() == 6 else BLACK
    if not in_month:
        color = BLACK
    bbox = draw.textbbox((x, y), text, font=f_num, anchor="lt")
    if day == inp.today:
        # Circle "today" (a pill for two digits), kept inside the date row.
        h = bbox[3] - bbox[1] + 14
        w = max(h, bbox[2] - bbox[0] + 20)
        left, top = x_l + 5, y_top + 5
        draw.rounded_rectangle((left, top, left + w, top + h), radius=h / 2, fill=accent)
        x += left + (w - (bbox[2] - bbox[0])) / 2 - bbox[0]
        y += top + 7 - bbox[1]
        bbox = (left, top, left + w, top + h)
        color = accent_ink
    draw.text((x, y), text, font=f_num, fill=color, anchor="lt")
    label_r = x_r - pad
    if more:  # count of events that didn't fit, in the top-right corner
        draw.text((label_r, (bbox[1] + bbox[3]) / 2), more, font=f_more, fill=BLACK, anchor="rm")
        label_r -= draw.textlength(more, font=f_more) + 12
    if holiday_names:
        hx = bbox[2] + 16
        label = fit(draw, " · ".join(holiday_names), f_hol, label_r - hx)
        draw.text((hx, (bbox[1] + bbox[3]) / 2), label, font=f_hol,
                  fill=holiday_ink if in_month else BLACK, anchor="lm")


BULLET = 10  # colored square before each timed event
BULLET_GAP = 7


def _timed_text(draw, ev, width, s, f_ev, f_ev_b, wrap=None):
    """(time label, title lines) for a timed event in a cell `width` px wide."""
    time_txt = fmt_time(ev.start, s.time_format) if s.show_event_times else ""
    text_w = width - BULLET - BULLET_GAP
    title_w = text_w - (draw.textlength(time_txt, font=f_ev_b) + 6 if time_txt else 0)
    if wrap is None:
        wrap = s.wrap_event_titles
    # A wrapped second line starts under the time, not the title, to get more room.
    lines = wrap2(draw, ev.title, f_ev, title_w, text_w) if wrap else [fit(draw, ev.title, f_ev, title_w)]
    return time_txt, lines


def _timed_line(draw, time_txt, lines, x_l, y, line_h, color, f_ev, f_ev_b):
    cy = y + line_h / 2
    box = (x_l, cy - BULLET / 2, x_l + BULLET, cy + BULLET / 2)
    draw.rectangle(box, fill=color)
    if color == WHITE:
        draw.rectangle(box, outline=BLACK)
    x = x_l + BULLET + BULLET_GAP
    title_x = x
    if time_txt:
        draw.text((x, cy), time_txt, font=f_ev_b, fill=BLACK, anchor="lm")
        title_x += draw.textlength(time_txt, font=f_ev_b) + 6
    draw.text((title_x, cy), lines[0], font=f_ev, fill=BLACK, anchor="lm")
    if len(lines) > 1:
        draw.text((x, cy + line_h), lines[1], font=f_ev, fill=BLACK, anchor="lm")


def _bar(draw, ev, week, xs, c0, c1, y, line_h, color, f):
    starts_here = week[c0] == ev.first_day
    ends_here = week[c1] == ev.last_day
    x_l = xs[c0] + (7 if starts_here else 0)
    x_r = xs[c1 + 1] - (7 if ends_here else 0)
    top, bot = y + 2, y + line_h - 2
    draw.rectangle((x_l, top, x_r, bot), fill=color)
    if color == WHITE:
        draw.rectangle((x_l, top, x_r, bot), outline=BLACK, width=2)
    label = ev.title
    if not ev.all_day and starts_here:
        label = f"{fmt_time(ev.start, '12h')} {label}"
    if not starts_here:
        label = f"‹ {label}"
    tx = x_l + 8
    draw.text((tx, (top + bot) / 2), fit(draw, label, f, x_r - tx - 6), font=f,
              fill=ink_on(color), anchor="lm")


def _mini_month(draw, x, y, year, month, s: Settings, accent):
    cw, rh = 27, 19
    f_title = font(SANS_BOLD, 17)
    f_num = font(SANS, 15)
    f_hd = font(SANS_BOLD, 13)
    draw.text((x + 7 * cw / 2, y + 8), f"{calendar.month_name[month].upper()} {year}",
              font=f_title, fill=BLACK, anchor="mm")
    first_wd = 6 if s.week_start == "sunday" else 0
    for i in range(7):
        wd = (first_wd + i) % 7
        draw.text((x + i * cw + cw / 2, y + 32), "MTWTFSS"[wd], font=f_hd,
                  fill=accent if wd == 6 else BLACK, anchor="mm")
    weeks = calendar.Calendar(firstweekday=first_wd).monthdayscalendar(year, month)
    for r, week in enumerate(weeks):
        for c, d in enumerate(week):
            if d:
                wd = (first_wd + c) % 7
                draw.text((x + c * cw + cw / 2, y + 53 + r * rh), str(d), font=f_num,
                          fill=accent if wd == 6 else BLACK, anchor="mm")
