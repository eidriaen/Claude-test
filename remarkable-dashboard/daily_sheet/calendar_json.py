"""Calendar from a JSON file a Power Automate flow writes to OneDrive.

The published-ICS route is capped by tenant policy: where publishing is limited
to "can view when I'm busy", the feed carries no titles at all and no amount of
parsing recovers them. Power Automate reads the calendar through your own
delegated access instead, so it returns what you can see -- subjects, locations,
attendees -- and needs no admin approval and no published link.

The flow writes a file; OneDrive syncs it to the machine that renders the sheet;
this module reads it off disk. No second credential, no network call here.

Shapes vary with how the flow is built, so parsing is deliberately forgiving:
a bare array or an object with `value`/`events`/`items`, and the common field
spellings for each of subject, start, end and location.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import TZ, Config
from .models import Event, SectionStatus

# Field name candidates, most likely first. The Office 365 connector emits the
# first of each; the others show up when a flow reshapes the output by hand.
_SUBJECT = ("subject", "title", "name", "summary")
_START = ("start", "startTime", "startWithTimeZone", "dateTimeStart", "starts")
_END = ("end", "endTime", "endWithTimeZone", "dateTimeEnd", "ends")
_LOCATION = ("location", "locationDisplayName", "where", "room")
_ALLDAY = ("isAllDay", "allDay", "is_all_day")


def _first(row: dict, names: tuple[str, ...]) -> object:
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
        # tolerate a nested {"dateTime": ...} the Graph shape uses
        low = {k.lower(): v for k, v in row.items()}
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return low[n.lower()]
    return None


def _text(value: object) -> str:
    if isinstance(value, dict):                      # {"displayName": "..."} etc.
        for k in ("displayName", "name", "value", "text"):
            if value.get(k):
                return str(value[k]).strip()
        return ""
    return str(value or "").strip()


def parse_dt(value: object) -> datetime | None:
    """Parse one timestamp into Europe/Oslo.

    A value carrying an offset is authoritative and merely converted. A naive
    one is assumed to already be Oslo local, which is what the connector returns
    when the flow sets its time zone -- guessing UTC there would shift every
    event by an hour or two depending on the season.
    """
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("DateTime") or ""
    text = str(value or "").strip()
    if not text:
        return None

    text = text.replace("Z", "+00:00")
    # Office 365 emits 7 fractional digits; fromisoformat accepts at most 6.
    if "." in text:
        head, _, tail = text.partition(".")
        digits = "".join(c for c in tail if c.isdigit())[:6]
        rest = tail[len(tail) - len(tail.lstrip("0123456789")):]
        text = f"{head}.{digits or '0'}{rest}"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text[:len(fmt) + 2], fmt)
                break
            except ValueError:
                continue
        else:
            return None

    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def _rows(data: object) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("value", "events", "items", "body", "data"):
            inner = data.get(key)
            if isinstance(inner, list):
                return [r for r in inner if isinstance(r, dict)]
            if isinstance(inner, dict):                   # Power Automate nests body
                nested = _rows(inner)
                if nested:
                    return nested
    return []


def parse_calendar_json(text: str) -> list[Event]:
    rows = _rows(json.loads(text))
    events: list[Event] = []
    for row in rows:
        start = parse_dt(_first(row, _START))
        end = parse_dt(_first(row, _END))
        if start is None:
            continue
        all_day = bool(_first(row, _ALLDAY))
        if end is None:
            end = start + timedelta(days=1 if all_day else 1 / 24)
        events.append(Event(
            start=start,
            end=end,
            title=_text(_first(row, _SUBJECT)) or "(no title)",
            location=_text(_first(row, _LOCATION)),
            all_day=all_day,
        ))
    return sorted(events, key=lambda e: e.start)


def load_json_events(cfg: Config, start: date, end: date) -> tuple[list[Event], SectionStatus]:
    """Read the flow's file and keep events touching [start, end]."""
    path = Path(cfg.calendar_json).expanduser()
    if not path.exists():
        return [], SectionStatus(
            ok=False,
            error=f"{path} not found — has the Power Automate flow run, and has OneDrive synced?")
    try:
        events = parse_calendar_json(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        return [], SectionStatus(ok=False, error=f"{type(exc).__name__}: {exc}")

    if not events:
        return [], SectionStatus(ok=False, error=f"{path.name} parsed but held no events")

    # A file that stops updating is the quiet failure here: it keeps parsing and
    # the sheet keeps rendering yesterday's calendar. Say so rather than serve
    # stale data silently.
    age = datetime.now().timestamp() - path.stat().st_mtime
    status = SectionStatus()
    if age > 36 * 3600:
        status = SectionStatus(
            ok=False,
            error=f"{path.name} last changed {int(age // 3600)}h ago — the flow may have stopped")

    kept = [e for e in events if e.end.date() >= start and e.start.date() <= end]
    return kept, status
