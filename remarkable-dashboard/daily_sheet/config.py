from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Europe/Oslo")

# reMarkable 2 canvas. We render 1 px = 1 pt so the PDF page is exactly the screen.
PAGE_W = 1404
PAGE_H = 1872


@dataclass
class Config:
    ics_url: str
    calendar_json: str
    asana_pat: str
    asana_board: str
    anthropic_api_key: str
    rmapi_bin: str
    remarkable_folder: str
    project_dir: Path
    out_dir: Path
    tasks_file: Path
    notes_dir: Path
    fixtures_dir: Path
    runs_log: Path
    use_fixtures: bool = False
    dry_run: bool = False

    @property
    def archive_folder(self) -> str:
        return f"{self.remarkable_folder}/Archive"


def load_config(use_fixtures: bool = False, dry_run: bool = False) -> Config:
    load_dotenv(PROJECT_DIR / ".env")
    out_dir = PROJECT_DIR / "out"
    return Config(
        ics_url=os.getenv("ICS_URL", "").strip(),
        calendar_json=os.getenv("CALENDAR_JSON", "").strip(),
        asana_pat=os.getenv("ASANA_PAT", "").strip(),
        asana_board=os.getenv("ASANA_BOARD", "Incoming + active projects").strip()
                    or "Incoming + active projects",
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "").strip(),
        rmapi_bin=os.getenv("RMAPI_BIN", "rmapi").strip() or "rmapi",
        remarkable_folder=os.getenv("REMARKABLE_FOLDER", "Daily").strip() or "Daily",
        project_dir=PROJECT_DIR,
        out_dir=out_dir,
        tasks_file=PROJECT_DIR / "tasks.json",
        notes_dir=PROJECT_DIR / "notes",
        fixtures_dir=PROJECT_DIR / "fixtures",
        runs_log=PROJECT_DIR / "runs.log",
        use_fixtures=use_fixtures,
        dry_run=dry_run,
    )
