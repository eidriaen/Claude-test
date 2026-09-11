from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional


@dataclass
class Event:
    start: datetime          # tz-aware, Europe/Oslo
    end: datetime
    title: str
    location: str = ""
    all_day: bool = False

    @property
    def day(self) -> date:
        return self.start.date()


@dataclass
class Task:
    """Private task, persisted in tasks.json."""
    id: str
    title: str
    priority: int = 3            # 1 | 2 | 3
    status: str = "open"         # open | done
    source: str = "manual"       # pen | manual
    created: str = ""            # YYYY-MM-DD
    completed: Optional[str] = None
    notes: str = ""

    def carried_days(self, today: date) -> int:
        """Days since creation; 'carried' tag is shown when > 3 and still open."""
        try:
            return (today - date.fromisoformat(self.created)).days
        except ValueError:
            return 0

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Task":
        return cls(
            id=d["id"],
            title=d["title"],
            priority=int(d.get("priority", 3)),
            status=d.get("status", "open"),
            source=d.get("source", "manual"),
            created=d.get("created", ""),
            completed=d.get("completed"),
            notes=d.get("notes", "") or "",
        )


@dataclass
class AsanaTask:
    gid: str
    name: str
    project: str = ""
    due: Optional[date] = None
    permalink: str = ""


@dataclass
class ProjectCard:
    """One card on the pipeline board.

    `budget` is whatever numeric custom field reads as money; `fields` holds the
    other custom values worth printing, in board order. Both are discovered at
    fetch time rather than hard-coded, because custom field names are per
    workspace and renaming one in Asana should not blank the page.
    """
    gid: str
    name: str
    section: str
    budget: Optional[float] = None
    fields: list[tuple[str, str]] = field(default_factory=list)
    due: Optional[date] = None

    def budget_str(self) -> str:
        """Norwegian thousands grouping: 1 250 000 kr."""
        if self.budget is None:
            return ""
        return f"{self.budget:,.0f}".replace(",", " ") + " kr"


@dataclass
class SectionStatus:
    """Per-source fetch status so a failing source still ships the sheet."""
    ok: bool = True
    error: str = ""


@dataclass
class IngestionReport:
    completed_private: list[str] = field(default_factory=list)
    completed_asana: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)   # paths to PNG strips
    note: str = ""                                          # e.g. "no sheet found for yesterday"

    @property
    def completed(self) -> int:
        return len(self.completed_private) + len(self.completed_asana)

    def summary(self) -> str:
        parts = [f"Completed {self.completed}"]
        added = f"Added {len(self.added)}"
        if self.added:
            added += " (" + ", ".join(self.added[:4]) + (", …" if len(self.added) > 4 else "") + ")"
        parts.append(added)
        parts.append(f"Unreadable {len(self.unreadable)}")
        return " · ".join(parts)


@dataclass
class Region:
    """A pen-input region on a rendered page. Coordinates are top-left-origin
    pixels on the 1404×1872 canvas."""
    id: str                # task id / asana gid / "newtasks" / "notes"
    kind: str              # check | priority | newtasks | notes
    page: int              # 1-based
    x0: int
    y0: int
    x1: int
    y1: int
    line_height: int = 0   # for newtasks: spacing between ruled lines

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Region":
        return cls(**d)
