"""The Personal page: Trello cards on the sheet, ticks back to Trello.

Same shape as the Asana round trip -- render from fixtures, draw ink, read it
back -- but the point here is routing: a tick on the Personal page must reach
Trello and never Asana, and a line written there must become a card, not a
task.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from daily_sheet import readback
from daily_sheet.cli import apply_marks
from daily_sheet.config import load_config
from daily_sheet.models import IngestionReport, Region, SectionStatus, TrelloCard
from daily_sheet.readback import Marks, read_marks
from daily_sheet.render import NAV_ITEMS, SheetData, render_sheet
from daily_sheet.tasks import TaskStore
from daily_sheet.trello_client import TrelloClient, load_trello

from .test_roundtrip import FakeAsana, _annotate

TODAY = date(2026, 10, 7)


class FakeTrello:
    def __init__(self):
        self.completed: list[str] = []
        self.created: list[str] = []
        self.moved: list[tuple[str, str]] = []
        self.assigned: list[tuple[str, str]] = []

    def move(self, card_id, key):
        self.moved.append((card_id, key))
        return {"this": "To-do this week", "next": "To-do next week", "month": "To-do next month"}[key]

    def add_member(self, card_id, member_id):
        self.assigned.append((card_id, member_id))
        return True

    def complete(self, card_id):
        self.completed.append(card_id)
        return "moved to Done"

    def create_card(self, title):
        self.created.append(title)
        return "newcard"


@pytest.fixture
def sheet(tmp_path, monkeypatch):
    cfg = load_config(use_fixtures=True)
    cards, lists, status, client = load_trello(cfg, TODAY)
    assert status.ok and cards, "fixture board must load"
    data = SheetData(TODAY, [], SectionStatus(), [], SectionStatus(), [], [], SectionStatus(),
                     IngestionReport(), trello_cards=cards, trello_lists=lists,
                     trello_status=status, trello_members=client.board_members())
    pdf, layout_path = render_sheet(data, tmp_path / "out")
    monkeypatch.setattr(readback, "_ask_claude", lambda *a, **k: "")
    return {"cards": cards, "pdf": pdf, "layout_path": layout_path,
            "layout": json.loads(layout_path.read_text()), "tmp": tmp_path}


# --- the page exists and is wired into the nav -------------------------------
def test_personal_is_in_the_nav_and_layout(sheet):
    assert ("Personal", "personal") in NAV_ITEMS
    assert "personal" in sheet["layout"]["sections"]
    regions = [Region.from_json(r) for r in sheet["layout"]["regions"]]
    ticks = {r.id for r in regions if r.kind == "check" and r.id.startswith("trello:")}
    assert ticks == {c.rid for c in sheet["cards"]}, "every open card has a checkbox"
    assert any(r.id == "newpersonal" for r in regions), "the page has a New personal tasks box"


def test_cards_are_grouped_by_list_in_board_order(sheet):
    """Fixture lists are To do, This weekend, Waiting; cards keep that order
    on the page, so the ids of the tick regions follow it too."""
    regions = [Region.from_json(r) for r in sheet["layout"]["regions"]]
    ticks = [r for r in regions if r.kind == "check" and r.id.startswith("trello:")]
    ticks.sort(key=lambda r: (r.page, r.y0))
    by_id = {c.rid: c for c in sheet["cards"]}
    lists_seen = [by_id[r.id].list_name for r in ticks]
    assert lists_seen == sorted(lists_seen, key=["To do", "This weekend", "Waiting"].index)


# --- a tick goes to Trello and nowhere else ---------------------------------
def test_tick_on_personal_page_completes_the_card_in_trello(sheet, tmp_path):
    target = sheet["cards"][0]
    annotated = _annotate(sheet["pdf"], sheet["layout"], {target.rid}, tmp_path / "a.pdf")
    marks = read_marks(annotated, sheet["layout_path"], api_key="")
    assert marks.checked == [target.rid]

    asana, trello, report = FakeAsana(), FakeTrello(), IngestionReport()
    store = TaskStore(tmp_path / "tasks.json")
    apply_marks(marks, store, asana, TODAY, report, trello=trello)

    assert trello.completed == [target.id]
    assert asana.completed == [], "a card id must never be sent to Asana"
    assert report.completed == 1 and report.completed_trello == [target.rid]


def test_tick_with_trello_not_connected_is_reported_not_lost(tmp_path):
    marks = Marks(checked=["trello:5f1a0000000000000000a001"])
    asana, report = FakeAsana(), IngestionReport()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), asana, TODAY, report, trello=None)
    assert asana.completed == []
    assert any("Trello is not connected" in u for u in report.unreadable)


# --- the pickers: move and assign ------------------------------------------
def _ink(sheet, tmp_path, region_ids: set[str], name: str):
    """Tick the named regions of any kind and hand back the annotated PDF."""
    from PIL import ImageDraw
    from .test_roundtrip import _draw_tick
    images = readback.page_images(sheet["pdf"], tuple(sheet["layout"]["page_size"]))
    for r in (Region.from_json(x) for x in sheet["layout"]["regions"]):
        if r.id in region_ids:
            _draw_tick(ImageDraw.Draw(images[r.page - 1]), r)
    rgb = [im.convert("RGB") for im in images]
    out = tmp_path / name
    rgb[0].save(out, save_all=True, append_images=rgb[1:], resolution=72.0)
    return out


def test_every_card_has_move_and_assign_boxes(sheet):
    regions = [Region.from_json(r) for r in sheet["layout"]["regions"]]
    for c in sheet["cards"]:
        picks = {r.id.rsplit("|", 1)[1] for r in regions if r.kind == "pick1" and r.id.startswith(c.rid + "|")}
        flags = {r.id.rsplit("|", 1)[1] for r in regions if r.kind == "flag" and r.id.startswith(c.rid + "|")}
        assert picks == {"this", "next", "month"}
        assert flags == {"m-adrian", "m-siri"}


def test_marking_next_week_moves_the_card(sheet, tmp_path):
    card = sheet["cards"][0]
    pdf = _ink(sheet, tmp_path, {f"{card.rid}|next"}, "move.pdf")
    marks = read_marks(pdf, sheet["layout_path"], api_key="")
    assert marks.picks == {card.rid: "next"}
    assert marks.checked == [], "a picker mark is not a done-tick"

    trello, report = FakeTrello(), IngestionReport()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), None, TODAY, report, trello=trello,
                trello_cards=sheet["cards"])
    assert trello.moved == [(card.id, "next")]
    assert report.moved == [f"{card.name} -> To-do next week"]
    assert "Moved 1" in report.summary()


def test_two_move_boxes_on_one_row_is_ambiguous_and_left_alone(sheet, tmp_path):
    card = sheet["cards"][0]
    pdf = _ink(sheet, tmp_path, {f"{card.rid}|this", f"{card.rid}|month"}, "ambig.pdf")
    marks = read_marks(pdf, sheet["layout_path"], api_key="")
    assert card.rid not in marks.picks
    assert any("all marked" in u for u in marks.unreadable)


def test_assign_boxes_add_each_ticked_person(sheet, tmp_path):
    """Any-of, not one-of: both boxes ticked puts both people on the card."""
    card = next(c for c in sheet["cards"] if not c.member_ids)      # "Male gjerdet"
    pdf = _ink(sheet, tmp_path, {f"{card.rid}|m-adrian", f"{card.rid}|m-siri"}, "assign.pdf")
    marks = read_marks(pdf, sheet["layout_path"], api_key="")
    assert sorted(marks.flags[card.rid]) == ["m-adrian", "m-siri"]

    trello, report = FakeTrello(), IngestionReport()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), None, TODAY, report, trello=trello,
                trello_cards=sheet["cards"])
    assert sorted(trello.assigned) == [(card.id, "m-adrian"), (card.id, "m-siri")]
    assert len(report.assigned) == 2


def test_picks_without_trello_are_reported(tmp_path):
    marks = Marks(picks={"trello:abc": "this"}, flags={"trello:abc": ["m-siri"]})
    report = IngestionReport()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), None, TODAY, report, trello=None)
    assert sum("Trello is not connected" in u for u in report.unreadable) == 2


# --- handwriting on the Personal page becomes a card ------------------------
def test_new_personal_lines_become_cards_not_tasks(tmp_path):
    marks = Marks(new_personal=["Kjøpe melk", "Ringe rørlegger !!"])
    asana, trello, report = FakeAsana(), FakeTrello(), IngestionReport()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), asana, TODAY, report, trello=trello)
    assert trello.created == ["Kjøpe melk", "Ringe rørlegger"]
    assert asana.created == []
    assert report.added_trello == ["Kjøpe melk", "Ringe rørlegger"]
    assert "Trello +2" in report.summary()


def test_new_personal_box_has_pickers_per_line(sheet):
    regions = [Region.from_json(r) for r in sheet["layout"]["regions"]]
    box = next(r for r in regions if r.id == "newpersonal")
    move_x = min(r.x0 for r in regions if r.id.startswith("newp0|"))
    assert box.x1 < move_x, "the writing area stops before the pickers"
    assert {r.id for r in regions if r.kind == "pick1" and r.id.startswith("newp0|")} == \
        {"newp0|this", "newp0|next", "newp0|month"}
    assert {r.id for r in regions if r.kind == "flag" and r.id.startswith("newp0|")} == \
        {"newp0|m-adrian", "newp0|m-siri"}


def test_new_personal_line_is_filed_and_assigned_by_its_row_pickers(tmp_path):
    marks = Marks(new_personal=["Kjøpe melk", "Vaske bilen"], new_personal_rows=[0, 2],
                  picks={"newp2": "next"}, flags={"newp2": ["m-siri"], "newp0": ["m-adrian"]})
    trello, report = FakeTrello(), IngestionReport()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), None, TODAY, report, trello=trello)
    assert trello.created == ["Kjøpe melk", "Vaske bilen"]
    assert trello.moved == [("newcard", "next")]
    assert trello.assigned == [("newcard", "m-adrian"), ("newcard", "m-siri")]
    assert report.moved == ["Vaske bilen -> To-do next week"]


def test_new_personal_rows_that_do_not_pair_still_make_the_cards(tmp_path):
    """Two inked rows but one transcribed line: the boxes cannot be matched to
    a line, so the card is created unfiled rather than filed by a guess."""
    marks = Marks(new_personal=["Kjøpe melk"], new_personal_rows=[0, 1], picks={"newp0": "this"})
    trello = FakeTrello()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), None, TODAY, IngestionReport(), trello=trello)
    assert trello.created == ["Kjøpe melk"] and trello.moved == []


def test_new_personal_lines_are_not_re_added_on_a_second_sync(tmp_path):
    marks = Marks(new_personal=["Kjøpe melk"])
    trello = FakeTrello()
    apply_marks(marks, TaskStore(tmp_path / "t.json"), None, TODAY, IngestionReport(),
                already_added={"kjøpe melk"}, trello=trello)
    assert trello.created == []


def test_readback_keeps_personal_lines_apart_from_asana_lines(sheet, tmp_path, monkeypatch):
    """Both boxes share the region kind; only the id tells them apart. The
    transcription is stubbed so this checks the routing alone."""
    from PIL import ImageDraw
    regions = [Region.from_json(r) for r in sheet["layout"]["regions"]]
    box = next(r for r in regions if r.id == "newpersonal")
    images = readback.page_images(sheet["pdf"], tuple(sheet["layout"]["page_size"]))
    d = ImageDraw.Draw(images[box.page - 1])
    d.line([(box.x0 + 40, box.y0 + 40), (box.x0 + 400, box.y0 + 46)], fill=0, width=6)
    rgb = [im.convert("RGB") for im in images]
    out = tmp_path / "inked.pdf"
    rgb[0].save(out, save_all=True, append_images=rgb[1:], resolution=72.0)

    # The printed rules are grey and count as faint ink, so every box reaches
    # the (stubbed) model. Answer only for the box that carries the black line.
    import numpy as np

    def fake_claude(_key, img, _prompt, max_tokens=0):
        inner = np.asarray(img.convert("L"))[16:-16, 16:-16]     # skip the black border
        return "Vaske bilen" if (inner < 50).sum() > 500 else ""

    monkeypatch.setattr(readback, "_ask_claude", fake_claude)
    marks = read_marks(out, sheet["layout_path"], api_key="x")
    assert marks.new_personal == ["Vaske bilen"]
    assert marks.new_tasks == []


# --- the client's own logic, with the network stubbed ------------------------
class _Client(TrelloClient):
    def __init__(self, boards, lists, cards, members=()):
        cfg = load_config()
        cfg.trello_key, cfg.trello_token = "k", "t"
        # The machine's own .env must not leak into these: they describe a board
        # of their own, not whichever one is configured here.
        cfg.trello_board, cfg.trello_lists, cfg.trello_inbox_list = "", [], ""
        cfg.trello_done_list, cfg.trello_names = "Done", {}
        cfg.trello_move_lists = {"this": "To-do this week", "next": "To-do next week",
                                 "month": "To-do next month"}
        super().__init__(cfg, TODAY)
        self._fake = {"boards": boards, "lists": lists, "cards": cards, "members": list(members)}
        self.puts: list[tuple[str, dict]] = []

    def _get(self, path, **params):
        if path.endswith("/members/me"):
            return {"id": "m1"}
        if path.endswith("/boards"):
            return self._fake["boards"]
        if path.endswith("/lists"):
            return self._fake["lists"]
        if path.endswith("/cards"):
            return self._fake["cards"]
        if path.endswith("/members"):
            return self._fake["members"]
        raise AssertionError(path)

    def _put(self, path, **params):
        self.puts.append((path, params))
        return {}


_BOARDS = [{"id": "B1", "name": "Hjemme"}]
_LISTS = [{"id": "L1", "name": "To do"}, {"id": "L2", "name": "Done"}]


def test_single_board_is_picked_without_naming_it():
    c = _Client(_BOARDS, _LISTS, [])
    assert c.board() == ("B1", "Hjemme")


def test_two_boards_need_a_name():
    c = _Client([{"id": "B1", "name": "Hjemme"}, {"id": "B2", "name": "Jobb"}], _LISTS, [])
    with pytest.raises(RuntimeError, match="TRELLO_BOARD"):
        c.board()
    c.cfg.trello_board = "jobb"
    assert c.board() == ("B2", "Jobb")


def test_done_list_cards_stay_off_the_page_and_ticks_move_there():
    cards = [
        {"id": "c1", "name": "Open one", "idList": "L1", "idMembers": ["m1"], "labels": []},
        {"id": "c2", "name": "Finished", "idList": "L2", "idMembers": [], "labels": []},
        {"id": "c3", "name": "Due done", "idList": "L1", "dueComplete": True, "labels": []},
    ]
    c = _Client(_BOARDS, _LISTS, cards, members=[{"id": "m1", "fullName": "Adrian L", "initials": "AL"}])
    got = c.open_cards()
    assert [k.name for k in got] == ["Open one"]
    assert got[0].members == ["AL"] and got[0].list_name == "To do"

    assert c.complete("c1") == "moved to Done"
    assert c.puts == [("/cards/c1", {"idList": "L2", "dueComplete": "true", "pos": "top"})]


def test_trello_lists_picks_and_orders_the_lists_and_strips_emoji():
    """The real board: a 60-card backlog nobody wants on the tablet, and list
    names decorated with emoji the sheet's font cannot draw."""
    lists = [{"id": "L0", "name": "\U0001f4a1Ideas/Backlog"},
             {"id": "L1", "name": "\U0001f4daTo Do"},
             {"id": "L2", "name": "\U0001f4c5 To-do this week"},
             {"id": "L3", "name": "\U0001f4c5 To-do next week"}]
    cards = [{"id": "a", "name": "Backlog thing", "idList": "L0", "labels": []},
             {"id": "b", "name": "Someday", "idList": "L1", "labels": []},
             {"id": "c", "name": "Next week thing", "idList": "L3", "labels": []},
             {"id": "d", "name": "WoW launch \U0001f431", "idList": "L2", "labels": []}]
    c = _Client(_BOARDS, lists, cards)
    c.cfg.trello_lists = ["To-do this week", "to do next week", "No such list"]
    got = c.open_cards()
    assert [(k.name, k.list_name) for k in got] == [
        ("WoW launch", "To-do this week"), ("Next week thing", "To-do next week")]
    assert c.shown_list_names() == ["To-do this week", "To-do next week"]
    assert c.inbox_list() == ("L0", "\U0001f4a1Ideas/Backlog"), "inbox default is still the first list"
    c.cfg.trello_inbox_list = "to-do this week"
    assert c.inbox_list() == ("L2", "\U0001f4c5 To-do this week")


