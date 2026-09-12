"""Fetch the Outlook published ICS feed and expand events (incl. recurrence)
into a date range, normalised to Europe/Oslo."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import icalendar
import recurring_ical_events
import requests

from .config import TZ, Config
from .models import Event, SectionStatus


def fetch_ics(cfg: Config) -> bytes:
    if cfg.use_fixtures:
        return (cfg.fixtures_dir / "sample.ics").read_bytes()
    if not cfg.ics_url:
        raise RuntimeError("ICS_URL is not set")
    r = requests.get(cfg.ics_url, timeout=20, headers={"User-Agent": "daily-sheet/1.0"})
    r.raise_for_status()
    if b"BEGIN:VCALENDAR" not in r.content[:512]:
        raise RuntimeError("ICS_URL did not return an iCalendar document")
    return r.content


def _to_oslo(v, all_day: bool) -> datetime:
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=TZ)
        return v.astimezone(TZ)
    # date (all-day)
    return datetime.combine(v, time.min, tzinfo=TZ)


def expand_events(ics_bytes: bytes, start: date, end: date) -> list[Event]:
    """Events overlapping [start, end) — end exclusive."""
    cal = icalendar.Calendar.from_ical(ics_bytes)
    out: list[Event] = []
    for comp in recurring_ical_events.of(cal).between(start, end):
        dtstart = comp.get("DTSTART").dt
        dtend_prop = comp.get("DTEND")
        all_day = not isinstance(dtstart, datetime)
        s = _to_oslo(dtstart, all_day)
        if dtend_prop is not None:
            e = _to_oslo(dtend_prop.dt, all_day)
        else:
            dur = comp.get("DURATION")
            e = s + (dur.dt if dur is not None else (timedelta(days=1) if all_day else timedelta(hours=1)))
        title = str(comp.get("SUMMARY", "") or "").strip() or "(untitled)"
        # Outlook marks private items as "Private Appointment" at this sharing level; keep as-is.
        location = str(comp.get("LOCATION", "") or "").strip()
        out.append(Event(start=s, end=e, title=title, location=location, all_day=all_day))
    out.sort(key=lambda ev: (ev.start, ev.end))
    return out


def load_events(cfg: Config, start: date, end: date) -> tuple[list[Event], SectionStatus]:
    """Whichever calendar source is configured.

    CALENDAR_JSON wins when set: it exists precisely because the published ICS
    could not carry titles, so falling back to ICS on a bad read would quietly
    restore the problem it was added to solve.
    """
    if cfg.calendar_json and not cfg.use_fixtures:
        from .calendar_json import load_json_events
        return load_json_events(cfg, start, end)
    try:
        events = expand_events(fetch_ics(cfg), start, end)
    except Exception as exc:  # noqa: BLE001 — any source failure degrades that section only
        return [], SectionStatus(ok=False, error=f"{type(exc).__name__}: {exc}")
    if is_free_busy(events):
        # A grid of "Busy" looks like a working calendar and is not one. Say
        # the feed carries no titles instead, because the fix is at the source
        # and nothing on a page full of Busy points at it.
        return [], SectionStatus(
            ok=False,
            error=f"feed carries no titles ({len(events)} events, all 'Busy') — "
                  f"published at 'Can view when I'm busy'. Use CALENDAR_JSON.")
    return events, SectionStatus()


# Words a free/busy publish leaves behind instead of a title, in the languages
# this calendar actually speaks.
BUSY_WORDS = {"", "busy", "opptatt", "tentative", "free", "ledig",
              "opptatt/busy", "privat", "private", "no title", "(no title)"}


def is_free_busy(events: list[Event], threshold: float = 0.8) -> bool:
    """Is this feed stripped of titles?

    By proportion, not unanimously: requiring *every* title to be a busy-word
    missed it on a real feed, where one differently-named entry let a useless
    calendar through as healthy.
    """
    if not events:
        return False
    opaque = sum(1 for e in events if (e.title or "").strip().lower() in BUSY_WORDS)
    return opaque >= len(events) * threshold


def events_on(events: list[Event], day: date) -> list[Event]:
    """Events touching a given day (multi-day/all-day events appear on each day)."""
    day_start = datetime.combine(day, time.min, tzinfo=TZ)
    day_end = day_start + timedelta(days=1)
    return [ev for ev in events if ev.start < day_end and ev.end > day_start]
