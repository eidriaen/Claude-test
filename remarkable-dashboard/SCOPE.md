# Daily Sheet for reMarkable 2 — Scope (v1)

A generator, not an app. Every morning it reads yesterday's pen marks off the tablet, updates a local task store, pulls calendar and Asana data, renders a multi-page PDF, pushes it to the reMarkable, and archives yesterday's sheet. Interactivity comes from two things only: finger-tap hyperlinks between pages, and pen input read back on the next run.

## 1. Constraints and decisions

| Area | Decision |
|---|---|
| Device | reMarkable 2 — 1404 × 1872 px, 3:4, 226 ppi. All layout targets this exactly. |
| Tablet I/O | reMarkable cloud API (Connect subscription present) via an open-source tool — rmapi or a `remarkable-mcp` server in cloud mode. Same tool handles upload, annotation read-back, and moving files. No Folio, no remarkdown. |
| Work calendar | Outlook **published ICS feed** ("titles and locations" level). Fetched directly, read-only. No Microsoft Graph, no M365 connector, nothing requiring IT approval. |
| Asana | Personal access token. Read tasks assigned to me; write only to mark complete. |
| Private tasks | Local `tasks.json` in the repo, git-tracked. |
| Runtime | Single `generate` command. Cron at 07:00 local (Oslo) plus manual run. |
| Secrets | Three values in a gitignored `.env`: ICS URL, Asana PAT, reMarkable device token. The ICS URL is treated as a secret. |
| Rendering | PDF with internal link annotations. E-ink-friendly: high contrast, no greys lighter than ~40%, tap targets ≥ 44 px, one clean typographic system. |

## 2. Document

One PDF per day: `Daily Sheet — YYYY-MM-DD`, pushed to a `Daily/` folder on the tablet. Every page carries the same tappable nav bar: **Today · Week · Tasks · Asana · Notes**.

| # | Page | Content |
|---|---|---|
| 1 | Today | Date, week number, day-of-year. Hour grid 07–20 with events from ICS. Top-3 priorities across both task lists. **Ingestion report** block (see §4). |
| 2–4 | Week | Three pages: last / this / next week. Seven columns, events as blocks. "‹ Prev" and "Next ›" link zones top corners. Weekend columns narrower. |
| 5 | Tasks | Open private tasks sorted by priority then age. Per row: checkbox, priority digit box, title, `carried Nd` tag if open > 3 days. Lined **New tasks** box at the bottom (min 6 lines). |
| 6 | Asana | Incomplete tasks assigned to me, sorted by due date, overdue first. Per row: checkbox, title, project, due. |
| 7 | Notes | Blank lined page. |

If a task list overflows, paginate (Tasks 2/2) rather than shrink type. Nav bar links go to page 1 of each section.

## 3. Daily loop

1. **Locate yesterday's sheet** on the tablet (`Daily/Daily Sheet — <yesterday>`). If missing or unannotated, log it and skip to step 4.
2. **Read back** annotations (see §4). Update `tasks.json`; complete Asana tasks that were ticked.
3. **Archive** yesterday's sheet to `Daily/Archive/`.
4. **Fetch**: ICS feed (last, this, next week), Asana tasks assigned to me.
5. **Render** today's PDF plus a sidecar `layout.json` recording the coordinates and IDs of every checkbox, priority box, and input region.
6. **Push** to `Daily/`.
7. Log a one-line summary to stdout and `runs.log`.

Failure policy: any single source failing (ICS down, Asana 401) renders that section with an "unavailable" notice and still ships the sheet. Only a tablet-push failure is a hard error.

## 4. Read-back rules

- **Checkboxes and priority boxes are read by ink density**, not OCR. The reader renders yesterday's annotated page to an image, looks inside each region from `layout.json`, and treats ink above a threshold as a mark. Deterministic and cheap.
- Each checkbox maps to a **stable task ID** (private task ID or Asana GID) via `layout.json`.
- Ticked private task → `status: done`, `completed: <date>`.
- Ticked Asana task → mark complete in Asana.
- **Priority box** with a written digit → OCR that region only; set priority 1–3. Anything else ignored.
- **New tasks box** → region image sent to Claude for handwriting transcription. One task per line. Trailing `!!` = P1, `!` = P2, default P3. Empty lines ignored. Source recorded as `pen`.
- **Notes page** → full page transcribed to `notes/YYYY-MM-DD.md`. No further parsing.
- **Ingestion report** on today's page 1: `Completed 3 · Added 2 (Call dentist, Book car) · Unreadable 1`. Unreadable lines are re-rendered verbatim as images in a small "couldn't read" strip so they can be rewritten.
- No destructive actions: nothing is ever deleted from the task store; Asana tasks are only completed, never edited or removed.

## 5. Task store

`tasks.json` — array of:

```
id          stable short id
title       string
priority    1 | 2 | 3
status      open | done
source      pen | manual | carried
created     YYYY-MM-DD
completed   YYYY-MM-DD | null
notes       free text, optional
```

Rules: `carried` is derived at render time (open > 3 days), not stored. Manual edits to the file are allowed and expected in v1.

## 6. Verify in the first hour

1. Outlook → Settings → Calendar → Shared calendars → Publish is available and the ICS URL returns events. If greyed out, fall back to the Google Calendar secret iCal address (accept refresh lag).
2. Chosen reMarkable tool can, on current rM2 firmware: upload a PDF to a folder, list a folder, download a document's annotations, render an annotated page to PNG, move a document. This has broken across firmware updates before — test before building on it.
3. Internal PDF links register as finger taps on the device (and are ignored by the pen — expected).
4. Ink-density thresholds on a real ticked page vs. an untouched one.

## 7. Out of scope (v1)

- **Room availability** — no data path without Graph or IT approval. Candidates for v2: Power Automate flow writing free/busy to OneDrive; browser automation of Outlook Room Finder for on-demand use.
- Live or intra-day refresh; the sheet is a 07:00 snapshot with manual re-run.
- Editing existing task text by pen.
- Writing to the work calendar.
- Multiple devices, Kindle, other page sizes.
- Any layout beyond the one described here.

## 8. v2 candidates

Rooms page (once a data path exists) · handwritten event capture ("14:00 dentist" on Today → private calendar) · weekly review page generated Sunday evening · per-project Asana filters · Copilot-free email triage list if a safe source appears.
