"""The `generate` command — one run of the daily loop (SCOPE §3).

    python -m daily_sheet generate                 # the real thing
    python -m daily_sheet generate --fixtures --dry-run
    python -m daily_sheet calibrate <annotated.pdf> out/layout.json

Failure policy: any single source failing renders that section with an
"unavailable" notice and still ships the sheet. Only a tablet push failure is
a hard error (exit 1).
"""
from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

from .asana_client import load_asana
from .calendar_ics import load_events
from .config import Config, load_config
from .models import IngestionReport, SectionStatus
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
def ingest_yesterday(cfg: Config, rm: Rmapi, store: TaskStore, asana, today: date) -> IngestionReport:
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

    apply_marks(marks, store, asana, today, report)
    write_notes(cfg, marks.notes, yesterday)

    try:
        rm.move(doc, cfg.archive_folder)
    except RmapiError as exc:
        log(cfg, f"warn: archive failed: {exc}")
    return report


def apply_marks(marks: Marks, store: TaskStore, asana, today: date, report: IngestionReport) -> None:
    """Turn pen marks into task-store and Asana changes. Nothing is destructive."""
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

    for line in marks.new_tasks:
        parsed = parse_pen_line(line)
        if parsed:
            title, prio = parsed
            store.add(title, prio, source="pen", today=today)
            report.added.append(title)

    report.unreadable.extend(str(p) for p in marks.unreadable)


def write_notes(cfg: Config, marks_notes: str, day: date) -> None:
    if not marks_notes.strip():
        return
    cfg.notes_dir.mkdir(parents=True, exist_ok=True)
    (cfg.notes_dir / f"{day.isoformat()}.md").write_text(marks_notes.rstrip() + "\n", encoding="utf-8")


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
    report = ingest_yesterday(cfg, rm, store, asana, today)
    if report.note:
        log(cfg, f"read-back: {report.note}")

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
        private_tasks=store.open_tasks(),
        asana_tasks=asana_tasks,
        asana_status=asana_status,
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
            how = rm.upload(pdf, cfg.remarkable_folder)
            log(cfg, f"pushed to {cfg.remarkable_folder} — {how}")
        except RmapiError as exc:
            log(cfg, f"ERROR: push failed: {exc}")
            return 1

    # 7. summary
    log(cfg, f"{today.isoformat()}  {report.summary()}  ·  "
             f"{len(data.private_tasks)} open · {len(asana_tasks)} asana · "
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
        flag = "  ← would count as marked" if ratio >= CHECK_MIN else ""
        print(f"{ratio:8.4f}  {r.kind:<9} {r.page:>4}  {r.id}{flag}")
    print(f"\ncurrent CHECK_MIN = {CHECK_MIN}. Set INK_CHECK_MIN in .env to override.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="daily_sheet", description="reMarkable daily sheet generator")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="run the daily loop")
    g.add_argument("--date", help="render for this date instead of today (YYYY-MM-DD)")
    g.add_argument("--fixtures", action="store_true", help="use fixture calendar/Asana data")
    g.add_argument("--dry-run", action="store_true", help="render but do not push to the tablet")

    c = sub.add_parser("calibrate", help="print ink ratios for an annotated sheet")
    c.add_argument("pdf", type=Path)
    c.add_argument("layout", type=Path)

    sub.add_parser("doctor", help="check every connection and report what's broken")

    args = p.parse_args(argv)

    if args.cmd == "calibrate":
        return calibrate(args.pdf, args.layout)

    if args.cmd == "doctor":
        from .doctor import doctor
        return doctor(load_config())

    cfg = load_config(use_fixtures=args.fixtures, dry_run=args.dry_run)
    today = date.fromisoformat(args.date) if args.date else date.today()
    try:
        return generate(cfg, today)
    except Exception:  # noqa: BLE001
        log(cfg, "FATAL:\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
