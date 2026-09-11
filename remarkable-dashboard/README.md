# Daily Sheet for reMarkable 2

A generator, not an app. Every morning it reads yesterday's pen marks off the
tablet, updates a local task store, pulls calendar and Asana data, renders a
7-page PDF, pushes it to the reMarkable, and archives yesterday's sheet.

Interactivity comes from two things only: finger-tap links between pages, and
pen input read back on the next run. Full design in [SCOPE.md](SCOPE.md).

```
┌─ yesterday ──────────┐   ┌─ today ─────────────────────────────┐
│ tablet → annotated   │   │ ICS ─┐                              │
│   PDF → ink density  │──▶│ Asana┼─▶ render 7-page PDF ─▶ tablet│
│   → tasks.json/Asana │   │ tasks┘   + layout.json              │
└──────────────────────┘   └─────────────────────────────────────┘
```

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in as you go — see Setup below

# see it work with no credentials and no tablet:
python -m daily_sheet generate --fixtures --dry-run
open "out/Daily Sheet — $(date +%F).pdf"
```

That renders a complete sheet from the sample calendar and Asana data in
`fixtures/`. Sideload the PDF to your reMarkable by hand to check the one thing
tests can't: **do the nav-bar links respond to finger taps?** (SCOPE §6.3)

## Setup

Three credentials, all in `.env`, none of them needing IT approval.

### 1. Calendar — Outlook published ICS

Outlook Web → **Settings → Calendar → Shared calendars → Publish a calendar**.
Pick your calendar, permission **"Can view titles and locations"**, copy the
**ICS** link (not the HTML one) into `ICS_URL`.

The URL is a bearer credential — anyone holding it can read your calendar — so
it lives in `.env`, which is gitignored.

> If Publish is greyed out by tenant policy, fall back to Google Calendar's
> secret iCal address and accept the refresh lag (SCOPE §6.1).

### 2. Asana — personal access token

https://app.asana.com/0/my-apps → **Create new token** → paste into `ASANA_PAT`.

Reads tasks assigned to you across all your workspaces. The only write it ever
makes is marking a task complete.

### 3. Anthropic — handwriting transcription

An API key in `ANTHROPIC_API_KEY`. Used only for the free-text regions and for
priority digits that already tested positive for ink. Checkboxes never reach the
model — they are pure pixel counting.

### 4. reMarkable — rmapi

**macOS / Linux**

```bash
brew install rmapi          # or: go install github.com/ddvk/rmapi@latest
rmapi                       # pairs with a one-time code from my.remarkable.com
```

**Windows**

Grab the Windows build from the [rmapi releases page](https://github.com/ddvk/rmapi/releases),
unzip `rmapi.exe` somewhere stable (e.g. `C:\tools\rmapi.exe`), then point `.env`
at it — it doesn't need to be on PATH:

```ini
RMAPI_BIN=C:\tools\rmapi.exe
```

Pair it once by running it with no arguments:

```powershell
C:\tools\rmapi.exe
```

It asks for a one-time code from **https://my.remarkable.com/device/desktop/connect**.
The token it writes is per-user, so pair it as the same Windows user the
scheduled task runs as — otherwise the task authenticates as nobody and the
push fails.

Then verify it can do all four things this depends on, because rmapi has broken
across firmware updates before (SCOPE §6.2):

```bash
rmapi mkdir Daily
rmapi put "out/Daily Sheet — 2026-09-11.pdf" Daily
rmapi ls Daily
rmapi geta "Daily/Daily Sheet — 2026-09-11"     # after writing on it
rmapi mv "Daily/Daily Sheet — 2026-09-11" Daily/Archive
```

If `geta` fails, read-back won't work and the run will log it and still ship the
sheet — you just lose the pen loop until it's fixed.

## Daily use

```bash
python -m daily_sheet generate          # macOS / Linux
py -m daily_sheet generate              # Windows
```

Useful flags:

| Flag | What it does |
|---|---|
| `--fixtures` | sample calendar/Asana data, no network |
| `--dry-run` | render but don't push to the tablet |
| `--date 2026-09-11` | render for a specific day |

## Scheduling it

### Windows

```powershell
.\install-task.ps1              # daily at 08:00
.\install-task.ps1 -At 07:30    # re-run to change the time
.\install-task.ps1 -Remove
```

That registers a task called **reMarkable Daily Sheet** which calls `run.ps1`.
It runs only while you're logged on, so there's no stored password. If the
machine is asleep or off at the scheduled time the run isn't lost —
`StartWhenAvailable` fires it as soon as the machine is back, so a sheet still
lands before you pick the tablet up.

```powershell
Start-ScheduledTask -TaskName 'reMarkable Daily Sheet'   # run it now
Get-Content runs.log -Tail 20                            # what happened
```

A failed push exits non-zero, so a broken run shows up as **Last Run Result**
`0x1` in Task Scheduler rather than silently doing nothing.

### macOS / Linux

```cron
0 8 * * * cd /path/to/remarkable-dashboard && /usr/bin/python3 -m daily_sheet generate >> runs.log 2>&1
```

## The sheet

| Page | Content |
|---|---|
| 1 | **Today** — hour grid 07–20, top-3 priorities, yesterday's ingestion report |
| 2–4 | **Week** — last / this / next, with ‹ Prev and Next › tap zones |
| 5 | **Tasks** — checkbox, priority box, `carried Nd` tag, New tasks box |
| 6 | **Asana** — assigned to you, overdue first |
| 7 | **Notes** — blank ruled page |

Every page carries the same tappable nav bar. Task lists paginate rather than
shrink the type.

## How pen marks are read

Rendering writes a sidecar `out/layout-YYYY-MM-DD.json` recording the pixel box
of every checkbox, priority box and writing area. Next morning the annotated PDF
is rasterised to the same 1404×1872 grid and each region is measured.

A box counts as marked only if the ink is **both** dense enough *and* spans the
box — density alone can't tell a tick from a resting pen nib, which is a real
failure mode the tests pin down. Tune both thresholds against a real page:

```bash
python -m daily_sheet calibrate "annotated.pdf" out/layout-2026-09-11.json
```

It prints the ink ratio for every box, sorted, and marks which ones would count.
Set `INK_CHECK_MIN` / `INK_SPAN_MIN` in `.env` from what you see.

Then:

- ticked private task → `status: done`
- ticked Asana task → completed in Asana
- digit in the grey box → new priority 1–3
- **New tasks** box → transcribed, one task per line, `!!` = P1, `!` = P2
- **Notes** page → `notes/YYYY-MM-DD.md`
- anything unreadable comes back as an image strip on tomorrow's page 1

Nothing is ever deleted. Asana tasks are only completed, never edited.

## Failure policy

Any single source failing renders that section with an "unavailable" notice and
**still ships the sheet**. Only a tablet push failure is a hard error (exit 1).
A missing or unannotated sheet just logs and moves on.

## Tests

```bash
python -m pytest
```

21 tests, no network, no model calls. The round-trip tests render a real sheet,
draw pen ticks into it at the coordinates `layout.json` claims, read it back,
and assert the task store changed correctly — the whole product minus the
tablet.

## Not in v1

Room availability (no data path without Graph or IT approval), intra-day
refresh, editing task text by pen, writing to the work calendar. See SCOPE §7–8.
