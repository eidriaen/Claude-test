"""The `generate` command — one run of the daily loop (SCOPE §3).

    python -m daily_sheet generate                 # the real thing
    python -m daily_sheet generate --fixtures --dry-run
    python -m daily_sheet sync                     # read today's ticks now
    python -m daily_sheet doctor                   # check every connection
    python -m daily_sheet calibrate <annotated.pdf> out/layout.json

Failure policy: any single source failing renders that section with an
"unavailable" notice and still ships the sheet. Only a tablet push failure is
a hard error (exit 1).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from .asana_client import load_asana, load_board
from .calendar_ics import load_events
from .config import Config, load_config
from .models import AsanaTask, IngestionReport, SectionStatus
from .readback import Marks, read_marks, set_strip_dir
from .remarkable import Rmapi, RmapiError
from .render import SheetData, render_sheet
from .tasks import TaskStore, parse_pen_line


def log(cfg: Config, msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    try:
        with cfg.runs_log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def sheet_name(d: date) -> str:
    return f"Daily Sheet — {d.isoformat()}"


# --- steps 1–3: read yesterday back ----------------------------------------
def ingest_yesterday(cfg: Config, rm: Rmapi, store: TaskStore, asana, today: date,
                     asana_tasks=None) -> IngestionReport:
    """Locate, read back, and archive yesterday's sheet. Never raises."""
    report = IngestionReport()
    yesterday = today - timedelta(days=1)
    name = sheet_name(yesterday)
    layout = cfg.out_dir / f"layout-{yesterday.isoformat()}.json"

    if not layout.exists():
        report.note = f"No layout for {yesterday.isoformat()} — nothing to read back."
        return report
    if cfg.use_fixtures or not rm.available():
        report.note = "Tablet unavailable — skipped read-back."
        return report

    doc = rm.find(cfg.remarkable_folder, name)
    if not doc:
        report.note = f"No sheet found on tablet for {yesterday.isoformat()}."
        return report

    try:
        annotated = rm.download_annotated(doc, cfg.out_dir / "annotated")
        set_strip_dir(cfg.out_dir / "strips")
        marks = read_marks(annotated, layout, cfg.anthropic_api_key,
                           debug_dir=cfg.out_dir / "debug" if cfg.dry_run else None)
    except (RmapiError, Exception) as exc:  # noqa: BLE001 — read-back is best-effort
        report.note = f"Read-back failed: {type(exc).__name__}: {exc}"
        return report

    apply_marks(marks, store, asana, today, report, asana_tasks=asana_tasks)
    write_notes(cfg, marks.notes, yesterday)

    try:
        where = rm.archive(doc)
        log(cfg, f"archived yesterday's sheet to {where}")
    except RmapiError as exc:
        log(cfg, f"warn: archive failed: {exc}")
    return report


def apply_marks(marks: Marks, store: TaskStore, asana, today: date, report: IngestionReport,
                already_added: set[str] | None = None, asana_tasks=None) -> None:
    """Turn pen marks into task-store and Asana changes. Nothing is destructive.

    `already_added` holds titles this sheet has contributed before — sync can run
    repeatedly against the same page, and without it every run would re-add the
    same handwritten line.
    """
    seen = already_added or set()
    known = {t.id for t in store.tasks}
    done: set[str] = set()          # guards the one external write we make

    for rid in dict.fromkeys(marks.checked):   # order-preserving dedupe
        if rid in done:
            continue
        done.add(rid)
        if rid in known:
            if (t := store.complete(rid, today)) is not None:
                report.completed_private.append(t.title)
        elif asana is not None:
            try:
                asana.complete(rid)
                report.completed_asana.append(rid)
            except Exception as exc:  # noqa: BLE001
                report.unreadable.append(f"asana {rid}: {exc}")

    for rid, prio in marks.priorities.items():
        store.set_priority(rid, prio)

    by_gid = {t.gid: t for t in (asana_tasks or [])}
    for gid, week in marks.set_week.items():
        if gid in by_gid:
            _assign_week(asana, gid, by_gid[gid].name, week, report)

    for gid, value in marks.set_priority.items():
        task = by_gid.get(gid)
        if task is None or task.priority.lower() == value.lower():
            continue        # already there; nothing to write
        try:
            asana.set_priority(task, value)
            report.priorities.append(f"{task.name} -> {value}")
        except Exception as exc:  # noqa: BLE001
            report.unreadable.append(f"priority for {task.name}: {exc}")

    # Each handwritten line sits on a ruled row with its own pickers, so pair
    # them up. A mismatch means we cannot say which boxes belong to which line,
    # and guessing would file a task under a priority nobody chose -- so the
    # text is still captured, just without them.
    rows = marks.new_task_rows
    paired = len(rows) == len([l for l in marks.new_tasks if parse_pen_line(l)])

    idx = 0
    for line in marks.new_tasks:
        parsed = parse_pen_line(line)
        if not parsed:
            continue
        title, prio = parsed
        row = rows[idx] if paired and idx < len(rows) else None
        idx += 1
        if title.strip().lower() in seen:
            continue

        # Everything lives in Asana now; tasks.json only keeps a local record so
        # a handwritten line is not lost if the Asana write fails.
        store.add(title, prio, source="pen", today=today)
        if asana is None:
            report.added.append(title)
            continue
        try:
            gid = asana.create_task(title)
        except Exception as exc:  # noqa: BLE001
            report.unreadable.append(f"could not create '{title}' in Asana: {exc}")
            continue
        report.added.append(title)

        if row is None or gid is None:
            continue
        picked = marks.set_priority.get(f"new{row}", "")
        if picked:
            try:
                asana.set_priority(AsanaTask(gid=gid, name=title,
                                             priority_field=_priority_field(asana_tasks),
                                             priority_options=_priority_options(asana_tasks)),
                                   picked)
            except Exception as exc:  # noqa: BLE001
                report.unreadable.append(f"priority for '{title}': {exc}")
        week = marks.set_week.get(f"new{row}", "")
        if week:
            _assign_week(asana, gid, title, week, report)

    report.unreadable.extend(str(p) for p in marks.unreadable)


