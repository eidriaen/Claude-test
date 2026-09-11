"""End-to-end: render a sheet, draw pen marks on it, read it back, assert the
task store changed correctly. This is the whole product in one test — if this
passes, the only untested link left is the tablet itself.

No network and no model calls: we stub the free-text transcription, since
checkbox handling is the deterministic part we actually want to pin down.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from daily_sheet import readback
from daily_sheet.asana_client import load_asana
from daily_sheet.calendar_ics import load_events
from daily_sheet.cli import apply_marks
from daily_sheet.config import load_config
from daily_sheet.models import IngestionReport, Region, Task
from daily_sheet.readback import read_marks
from daily_sheet.render import SheetData, render_sheet
from daily_sheet.tasks import TaskStore

TODAY = date(2026, 9, 11)


class FakeAsana:
    """Records completions instead of calling Asana."""
    def __init__(self):
        self.completed: list[str] = []

    def complete(self, gid: str) -> None:
        self.completed.append(gid)


def _draw_tick(d: ImageDraw.ImageDraw, r: Region) -> None:
    w, h = r.x1 - r.x0, r.y1 - r.y0
    d.line([(r.x0 + w * 0.22, r.y0 + h * 0.52), (r.x0 + w * 0.42, r.y0 + h * 0.76),
            (r.x0 + w * 0.80, r.y0 + h * 0.24)], fill=0, width=5)


def _annotate(pdf: Path, layout: dict, tick_ids: set[str], out_pdf: Path) -> Path:
    """Rasterise the sheet, draw ticks in the named regions, save back as PDF.

    This is what the tablet hands back: a PDF with the pen strokes baked in.
    """
    size = tuple(layout["page_size"])
    images = readback.page_images(pdf, size)
    regions = [Region.from_json(x) for x in layout["regions"]]
    for r in regions:
        if r.kind == "check" and r.id in tick_ids:
            _draw_tick(ImageDraw.Draw(images[r.page - 1]), r)
    rgb = [im.convert("RGB") for im in images]
    rgb[0].save(out_pdf, save_all=True, append_images=rgb[1:], resolution=72.0)
    return out_pdf


class FakeAsana:
    """Records completions instead of calling Asana."""
    def __init__(self):
        self.completed: list[str] = []
        self.created: list[str] = []

    def complete(self, gid): self.completed.append(gid)
    def create_task(self, title, workspace_gid=None): self.created.append(title); return "new"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A rendered sheet built from the fixture Asana tasks."""
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text("[]")
    store = TaskStore(tasks_file)

    cfg = load_config(use_fixtures=True)
    monday = TODAY - timedelta(days=TODAY.weekday())
    events, est = load_events(cfg, monday - timedelta(days=7), monday + timedelta(days=14))
    asana_tasks, ast, _ = load_asana(cfg, TODAY)
    data = SheetData(TODAY, events, est, asana_tasks, ast, [], [], _ok(), IngestionReport())
    pdf, layout_path = render_sheet(data, tmp_path / "out")

    # never call the model in tests
    monkeypatch.setattr(readback, "_ask_claude", lambda *a, **k: "")
    return {"store": store, "pdf": pdf, "layout_path": layout_path,
            "layout": json.loads(layout_path.read_text()), "tmp": tmp_path,
            "asana_tasks": asana_tasks}


def test_round_trip_completes_only_the_ticked_task(project):
    """One tick must complete exactly one task -- neighbours must not bleed in."""
    gids = [t.gid for t in project["asana_tasks"]]
    target = gids[0]
    annotated = _annotate(project["pdf"], project["layout"], {target},
                          project["tmp"] / "annotated.pdf")
    marks = read_marks(annotated, project["layout_path"], api_key="")

    assert marks.checked == [target], f"expected only {target!r}, got {marks.checked}"

    asana, report = FakeAsana(), IngestionReport()
    apply_marks(marks, project["store"], asana, TODAY, report)

    assert asana.completed == [target]
    assert set(gids[1:]).isdisjoint(asana.completed), "untouched rows must stay open"


def test_round_trip_with_nothing_ticked_changes_nothing(project):
    annotated = _annotate(project["pdf"], project["layout"], set(), project["tmp"] / "clean.pdf")
    marks = read_marks(annotated, project["layout_path"], api_key="")

    assert marks.checked == [], f"false positives: {marks.checked}"

    store = project["store"]
    before = json.dumps([t.to_json() for t in store.tasks], sort_keys=True)
    apply_marks(marks, store, None, TODAY, IngestionReport())
    assert json.dumps([t.to_json() for t in store.tasks], sort_keys=True) == before


def test_round_trip_ticking_several(project):
    """Several ticks in one pass, each completed in Asana exactly once."""
    gids = [t.gid for t in project["asana_tasks"]][:3]
    annotated = _annotate(project["pdf"], project["layout"], set(gids),
                          project["tmp"] / "multi.pdf")
    marks = read_marks(annotated, project["layout_path"], api_key="")

    assert set(marks.checked) == set(gids)

    asana, report = FakeAsana(), IngestionReport()
    apply_marks(marks, project["store"], asana, TODAY, report)

    assert sorted(asana.completed) == sorted(gids)
    assert len(asana.completed) == len(set(asana.completed)), "no task completed twice"
    assert report.completed == len(gids)


def test_a_task_appears_on_the_sheet_only_while_open(project, tmp_path):
    """A task completed in Asana must not get a checkbox on the next sheet.

    The sheet is rendered from whatever Asana returns as incomplete, so this
    pins the contract that the renderer draws only what it is given -- a stale
    row would invite ticking something already done.
    """
    remaining = project["asana_tasks"][1:]
    gone = project["asana_tasks"][0].gid
    tomorrow = TODAY + timedelta(days=1)
    data = SheetData(tomorrow, [], _ok(), remaining, _ok(), [], [], _ok(), IngestionReport())
    _, layout_path = render_sheet(data, tmp_path / "out2")
    ids = {r["id"] for r in json.loads(layout_path.read_text())["regions"] if r["kind"] == "check"}

    assert gone not in ids, "a completed task must not come back tomorrow"
    assert {t.gid for t in remaining} <= ids


def _ok():
    from daily_sheet.models import SectionStatus
    return SectionStatus()
