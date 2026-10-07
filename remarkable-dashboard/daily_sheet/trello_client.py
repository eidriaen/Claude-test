"""The shared personal board, read from Trello.

The Personal page is the Tasks page for life outside work: one Trello board
shared with a partner, every open card listed with whose it is and when it is
due, a tick to finish it. Trello's REST API is simple enough that this talks
to it directly -- a key and a token on every request, nothing else.

Cards sit in lists, and the lists are the structure: a card in the "Done"
list is finished and stays off the sheet; a new line written on the sheet
becomes a card in the inbox list (the first list on the board unless
TRELLO_INBOX_LIST says otherwise). Ticking a card moves it to "Done" when
there is such a list, and archives it otherwise -- either way it leaves the
page and the other person sees it was done.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

import requests

from .config import Config
from .models import SectionStatus, TrelloCard

API = "https://api.trello.com/1"

CARD_FIELDS = "name,due,dueComplete,idList,idMembers,labels,shortUrl,closed,pos"


def _norm(name: str) -> str:
    """How list names are compared: letters, digits and spaces only, lowercase.

    Board lists tend to be decorated -- "📅 To-do this week" -- and nobody
    should have to type the emoji into .env to name one.
    """
    return " ".join(re.sub(r"[^\w\s]", " ", name, flags=re.UNICODE).split()).lower()


def _clean(text: str) -> str:
    """Drop what the sheet's font cannot draw (emoji, symbols) and tidy spaces.

    Helvetica has no glyph for a calendar or a cat, and reportlab draws a
    missing glyph as nothing or as a black box, either of which reads worse
    than the name without it.
    """
    kept = "".join(ch for ch in text if ord(ch) < 0x2000 or ch in "–—‘’“”…")
    return " ".join(kept.split())


def _initials(member: dict) -> str:
    """Trello keeps initials per member; fall back to the name's capitals."""
    ini = (member.get("initials") or "").strip()
    if ini:
        return ini[:3]
    name = (member.get("fullName") or member.get("username") or "").strip()
    return "".join(w[0] for w in name.split() if w)[:3].upper()