def _priority_field(tasks) -> str:
    """The Priority field gid, taken from any task that has one.

    A task we just created carries no custom fields yet, so borrow the gid from
    a task already on the board -- the field is per project, not per task.
    """
    for t in (tasks or []):
        if t.priority_field:
            return t.priority_field
    return ""


def _priority_options(tasks) -> dict:
    for t in (tasks or []):
        if t.priority_options:
            return t.priority_options
    return {}


def _assign_week(asana, gid: str, title: str, week: str, report: IngestionReport) -> None:
    """Add a task to the "YYYY: Week ##" project for this or next week."""
    if asana is None:
        return
    try:
        pgid, name = asana.week_project(0 if week == "this" else 1)
        if not pgid:
            report.unreadable.append(f"no Asana project called '{name}' — '{title}' not filed")
            return
        asana.add_to_project(gid, pgid)
        report.weeks.append(f"{title} -> {name}")
    except Exception as exc:  # noqa: BLE001
        report.unreadable.append(f"week for '{title}': {exc}")


def write_notes(cfg: Config, marks_notes: str, day: date) -> None:
    if not marks_notes.strip():
        return
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    (cfg.notes_dir / f"{day.isoformat()}.md").write_text(marks_notes.rstrip() + "\n", encoding="utf-8")


def ingest_today(cfg: Config, rm: Rmapi, store: TaskStore, asana, today: date,
                 report: IngestionReport, asana_tasks=None) -> bool:
    """Read marks off today's sheet before we regenerate over the top of it.

    Re-running on a day that already has a sheet is a refresh, not a new day:
    the same document gets replaced in place. Anything already ticked on it has
    to be captured first, or regenerating would quietly discard it.

    Returns True if a sheet was found and read, which tells the caller it is
    safe to overwrite rather than archive.
    """
    layout = cfg.out_dir / f"layout-{today.isoformat()}.json"
    if cfg.use_fixtures or not layout.exists() or not rm.available():
        return False
    doc = rm.find(cfg.remarkable_folder, sheet_name(today))
    if not doc:
        return False

    try:
        annotated = rm.download_annotated(doc, cfg.out_dir / "annotated")
        set_strip_dir(cfg.out_dir / "strips")
        marks = read_marks(annotated, layout, cfg.anthropic_api_key)
    except Exception as exc:  # noqa: BLE001 — never block the refresh
        log(cfg, f"warn: could not read today's sheet before replacing it: "
                 f"{type(exc).__name__}: {exc}")
        return False

    state_file = cfg.out_dir / f"synced-{today.isoformat()}.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    already = {t.lower() for t in state.get("added", [])}

    apply_marks(marks, store, asana, today, report, already_added=already,
                asana_tasks=asana_tasks)
    write_notes(cfg, marks.notes, today)

    state["added"] = sorted(already | {t.lower() for t in report.added})
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return True