def test_without_a_done_list_ticks_archive_the_card():
    c = _Client(_BOARDS, [{"id": "L1", "name": "To do"}], [])
    assert c.complete("c9") == "archived"
    assert c.puts[0][1]["closed"] == "true"
    assert c.inbox_list() == ("L1", "To do")


def test_move_goes_to_the_configured_list_and_cards_know_where_they_sit():
    lists = [{"id": "L1", "name": "\U0001f4c5 To-do this week"},
             {"id": "L2", "name": "\U0001f4c5 To-do next week"},
             {"id": "L3", "name": "Concurrent tasks \U0001f501"}]
    cards = [{"id": "c1", "name": "A", "idList": "L2", "idMembers": ["m2"], "labels": []},
             {"id": "c2", "name": "B", "idList": "L3", "idMembers": [], "labels": []}]
    members = [{"id": "m2", "fullName": "Siri Hegna Berge", "initials": "SB"},
               {"id": "m1", "fullName": "Arcadie", "initials": "A"}]
    c = _Client(_BOARDS, lists, cards, members=members)
    got = {k.name: k for k in c.open_cards()}
    assert got["A"].list_key == "next" and got["A"].member_ids == ["m2"]
    assert got["B"].list_key == ""
    assert [m["name"] for m in c.board_members()] == ["Arcadie", "Siri"], "me first"
    assert c.move("c2", "this") == "To-do this week"
    assert c.puts[-1] == ("/cards/c2", {"idList": "L1", "pos": "bottom"})
    assert set(c.move_targets()) == {"this", "next"}, "next month is not on this board"
    import pytest as _pt
    with _pt.raises(RuntimeError):
        c.move("c2", "month")