class TrelloClient:
    def __init__(self, cfg: Config, today: date):
        self.cfg = cfg
        self.today = today
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"
        self._board: tuple[str, str] | None = None     # (gid, name)
        self._lists: list[tuple[str, str]] = []        # (id, name) in board order
        self._members: list[dict] = []                 # board members, me first

    # -- plumbing ----------------------------------------------------------
    def _auth(self) -> dict:
        return {"key": self.cfg.trello_key, "token": self.cfg.trello_token}

    def _get(self, path: str, **params):
        r = self._session.get(f"{API}{path}", params={**self._auth(), **params}, timeout=20)
        r.raise_for_status()
        return r.json()

    def _put(self, path: str, **params):
        if self.cfg.use_fixtures or self.cfg.dry_run:
            return None
        r = self._session.put(f"{API}{path}", params={**self._auth(), **params}, timeout=20)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, **params):
        if self.cfg.use_fixtures or self.cfg.dry_run:
            return None
        r = self._session.post(f"{API}{path}", params={**self._auth(), **params}, timeout=20)
        r.raise_for_status()
        return r.json()

    # -- read --------------------------------------------------------------
    def my_boards(self) -> list[tuple[str, str]]:
        """(id, name) of every open board the token can see."""
        return [(b["id"], b.get("name", "")) for b in
                self._get("/members/me/boards", filter="open", fields="name")]

    def board(self) -> tuple[str, str]:
        """The board the Personal page reads, resolved once.

        TRELLO_BOARD names it. Left blank, and the token sees exactly one board,
        that one is it -- the usual case for a board made for this. More than
        one and we refuse to guess: printing the wrong couple's chores would be
        a strange failure to debug from the tablet.
        """
        if self._board is not None:
            return self._board
        boards = self.my_boards()
        want = self.cfg.trello_board.strip().lower()
        if want:
            match = [b for b in boards if b[1].strip().lower() == want]
            if not match:
                have = ", ".join(repr(n) for _, n in boards) or "none"
                raise RuntimeError(
                    f"no board called {self.cfg.trello_board!r} — boards here: {have}")
            self._board = match[0]
        elif len(boards) == 1:
            self._board = boards[0]
        elif not boards:
            raise RuntimeError("the token sees no boards")
        else:
            have = ", ".join(repr(n) for _, n in boards)
            raise RuntimeError(f"set TRELLO_BOARD in .env — boards here: {have}")
        return self._board

    def lists(self) -> list[tuple[str, str]]:
        """(id, name) of the board's open lists, left to right."""
        if not self._lists:
            bid, _ = self.board()
            self._lists = [(l["id"], l.get("name", "")) for l in
                           self._get(f"/boards/{bid}/lists", filter="open", fields="name,pos")]
        return self._lists

    def _list_named(self, name: str) -> tuple[str, str] | None:
        want = _norm(name)
        return next((l for l in self.lists() if _norm(l[1]) == want), None)

    def shown_lists(self) -> list[tuple[str, str]]:
        """The lists the Personal page prints, in the order it prints them.

        TRELLO_LISTS picks and orders them; left blank, every list but Done is
        shown in board order. A configured name that matches nothing is
        skipped rather than fatal -- a renamed list should cost one section,
        not the page.
        """
        done = self.done_list()
        if not self.cfg.trello_lists:
            return [l for l in self.lists() if l != done]
        out = []
        for name in self.cfg.trello_lists:
            found = self._list_named(name)
            if found and found != done and found not in out:
                out.append(found)
        return out

    def done_list(self) -> tuple[str, str] | None:
        return self._list_named(self.cfg.trello_done_list)

    def inbox_list(self) -> tuple[str, str] | None:
        if self.cfg.trello_inbox_list:
            return self._list_named(self.cfg.trello_inbox_list)
        open_lists = [l for l in self.lists() if l != self.done_list()]
        return open_lists[0] if open_lists else None

    def move_targets(self) -> dict[str, tuple[str, str]]:
        """picker key -> (list id, list name) for the lists a tick can move a
        card to. A configured name that matches no list is left out, and the
        page then simply has no box for it."""
        out = {}
        for key, name in self.cfg.trello_move_lists.items():
            found = self._list_named(name)
            if found:
                out[key] = found
        return out

    def board_members(self) -> list[dict]:
        """Board members as {id, name, initials}, the token's owner first.

        The page gets one assign-box per member, so the order is the column
        order -- and the person holding the pen is the one most often assigning
        to themselves, so they come first.
        """
        if self.cfg.use_fixtures:
            return self._fixture()["members"]
        if not self._members:
            bid, _ = self.board()
            me = self._get("/members/me", fields="id")["id"]
            raw = self._get(f"/boards/{bid}/members", fields="fullName,initials,username")
            members = []
            for m in raw:
                full = (m.get("fullName") or m.get("username") or "").strip()
                first = _clean(full.split()[0]) if full else "?"
                # TRELLO_NAMES=Arcadie:Adrian -- a Trello display name is not
                # always the name the household uses.
                first = self.cfg.trello_names.get(full.lower(), self.cfg.trello_names.get(first.lower(), first))
                members.append({"id": m["id"], "name": first, "initials": _initials(m)})
            members.sort(key=lambda m: (m["id"] != me, m["name"].lower()))
            self._members = members
        return self._members

    def open_cards(self) -> list[TrelloCard]:
        """Every card not in the Done list, in list order then due date."""
        if self.cfg.use_fixtures:
            return self._fixture_cards()
        if not (self.cfg.trello_key and self.cfg.trello_token):
            raise RuntimeError("TRELLO_KEY / TRELLO_TOKEN are not set")

        bid, _ = self.board()
        lists = self.shown_lists()
        list_name = {lid: _clean(name) for lid, name in lists}
        order = {lid: i for i, (lid, _) in enumerate(lists)}
        members = {m["id"]: m["initials"] for m in self.board_members()}
        key_of_list = {lid: key for key, (lid, _name) in self.move_targets().items()}

        out: list[TrelloCard] = []
        for c in self._get(f"/boards/{bid}/cards", filter="open", fields=CARD_FIELDS):
            if c.get("closed") or c.get("dueComplete"):
                continue
            if c.get("idList") not in list_name:        # Done, or a list not shown
                continue
            due = (c.get("due") or "")[:10]
            out.append(TrelloCard(
                id=c["id"],
                name=_clean(c.get("name") or "") or "(untitled)",
                list_name=list_name[c["idList"]],
                due=date.fromisoformat(due) if due else None,
                members=[members[m] for m in c.get("idMembers", []) if m in members],
                member_ids=[m for m in c.get("idMembers", []) if m in members],
                labels=[l.get("name") or l.get("color", "") for l in c.get("labels", [])
                        if l.get("name") or l.get("color")],
                url=c.get("shortUrl", ""),
                list_key=key_of_list.get(c["idList"], ""),
                pos=float(c.get("pos") or 0),
            ))
        # Within a list: dated cards first, then the order the board shows them
        # in -- the order someone chose by dragging, which is worth keeping.
        out.sort(key=lambda k: (order.get(_list_id_of(k, lists), 99),
                                k.due is None, k.due or date.max, k.pos))
        return out

    def shown_list_names(self) -> list[str]:
        return [_clean(name) for _, name in self.shown_lists()]

    # -- write -------------------------------------------------------------
    def complete(self, card_id: str) -> str:
        """Finish a card. Returns what was done, for the log."""
        done = self.done_list()
        if done:
            self._put(f"/cards/{card_id}", idList=done[0], dueComplete="true", pos="top")
            return f"moved to {done[1]}"
        self._put(f"/cards/{card_id}", closed="true", dueComplete="true")
        return "archived"

    def move(self, card_id: str, key: str) -> str:
        """Move a card to the list a picker key stands for. Returns the list name."""
        target = self.move_targets().get(key)
        if target is None:
            raise RuntimeError(f"no list configured for {key!r}")
        self._put(f"/cards/{card_id}", idList=target[0], pos="bottom")
        return _clean(target[1])

    def add_member(self, card_id: str, member_id: str) -> bool:
        """Put a member on a card. False when they were on it already, which
        is the common case for a box whose bar was already drawn."""
        if self.cfg.use_fixtures or self.cfg.dry_run:
            return True
        r = self._session.post(f"{API}/cards/{card_id}/idMembers",
                               params={**self._auth(), "value": member_id}, timeout=20)
        if r.status_code == 400 and "already" in r.text.lower():
            return False
        r.raise_for_status()
        return True

    def create_card(self, title: str) -> str | None:
        """A handwritten line from the Personal page becomes a card in the inbox."""
        inbox = self.inbox_list()
        if inbox is None:
            raise RuntimeError("no list to put new cards in")
        if self.cfg.use_fixtures or self.cfg.dry_run:
            return None
        made = self._post("/cards", idList=inbox[0], name=title.strip(), pos="bottom")
        return (made or {}).get("id")

    # -- fixtures ----------------------------------------------------------
    def _fixture(self) -> dict:
        path = self.cfg.fixtures_dir / "trello.json"
        if not path.exists():
            return {"members": [], "cards": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _fixture_cards(self) -> list[TrelloCard]:
        raw = self._fixture()
        initials = {m["id"]: m["initials"] for m in raw["members"]}
        out = []
        for c in raw["cards"]:
            due = c.get("due_on")
            out.append(TrelloCard(
                id=c["id"], name=c["name"], list_name=c.get("list", ""),
                due=self.today + timedelta(days=int(due)) if due not in (None, "") else None,
                members=[initials[m] for m in c.get("members", []) if m in initials],
                member_ids=[m for m in c.get("members", []) if m in initials],
                labels=list(c.get("labels", [])), list_key=c.get("list_key", ""),
            ))
        return out


def _list_id_of(card: TrelloCard, lists: list[tuple[str, str]]) -> str:
    return next((lid for lid, name in lists if _clean(name) == card.list_name), "")


def load_trello(cfg: Config, today: date) -> tuple[list[TrelloCard], list[str], SectionStatus, "TrelloClient | None"]:
    """(cards, list names in board order, status, client). Never raises.

    No key and no token means the feature is simply not set up, and the page
    says so instead of the whole run failing -- the work half of the sheet
    does not depend on the personal half.
    """
    if cfg.use_fixtures:
        client = TrelloClient(cfg, today)
        cards = client._fixture_cards()
        names = list(dict.fromkeys(c.list_name for c in cards if c.list_name))
        return cards, names, SectionStatus(), client
    if not (cfg.trello_key and cfg.trello_token):
        return [], [], SectionStatus(ok=False, error="TRELLO_KEY / TRELLO_TOKEN not set — see README"), None
    client = TrelloClient(cfg, today)
    try:
        cards = client.open_cards()
        return cards, client.shown_list_names(), SectionStatus(), client
    except Exception as exc:  # noqa: BLE001
        return [], [], SectionStatus(ok=False, error=f"{type(exc).__name__}: {exc}"), client