# --- board: what the Projects page will be built from ------------------------
def inspect_board(cfg: Config, name: str) -> int:
    """Print the board's sections, custom fields, and a sample card.

    Custom field names are per workspace, so the Projects page discovers them
    rather than hard-coding. This shows what it found, which is the quickest way
    to see why a column is blank or a budget is not being picked up.
    """
    from .asana_client import AsanaClient, _money

    client = AsanaClient(cfg, date.today())
    try:
        found = client.find_project(name)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  Asana error: {type(exc).__name__}: {exc}\n")
        return 1
    if not found:
        print(f"\n  No board matching {name!r}.\n")
        try:
            projects = client.all_projects()
        except Exception as exc:  # noqa: BLE001
            print(f"  Could not list your projects either: {exc}\n")
            return 1
        if not projects:
            print("  This token cannot see any projects at all, which usually means")
            print("  it was created in a different Asana account. Make a new token at")
            print("  app.asana.com/0/my-apps while signed in as the right user.\n")
            return 1
        print(f"  {len(projects)} project(s) this token can see:\n")
        for _gid, pname, _ws in sorted(projects, key=lambda p: p[1].lower()):
            print(f"    {pname}")
        print("\n  Copy the exact name into .env as:")
        print("    ASANA_BOARD=<name>\n")
        print("  If the board you want is not listed, the token cannot reach it —")
        print("  open the board in Asana and check you are a member of it.\n")
        return 1

    gid, _ws = found
    print(f"\n  Board: {name}   (gid {gid})")

    try:
        sections = client.sections(gid)
        cards = client.board(gid)
    except Exception as exc:  # noqa: BLE001
        print(f"  Could not read the board: {type(exc).__name__}: {exc}\n")
        return 1

    print(f"\n  Sections ({len(sections)}), in board order:")
    counts: dict[str, int] = {}
    for c in cards:
        counts[c.section] = counts.get(c.section, 0) + 1
    for s in sections:
        print(f"    {s}  —  {counts.get(s, 0)} card(s)")
    stray = sorted(set(counts) - set(sections))
    for s in stray:
        print(f"    {s}  —  {counts[s]} card(s)   (not a board section)")

    labels: dict[str, str] = {}
    for c in cards:
        for label, value in c.fields:
            labels.setdefault(label, value)
    print(f"\n  Custom fields seen on cards ({len(labels)}):")
    for label, sample in labels.items():
        print(f"    {label:<28} e.g. {sample!r}")
    budgeted = [c for c in cards if c.budget is not None]
    print(f"\n  Read as budget: {len(budgeted)} of {len(cards)} cards")
    if not budgeted and labels:
        guess = [l for l in labels if _money(l)]
        print("    No numeric field matched a money-ish name.")
        print(f"    Candidates by name: {guess or 'none'}")
        print("    Tell me the real field name and I'll match it exactly.")

    if cards:
        c = cards[0]
        print(f"\n  Sample card:\n    name    {c.name}\n    section {c.section}")
        print(f"    budget  {c.budget_str() or '(none)'}")
        for label, value in c.fields:
            print(f"    {label:<7} {value}")
    print()
    return 0


# --- tablet: what is actually up there --------------------------------------
def tablet(cfg: Config) -> int:
    """List the sheets on the tablet, flagging which one sync will read.

    Worth its own command because "where did my ticks go" is the question this
    project raises most, and the answer is nearly always that a later generate
    moved the sheet to Archive.
    """
    rm = Rmapi(cfg)
    ok, reason = rm.status()
    if not ok:
        print(f"\n  {reason}\n")
        return 1

    today = date.today().isoformat()
    for folder in (cfg.remarkable_folder, cfg.archive_folder):
        entries = sorted(rm.ls(folder))
        print(f"\n  {folder}/")
        if not entries:
            print("    (empty)")
            continue
        for e in entries:
            mark = ""
            if e == sheet_name(date.today()):
                mark = "  <- sync reads this one" if folder == cfg.remarkable_folder else \
                       "  <- today's sheet, but ARCHIVED: sync will not read it"
            elif today in e:
                mark = "  <- today"
            print(f"    {e}{mark}")
    print()
    return 0


