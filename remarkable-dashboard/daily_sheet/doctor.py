"""`doctor` — check every link in the chain and say which one is broken.

Each check prints OK / WARN / FAIL plus, on anything other than OK, the one
thing to go and do about it. Nothing here writes: no pushes, no Asana
completions, no files touched. Safe to run whenever.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from .calendar_ics import load_events
from .config import Config
from .remarkable import Rmapi, RmapiError

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"
_SYM = {OK: "[ok]", WARN: "[warn]", FAIL: "[FAIL]"}


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []   # status, title, detail, fix

    def add(self, status: str, title: str, detail: str = "", fix: str = "") -> None:
        self.rows.append((status, title, detail, fix))

    def print(self) -> int:
        width = max(len(t) for _, t, _, _ in self.rows) + 2
        print()
        for status, title, detail, _ in self.rows:
            print(f"  {_SYM[status]:<7} {title:<{width}} {detail}")
        fixes = [(t, f) for s, t, _, f in self.rows if f and s != OK]
        if fixes:
            print("\n  What to do:\n")
            for title, fix in fixes:
                print(f"  · {title}")
                for line in fix.splitlines():
                    print(f"      {line}")
                print()
        failed = sum(1 for s, _, _, _ in self.rows if s == FAIL)
        warned = sum(1 for s, _, _, _ in self.rows if s == WARN)
        print(f"  {len(self.rows)} checks · {failed} failed · {warned} warnings\n")
        return 1 if failed else 0


# --- individual checks ------------------------------------------------------
def check_secrets(cfg: Config, rep: Report) -> None:
    for label, value, why in [
        ("ICS_URL", cfg.ics_url, "calendar will render 'unavailable'"),
        ("ASANA_PAT", cfg.asana_pat, "Asana page will render 'unavailable'"),
        ("ANTHROPIC_API_KEY", cfg.anthropic_api_key,
         "checkbox ticks still work; handwriting won't be transcribed"),
    ]:
        if value:
            rep.add(OK, f".env {label}", "set")
        else:
            rep.add(WARN, f".env {label}", f"not set — {why}",
                    f"Add {label}=... to .env")


def check_rmapi(cfg: Config, rm: Rmapi, rep: Report) -> bool:
    ok, reason = rm.status()
    if not ok:
        rep.add(FAIL, "rmapi", reason,
                "Install rmapi, set RMAPI_BIN in .env to its full path,\n"
                "then run it once to pair with my.remarkable.com")
        return False
    rep.add(OK, "rmapi", "installed and paired")
    return True


def check_tablet(cfg: Config, rm: Rmapi, rep: Report) -> list[str]:
    try:
        entries = rm.ls(cfg.remarkable_folder)
    except RmapiError as exc:
        rep.add(FAIL, f"tablet folder {cfg.remarkable_folder}/", str(exc),
                "Check the tablet is syncing and REMARKABLE_FOLDER is right")
        return []
    sheets = sorted(e for e in entries if e.startswith("Daily Sheet"))
    if sheets:
        rep.add(OK, f"tablet folder {cfg.remarkable_folder}/",
                f"{len(sheets)} sheet(s), newest: {sheets[-1]}")
    else:
        rep.add(WARN, f"tablet folder {cfg.remarkable_folder}/", "no sheets yet",
                "Run: py -m daily_sheet generate")
    return sheets


def check_calendar(cfg: Config, rep: Report) -> None:
    if not cfg.ics_url:
        return
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    events, status = load_events(cfg, monday - timedelta(days=7), monday + timedelta(days=14))
    if not status.ok:
        rep.add(FAIL, "calendar (ICS)", status.error,
                "Check ICS_URL is the .ics link, not the HTML one, and that it still works")
        return
    if not events:
        rep.add(WARN, "calendar (ICS)", "fetched, but no events in the 3-week window",
                "If your calendar is not empty, the feed may be the wrong calendar")
        return

    # A free/busy-only publish level strips titles. Requiring *every* title to
    # be a busy-word missed this on a real feed: one differently-named entry
    # and the check passed while the sheet showed nothing but "Busy". Judge it
    # by proportion, and print the titles so the answer is visible either way.
    busy_words = {"", "busy", "opptatt", "tentative", "free", "ledig",
                  "opptatt/busy", "privat", "private", "no title", "(no title)"}
    titles = [(e.title or "").strip() for e in events]
    opaque = sum(1 for t in titles if t.lower() in busy_words)
    distinct = sorted({t for t in titles if t.lower() not in busy_words})

    source = "Power Automate JSON" if cfg.calendar_json else "ICS"
    if opaque >= len(titles) * 0.8:
        sample = ", ".join(distinct[:3]) or "none"
        rep.add(WARN, f"calendar ({source})",
                f"{len(events)} events, {opaque} of them untitled (real titles: {sample})",
                "The feed is published at 'Can view when I'm busy', which strips\n"
                "titles at source — no parsing recovers them.\n"
                "Re-publish at 'Can view titles and locations', or switch to the\n"
                "Power Automate source: see 'Calendar via Power Automate' in the README.")
    else:
        rep.add(OK, f"calendar ({source})",
                f"{len(events)} events, {len(titles) - opaque} titled "
                f"(e.g. {', '.join(distinct[:2]) or '—'})")


def check_asana(cfg: Config, rep: Report) -> None:
    if not cfg.asana_pat:
        return
    from .asana_client import AsanaClient
    client = AsanaClient(cfg, date.today())
    try:
        me = client._get("/users/me", opt_fields="name,email,workspaces.name")
    except Exception as exc:  # noqa: BLE001
        rep.add(FAIL, "Asana", f"{type(exc).__name__}: {exc}",
                "Check ASANA_PAT. Create a new token at app.asana.com/0/my-apps")
        return
    ws = ", ".join(w.get("name", "?") for w in me.get("workspaces", []))
    try:
        tasks = client.my_open_tasks()
    except Exception as exc:  # noqa: BLE001
        rep.add(WARN, "Asana", f"authenticated as {me.get('name')}, but listing failed: {exc}")
        return
    rep.add(OK, "Asana", f"{me.get('name')} · {len(tasks)} open task(s) · workspaces: {ws}")


def check_anthropic(cfg: Config, rep: Report) -> None:
    if not cfg.anthropic_api_key:
        return
    try:
        from anthropic import Anthropic
    except ImportError:
        rep.add(FAIL, "Anthropic API", "the anthropic package is not installed",
                "Run: py -m pip install -r requirements.txt")
        return
    try:
        # Cheapest possible real call: one token out.
        Anthropic(api_key=cfg.anthropic_api_key).messages.create(
            model="claude-opus-5", max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
    except Exception as exc:  # noqa: BLE001
        rep.add(FAIL, "Anthropic API", f"{type(exc).__name__}: {exc}",
                "Check ANTHROPIC_API_KEY and that the account has credit\n"
                "(console.anthropic.com → Billing)")
        return
    rep.add(OK, "Anthropic API", "key valid")


def check_readback(cfg: Config, rm: Rmapi, rmapi_ok: bool, sheets: list[str], rep: Report) -> None:
    """The chain that makes ticking a box actually do something.

    Needs three things to line up: yesterday's sheet still in Daily/ (not
    already archived), the layout.json written when it was rendered, and a
    tablet we can download it from.
    """
    today = date.today()
    yesterday = today - timedelta(days=1)
    name = f"Daily Sheet — {yesterday.isoformat()}"
    layout = cfg.out_dir / f"layout-{yesterday.isoformat()}.json"

    have_layout = layout.exists()
    on_tablet = rmapi_ok and name in sheets

    if have_layout and on_tablet:
        rep.add(OK, "read-back (ticks -> Asana)",
                f"ready — tomorrow's run will read {name}")
        return

    layouts = sorted(p.stem.replace("layout-", "") for p in cfg.out_dir.glob("layout-*.json"))
    detail = []
    if not have_layout:
        detail.append(f"no layout for {yesterday.isoformat()}")
    if rmapi_ok and not on_tablet:
        detail.append(f"'{name}' not in {cfg.remarkable_folder}/")

    rep.add(WARN, "read-back (ticks -> Asana)", "; ".join(detail) or "not ready",
            "This is normal on day one — read-back reads YESTERDAY's sheet,\n"
            "so it starts working the morning after your first real run.\n"
            f"Layouts on disk: {', '.join(layouts) or 'none'}\n"
            "Note: ticks only count on the sheet still in Daily/. Re-running\n"
            "generate archives the previous sheet, so marks made on an\n"
            "archived copy are not read.")


def check_schedule(rep: Report) -> None:
    """Windows only; silent elsewhere."""
    import platform
    import subprocess
    if platform.system() != "Windows":
        return
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/tn", "reMarkable Daily Sheet"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return
    if r.returncode == 0:
        rep.add(OK, "scheduled task", "registered")
    else:
        rep.add(WARN, "scheduled task", "not registered",
                "Run: .\\install-task.ps1")


# --- entry point ------------------------------------------------------------
def doctor(cfg: Config) -> int:
    print("\nreMarkable daily sheet — connection check")
    rep = Report()
    rm = Rmapi(cfg)

    check_secrets(cfg, rep)
    rmapi_ok = check_rmapi(cfg, rm, rep)
    sheets = check_tablet(cfg, rm, rep) if rmapi_ok else []
    check_calendar(cfg, rep)
    check_asana(cfg, rep)
    check_anthropic(cfg, rep)
    check_readback(cfg, rm, rmapi_ok, sheets, rep)
    check_schedule(rep)

    return rep.print()
