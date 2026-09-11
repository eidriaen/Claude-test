"""Pairing handwritten lines with the pickers beside them.

The transcription comes back as a list of lines with no idea which ruled row
each came from, but the priority and week boxes belong to specific rows. Get
this wrong and a task is filed under a priority nobody chose.
"""
from __future__ import annotations

from datetime import date

import pytest

from daily_sheet.cli import apply_marks
from daily_sheet.models import AsanaTask, IngestionReport
from daily_sheet.readback import Marks

TODAY = date(2026, 9, 11)


class FakeAsana:
    def __init__(self, week_gid="W38"):
        self.created, self.priorities, self.filed = [], [], []
        self._week_gid = week_gid

    def create_task(self, title, workspace_gid=None):
        self.created.append(title)
        return f"gid-{len(self.created)}"

    def set_priority(self, task, value):
        self.priorities.append((task.gid, value))

    def week_project(self, offset):
        return (self._week_gid, "2026: Week 38") if self._week_gid else ("", "2026: Week 38")

    def add_to_project(self, gid, pgid):
        self.filed.append((gid, pgid))

    def complete(self, gid):
        pass


class NullStore:
    tasks: list = []

    def add(self, *a, **k): pass
    def complete(self, *a, **k): return None
    def set_priority(self, *a, **k): pass
    def save(self): pass


def test_lines_take_the_pickers_from_their_own_row():
    """Writing on rows 1 and 3 must not pick up the boxes from rows 0 and 1."""
    marks = Marks(
        new_tasks=["Ring tannlegen", "Bestille papir"],
        new_task_rows=[1, 3],
        set_priority={"new1": "High", "new3": "Low", "new0": "Medium"},
        set_week={"new3": "next"},
    )
    asana, report = FakeAsana(), IngestionReport()
    apply_marks(marks, NullStore(), asana, TODAY, report)

    assert asana.created == ["Ring tannlegen", "Bestille papir"]
    assert asana.priorities == [("gid-1", "High"), ("gid-2", "Low")], \
        "row 0's Medium belongs to no written line and must not be applied"
    assert asana.filed == [("gid-2", "W38")], "only the second line asked for a week"


def test_a_mismatch_drops_the_pickers_but_keeps_the_text():
    """If the row count and line count disagree we cannot say which boxes go
    with which line. The words are still worth capturing; a guessed priority is
    not."""
    marks = Marks(
        new_tasks=["Ring tannlegen", "Bestille papir"],
        new_task_rows=[0, 1, 2],          # more inked rows than transcribed lines
        set_priority={"new0": "High", "new1": "Low", "new2": "Medium"},
    )
    asana, report = FakeAsana(), IngestionReport()
    apply_marks(marks, NullStore(), asana, TODAY, report)

    assert len(asana.created) == 2, "the tasks are still created"
    assert asana.priorities == [], "but nothing is guessed at"


def test_a_missing_week_project_is_reported_not_invented():
    """Creating projects in someone's workspace is a far larger side effect
    than this tool should take on its own."""
    marks = Marks(new_tasks=["Ring tannlegen"], new_task_rows=[0],
                  set_week={"new0": "this"})
    asana, report = FakeAsana(week_gid=""), IngestionReport()
    apply_marks(marks, NullStore(), asana, TODAY, report)

    assert asana.created == ["Ring tannlegen"]
    assert asana.filed == []
    assert any("2026: Week 38" in u for u in report.unreadable), "and it must say so"


def test_week_marks_on_existing_tasks_file_them():
    marks = Marks(set_week={"1201": "this"})
    asana, report = FakeAsana(), IngestionReport()
    apply_marks(marks, NullStore(), asana, TODAY, report,
                asana_tasks=[AsanaTask(gid="1201", name="Send tilbud")])
    assert asana.filed == [("1201", "W38")]
    assert report.weeks == ["Send tilbud -> 2026: Week 38"]
