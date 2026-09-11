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


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A task store with known ids, plus a rendered sheet for it."""
    tasks_file = tmp_path / "tasks.json"
    tasks_file.write_text(json.dumps([
        Task("aaa", "Ring tannlegen", 1, "open", "manual", "2026-09-01").to_json(),
        Task("bbb", "Levere bilen", 2, "open", "manual", "2026-09-08").to_json(),
        Task("ccc", "Kjøpe gave", 3, "open", "manual", "2026-09-10").to_json(),
    ]))
    store = TaskStore(tasks_file)

    cfg = load_config(use_fixtures=True)
    monday = TODAY - timedelta(days=TODAY.weekday())
    events, est = load_events(cfg, monday - timedelta(days=7), monday + timedelta(days=14))
    asana_tasks, ast, _ = load_asana(cfg, TODAY)
    data = SheetData(TODAY, events, est, store.open_tasks(), asana_tasks, ast, IngestionReport())
    pdf, layout_path = render_sheet(data, tmp_path / "out")

    # never call the model in tests
    monkeypatch.setattr(readback, "_ask_claude", lambda *a, **k: "")
    return {"store": store, "pdf": pdf, "layout_path": layout_path,
            "layout": json.loads(layout_path.read_text()), "tmp": tmp_path,
            "asana_tasks": asana_tasks}


def test_round_trip_completes_only_the_ticked_task(project):
    annotated = _annotate(project["pdf"], project["layout"], {"aaa"}, project["tmp"] / "annotated.pdf")
    marks = read_marks(annotated, project["layout_path"], api_key="")

    assert marks.checked == ["aaa"], f"expected only 'aaa', got {marks.checked}"

    store, report = project["store"], IngestionReport()
    apply_marks(marks, store, None, TODAY, report)

    assert store.by_id("aaa").status == "done"
    assert store.by_id("bbb").status == "open"
    assert store.by_id("ccc").status == "open"
    assert report.completed_private == ["Ring tannlegen"]


def test_round_trip_with_nothing_ticked_changes_nothing(project):
    annotated = _annotate(project["pdf"], project["layout"], set(), project["tmp"] / "clean.pdf")
    marks = read_marks(annotated, project["layout_path"], api_key="")

    assert marks.checked == [], f"false positives: {marks.checked}"

    store = project["store"]
    before = json.dumps([t.to_json() for t in store.tasks], sort_keys=True)
    apply_marks(marks, store, None, TODAY, IngestionReport())
    assert json.dumps([t.to_json() for t in store.tasks], sort_keys=True) == before


def test_round_trip_ticking_several_including_asana(project):
    """A task on the Tasks page and an Asana task on the Asana page, one pass."""
    asana_gid = project["asana_tasks"][0].gid
    annotated = _annotate(project["pdf"], project["layout"], {"bbb", "ccc", asana_gid},
                          project["tmp"] / "multi.pdf")
    marks = read_marks(annotated, project["layout_path"], api_key="")

    assert set(marks.checked) == {"bbb", "ccc", asana_gid}

    store, asana, report = project["store"], FakeAsana(), IngestionReport()
    apply_marks(marks, store, asana, TODAY, report)

    assert store.by_id("bbb").status == "done"
    assert store.by_id("ccc").status == "done"
    assert store.by_id("aaa").status == "open"
    assert asana.completed == [asana_gid], "the Asana task should be completed exactly once"
    assert report.completed == 3


def test_a_task_appears_on_todays_sheet_only_while_open(project, tmp_path):
    """Completing a task removes its checkbox from the next sheet."""
    store = project["store"]
    store.complete("aaa", TODAY)

    cfg = load_config(use_fixtures=True)
    tomorrow = TODAY + timedelta(days=1)
    data = SheetData(tomorrow, [], project["layout"] and _ok(), store.open_tasks(), [], _ok(),
                     IngestionReport())
    _, layout_path = render_sheet(data, tmp_path / "out2")
    ids = {r["id"] for r in json.loads(layout_path.read_text())["regions"] if r["kind"] == "check"}

    assert "aaa" not in ids, "a completed task must not come back tomorrow"
    assert {"bbb", "ccc"} <= ids


def _ok():
    from daily_sheet.models import SectionStatus
    return SectionStatus()
