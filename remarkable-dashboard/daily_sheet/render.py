"""Render the daily PDF (1404×1872, one px = one pt) plus layout.json.

Coordinates in this module are top-left-origin pixels, matching what the
read-back step sees when the page is rasterised. `_Y()` flips to PDF space.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from reportlab.lib.colors import Color, black, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

from .calendar_ics import events_on
from .config import PAGE_H, PAGE_W, TZ
from .models import AsanaTask, Event, IngestionReport, ProjectCard, Region, SectionStatus

# --- typographic system -----------------------------------------------------
F = "Helvetica"
FB = "Helvetica-Bold"
GREY = Color(0.45, 0.45, 0.45)     # ≥ ~55 % ink — safe on e-ink
RULE = Color(0.55, 0.55, 0.55)

M = 60                 # page margin
NAV_H = 72             # nav bar tap height (≥ 44)
NAV_Y = 40
CONTENT_TOP = NAV_Y + NAV_H + 40
BOX = 44               # checkbox / priority box side
PBOX = 38              # selector box side (>= 44 tap target once its gap counts)
PRIO = ("High", "Medium", "Low")
WEEKS = (("this", "TW"), ("next", "NW"))   # assign to "YYYY: Week ##"
MOVES = (("this", "TW"), ("next", "NW"), ("month", "NM"))   # Personal: move card to list
ROW_H = 72
TODAY_ROW_H = 56       # priority rows on page 1
TODAY_TASKS = 10       # how many the block shows
TODAY_DUE_COL = 170    # due column; priority is right-aligned just left of it
PRIO_ROW_H = 84        # task rows carry the pickers, so they need more height
TASK_DUE_COL = 140     # right-hand due column on the Tasks page
NEW_BOX_LINES = 4      # minimum ruled lines in the New tasks box
NEW_BOX_H = 4 * 78 + 30
HOUR_FIRST, HOUR_LAST = 8, 17      # grid shows 08:00 … 18:00
WEEK_DAYS = 5                      # Mon–Fri; weekend events are flagged, not drawn
# Last week through thirteen ahead: a quarter of planning, which is as far out
# as the pipeline board's dates tend to reach.
WEEK_OFFSETS = tuple(range(-1, 14))

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTHS_SHORT = [m[:3] for m in MONTHS]

NAV_ITEMS = [("Today", "today"), ("Week", "week"), ("Tasks", "tasks"), ("Personal", "personal"),
             ("Projects", "projects"), ("Accounting", "accounting")]

# Accounting page: cards with no month yet are listed last under this header.
NO_MONTH = "No accounting month"
_MONTH_INDEX = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_INDEX.update({"mai": 5, "okt": 10, "des": 12})     # Norwegian spellings that differ


def _Y(y: float) -> float:
    return PAGE_H - y


def _fit(text: str, font: str, size: float, max_w: float) -> str:
    if pdfmetrics.stringWidth(text, font, size) <= max_w:
        return text
    ell = "…"
    while text and pdfmetrics.stringWidth(text + ell, font, size) > max_w:
        text = text[:-1]
    return text.rstrip() + ell


def _fmt_date_long(d: date) -> str:
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} {d.year}"


def _fmt_range(a: date, b: date) -> str:
    if a.month == b.month:
        return f"{a.day}–{b.day} {MONTHS_SHORT[a.month - 1]}"
    return f"{a.day} {MONTHS_SHORT[a.month - 1]} – {b.day} {MONTHS_SHORT[b.month - 1]}"


def _hours(ev: Event, day: date) -> tuple[float, float]:
    """Event span in fractional hours within `day`, clipped to the grid."""
    day_start = datetime.combine(day, time.min, tzinfo=TZ)
    s = (ev.start - day_start).total_seconds() / 3600
    e = (ev.end - day_start).total_seconds() / 3600
    return max(s, HOUR_FIRST), min(e, HOUR_LAST + 1)


def _outside_grid(ev: Event, day: date) -> bool:
    """True if the event falls wholly outside the drawn hours on `day`.

    An event that merely overlaps an edge is clipped and still visible; this is
    only for ones with nowhere on the page at all.
    """
    start, end = _hours(ev, day)
    return end <= start


def _lanes(events: list[Event], day: date) -> list[tuple[Event, int, int]]:
    """Greedy lane packing for overlapping timed events → (event, lane, n_lanes)."""
    timed = [ev for ev in events if not ev.all_day]
    timed.sort(key=lambda ev: (ev.start, ev.end))
    placed: list[tuple[Event, int]] = []
    lane_end: list[datetime] = []
    for ev in timed:
        for i, end in enumerate(lane_end):
            if ev.start >= end:
                lane_end[i] = ev.end
                placed.append((ev, i))
                break
        else:
            lane_end.append(ev.end)
            placed.append((ev, len(lane_end) - 1))
    n = max(1, len(lane_end))
    return [(ev, lane, n) for ev, lane in placed]


# --- inputs -----------------------------------------------------------------
@dataclass
class SheetData:
    today: date
    events: list[Event]                 # last / this / next week
    events_status: SectionStatus
    asana_tasks: list[AsanaTask]        # incomplete, assigned to me, sorted
    asana_status: SectionStatus
    projects: list[ProjectCard]         # pipeline board cards
    project_sections: list[str]         # section names, in board order
    projects_status: SectionStatus
    report: IngestionReport
    unreadable_pngs: list[Path] = field(default_factory=list)
    # The shared Trello board. Optional: an unset key leaves the Personal page
    # saying so, and the rest of the sheet is unaffected.
    trello_cards: list = field(default_factory=list)          # open TrelloCard rows
    trello_lists: list[str] = field(default_factory=list)     # list names, board order
    trello_status: SectionStatus = field(default_factory=SectionStatus)
    trello_members: list = field(default_factory=list)        # {id, name, initials}, me first


@dataclass
class PageSpec:
    kind: str                # today | week | tasks | personal | projects | accounting
    n: int                   # 1-based page number
    week_offset: int = 0     # week pages: -1 / 0 / 1
    items: list = field(default_factory=list)
    idx: int = 1             # tasks/asana: page i of total
    total: int = 1
    last: bool = True


# --- renderer ---------------------------------------------------------------
class Renderer:
    # Every task page ends with a New tasks box, so the row count is the same
    # throughout -- earlier pages no longer get to be fuller than the last.
    TASK_ROWS_FULL = 14
    TASK_ROWS_WITH_BOX = 14
    ASANA_ROWS = 20
    PERSONAL_ROWS = 14          # list headers + cards per Personal page, box included
    PROJECT_ROWS = 22           # section headers + cards per Projects page

    def __init__(self, data: SheetData, out_pdf: Path, out_layout: Path):
        self.d = data
        self.out_pdf = out_pdf
        self.out_layout = out_layout
        self.c = rl_canvas.Canvas(str(out_pdf), pagesize=(PAGE_W, PAGE_H))
        self.c.setTitle(f"Daily Sheet — {data.today.isoformat()}")
        self.regions: list[Region] = []
        self.pages = self._plan()

    # -- planning -------------------------------------------------------
    def _plan(self) -> list[PageSpec]:
        pages: list[PageSpec] = [PageSpec("today", 1)]
        for off in WEEK_OFFSETS:
            pages.append(PageSpec("week", len(pages) + 1, week_offset=off))

        rest = list(self.d.asana_tasks)
        chunks: list[list] = []
        while len(rest) > self.TASK_ROWS_FULL:
            chunks.append(rest[: self.TASK_ROWS_FULL])
            rest = rest[self.TASK_ROWS_FULL:]
        chunks.append(rest)
        for i, ch in enumerate(chunks, 1):
            pages.append(PageSpec("tasks", len(pages) + 1, items=ch, idx=i,
                                  total=len(chunks), last=i == len(chunks)))

        pers_pages = self._personal_pages()
        for i, ch in enumerate(pers_pages, 1):
            pages.append(PageSpec("personal", len(pages) + 1, items=ch, idx=i,
                                  total=len(pers_pages), last=i == len(pers_pages)))

        proj_pages = self._project_pages()
        for i, ch in enumerate(proj_pages, 1):
            pages.append(PageSpec("projects", len(pages) + 1, items=ch, idx=i,
                                  total=len(proj_pages), last=i == len(proj_pages)))

        acct_pages = self._accounting_pages()
        for i, ch in enumerate(acct_pages, 1):
            pages.append(PageSpec("accounting", len(pages) + 1, items=ch, idx=i,
                                  total=len(acct_pages), last=i == len(acct_pages)))
        return pages

    @property
    def pipeline(self) -> list:
        """Cards still open in Asana -- what the Projects page shows."""
        return [c for c in self.d.projects if not c.completed]

    def _personal_pages(self) -> list[list[tuple[str, object]]]:
        """Rows for the Personal page: ("list", name) headers and ("card", card)
        rows, in board order, cut into pages. A list header never ends a page
        -- it would announce cards that are on the next one."""
        rows: list[tuple[str, object]] = []
        by_list: dict[str, list] = {}
        for c in self.d.trello_cards:
            by_list.setdefault(c.list_name, []).append(c)
        order = list(self.d.trello_lists) + [n for n in by_list if n not in self.d.trello_lists]
        for name in order:
            cards = by_list.get(name, [])
            if not cards:
                continue
            rows.append(("list", name))
            rows.extend(("card", c) for c in cards)

        pages: list[list[tuple[str, object]]] = []
        while len(rows) > self.PERSONAL_ROWS:
            cut = self.PERSONAL_ROWS
            if rows[cut - 1][0] == "list":
                cut -= 1
            pages.append(rows[:cut])
            rows = rows[cut:]
        pages.append(rows)
        return pages

    def _project_pages(self) -> list[list[tuple[str, list]]]:
        """Group cards by board section, then split into page-sized chunks.

        A section header plus its cards is kept together where it fits; a long
        section spills to the next page under a "(cont.)" header rather than
        being shrunk.
        """
        if not hasattr(self, "_proj_cache"):
            by_section: dict[str, list] = {}
            for c in self.pipeline:
                by_section.setdefault(c.section, []).append(c)
            order = [s for s in self.d.project_sections if s in by_section]
            order += [s for s in by_section if s not in order]
            self._proj_cache = self._chunk_sections(order, by_section)
        return self._proj_cache

    def _accounting_pages(self) -> list[list[tuple[str, list]]]:
        """Every card, delivered or not, grouped by Accounting Month from
        January this year to December next year, earliest first.

        Completed cards count too: the books do not care that a project is
        off the pipeline. Cards with no month yet come last under their own
        header so they are not lost, just parked.
        """
        if not hasattr(self, "_acct_cache"):
            by_month: dict[str, list] = {}
            for c in self._accounting_cards():
                by_month.setdefault(_month_label(c), []).append(c)
            order = sorted(by_month, key=_month_key)
            # A new year always starts a new page: the year boundary is where
            # the books close, so its months should not trail off the bottom
            # of last year's page. Cards with no usable month ride along with
            # the final year rather than getting a page of their own.
            years: list[list[str]] = []
            for label in order:
                k = _month_key(label)
                if k[0] == 0 and (not years or _month_key(years[-1][0])[1] != k[1]):
                    years.append([label])
                elif years:
                    years[-1].append(label)
                else:
                    years.append([label])
            pages: list[list[tuple[str, list]]] = []
            for labels in years:
                pages += [pg for pg in self._chunk_sections(labels, by_month) if pg]
            self._acct_cache = pages or [[]]
        return self._acct_cache

    def _accounting_cards(self) -> list:
        first = (self.d.today.year, 1)
        last = (self.d.today.year + 1, 12)
        out = []
        for c in self.d.projects:
            k = _month_key(_month_label(c))
            if k[0] == 0 and not (first <= (k[1], k[2]) <= last):
                continue            # a real month, but outside the window
            out.append(c)           # in the window, or no usable month yet
        return out

    def _chunk_sections(self, order: list[str], groups: dict[str, list]) -> list[list[tuple[str, list]]]:
        """Split (header, cards) groups into page-sized chunks, in `order`.

        A header plus its cards is kept together where it fits; a long group
        spills to the next page under a "(cont.)" header rather than being
        shrunk.
        """
        pages: list[list[tuple[str, list]]] = []
        page: list[tuple[str, list]] = []
        used = 0
        for name in order:
            cards = groups[name]
            i = 0
            while i < len(cards):
                room = self.PROJECT_ROWS - used - 1        # -1 for the header
                if room < 1:
                    pages.append(page)
                    page, used, room = [], 0, self.PROJECT_ROWS - 1
                take = cards[i:i + room]
                label = name if i == 0 else f"{name} (cont.)"
                page.append((label, take))
                used += len(take) + 1
                i += len(take)
        if page:
            pages.append(page)
        return pages or [[]]

    def first_page_of(self, kind: str) -> int:
        if kind == "week":       # nav goes to *this* week, not last week
            return next(p.n for p in self.pages if p.kind == "week" and p.week_offset == 0)
        return next(p.n for p in self.pages if p.kind == kind)

    # -- drawing primitives --------------------------------------------
    def text(self, x, y, s, font=F, size=26, color=black, align="left"):
        """y is the baseline, top-left-origin."""
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        if align == "right":
            self.c.drawRightString(x, _Y(y), s)
        elif align == "center":
            self.c.drawCentredString(x, _Y(y), s)
        else:
            self.c.drawString(x, _Y(y), s)

    def rect(self, x, y, w, h, stroke=1.5, fill=None, color=black):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(stroke)
        if fill is not None:
            self.c.setFillColor(fill)
        self.c.rect(x, _Y(y + h), w, h, stroke=1 if stroke else 0, fill=1 if fill is not None else 0)

    def line(self, x0, y0, x1, y1, width=1.5, color=black):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x0, _Y(y0), x1, _Y(y1))

    def link(self, dest: str, x, y, w, h):
        self.c.linkRect("", dest, (x, _Y(y + h), x + w, _Y(y)), relative=0, thickness=0)

    def region(self, rid, kind, page, x, y, w, h, line_height=0):
        self.regions.append(Region(rid, kind, page, int(x), int(y), int(x + w), int(y + h), line_height))

    def checkbox(self, rid: str, page: int, x, y):
        self.rect(x, y, BOX, BOX, stroke=2.5)
        # Also lay an interactive AcroForm checkbox over the drawn one. If the
        # viewer honours PDF forms, tapping it records real state we can read
        # exactly; if it ignores them (which e-ink readers generally do), this
        # is inert and the pen tick still works. Costs a few bytes to find out.
        try:
            self.c.acroForm.checkbox(
                name=f"cb_{rid}", x=x, y=_Y(y + BOX), size=BOX,
                buttonStyle="check", borderWidth=0, forceBorder=False,
            )
        except Exception:  # noqa: BLE001 — a form field is a bonus, never required
            pass
        self.region(rid, "check", page, x, y, BOX, BOX)

    def priority_picker(self, task, page: int, x, y) -> float:
        return self.priority_picker_at(task.gid, page, x, y, task.priority)

    def priority_picker_at(self, rid: str, page: int, x, y, current: str = "") -> float:
        """Three boxes -- H M L -- one per Asana priority. Returns the width used.

        The task's current priority is shown by a bar *under* its box rather
        than inside it. Ink inside a box is what read-back measures, so drawing
        the current state there would read as a fresh choice on every run and
        the priority could never be changed.
        """
        gap = 10
        for i, name in enumerate(PRIO):
            bx = x + i * (PBOX + gap)
            self.rect(bx, y, PBOX, PBOX, stroke=1.5, color=GREY)
            # The H/M/L letters live in a column header, not in the boxes: ink
            # inside a box is exactly what read-back measures, so a printed
            # glyph there reads as a mark and every row arrives ambiguous.
            if current.lower() == name.lower():
                self.rect(bx, y + PBOX + 4, PBOX, 5, stroke=0, fill=black)
            self.region(f"{rid}|{name}", "prio3", page, bx, y, PBOX, PBOX)
        return 3 * PBOX + 2 * gap

    def week_picker(self, rid: str, page: int, x, y, current: str = "") -> float:
        """Two boxes -- this week, next week -- assigning to the week project.

        Marked state shows as a bar under the box for the same reason as the
        priority picker: ink inside a box is what read-back measures, so drawing
        the current state there would read as a fresh choice every run.
        """
        gap = 8
        for i, (key, _label) in enumerate(WEEKS):
            bx = x + i * (PBOX + gap)
            self.rect(bx, y, PBOX, PBOX, stroke=1.5, color=GREY)
            if current == key:
                self.rect(bx, y + PBOX + 4, PBOX, 5, stroke=0, fill=black)
            self.region(f"{rid}|{key}", "week2", page, bx, y, PBOX, PBOX)
        return len(WEEKS) * PBOX + (len(WEEKS) - 1) * gap

    def pick_boxes(self, rid: str, page: int, x, y, options, current, kind: str) -> float:
        """A row of boxes, one per option, each its own read-back region.

        `current` is the set of keys already in force; those get the bar under
        the box, never ink inside it, for the reason the other pickers give.
        `kind` is "pick1" (one of them) or "flag" (any of them).
        """
        gap = 8
        for i, (key, _label) in enumerate(options):
            bx = x + i * (PBOX + gap)
            self.rect(bx, y, PBOX, PBOX, stroke=1.5, color=GREY)
            if key in current:
                self.rect(bx, y + PBOX + 4, PBOX, 5, stroke=0, fill=black)
            self.region(f"{rid}|{key}", kind, page, bx, y, PBOX, PBOX)
        return len(options) * PBOX + (len(options) - 1) * gap

    def pick_header(self, x, y, options) -> None:
        gap = 8
        for i, (_key, label) in enumerate(options):
            self.text(x + i * (PBOX + gap) + PBOX / 2, y, label, FB, 17, GREY, align="center")

    def week_header(self, x, y) -> None:
        gap = 8
        for i, (_key, label) in enumerate(WEEKS):
            self.text(x + i * (PBOX + gap) + PBOX / 2, y, label, FB, 17, GREY, align="center")

    def priority_header(self, x, y) -> None:
        gap = 10
        for i, name in enumerate(PRIO):
            self.text(x + i * (PBOX + gap) + PBOX / 2, y, name[0], FB, 19, GREY, align="center")

    def priority_box(self, rid: str, page: int, x, y, current: int):
        self.rect(x, y, BOX, BOX, stroke=1.5, color=GREY)
        # tiny printed current priority in the corner; the pen digit goes in the middle
        self.text(x + BOX - 4, y + 13, str(current), F, 12, GREY, align="right")
        self.region(rid, "priority", page, x, y, BOX, BOX)

    # -- shared chrome -------------------------------------------------
    def nav(self, current: str):
        w = (PAGE_W - 2 * M) / len(NAV_ITEMS)
        for i, (label, kind) in enumerate(NAV_ITEMS):
            x = M + i * w
            active = kind == current
            self.rect(x, NAV_Y, w, NAV_H, stroke=2, fill=black if active else white)
            self.text(x + w / 2, NAV_Y + NAV_H / 2 + 10, label, FB, 28, white if active else black, align="center")
            self.link(f"sec-{kind}", x, NAV_Y, w, NAV_H)
        self.line(M, NAV_Y + NAV_H + 20, PAGE_W - M, NAV_Y + NAV_H + 20, 1, RULE)

    def footer(self, page: PageSpec):
        self.text(PAGE_W - M, PAGE_H - 24, f"{page.n} / {len(self.pages)}", F, 18, GREY, align="right")
        self.text(M, PAGE_H - 24, f"Daily Sheet — {self.d.today.isoformat()}", F, 18, GREY)

    def unavailable(self, y: float, what: str, status: SectionStatus):
        self.rect(M, y, PAGE_W - 2 * M, 90, stroke=1.5, color=GREY)
        self.text(M + 24, y + 38, f"{what} unavailable", FB, 26)
        self.text(M + 24, y + 72, _fit(status.error, F, 20, PAGE_W - 2 * M - 48), F, 20, GREY)

    # -- hour grid (shared by Today and Week) ---------------------------
    def hour_grid(self, x0, y0, w, h, days: list[date], col_w: list[float], label_col=70, day_headers=True):
        """Draw the hour grid and all events for `days`, columns of width col_w."""
        px_h = h / (HOUR_LAST + 1 - HOUR_FIRST)
        # hour labels + horizontal rules
        for i, hr in enumerate(range(HOUR_FIRST, HOUR_LAST + 2)):
            y = y0 + i * px_h
            self.line(x0 + label_col, y, x0 + w, y, 1 if hr != HOUR_FIRST else 1.5, RULE if hr not in (HOUR_FIRST, HOUR_LAST + 1) else black)
            if hr <= HOUR_LAST:
                self.text(x0 + label_col - 12, y + 22, f"{hr:02d}", F, 20, GREY, align="right")
        # columns
        x = x0 + label_col
        for day, cw in zip(days, col_w):
            self.line(x, y0, x, y0 + h, 1, RULE)
            for ev, lane, n in _lanes(events_on(self.d.events, day), day):
                s, e = _hours(ev, day)
                if e <= s:
                    continue
                ex = x + 4 + (cw - 8) * lane / n
                ew = (cw - 8) / n - 3
                ey = y0 + (s - HOUR_FIRST) * px_h
                eh = max((e - s) * px_h, 30)
                eh = min(eh, y0 + h - ey)
                self.rect(ex, ey, ew, eh, stroke=2, fill=white)
                self.rect(ex, ey, 6, eh, stroke=0, fill=black)   # left ink bar
                title_size = 22 if ew > 300 else 18
                if eh >= 26:
                    self.text(ex + 14, ey + title_size + 2, _fit(ev.title, FB, title_size, ew - 20), FB, title_size)
                if eh >= 56 and ev.location:
                    self.text(ex + 14, ey + title_size + 24, _fit(ev.location, F, 17, ew - 20), F, 17, GREY)
                elif eh >= 26 and ew > 300:
                    when = f"{ev.start.astimezone(TZ):%H:%M}–{ev.end.astimezone(TZ):%H:%M}"
                    self.text(ex + ew - 10, ey + title_size + 2, when, F, 17, GREY, align="right")
            x += cw
        self.line(x, y0, x, y0 + h, 1.5, black)

    # -- pages ----------------------------------------------------------
    def page_today(self, page: PageSpec):
        d = self.d.today
        self.c.bookmarkPage("sec-today")
        self.nav("today")
        y = CONTENT_TOP + 40
        self.text(M, y, _fmt_date_long(d), FB, 46)
        iso = d.isocalendar()
        self.text(PAGE_W - M, y, f"Week {iso.week}  ·  Day {d.timetuple().tm_yday}", F, 24, GREY, align="right")

        # hour grid
        gy = y + 50
        gh = 820
        if self.d.events_status.ok:
            self.hour_grid(M, gy, PAGE_W - 2 * M, gh, [d], [PAGE_W - 2 * M - 70])
            today_events = events_on(self.d.events, d)
            notes = []
            all_day = [ev for ev in today_events if ev.all_day]
            if all_day:
                notes.append("All day: " + ", ".join(ev.title for ev in all_day))
            # The grid stops at 18:00 for legibility, so anything wholly outside
            # it has nowhere to be drawn. Naming it costs one line; dropping it
            # would leave the page asserting a clear evening.
            outside = [ev for ev in today_events if not ev.all_day and _outside_grid(ev, d)]
            if outside:
                notes.append("Outside " + f"{HOUR_FIRST:02d}–{HOUR_LAST + 1:02d}: "
                             + ", ".join(f"{ev.start.astimezone(TZ):%H:%M} {ev.title}"
                                         for ev in outside[:3]))
            if notes:
                self.text(M + 70, gy - 10, _fit("   ·   ".join(notes), F, 20,
                                                PAGE_W - 2 * M - 80), F, 20, GREY)
        else:
            self.unavailable(gy, "Calendar", self.d.events_status)

        # Priorities. The old block showed three and was followed by a report
        # on yesterday's sheet -- but that report says what already happened,
        # and the page is read to decide what to do next. The space goes to
        # more of the list instead.
        ty = gy + gh + 44
        self.text(M, ty, "Priorities", FB, 28)
        self.text(PAGE_W - M, ty, "tick to complete", F, 18, GREY, align="right")
        self.line(M, ty + 12, PAGE_W - M, ty + 12, 1.5)

        ry = ty + 28
        rows = self._top_tasks(TODAY_TASKS)
        prio_x = PAGE_W - M - TODAY_DUE_COL
        for a in rows:
            high = a.priority.lower() == "high"
            self.checkbox(a.gid, page.n, M, ry + 6)
            tx = M + BOX + 20

            label = a.priority.title() if a.priority else ""
            due = _due_label(a.due, self.d.today) if a.due else ""
            name_w = prio_x - tx - 20

            self.text(tx, ry + 32, _fit(a.name, FB if high else F, 27, name_w),
                      FB if high else F, 27)
            if label:
                self.text(prio_x, ry + 32, label, FB if high else F, 20,
                          black if high else GREY, align="right")
            if due:
                overdue = a.due is not None and a.due < self.d.today
                self.text(PAGE_W - M, ry + 32, due, FB if overdue else F, 20,
                          black if overdue else GREY, align="right")
            ry += TODAY_ROW_H

        if not rows:
            self.text(M, ry + 30, "Nothing open.", F, 24, GREY)
        self.footer(page)

    def _top_tasks(self, n: int) -> list:
        """The n tasks worth seeing first: High, then Medium, then Low.

        Filling down the priorities rather than showing only High means the
        block is the same height every day -- a page whose shape changes with
        how much you happened to mark is harder to read at a glance.
        """
        return sorted(
            self.d.asana_tasks,
            key=lambda a: (a.rank(), a.due is None, a.due or date.max, a.name.lower()),
        )[:n]

    def _top3_unused(self) -> list[tuple[str, str, str]]:
        """(region id, label, tag) across both lists. Score: lower is more urgent."""
        high = [a for a in self.d.asana_tasks if a.priority.lower() == "high"]
        if high:
            return [(a.gid, a.name,
                     _due_label(a.due, self.d.today) if a.due else "no due date")
                    for a in high[:3]]

        # Nothing marked High -- fall back to what is most urgent by date, so
        # the block is never empty while there is open work.
        scored: list[tuple[float, str, str, str]] = []
        for a in self.d.asana_tasks:
            if a.due is None:
                s = 3.5
            elif a.due < self.d.today:
                s = 0.5
            elif a.due == self.d.today:
                s = 1.2
            elif a.due <= self.d.today + timedelta(days=3):
                s = 2.2
            else:
                s = 3.2
            tag = _due_label(a.due, self.d.today) if a.due else "no due date"
            scored.append((s, a.gid, a.name, tag))
        scored.sort(key=lambda x: x[0])
        return [(rid, label, tag) for _, rid, label, tag in scored[:3]]

    def page_week(self, page: PageSpec):
        today = self.d.today
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=page.week_offset)
        days = [monday + timedelta(days=i) for i in range(WEEK_DAYS)]
        if page.week_offset == 0:
            self.c.bookmarkPage("sec-week")
        self.c.bookmarkPage(f"week{page.week_offset}")
        self.nav("week")
        y = CONTENT_TOP + 36
        label = {-1: "Last week", 0: "This week", 1: "Next week"}.get(
            page.week_offset, f"In {page.week_offset} weeks")
        iso = monday.isocalendar()
        self.text(PAGE_W / 2, y, f"{label}  ·  Week {iso.week}  ·  {_fmt_range(days[0], days[-1])}", FB, 30, align="center")
        # prev / next tap zones (top corners)
        zone_w, zone_h = 200, 64
        if page.week_offset > WEEK_OFFSETS[0]:
            self.rect(M, y - 44, zone_w, zone_h, stroke=1.5, color=GREY)
            self.text(M + zone_w / 2, y, "‹ Prev", F, 24, align="center")
            self.link(f"week{page.week_offset - 1}", M, y - 44, zone_w, zone_h)
        if page.week_offset < WEEK_OFFSETS[-1]:
            self.rect(PAGE_W - M - zone_w, y - 44, zone_w, zone_h, stroke=1.5, color=GREY)
            self.text(PAGE_W - M - zone_w / 2, y, "Next ›", F, 24, align="center")
            self.link(f"week{page.week_offset + 1}", PAGE_W - M - zone_w, y - 44, zone_w, zone_h)

        # Cycling thirteen weeks one tap at a time is a long way back, so every
        # page away from the present offers the way home.
        if page.week_offset not in (0, 1):
            hw = 150
            hx = PAGE_W / 2 - hw / 2
            self.rect(hx, y + 22, hw, 44, stroke=1.2, color=GREY)
            self.text(PAGE_W / 2, y + 52, "This week", F, 19, GREY, align="center")
            self.link("week0", hx, y + 22, hw, 44)

        if not self.d.events_status.ok:
            self.unavailable(y + 60, "Calendar", self.d.events_status)
            self.footer(page)
            return

        label_col = 70
        usable = PAGE_W - 2 * M - label_col
        col_w = [usable / len(days)] * len(days)

        # Anything falling on a day the grid does not draw would vanish without
        # trace, so say it is there rather than let the page imply an empty
        # weekend. Rare, but a dropped event is worse than a small line of text.
        in_week = [ev for ev in self.d.events
                   if monday <= ev.start.date() <= monday + timedelta(days=6)]
        hidden = [ev for ev in in_week
                  if ev.start.weekday() >= WEEK_DAYS
                  or (not ev.all_day and _outside_grid(ev, ev.start.date()))]
        # Drawn at the foot rather than the header: it is a footnote about the
        # grid, and in the corner it competed with the week title for the first
        # glance while saying something far less important.
        note_h = 34 if hidden else 0

        # day headers + all-day strip
        hy = y + 40
        x = M + label_col
        for d, cw in zip(days, col_w):
            is_today = d == today
            if is_today:
                self.rect(x, hy, cw, 58, stroke=0, fill=black)
            self.text(x + cw / 2, hy + 24, DAYS_SHORT[d.weekday()], F, 18, white if is_today else GREY, align="center")
            self.text(x + cw / 2, hy + 50, str(d.day), FB, 24, white if is_today else black, align="center")
            all_day = [ev for ev in events_on(self.d.events, d) if ev.all_day]
            if all_day:
                self.text(x + cw / 2, hy + 80, _fit(" · ".join(ev.title for ev in all_day), F, 16, cw - 8), F, 16, GREY, align="center")
            x += cw
        gy = hy + 96
        gh = PAGE_H - 60 - gy - note_h
        self.hour_grid(M, gy, PAGE_W - 2 * M, gh, days, col_w, label_col=label_col)

        if hidden:
            parts = []
            for ev in hidden[:3]:
                when = "all day" if ev.all_day else f"{ev.start.astimezone(TZ):%a %H:%M}"
                parts.append(f"{when} {ev.title}")
            more = f"  +{len(hidden) - 3} more" if len(hidden) > 3 else ""
            self.text(M, gy + gh + 24,
                      _fit("Not on the grid:  " + "   ·   ".join(parts) + more,
                           F, 18, PAGE_W - 2 * M), F, 18, GREY)
        self.footer(page)

    def _task_columns(self) -> tuple[float, float]:
        """(priority x, week x). Shared so the New tasks box lines up with the
        rows above it -- they drifted apart when each computed its own."""
        prio_w = 3 * PBOX + 2 * 8
        week_w = 2 * PBOX + 8
        week_x = PAGE_W - M - TASK_DUE_COL - week_w
        return week_x - 16 - prio_w, week_x

    def page_tasks(self, page: PageSpec):
        """Asana tasks assigned to me. Ticking a box completes them in Asana."""
        if page.idx == 1:
            self.c.bookmarkPage("sec-tasks")
        self.nav("tasks")
        y = CONTENT_TOP + 36
        title = "Tasks" + (f"  {page.idx}/{page.total}" if page.total > 1 else "")
        self.text(M, y, title, FB, 36)
        self.text(PAGE_W - M, y, "tick = done  ·  TW/NW = this/next week", F, 18, GREY, align="right")
        self.line(M, y + 16, PAGE_W - M, y + 16, 1.5)

        if not self.d.asana_status.ok:
            self.unavailable(y + 50, "Asana", self.d.asana_status)
            self.footer(page)
            return

        prio_x, week_x = self._task_columns()
        self.priority_header(prio_x, y + 38)
        self.week_header(week_x, y + 38)

        ry = y + 48
        for a in page.items:
            overdue = a.due is not None and a.due < self.d.today
            high = a.priority.lower() == "high"
            self.checkbox(a.gid, page.n, M, ry + 16)
            tx = M + BOX + 18
            name_w = prio_x - tx - 16
            self.text(tx, ry + 36, _fit(a.name, FB if (overdue or high) else F, 25, name_w),
                      FB if (overdue or high) else F, 25)
            if a.project:
                self.text(tx, ry + 62, _fit(a.project, F, 17, name_w), F, 17, GREY)

            due = _due_label(a.due, self.d.today) if a.due else "—"
            self.text(PAGE_W - M, ry + 44, due, FB if overdue else F, 20,
                      black if overdue else GREY, align="right")
            if a.priority_options:
                self.priority_picker(a, page.n, prio_x, ry + 12)
            self.week_picker(a.gid, page.n, week_x, ry + 12, a.week)

            self.line(M, ry + PRIO_ROW_H, PAGE_W - M, ry + PRIO_ROW_H, 0.75, RULE)
            ry += PRIO_ROW_H

        if not page.items and page.idx == 1:
            self.text(M, ry + 44, "Nothing assigned to you.", F, 24, GREY)
            ry += ROW_H

        self._new_tasks_box(page, max(ry + 24, PAGE_H - 60 - NEW_BOX_H))
        self.footer(page)

    def _new_tasks_box(self, page: PageSpec, top: float) -> None:
        """Ruled lines for handwritten tasks, each with its own pickers.

        Every task page carries one. Writing a task down should not mean
        flipping to the last page first, and the rows this costs are cheaper
        than the friction of not having it where you are.
        """
        box_h = PAGE_H - 60 - top
        lines = max(NEW_BOX_LINES, int(box_h // 78))
        line_h = box_h / lines

        prio_x, week_x = self._task_columns()

        self.text(M, top - 12, "New tasks", FB, 26)
        # The hint moves left to clear the column headers, which have to sit
        # over their own columns to mean anything.
        self.text(M + 190, top - 12, "one per line  ·  goes to Asana", F, 17, GREY)
        self.priority_header(prio_x, top - 12)
        self.week_header(week_x, top - 12)
        self.rect(M, top, PAGE_W - 2 * M, box_h, stroke=2)
        for i in range(1, lines):
            self.line(M, top + i * line_h, PAGE_W - M, top + i * line_h, 0.75, RULE)

        # The writing area stops short of the pickers so a long task title does
        # not run its own ink into the boxes and read as a choice.
        self.region("newtasks", "newtasks", page.n, M, top,
                    prio_x - M - 12, box_h, int(line_h))

        box_y = (line_h - PBOX) / 2
        for i in range(lines):
            ly = top + i * line_h + box_y
            self.priority_picker_at(f"new{i}", page.n, prio_x, ly)
            self.week_picker(f"new{i}", page.n, week_x, ly)

    def _member_options(self) -> list[tuple[str, str]]:
        """(member id, box label) per board member -- a single letter, the
        first of the name, so the column fits over a 38 px box."""
        return [(m["id"], (m["name"] or m["initials"] or "?")[0].upper()) for m in self.d.trello_members]

    def _personal_columns(self) -> tuple[float, float]:
        """(move x, member x): the two picker groups, right of the name."""
        n_mem = max(1, len(self._member_options()))
        mem_w = n_mem * PBOX + (n_mem - 1) * 8
        move_w = len(MOVES) * PBOX + (len(MOVES) - 1) * 8
        mem_x = PAGE_W - M - TASK_DUE_COL - mem_w
        return mem_x - 20 - move_w, mem_x

    def page_personal(self, page: PageSpec):
        """The shared Trello board. Tick = done; TW/NW/NM move the card to that
        list; a box per person assigns them."""
        if page.idx == 1:
            self.c.bookmarkPage("sec-personal")
        self.nav("personal")
        y = CONTENT_TOP + 36
        title = "Personal" + (f"  {page.idx}/{page.total}" if page.total > 1 else "")
        self.text(M, y, title, FB, 36)
        people = " / ".join(m["name"] for m in self.d.trello_members)
        hint = "tick = done  ·  TW/NW/NM = move to this/next week, next month"
        if people:
            hint += f"  ·  assign {people}"
        self.text(PAGE_W - M, y, hint, F, 17, GREY, align="right")
        self.line(M, y + 16, PAGE_W - M, y + 16, 1.5)

        if not self.d.trello_status.ok:
            self.unavailable(y + 50, "Trello", self.d.trello_status)
            self.footer(page)
            return

        move_x, mem_x = self._personal_columns()
        members = self._member_options()
        self.pick_header(move_x, y + 38, MOVES)
        self.pick_header(mem_x, y + 38, members)

        ry = y + 48
        for kind, item in page.items:
            if kind == "list":
                self.text(M, ry + 48, str(item), FB, 22, GREY)
                self.line(M, ry + ROW_H - 8, PAGE_W - M, ry + ROW_H - 8, 1, RULE)
                ry += ROW_H
                continue
            c = item
            overdue = c.due is not None and c.due < self.d.today
            soon = c.due is not None and c.due <= self.d.today
            self.checkbox(c.rid, page.n, M, ry + 16)
            tx = M + BOX + 18
            name_w = move_x - tx - 16
            self.text(tx, ry + 36, _fit(c.name, FB if soon else F, 25, name_w), FB if soon else F, 25)
            # Who it is on, then its labels: the bars under the boxes say the
            # same thing, but a name reads faster than a bar.
            who = " & ".join(m["name"] for m in self.d.trello_members if m["id"] in c.member_ids) \
                or (" & ".join(c.members) if c.members else "")
            sub = "  ·  ".join(x for x in [who] + list(c.labels) if x)
            if sub:
                self.text(tx, ry + 62, _fit(sub, F, 17, name_w), F, 17, GREY)

            due = _due_label(c.due, self.d.today) if c.due else "—"
            self.text(PAGE_W - M, ry + 44, due, FB if overdue else F, 20,
                      black if overdue else GREY, align="right")
            self.pick_boxes(c.rid, page.n, move_x, ry + 12, MOVES, {c.list_key}, "pick1")
            if members:
                self.pick_boxes(c.rid, page.n, mem_x, ry + 12, members, set(c.member_ids), "flag")

            self.line(M, ry + PRIO_ROW_H, PAGE_W - M, ry + PRIO_ROW_H, 0.75, RULE)
            ry += PRIO_ROW_H

        if not page.items and page.idx == 1:
            self.text(M, ry + 44, "Nothing open on the board.", F, 24, GREY)
            ry += ROW_H

        self._new_personal_box(page, max(ry + 24, PAGE_H - 60 - NEW_BOX_H))
        self.footer(page)

    def _new_personal_box(self, page: PageSpec, top: float) -> None:
        """Ruled lines for handwritten cards, each with the same pickers as the
        rows above: TW/NW/NM files the new card into that list, a person-box
        puts them on it. Unmarked, a line lands in the inbox list."""
        box_h = PAGE_H - 60 - top
        lines = max(NEW_BOX_LINES, int(box_h // 78))
        line_h = box_h / lines
        move_x, mem_x = self._personal_columns()
        members = self._member_options()

        self.text(M, top - 12, "New personal tasks", FB, 26)
        self.text(M + 290, top - 12, "one per line  ·  goes to Trello", F, 17, GREY)
        self.pick_header(move_x, top - 12, MOVES)
        self.pick_header(mem_x, top - 12, members)
        self.rect(M, top, PAGE_W - 2 * M, box_h, stroke=2)
        for i in range(1, lines):
            self.line(M, top + i * line_h, PAGE_W - M, top + i * line_h, 0.75, RULE)

        # The writing area stops short of the pickers so a long title does not
        # run its ink into the boxes and read as a choice.
        self.region("newpersonal", "newtasks", page.n, M, top,
                    move_x - M - 12, box_h, int(line_h))

        box_y = (line_h - PBOX) / 2
        for i in range(lines):
            ly = top + i * line_h + box_y
            self.pick_boxes(f"newp{i}", page.n, move_x, ly, MOVES, set(), "pick1")
            if members:
                self.pick_boxes(f"newp{i}", page.n, mem_x, ly, members, set(), "flag")

    def page_projects(self, page: PageSpec):
        """The pipeline board, grouped by section, in board order.

        Read-only: these are deals, not to-dos, and moving one between sections
        is a judgement call that belongs in Asana rather than a tick box.
        """
        if page.idx == 1:
            self.c.bookmarkPage("sec-projects")
        self.nav("projects")
        y = CONTENT_TOP + 36
        title = "Projects" + (f"  {page.idx}/{page.total}" if page.total > 1 else "")
        self.text(M, y, title, FB, 36)

        if not self.d.projects_status.ok:
            self.line(M, y + 16, PAGE_W - M, y + 16, 1.5)
            self.unavailable(y + 50, "Project board", self.d.projects_status)
            self.footer(page)
            return

        self.line(M, y + 16, PAGE_W - M, y + 16, 1.5)

        ry = y + 44
        if page.idx == 1 and self.pipeline:
            ry = self._stage_band(y + 28)
        self._card_sections(page, ry, self._project_bits)

        if not page.items:
            self.text(M, ry + 30, "No cards on the board.", F, 24, GREY)
        self.footer(page)

    def _project_bits(self, c) -> list[str]:
        bits = [v for _, v in c.fields][:3]
        if c.due:
            bits.append(_due_label(c.due, self.d.today))
        return bits

    def _card_sections(self, page: PageSpec, ry: float, bits_of) -> float:
        """Draw the page's (header, cards) groups from `ry` down; returns the
        y after the last one. `bits_of(card)` gives the grey detail line."""
        for section, cards in page.items:
            sub = sum(c.budget or 0 for c in cards)
            self.rect(M, ry - 4, PAGE_W - 2 * M, 34, stroke=0, fill=Color(0.92, 0.92, 0.90))
            self.text(M + 10, ry + 20, _fit(section.upper(), FB, 19, PAGE_W - 2 * M - 200), FB, 19)
            if sub:
                self.text(PAGE_W - M - 10, ry + 20, _kr(sub), F, 18, GREY, align="right")
            ry += 42

            for c in cards:
                budget = c.budget_str()
                bw = pdfmetrics.stringWidth(budget, FB, 21) + 20 if budget else 0
                self.text(M + 14, ry + 22, _fit(c.name, F, 23, PAGE_W - 2 * M - bw - 30), F, 23)
                if budget:
                    self.text(PAGE_W - M, ry + 22, budget, FB, 21, align="right")

                bits = bits_of(c)
                if bits:
                    self.text(M + 14, ry + 44, _fit("  ·  ".join(bits), F, 17,
                                                    PAGE_W - 2 * M - 30), F, 17, GREY)
                    ry += 56
                else:
                    ry += 38
                if ry > PAGE_H - 90:
                    break
            ry += 8
        return ry

    def _stage_band(self, y: float) -> float:
        """Three totals across the top: what is won, what is committed, and what
        is still only possible. One summed pipeline number hid that distinction.

        Returns the y to continue drawing from.
        """
        totals = _stage_totals(self.pipeline)
        counts: dict[str, int] = {"active": 0, "signed": 0, "incoming": 0}
        for c in self.pipeline:
            counts[_stage(c.section)] += 1

        cells = [("Active", totals["active"], counts["active"]),
                 ("Signed", totals["signed"], counts["signed"]),
                 ("Incoming", totals["incoming"], counts["incoming"])]
        return self._band(y, cells, highlight=0)     # won work is the one to read first

    def _band(self, y: float, cells: list[tuple[str, float, int]], highlight: int) -> float:
        """Three boxes across the top -- label, kr total, count -- with a black
        bar over the one to read first. Returns the y to continue drawing from."""
        w = (PAGE_W - 2 * M) / 3
        h = 96
        for i, (label, amount, n) in enumerate(cells):
            x = M + i * w
            self.rect(x, y, w, h, stroke=1.5)
            if i == highlight:
                self.rect(x, y, w, 6, stroke=0, fill=black)
            self.text(x + 14, y + 30, label.upper(), F, 17, GREY)
            self.text(x + 14, y + 62, _kr(amount) if amount else "—", FB, 28)
            self.text(x + w - 14, y + 30, f"{n}", F, 17, GREY, align="right")
        return y + h + 26

    def page_accounting(self, page: PageSpec):
        """The Projects board again, grouped by Accounting Month instead of
        pipeline stage, earliest month first, January this year to December
        next year, delivered and completed projects included.

        Read-only, like Projects: which month a project books in is decided in
        Asana. The band on top gives last month, this month and everything
        after; earlier months are on the page but not in the band.
        """
        if page.idx == 1:
            self.c.bookmarkPage("sec-accounting")
        self.nav("accounting")
        y = CONTENT_TOP + 36
        title = "Accounting" + (f"  {page.idx}/{page.total}" if page.total > 1 else "")
        self.text(M, y, title, FB, 36)
        span = f"Jan {self.d.today.year} – Dec {self.d.today.year + 1}"
        self.text(PAGE_W - M, y, f"by accounting month  ·  {span}  ·  delivered included",
                  F, 18, GREY, align="right")
        self.line(M, y + 16, PAGE_W - M, y + 16, 1.5)

        if not self.d.projects_status.ok:
            self.unavailable(y + 50, "Project board", self.d.projects_status)
            self.footer(page)
            return

        ry = y + 44
        cards = self._accounting_cards()
        if page.idx == 1 and cards:
            ry = self._month_band(y + 28, cards)
        self._card_sections(page, ry, self._accounting_bits)

        if not page.items:
            self.text(M, ry + 30, "No cards with an accounting month in this window.", F, 24, GREY)
        self.footer(page)

    def _accounting_bits(self, c) -> list[str]:
        """Pipeline stage first (the grouping this page gave up), then the
        fields that matter for the books."""
        bits = [c.section]
        for needles in (("stage",), ("invoic", "status"), ("client",)):
            v = _field(c, *needles)
            if v:
                bits.append(v)
        return bits

    def _month_band(self, y: float, cards) -> float:
        t = self.d.today
        this = (t.year, t.month)
        prev = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
        totals = {"prev": 0.0, "this": 0.0, "later": 0.0}
        counts = {"prev": 0, "this": 0, "later": 0}
        for c in cards:
            k = _month_key(_month_label(c))
            if k[0] != 0:
                continue                  # no usable month: listed, not summed
            ym = (k[1], k[2])
            if ym == prev:
                bucket = "prev"
            elif ym == this:
                bucket = "this"
            elif ym > this:
                bucket = "later"
            else:
                continue                  # older months are on the page, not in the band
            totals[bucket] += c.budget or 0
            counts[bucket] += 1
        cells = [(MONTHS_SHORT[prev[1] - 1] + " (previous month)", totals["prev"], counts["prev"]),
                 ("This month", totals["this"], counts["this"]),
                 ("Later", totals["later"], counts["later"])]
        return self._band(y, cells, highlight=1)

    def render(self) -> None:
        draw = {"today": self.page_today, "week": self.page_week, "tasks": self.page_tasks,
                "personal": self.page_personal, "projects": self.page_projects,
                "accounting": self.page_accounting}
        for page in self.pages:
            draw[page.kind](page)
            self.c.showPage()
        self.c.save()
        layout = {
            "date": self.d.today.isoformat(),
            "page_size": [PAGE_W, PAGE_H],
            "pages": len(self.pages),
            "sections": {kind: self.first_page_of(kind) for _, kind in NAV_ITEMS},
            "regions": [r.to_json() for r in self.regions],
        }
        self.out_layout.write_text(json.dumps(layout, indent=2), encoding="utf-8")


# Where each board section falls on the way to signed work. Matched on the
# section name so renaming "Active" to "Active projects" still lands right, and
# anything unrecognised counts as pipeline rather than being dropped from the
# totals -- an unnoticed rename should understate nothing.
def _field(card, *needles: str) -> str:
    """Value of the first custom field whose label contains every needle,
    case-insensitively -- so a rename from "Accounting Month" to "Acc. month"
    in Asana keeps working."""
    for label, value in card.fields:
        low = label.lower()
        if all(n in low for n in needles):
            return value
    return ""


def _month_label(card) -> str:
    return _field(card, "accounting", "month") or NO_MONTH


def _month_key(label: str) -> tuple:
    """Sort key for an Accounting Month value such as "June 2026" or "2026 aug":
    real months first in calendar order, then anything unparseable
    alphabetically, then the no-month bucket."""
    if label == NO_MONTH:
        return (2, 0, 0, "")
    parts = label.replace(",", " ").replace("-", " ").split()
    if len(parts) == 2:
        for mon, yr in ((parts[0], parts[1]), (parts[1], parts[0])):
            m = _MONTH_INDEX.get(mon.lower()[:3])
            if m and yr.isdigit():
                return (0, int(yr), m, "")
    return (1, 0, 0, label.lower())


def _stage(section: str) -> str:
    low = section.lower()
    if "active" in low:
        return "active"
    if "signed" in low:
        return "signed"
    return "incoming"


def _stage_totals(cards) -> dict[str, float]:
    out = {"active": 0.0, "signed": 0.0, "incoming": 0.0}
    for c in cards:
        out[_stage(c.section)] += c.budget or 0
    return out


def _kr(amount: float) -> str:
    """Norwegian thousands grouping — 1 250 000 kr."""
    return f"{amount:,.0f}".replace(",", " ") + " kr"


def _due_label(d: date, today: date) -> str:
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == -1:
        return "yesterday"
    if delta < 0:
        return f"{-delta}d overdue"
    if delta == 1:
        return "tomorrow"
    if delta < 7:
        return DAYS_SHORT[d.weekday()]
    return f"{d.day} {MONTHS_SHORT[d.month - 1]}"


def render_sheet(data: SheetData, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / f"Daily Sheet — {data.today.isoformat()}.pdf"
    layout = out_dir / "layout.json"
    Renderer(data, pdf, layout).render()
    return pdf, layout
