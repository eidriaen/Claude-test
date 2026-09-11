"""Power Automate JSON calendar source.

Flows get built by hand and reshaped later, so the parser has to tolerate the
common variations rather than assume one exact schema.
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from daily_sheet.calendar_json import parse_calendar_json, parse_dt
from daily_sheet.config import TZ


def _ev(**kw):
    return json.dumps({"value": [kw]})


def test_office365_shape():
    events = parse_calendar_json(_ev(
        subject="Standup", location="Pantone", isAllDay=False,
        start="2026-09-11T09:15:00.0000000", end="2026-09-11T09:30:00.0000000"))
    assert len(events) == 1
    e = events[0]
    assert e.title == "Standup" and e.location == "Pantone"
    assert (e.start.hour, e.start.minute) == (9, 15)


def test_graph_nested_shape():
    events = parse_calendar_json(_ev(
        subject="Workshop",
        start={"dateTime": "2026-09-11T13:00:00Z"},
        end={"dateTime": "2026-09-11T15:00:00Z"},
        location={"displayName": "RGB"}))
    e = events[0]
    assert e.location == "RGB"
    # 13:00 UTC in September is 15:00 in Oslo (CEST).
    assert e.start.hour == 15, "an offset-carrying time must be converted, not relabelled"


def test_bare_array_and_alternate_field_names():
    events = parse_calendar_json(json.dumps([
        {"title": "Hjemmekontor", "startTime": "2026-09-12", "endTime": "2026-09-13",
         "allDay": True}]))
    assert events[0].title == "Hjemmekontor" and events[0].all_day


def test_naive_time_is_oslo_not_utc():
    """The connector returns local time when the flow sets a time zone.

    Reading it as UTC would shift every event by one or two hours depending on
    the season -- wrong in a way that looks like a plausible schedule.
    """
    dt = parse_dt("2026-09-11T09:00:00")
    assert dt.hour == 9
    assert dt.utcoffset().total_seconds() == 2 * 3600     # CEST


def test_seven_digit_fractional_seconds():
    """Office 365 emits 7 digits; fromisoformat accepts at most 6."""
    assert parse_dt("2026-09-11T09:15:00.0000000") is not None


def test_rows_without_a_start_are_skipped_not_fatal():
    events = parse_calendar_json(json.dumps({"value": [
        {"subject": "broken"},
        {"subject": "fine", "start": "2026-09-11T10:00:00", "end": "2026-09-11T11:00:00"},
    ]}))
    assert [e.title for e in events] == ["fine"]


def test_missing_title_does_not_produce_an_empty_row():
    events = parse_calendar_json(_ev(start="2026-09-11T10:00:00", end="2026-09-11T11:00:00"))
    assert events[0].title == "(no title)"


# --- the shape the Office 365 connector actually emits -----------------------
def _o365(**kw):
    """One row as the V3 connector returns it: naive UTC *and* offset fields."""
    row = {"timeZone": "UTC", "isAllDay": False}
    row.update(kw)
    return json.dumps({"value": [row]})


def test_naive_start_is_utc_when_the_row_says_so():
    """The connector returns `start` naive *and* `startWithTimeZone` with an
    offset, and the naive one is UTC. Reading it as Oslo puts every meeting two
    hours early in summer -- a schedule that still looks plausible, so nothing
    on the sheet would give it away."""
    events = parse_calendar_json(_o365(
        subject="Kongsberg prosjekter",
        start="2026-09-11T07:00:00.0000000", end="2026-09-11T07:30:00.0000000",
        startWithTimeZone="2026-09-11T07:00:00+00:00",
        endWithTimeZone="2026-09-11T07:30:00+00:00"))
    assert (events[0].start.hour, events[0].start.minute) == (9, 0)
    assert (events[0].end.hour, events[0].end.minute) == (9, 30)


def test_naive_without_a_timezone_field_stays_oslo():
    """A hand-shaped flow that sets its own time zone returns local time."""
    events = parse_calendar_json(json.dumps({"value": [
        {"subject": "x", "start": "2026-09-11T07:00:00", "end": "2026-09-11T08:00:00"}]}))
    assert events[0].start.hour == 7


def test_all_day_lands_on_its_own_day():
    """An all-day event is a date, not an instant.

    Converting its midnight-UTC stamp puts it at 02:00 Oslo in summer, which
    drags the day-long bar onto the day and off the top of the grid.
    """
    events = parse_calendar_json(_o365(
        subject="MSPO (Polen)", isAllDay=True,
        start="2026-09-07T00:00:00.0000000", end="2026-09-09T00:00:00.0000000",
        startWithTimeZone="2026-09-07T00:00:00+00:00",
        endWithTimeZone="2026-09-09T00:00:00+00:00"))
    e = events[0]
    assert e.all_day
    assert (e.start.date().isoformat(), e.start.hour) == ("2026-09-07", 0)


def test_recurring_instances_each_come_through():
    """Power Automate expands a series into one row per instance, so the sheet
    needs no RRULE logic for this source -- but each instance must survive."""
    rows = [{"subject": "Standup", "timeZone": "UTC", "isAllDay": False,
             "startWithTimeZone": f"2026-09-{d}T07:10:00+00:00",
             "endWithTimeZone": f"2026-09-{d}T07:40:00+00:00"} for d in (15, 16, 17)]
    events = parse_calendar_json(json.dumps({"value": rows}))
    assert len(events) == 3
    assert {e.start.day for e in events} == {15, 16, 17}
    assert all(e.start.hour == 9 for e in events), "each instance converts to Oslo"
