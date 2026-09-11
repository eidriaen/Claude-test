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
ROW_H = 72
HOUR_FIRST, HOUR_LAST = 7, 20      # grid shows 07:00 … 21:00

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
MONTHS_SHORT = [m[:3] for m in MONTHS]

NAV_ITEMS = [("Today", "today"), ("Week", "week"), ("Tasks", "tasks"), ("Projects", "projects"), ("Notes", "notes")]


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


@dataclass
class PageSpec:
    kind: str                # today | week | tasks | asana | notes
    n: int                   # 1-based page number
    week_offset: int = 0     # week pages: -1 / 0 / 1
    items: list = field(default_factory=list)
    idx: int = 1             # tasks/asana: page i of total
    total: int = 1
    last: bool = True


# --- renderer ---------------------------------------------------------------
class Renderer:
    TASK_ROWS_FULL = 20          # rows when the page has no "New tasks" box
    TASK_ROWS_WITH_BOX = 13
    ASANA_ROWS = 20
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
        for off in (-1, 0, 1):
            pages.append(PageSpec("week", len(pages) + 1, week_offset=off))

        rest = list(self.d.asana_tasks)
        chunks: list[list] = []
        while len(rest) > self.TASK_ROWS_WITH_BOX:
            chunks.append(rest[: self.TASK_ROWS_FULL])
            rest = rest[self.TASK_ROWS_FULL:]
        chunks.append(rest)
        for i, ch in enumerate(chunks, 1):
            pages.append(PageSpec("tasks", len(pages) + 1, items=ch, idx=i,
                                  total=len(chunks), last=i == len(chunks)))

        proj_pages = self._project_pages()
        for i, ch in enumerate(proj_pages, 1):
            pages.append(PageSpec("projects", len(pages) + 1, items=ch, idx=i,
                                  total=len(proj_pages), last=i == len(proj_pages)))

        pages.append(PageSpec("notes", len(pages) + 1))
        return pages

    def _project_pages(self) -> list[list[tuple[str, list]]]:
        """Group cards by board section, then split into page-sized chunks.

        A section header plus its cards is kept together where it fits; a long
        section spills to the next page under a "(cont.)" header rather than
        being shrunk.
        """
        if not hasattr(self, "_proj_cache"):
            by_section: dict[str, list] = {}
            for c in self.d.projects:
                by_section.setdefault(c.section, []).append(c)
            order = [s for s in self.d.project_sections if s in by_section]
            order += [s for s in by_section if s not in order]

            pages: list[list[tuple[str, list]]] = []
            page: list[tuple[str, list]] = []
            used = 0
            for name in order:
                cards = by_section[name]
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
            self._proj_cache = pages or [[]]
        return self._proj_cache

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
        self.region(rid, "check", page, x, y, BOX, BOX)

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
        gh = 930
        if self.d.events_status.ok:
            self.hour_grid(M, gy, PAGE_W - 2 * M, gh, [d], [PAGE_W - 2 * M - 70])
            all_day = [ev for ev in events_on(self.d.events, d) if ev.all_day]
            if all_day:
                self.text(M + 70, gy - 10, "All day: " + ", ".join(ev.title for ev in all_day), F, 20, GREY)
        else:
            self.unavailable(gy, "Calendar", self.d.events_status)

        # top-3 priorities
        ty = gy + gh + 50
        self.text(M, ty, "Top priorities", FB, 28)
        self.line(M, ty + 12, PAGE_W - M, ty + 12, 1.5)
        ry = ty + 30
        for tid, label, tag in self._top3()[:3]:
            self.checkbox(tid, page.n, M, ry + 12)
            self.text(M + BOX + 20, ry + 44, _fit(label, F, 26, PAGE_W - 2 * M - BOX - 260), F, 26)
            self.text(PAGE_W - M, ry + 44, tag, F, 20, GREY, align="right")
            ry += ROW_H
        if not self._top3():
            self.text(M, ry + 40, "Nothing open.", F, 24, GREY)

        # ingestion report
        iy = max(ry + 40, PAGE_H - 330)
        r = self.d.report
        self.text(M, iy, "Yesterday's sheet", FB, 28)
        self.line(M, iy + 12, PAGE_W - M, iy + 12, 1.5)
        self.text(M, iy + 52, r.note or r.summary(), F, 24)
        if r.note and (r.completed or r.added or r.unreadable):
            self.text(M, iy + 86, r.summary(), F, 24)
        if self.d.unreadable_pngs:
            sy = iy + 100
            self.text(M, sy, "Couldn't read — please rewrite:", F, 20, GREY)
            sy += 12
            for png in self.d.unreadable_pngs[:3]:
                try:
                    self.c.drawImage(str(png), M, _Y(sy + 56), width=PAGE_W - 2 * M, height=56,
                                     preserveAspectRatio=True, anchor="nw")
                except Exception:  # noqa: BLE001 — a bad strip must not sink the sheet
                    pass
                sy += 62
        self.footer(page)

    def _top3(self) -> list[tuple[str, str, str]]:
        """(region id, label, tag) across both lists. Score: lower is more urgent."""
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
        days = [monday + timedelta(days=i) for i in range(7)]
        if page.week_offset == 0:
            self.c.bookmarkPage("sec-week")
        self.c.bookmarkPage(f"week{page.week_offset}")
        self.nav("week")
        y = CONTENT_TOP + 36
        label = {-1: "Last week", 0: "This week", 1: "Next week"}[page.week_offset]
        iso = monday.isocalendar()
        self.text(PAGE_W / 2, y, f"{label}  ·  Week {iso.week}  ·  {_fmt_range(days[0], days[-1])}", FB, 30, align="center")
        # prev / next tap zones (top corners)
        zone_w, zone_h = 200, 64
        if page.week_offset > -1:
            self.rect(M, y - 44, zone_w, zone_h, stroke=1.5, color=GREY)
            self.text(M + zone_w / 2, y, "‹ Prev", F, 24, align="center")
            self.link(f"week{page.week_offset - 1}", M, y - 44, zone_w, zone_h)
        if page.week_offset < 1:
            self.rect(PAGE_W - M - zone_w, y - 44, zone_w, zone_h, stroke=1.5, color=GREY)
            self.text(PAGE_W - M - zone_w / 2, y, "Next ›", F, 24, align="center")
            self.link(f"week{page.week_offset + 1}", PAGE_W - M - zone_w, y - 44, zone_w, zone_h)

        if not self.d.events_status.ok:
            self.unavailable(y + 60, "Calendar", self.d.events_status)
            self.footer(page)
            return

        # column widths: weekend 0.6×
        label_col = 70
        usable = PAGE_W - 2 * M - label_col
        unit = usable / (5 + 2 * 0.6)
        col_w = [unit if d.weekday() < 5 else unit * 0.6 for d in days]

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
        gh = PAGE_H - 60 - gy
        self.hour_grid(M, gy, PAGE_W - 2 * M, gh, days, col_w, label_col=label_col)
        self.footer(page)

    def page_tasks(self, page: PageSpec):
        """Asana tasks assigned to me. Ticking a box completes them in Asana."""
        if page.idx == 1:
            self.c.bookmarkPage("sec-tasks")
        self.nav("tasks")
        y = CONTENT_TOP + 36
        title = "Tasks" + (f"  {page.idx}/{page.total}" if page.total > 1 else "")
        self.text(M, y, title, FB, 36)
        self.text(PAGE_W - M, y, "tick = complete in Asana", F, 18, GREY, align="right")
        self.line(M, y + 16, PAGE_W - M, y + 16, 1.5)

        if not self.d.asana_status.ok:
            self.unavailable(y + 50, "Asana", self.d.asana_status)
            self.footer(page)
            return

        ry = y + 40
        due_col = 180
        for a in page.items:
            overdue = a.due is not None and a.due < self.d.today
            self.checkbox(a.gid, page.n, M, ry + 14)
            tx = M + BOX + 20
            name_w = PAGE_W - M - tx - due_col - 20
            self.text(tx, ry + 34, _fit(a.name, FB if overdue else F, 25, name_w),
                      FB if overdue else F, 25)
            if a.project:
                self.text(tx, ry + 60, _fit(a.project, F, 17, name_w), F, 17, GREY)
            due = _due_label(a.due, self.d.today) if a.due else "—"
            self.text(PAGE_W - M, ry + 42, due, FB if overdue else F, 21,
                      black if overdue else GREY, align="right")
            self.line(M, ry + ROW_H, PAGE_W - M, ry + ROW_H, 0.75, RULE)
            ry += ROW_H
        if not page.items and page.idx == 1:
            self.text(M, ry + 44, "Nothing assigned to you.", F, 24, GREY)
            ry += ROW_H

        if page.last:
            by = max(ry + 50, PAGE_H - 60 - 6 * 80 - 60)
            self.text(M, by, "New tasks", FB, 28)
            self.text(PAGE_W - M, by, "one per line  ·  goes to Asana", F, 18, GREY, align="right")
            box_top = by + 20
            box_h = PAGE_H - 60 - box_top
            lines = max(6, int(box_h // 80))
            line_h = box_h / lines
            self.rect(M, box_top, PAGE_W - 2 * M, box_h, stroke=2)
            for i in range(1, lines):
                self.line(M, box_top + i * line_h, PAGE_W - M, box_top + i * line_h, 0.75, RULE)
            self.region("newtasks", "newtasks", page.n, M, box_top, PAGE_W - 2 * M, box_h, int(line_h))
        self.footer(page)

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

        total = sum(c.budget or 0 for c in self.d.projects)
        if total and page.idx == 1:
            self.text(PAGE_W - M, y, f"pipeline {_kr(total)}", FB, 22, align="right")
        self.line(M, y + 16, PAGE_W - M, y + 16, 1.5)

        ry = y + 44
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

                bits = [v for _, v in c.fields][:3]
                if c.due:
                    bits.append(_due_label(c.due, self.d.today))
                if bits:
                    self.text(M + 14, ry + 44, _fit("  ·  ".join(bits), F, 17,
                                                    PAGE_W - 2 * M - 30), F, 17, GREY)
                    ry += 56
                else:
                    ry += 38
                if ry > PAGE_H - 90:
                    break
            ry += 8

        if not page.items:
            self.text(M, ry + 30, "No cards on the board.", F, 24, GREY)
        self.footer(page)

    def page_notes(self, page: PageSpec):
        self.c.bookmarkPage("sec-notes")
        self.nav("notes")
        y = CONTENT_TOP + 36
        self.text(M, y, "Notes", FB, 36)
        self.text(PAGE_W - M, y, f"transcribed to notes/{self.d.today.isoformat()}.md", F, 18, GREY, align="right")
        top = y + 40
        bottom = PAGE_H - 60
        line_h = 80
        yy = top + line_h
        while yy <= bottom:
            self.line(M, yy, PAGE_W - M, yy, 0.75, RULE)
            yy += line_h
        self.region("notes", "notes", page.n, M, top, PAGE_W - 2 * M, bottom - top, line_h)
        self.footer(page)

    # -- run ------------------------------------------------------------
    def render(self) -> None:
        draw = {"today": self.page_today, "week": self.page_week, "tasks": self.page_tasks,
                "projects": self.page_projects, "notes": self.page_notes}
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
