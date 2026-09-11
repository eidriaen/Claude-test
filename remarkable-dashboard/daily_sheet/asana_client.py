"""Asana: read tasks assigned to me, and mark ticked ones complete. Nothing else."""
from __future__ import annotations

import json
from datetime import date, timedelta

import requests

from .config import Config
from .models import AsanaTask, ProjectCard, SectionStatus

API = "https://app.asana.com/api/1.0"

# Words that mark a numeric custom field as money. Norwegian first, since that
# is what this board is written in.
_MONEY_WORDS = ("budsjett", "budget", "kr", "nok", "verdi", "value", "beløp", "belop", "pris")


PRIORITY_NAMES = ("high", "medium", "low")


def _priority_of(task: dict) -> tuple[str, str, dict]:
    """(current value, field gid, {option name: gid}) for the Priority field.

    Found by looking for an enum field whose options look like a priority scale
    rather than by field name, so 'Priority', 'Prioritet' or 'Prio' all work.
    """
    for cf in task.get("custom_fields", []):
        options = {(o.get("name") or "").strip(): o.get("gid", "")
                   for o in cf.get("enum_options", []) if o.get("name")}
        if not options:
            continue
        lowered = {n.lower() for n in options}
        if not lowered & set(PRIORITY_NAMES):
            continue
        current = ((cf.get("enum_value") or {}).get("name") or "").strip()
        return current, cf.get("gid", ""), options
    return "", "", {}


def _money(label: str) -> bool:
    low = label.lower()
    return any(w in low for w in _MONEY_WORDS)


