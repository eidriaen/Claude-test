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
