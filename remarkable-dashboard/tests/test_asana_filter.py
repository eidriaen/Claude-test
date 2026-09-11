"""Which Asana tasks reach the Tasks page."""
from __future__ import annotations

from datetime import date

from daily_sheet.asana_client import AsanaClient
from daily_sheet.models import AsanaTask


# --- Tasks page excludes pipeline cards -------------------------------------
class _Client(AsanaClient):
    """AsanaClient with the network replaced, to test the filtering only."""
    def __init__(self, rows, board):
        from daily_sheet.config import load_config
        cfg = load_config()
        cfg.asana_pat = "test-token"        # the network is stubbed below
        super().__init__(cfg, date(2026, 9, 11))
        self._rows, self._board = rows, board

    def _get(self, path, **kw):
        return {"workspaces": [{"gid": "ws1", "name": "npg.no"}]}

    def _tasks_in_workspace(self, ws):
        return [AsanaTask(gid=r["gid"], name=r["name"], project_gids=r.get("in", []))
                for r in self._rows]

    def board_gid(self):
        return self._board


def test_board_cards_are_not_listed_under_tasks():
    """Pipeline cards are assigned to whoever owns the client, so they arrive as
    tasks too. Projects already shows them with the budget and stage that make
    them legible; repeating them under Tasks is noise."""
    rows = [{"gid": "1", "name": "Send tilbud", "in": ["BOARD"]},
            {"gid": "2", "name": "Oppdatere prisliste", "in": ["other"]},
            {"gid": "3", "name": "Ingen prosjekt"}]
    got = _Client(rows, "BOARD").my_open_tasks()
    assert [t.name for t in got] == ["Ingen prosjekt", "Oppdatere prisliste"]


def test_an_unresolvable_board_shows_everything():
    """Failing open matters here: a renamed board should show a task twice, not
    hide it. A duplicate is visible and annoying; a missing task is neither."""
    rows = [{"gid": "1", "name": "Send tilbud", "in": ["BOARD"]}]
    assert len(_Client(rows, "").my_open_tasks()) == 1
