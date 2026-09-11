"""Local task store: tasks.json. Never deletes; only appends and flips status."""
from __future__ import annotations

import json
import secrets
from datetime import date
from pathlib import Path

from .models import Task

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o/1/l/i ambiguity


def _new_id(existing: set[str]) -> str:
    while True:
        tid = "t-" + "".join(secrets.choice(_ALPHABET) for _ in range(4))
        if tid not in existing:
            return tid


class TaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.tasks: list[Task] = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
            self.tasks = [Task.from_json(d) for d in raw]
        else:
            self.tasks = []

    def save(self) -> None:
        self.path.write_text(
            json.dumps([t.to_json() for t in self.tasks], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def by_id(self, tid: str) -> Task | None:
        return next((t for t in self.tasks if t.id == tid), None)

    def open_tasks(self) -> list[Task]:
        """Sorted by priority, then age (oldest first)."""
        return sorted(
            (t for t in self.tasks if t.status == "open"),
            key=lambda t: (t.priority, t.created),
        )

    def add(self, title: str, priority: int = 3, source: str = "manual", today: date | None = None) -> Task:
        today = today or date.today()
        task = Task(
            id=_new_id({t.id for t in self.tasks}),
            title=title.strip(),
            priority=max(1, min(3, int(priority))),
            status="open",
            source=source,
            created=today.isoformat(),
        )
        self.tasks.append(task)
        return task

    def complete(self, tid: str, today: date | None = None) -> Task | None:
        task = self.by_id(tid)
        if task and task.status == "open":
            task.status = "done"
            task.completed = (today or date.today()).isoformat()
        return task

    def set_priority(self, tid: str, priority: int) -> Task | None:
        task = self.by_id(tid)
        if task and priority in (1, 2, 3):
            task.priority = priority
        return task


def parse_pen_line(line: str) -> tuple[str, int] | None:
    """'Call dentist !!' -> ('Call dentist', 1). Trailing !! = P1, ! = P2, else P3."""
    s = line.strip()
    if not s:
        return None
    if s.endswith("!!"):
        return s[:-2].strip(), 1
    if s.endswith("!"):
        return s[:-1].strip(), 2
    return s, 3