# --- sync: read today's marks back without re-rendering ---------------------
def sync(cfg: Config, day: date) -> int:
    """Read the marks on `day`'s sheet and push them, leaving the sheet in place.

    This is the read-back half of the daily loop on its own, so it can run on
    demand or on a short timer: tick a box, sync, and Asana has it. It never
    archives and never re-renders, so the sheet you are writing on stays put and
    marks you add later are picked up by the next sync.
    """
    layout = cfg.out_dir / f"layout-{day.isoformat()}.json"
    if not layout.exists():
        log(cfg, f"sync: no layout for {day.isoformat()} — nothing to read.")
        return 1

    rm = Rmapi(cfg)
    ok, reason = rm.status()
    if not ok:
        log(cfg, f"sync: {reason}")
        return 1

    name = sheet_name(day)
    doc = rm.find(cfg.remarkable_folder, name)
    if not doc:
        # Show what is actually there: a near-miss (different dash, trailing
        # space, mangled encoding) is invisible otherwise, and reads as "the
        # push never happened" when in fact the name simply doesn't match.
        have = rm.ls(cfg.remarkable_folder)
        log(cfg, f"sync: '{name}' not in {cfg.remarkable_folder}/ — nothing to read.")
        log(cfg, f"       looking for: {name!r}")
        if have:
            log(cfg, f"       folder has {len(have)} entr{'y' if len(have) == 1 else 'ies'}:")
            for entry in have:
                log(cfg, f"         {entry!r}")
        else:
            log(cfg, f"       folder is empty or unreadable — has a sheet been pushed?")
        return 1

    state_file = cfg.out_dir / f"synced-{day.isoformat()}.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    already = {t.lower() for t in state.get("added", [])}

    store = TaskStore(cfg.tasks_file)
    asana_tasks, asana_status, asana = load_asana(cfg, day)
    if not asana_status.ok:
        log(cfg, f"sync: warn: asana unavailable — {asana_status.error}")

    report = IngestionReport()
    try:
        annotated = rm.download_annotated(doc, cfg.out_dir / "annotated")
        set_strip_dir(cfg.out_dir / "strips")
        marks = read_marks(annotated, layout, cfg.anthropic_api_key)
    except Exception as exc:  # noqa: BLE001 — a bad read must not lose the sheet
        log(cfg, f"sync: read failed: {type(exc).__name__}: {exc}")
        return 1

    apply_marks(marks, store, asana, day, report, already_added=already,
                asana_tasks=asana_tasks)
    write_notes(cfg, marks.notes, day)
    store.save()

    state["added"] = sorted(already | {t.lower() for t in report.added})
    state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    log(cfg, f"sync {day.isoformat()}: {report.summary()}")
    return 0


# --- the run ----------------------------------------------------------------
def generate(cfg: Config, today: date) -> int:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    rm = Rmapi(cfg)
    store = TaskStore(cfg.tasks_file)

    # 4. fetch (Asana first — read-back needs the client to complete tasks)
    asana_tasks, asana_status, asana = load_asana(cfg, today)
    if not asana_status.ok:
        log(cfg, f"warn: asana unavailable — {asana_status.error}")

    # 1–3. yesterday
    report = ingest_yesterday(cfg, rm, store, asana, today, asana_tasks=asana_tasks)
    if report.note:
        log(cfg, f"read-back: {report.note}")

    # Same day, second run: this is a refresh of today's sheet, so capture
    # anything ticked on it before the replacement goes up.
    refreshing = ingest_today(cfg, rm, store, asana, today, report, asana_tasks=asana_tasks)
    if refreshing:
        log(cfg, "refreshing today's sheet — read its marks first")

    projects, project_sections, projects_status = load_board(cfg, asana, today)
    if not projects_status.ok:
        log(cfg, f"warn: project board unavailable — {projects_status.error}")

    monday = today - timedelta(days=today.weekday())
    events, events_status = load_events(cfg, monday - timedelta(days=7), monday + timedelta(days=14))
    if not events_status.ok:
        log(cfg, f"warn: calendar unavailable — {events_status.error}")

    # Asana may have changed under us during read-back; re-read cheaply from
    # what we already have by dropping the ones we just completed.
    done_gids = set(report.completed_asana)
    asana_tasks = [a for a in asana_tasks if a.gid not in done_gids]

    # 5. render
    strips = sorted((cfg.out_dir / "strips").glob("unreadable-*.png")) if (cfg.out_dir / "strips").exists() else []
    data = SheetData(
        today=today,
        events=events,
        events_status=events_status,
        asana_tasks=asana_tasks,
        asana_status=asana_status,
        projects=projects,
        project_sections=project_sections,
        projects_status=projects_status,
        report=report,
        unreadable_pngs=strips,
    )
    pdf, layout = render_sheet(data, cfg.out_dir)
    # keep the layout under a dated name so tomorrow's run can find it
    (cfg.out_dir / f"layout-{today.isoformat()}.json").write_text(
        layout.read_text(encoding="utf-8"), encoding="utf-8")
    store.save()

    # 6. push
    if cfg.dry_run:
        log(cfg, f"dry-run: not pushing. PDF at {pdf}")
    else:
        ok, reason = rm.status()
        if not ok:
            log(cfg, f"ERROR: {reason}")
            log(cfg, f"       sheet rendered at {pdf} but not pushed")
            return 1
        try:
            how = rm.upload(pdf, cfg.remarkable_folder, marks_already_read=refreshing)
            log(cfg, f"pushed to {cfg.remarkable_folder} — {how}")
        except RmapiError as exc:
            log(cfg, f"ERROR: push failed: {exc}")
            return 1

    # 7. summary
    log(cfg, f"{today.isoformat()}  {report.summary()}  ·  "
             f"{len(asana_tasks)} tasks · {len(projects)} projects · "
             f"{len(events)} events · {pdf.name}")
    return 0


