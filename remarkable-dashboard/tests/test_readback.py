"""Read-back is the part that can silently do the wrong thing, so it gets the
most tests. We fake pen marks by drawing into the rendered page at the exact
coordinates layout.json claims, then assert the reader finds them.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from daily_sheet.asana_client import load_asana
from daily_sheet.calendar_ics import load_events
from daily_sheet.config import load_config
from daily_sheet.models import IngestionReport, Region, Task
from daily_sheet.readback import CHECK_MIN, SPAN_MIN, ink_ratio, ink_span, is_marked, page_images
from daily_sheet.render import SheetData, render_sheet
from daily_sheet.tasks import TaskStore, parse_pen_line

TODAY = date(2026, 9, 11)


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    """Render a real sheet from fixtures and rasterise it."""
    out = tmp_path_factory.mktemp("out")
    cfg = load_config(use_fixtures=True)
    monday = TODAY - timedelta(days=TODAY.weekday())
    events, est = load_events(cfg, monday - timedelta(days=7), monday + timedelta(days=14))
    asana, ast, _ = load_asana(cfg, TODAY)
    store = TaskStore(cfg.tasks_file)
    data = SheetData(TODAY, events, est, store.open_tasks(), asana, ast, IngestionReport())
    pdf, layout_path = render_sheet(data, out)
    layout = json.loads(layout_path.read_text())
    images = page_images(pdf, tuple(layout["page_size"]))
    regions = [Region.from_json(d) for d in layout["regions"]]
    return {"pdf": pdf, "layout": layout, "images": images, "regions": regions, "store": store}


def _checks(sheet) -> list[Region]:
    return [r for r in sheet["regions"] if r.kind == "check"]


# --- layout sanity ---------------------------------------------------------
def test_every_open_task_has_a_checkbox(sheet):
    ids = {r.id for r in _checks(sheet)}
    for t in sheet["store"].open_tasks():
        assert t.id in ids, f"task {t.id} has no checkbox"


def test_regions_are_inside_the_page(sheet):
    w, h = sheet["layout"]["page_size"]
    for r in sheet["regions"]:
        assert 0 <= r.x0 < r.x1 <= w, f"{r.id} x out of bounds"
        assert 0 <= r.y0 < r.y1 <= h, f"{r.id} y out of bounds"


def test_tap_targets_are_at_least_44px(sheet):
    for r in sheet["regions"]:
        if r.kind in ("check", "priority"):
            assert r.x1 - r.x0 >= 44 and r.y1 - r.y0 >= 44, f"{r.id} too small to tap"


def test_free_text_regions_exist(sheet):
    kinds = {r.kind for r in sheet["regions"]}
    assert "newtasks" in kinds and "notes" in kinds


# --- ink detection ---------------------------------------------------------
def test_untouched_checkbox_reads_as_empty(sheet):
    for r in _checks(sheet):
        img = sheet["images"][r.page - 1]
        assert not is_marked(img, r), (
            f"{r.id} reads as ticked with no pen on it "
            f"(ratio {ink_ratio(img, r):.4f}, span {ink_span(img, r):.2f})")


def _tick(img: Image.Image, r: Region) -> Image.Image:
    """Draw a plausible pen tick inside the region."""
    out = img.copy()
    d = ImageDraw.Draw(out)
    w, h = r.x1 - r.x0, r.y1 - r.y0
    d.line([(r.x0 + w * 0.22, r.y0 + h * 0.52), (r.x0 + w * 0.42, r.y0 + h * 0.76),
            (r.x0 + w * 0.80, r.y0 + h * 0.24)], fill=0, width=5)
    return out


def test_ticked_checkbox_is_detected(sheet):
    r = _checks(sheet)[0]
    img = sheet["images"][r.page - 1]
    assert is_marked(_tick(img, r), r)


def test_tick_has_headroom_over_threshold(sheet):
    """A real tick should clear both thresholds by a wide margin, not squeak past."""
    r = _checks(sheet)[0]
    marked = _tick(sheet["images"][r.page - 1], r)
    assert ink_ratio(marked, r) > CHECK_MIN * 2
    assert ink_span(marked, r) > SPAN_MIN * 1.4


def test_tick_does_not_leak_into_neighbours(sheet):
    """Ticking one box must not trip the box next to it."""
    page1 = [r for r in _checks(sheet) if r.page == _checks(sheet)[0].page]
    if len(page1) < 2:
        pytest.skip("need two checkboxes on one page")
    a, b = page1[0], page1[1]
    marked = _tick(sheet["images"][a.page - 1], a)
    assert not is_marked(marked, b)


def test_stray_dot_does_not_count_as_a_tick(sheet):
    """A resting pen nib shouldn't complete a task."""
    r = _checks(sheet)[0]
    img = sheet["images"][r.page - 1].copy()
    d = ImageDraw.Draw(img)
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=0)
    assert not is_marked(img, r), "a resting pen nib must not complete a task"


# --- pen line parsing ------------------------------------------------------
@pytest.mark.parametrize("line,expected", [
    ("Call dentist", ("Call dentist", 3)),
    ("Call dentist!", ("Call dentist", 2)),
    ("Call dentist !!", ("Call dentist", 1)),
    ("  Ring tannlegen  ", ("Ring tannlegen", 3)),
    ("", None),
    ("   ", None),
])
def test_parse_pen_line(line, expected):
    assert parse_pen_line(line) == expected


# --- applying marks --------------------------------------------------------
def test_ticking_completes_and_never_deletes(tmp_path):
    from daily_sheet.cli import apply_marks
    from daily_sheet.readback import Marks

    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([
        Task("t1", "One", 3, "open", "manual", "2026-09-01").to_json(),
        Task("t2", "Two", 2, "open", "manual", "2026-09-02").to_json(),
    ]))
    store = TaskStore(path)
    report = IngestionReport()
    apply_marks(Marks(checked=["t1"], priorities={"t2": 1}, new_tasks=["Book car !!"]),
                store, None, TODAY, report)

    assert len(store.tasks) == 3, "nothing may be deleted from the store"
    assert store.by_id("t1").status == "done"
    assert store.by_id("t1").completed == TODAY.isoformat()
    assert store.by_id("t2").priority == 1
    added = [t for t in store.tasks if t.source == "pen"]
    assert len(added) == 1 and added[0].title == "Book car" and added[0].priority == 1
    assert report.completed == 1 and report.added == ["Book car"]


def test_unknown_id_without_asana_is_ignored(tmp_path):
    """A checkbox id we don't recognise and no Asana client must not crash."""
    from daily_sheet.cli import apply_marks
    from daily_sheet.readback import Marks

    path = tmp_path / "tasks.json"
    path.write_text("[]")
    store = TaskStore(path)
    apply_marks(Marks(checked=["nope"]), store, None, TODAY, IngestionReport())
    assert store.tasks == []