class AsanaClient:
    def __init__(self, cfg: Config, today: date):
        self.cfg = cfg
        self.today = today
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {cfg.asana_pat}"
        self._session.headers["Accept"] = "application/json"

    # -- read -------------------------------------------------------------
    def my_open_tasks(self) -> list[AsanaTask]:
        if self.cfg.use_fixtures:
            return self._fixture_tasks()
        if not self.cfg.asana_pat:
            raise RuntimeError("ASANA_PAT is not set")

        me = self._get("/users/me", opt_fields="workspaces.name")
        tasks: list[AsanaTask] = []
        for ws in me.get("workspaces", []):
            tasks.extend(self._tasks_in_workspace(ws["gid"]))
        return sort_asana(tasks)

    def _tasks_in_workspace(self, workspace_gid: str) -> list[AsanaTask]:
        out: list[AsanaTask] = []
        params = {
            "assignee": "me",
            "workspace": workspace_gid,
            "completed_since": "now",          # incomplete only
            "limit": 100,
            "opt_fields": ("name,due_on,due_at,completed,projects.name,permalink_url,"
                           "custom_fields.gid,custom_fields.name,"
                           "custom_fields.enum_value.name,"
                           "custom_fields.enum_options.gid,custom_fields.enum_options.name"),
        }
        url = f"{API}/tasks"
        while url:
            r = self._session.get(url, params=params, timeout=20)
            r.raise_for_status()
            body = r.json()
            for t in body.get("data", []):
                if t.get("completed"):
                    continue
                due = t.get("due_on") or (t.get("due_at") or "")[:10] or None
                prio, field_gid, options = _priority_of(t)
                out.append(AsanaTask(
                    gid=t["gid"],
                    name=(t.get("name") or "").strip() or "(untitled)",
                    project=", ".join(p.get("name", "") for p in t.get("projects", []) if p.get("name")),
                    due=date.fromisoformat(due) if due else None,
                    permalink=t.get("permalink_url", ""),
                    priority=prio, priority_field=field_gid, priority_options=options,
                ))
            nxt = (body.get("next_page") or {}).get("uri")
            url, params = (nxt, None) if nxt else (None, None)
        return out

    def _get(self, path: str, **params) -> dict:
        r = self._session.get(f"{API}{path}", params=params, timeout=20)
        r.raise_for_status()
        return r.json()["data"]

    # -- the pipeline board -----------------------------------------------
    def _paged(self, path: str, **params) -> list[dict]:
        """Follow next_page. A single page caps at 100, and a workspace with
        more projects than that would otherwise hide the one we want."""
        params.setdefault("limit", 100)
        url, out = f"{API}{path}", []
        while url:
            r = self._session.get(url, params=params or None, timeout=30)
            r.raise_for_status()
            body = r.json()
            out.extend(body.get("data", []))
            nxt = (body.get("next_page") or {}).get("uri")
            url, params = (nxt, None) if nxt else (None, None)
        return out

    def all_projects(self) -> list[tuple[str, str, str]]:
        """(gid, name, workspace gid) for every project this token can see."""
        me = self._get("/users/me", opt_fields="workspaces.name")
        out: list[tuple[str, str, str]] = []
        for ws in me.get("workspaces", []):
            for proj in self._paged(f"/workspaces/{ws['gid']}/projects",
                                    opt_fields="name", archived="false"):
                out.append((proj["gid"], (proj.get("name") or "").strip(), ws["gid"]))
        return out

    def find_project(self, name: str) -> tuple[str, str] | None:
        """(project gid, workspace gid) for the board called `name`, or None.

        Tried in three passes, loosest last: exact ignoring case, then ignoring
        punctuation and spacing, then substring. A board named "Incoming +
        Active Projects" or "Incoming & active projects" should not need the
        config edited to match, because the name in Asana is edited by people.
        """
        want = name.strip().lower()
        projects = self.all_projects()

        for gid, pname, ws in projects:
            if pname.lower() == want:
                return gid, ws

        def norm(v: str) -> str:
            return "".join(ch for ch in v.lower() if ch.isalnum())

        for gid, pname, ws in projects:
            if norm(pname) == norm(want):
                return gid, ws
        for gid, pname, ws in projects:
            if want in pname.lower() or norm(want) in norm(pname):
                return gid, ws
        return None

    def board(self, project_gid: str) -> list[ProjectCard]:
        """Every incomplete card on the board, tagged with its section."""
        params = {
            "limit": 100,
            "opt_fields": ("name,completed,due_on,memberships.section.name,"
                           "custom_fields.name,custom_fields.type,"
                           "custom_fields.display_value,custom_fields.number_value,"
                           "custom_fields.enum_value.name"),
        }
        url = f"{API}/projects/{project_gid}/tasks"
        cards: list[ProjectCard] = []
        while url:
            r = self._session.get(url, params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for t in body.get("data", []):
                if t.get("completed"):
                    continue
                cards.append(self._card(t))
            nxt = (body.get("next_page") or {}).get("uri")
            url, params = (nxt, None) if nxt else (None, None)
        return cards

    @staticmethod
    def _card(t: dict) -> ProjectCard:
        section = ""
        for m in t.get("memberships", []):
            if (m.get("section") or {}).get("name"):
                section = m["section"]["name"]
                break

        budget: float | None = None
        fields: list[tuple[str, str]] = []
        for cf in t.get("custom_fields", []):
            label = (cf.get("name") or "").strip()
            shown = (cf.get("display_value") or "").strip()
            if not label:
                continue
            # First numeric field that reads like money becomes the budget; the
            # rest are printed as-is so a workspace can carry whatever statuses
            # it likes without this needing to know their names.
            if budget is None and cf.get("number_value") is not None and _money(label):
                budget = float(cf["number_value"])
                continue
            if shown:
                fields.append((label, shown))

        due = t.get("due_on")
        return ProjectCard(
            gid=t["gid"],
            name=(t.get("name") or "").strip() or "(untitled)",
            section=section or "(no section)",
            budget=budget,
            fields=fields,
            due=date.fromisoformat(due) if due else None,
        )

    def sections(self, project_gid: str) -> list[str]:
        """Section names in board order, so the page reads like the board."""
        data = self._get(f"/projects/{project_gid}/sections", opt_fields="name", limit=100)
        return [(s.get("name") or "").strip() for s in data if s.get("name")]

    # -- write ------------------------------------------------------------
    def complete(self, gid: str) -> None:
        if self.cfg.use_fixtures or self.cfg.dry_run:
            return
        r = self._session.put(f"{API}/tasks/{gid}", json={"data": {"completed": True}}, timeout=20)
        r.raise_for_status()

    def set_priority(self, task: AsanaTask, value: str) -> None:
        """Set the Priority enum on one task. No-op if the board has no such field."""
        if self.cfg.use_fixtures or self.cfg.dry_run:
            return
        if not task.priority_field:
            raise RuntimeError("this task has no Priority field")
        match = next((n for n in task.priority_options if n.lower() == value.lower()), None)
        if match is None:
            raise RuntimeError(f"no Priority option called {value!r}")
        r = self._session.put(f"{API}/tasks/{task.gid}", timeout=20, json={"data": {
            "custom_fields": {task.priority_field: task.priority_options[match]},
        }})
        r.raise_for_status()

    def create_task(self, title: str, workspace_gid: str | None = None) -> str | None:
        """Create a task assigned to me. Returns its gid, or None in dry-run."""
        if self.cfg.use_fixtures or self.cfg.dry_run:
            return None
        if workspace_gid is None:
            me = self._get("/users/me", opt_fields="workspaces.name")
            ws = me.get("workspaces", [])
            if not ws:
                raise RuntimeError("no Asana workspace to create the task in")
            workspace_gid = ws[0]["gid"]
        r = self._session.post(f"{API}/tasks", timeout=20, json={"data": {
            "name": title, "assignee": "me", "workspace": workspace_gid,
        }})
        r.raise_for_status()
        return r.json()["data"]["gid"]

    # -- fixtures ---------------------------------------------------------
    def _fixture_tasks(self) -> list[AsanaTask]:
        raw = json.loads((self.cfg.fixtures_dir / "asana_tasks.json").read_text(encoding="utf-8"))
        out = []
        for t in raw:
            # fixture due_on is an offset in days from today so the sample stays relevant
            due = self.today + timedelta(days=int(t["due_on"])) if t.get("due_on") is not None else None
            out.append(AsanaTask(
                gid=t["gid"], name=t["name"], project=t.get("project", ""), due=due,
                priority=t.get("priority", ""),
                priority_field="fixture-field",
                priority_options={"High": "o1", "Medium": "o2", "Low": "o3"}))
        return sort_asana(out)


def sort_asana(tasks: list[AsanaTask]) -> list[AsanaTask]:
    """Priority first, then due date ascending with undated last.

    Priority leads because it is the judgement you made about the task; the due
    date is only when it happens to be scheduled.
    """
    return sorted(tasks, key=lambda t: (t.rank(), t.due is None, t.due or date.max,
                                        t.name.lower()))


def load_asana(cfg: Config, today: date) -> tuple[list[AsanaTask], SectionStatus, AsanaClient | None]:
    client = AsanaClient(cfg, today)
    try:
        return client.my_open_tasks(), SectionStatus(), client
    except Exception as exc:  # noqa: BLE001
        return [], SectionStatus(ok=False, error=f"{type(exc).__name__}: {exc}"), client


def load_board(cfg: Config, client: AsanaClient | None, today: date):
    """(cards, section names, status) for the pipeline board.

    Never raises: a board that has been renamed, or a token without access to
    it, leaves the Projects page showing an 'unavailable' notice while the rest
    of the sheet ships as normal.
    """
    if cfg.use_fixtures:
        return _fixture_board(cfg), _FIXTURE_SECTIONS, SectionStatus()
    if client is None or not cfg.asana_pat:
        return [], [], SectionStatus(ok=False, error="ASANA_PAT is not set")
    try:
        found = client.find_project(cfg.asana_board)
        if not found:
            return [], [], SectionStatus(
                ok=False, error=f"no board called {cfg.asana_board!r} — set ASANA_BOARD in .env")
        gid, _ws = found
        return client.board(gid), client.sections(gid), SectionStatus()
    except Exception as exc:  # noqa: BLE001
        return [], [], SectionStatus(ok=False, error=f"{type(exc).__name__}: {exc}")


_FIXTURE_SECTIONS = [
    "Incoming/leads", "Scoping/concept phase", "Need workshop", "Booked workshop",
    "Waiting on material/client", "Need offer/contract", "In negotiation/offer sent",
    "Signed/waiting to start", "Active",
]


def _fixture_board(cfg: Config) -> list[ProjectCard]:
    path = cfg.fixtures_dir / "board.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ProjectCard(gid=c["gid"], name=c["name"], section=c["section"],
                        budget=c.get("budget"),
                        fields=[tuple(f) for f in c.get("fields", [])])
            for c in raw]