# --- calibration helper (SCOPE §6.4) ---------------------------------------
def calibrate(pdf: Path, layout_path: Path) -> int:
    """Print ink ratios per region so thresholds can be set from a real page."""
    import json

    from .models import Region
    from .readback import CHECK_MIN, ink_ratio, page_images

    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    images = page_images(pdf, tuple(layout["page_size"]))
    rows = []
    for r in (Region.from_json(d) for d in layout["regions"]):
        if r.kind not in ("check", "priority") or r.page - 1 >= len(images):
            continue
        rows.append((ink_ratio(images[r.page - 1], r), r))
    rows.sort(reverse=True)
    print(f"{'ratio':>8}  {'kind':<9} {'page':>4}  id")
    for ratio, r in rows:
        flag = "  <- would count as marked" if ratio >= CHECK_MIN else ""
        print(f"{ratio:8.4f}  {r.kind:<9} {r.page:>4}  {r.id}{flag}")
    print(f"\ncurrent CHECK_MIN = {CHECK_MIN}. Set INK_CHECK_MIN in .env to override.")
    return 0


def _utf8_stdout() -> None:
    """Stop a legacy console codepage killing the run.

    Windows defaults stdout to cp1252, which cannot encode the arrows and
    dashes this tool prints -- and an unencodable character raises rather than
    degrading, so a cosmetic glyph takes down the whole command.

    Line buffering is set here too: piped into the window or the phone server,
    stdout would otherwise be block-buffered and the whole log would arrive in
    one lump at the end, which reads as a hung run.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    p = argparse.ArgumentParser(prog="daily_sheet", description="reMarkable daily sheet generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="run the daily loop")
    g.add_argument("--date", help="render for this date instead of today (YYYY-MM-DD)")
    g.add_argument("--fixtures", action="store_true", help="use fixture calendar/Asana data")
    g.add_argument("--dry-run", action="store_true", help="render but do not push to the tablet")

    c = sub.add_parser("calibrate", help="print ink ratios for an annotated sheet")
    c.add_argument("pdf", type=Path)
    c.add_argument("layout", type=Path)

    s = sub.add_parser("sync", help="read today's ticks and push them to Asana now")
    s.add_argument("--date", help="sync this date's sheet instead of today (YYYY-MM-DD)")

    b = sub.add_parser("board", help="show the Asana board the Projects page is built from")
    b.add_argument("--name", help="board name (default: ASANA_BOARD from .env)")

    sub.add_parser("tablet", help="list the sheets on the tablet")
    sub.add_parser("doctor", help="check every connection and report what's broken")

    args = p.parse_args(argv)

    if args.cmd == "calibrate":
        return calibrate(args.pdf, args.layout)

    if args.cmd == "doctor":
        from .doctor import doctor
        return doctor(load_config())

    if args.cmd == "tablet":
        return tablet(load_config())

    if args.cmd == "board":
        cfg = load_config()
        return inspect_board(cfg, args.name or cfg.asana_board)

    if args.cmd == "sync":
        cfg = load_config()
        day = date.fromisoformat(args.date) if args.date else date.today()
        try:
            return sync(cfg, day)
        except Exception:  # noqa: BLE001
            log(cfg, "FATAL:\n" + traceback.format_exc())
            return 1

    cfg = load_config(use_fixtures=args.fixtures, dry_run=args.dry_run)
    today = date.fromisoformat(args.date) if args.date else date.today()
    try:
        return generate(cfg, today)
    except Exception:  # noqa: BLE001
        log(cfg, "FATAL:\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
