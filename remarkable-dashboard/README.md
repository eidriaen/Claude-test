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

> If publishing is capped at "Can view when I'm busy", the feed carries no
> titles and no parsing recovers them — use **Calendar via Power Automate**
> below instead.

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
Check that page is showing the account you actually want before copying the code.

The token it writes is per-user, so pair it as the same Windows user the
scheduled task runs as — otherwise the task authenticates as nobody and the
push fails.

**If you pair the wrong account**, delete the token and start over:

```powershell
Remove-Item "$env:APPDATA\rmapi\rmapi.conf"     # Windows
rm ~/.rmapi                                      # macOS / Linux
```

Then revoke the device at my.remarkable.com → Settings → Devices. Deleting the
file stops your machine using the token; only revoking invalidates it.

> The Windows config path is `%APPDATA%\rmapi\rmapi.conf`, not `~/.rmapi` as on
> Unix. `rmapi.conf` holds live auth tokens — treat it like a password file.

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

## The window

Double-click **`Daily Sheet.bat`** (or `dashboard.pyw` directly). Five buttons:

| Button | What it does |
|---|---|
| **Sync + Generate Daily** | The whole cycle: reads your ticks, completes them in Asana, rebuilds today's sheet without them, pushes it back. One sheet per day — this replaces today's rather than adding another |
| **Sync only** | Pushes ticks to Asana without rebuilding the sheet |
| **What's on the tablet** | Lists `Daily/` and `Archive/`, flagging which sheet sync will read |
| **Check Asana board** | Shows the sections and custom fields the Projects page reads |
| **Check connections** | Tests rmapi, the calendar feed, Asana, and the API key |

**Update** in the bottom bar runs `git pull` in the project folder, so the
window can update itself without a terminal.

Right-click the `.bat` → **Send to → Desktop (create shortcut)** for an icon.

Everything below is the same thing from a terminal.

## The same buttons on your phone

`serve.py` is the window as a web page. It runs the same
`python -m daily_sheet …` commands and streams the same log back, so there is
still one implementation and two thin wrappers over it.

On the machine that owns the loop — the office mini PC, ideally, not a laptop
that travels:

```powershell
.\"Daily Sheet Server.bat"          # or: py serve.py
```

It prints the address and a link with the token in it:

```
Daily Sheet server on http://192.168.1.40:8080

On the phone, open:
    http://192.168.1.40:8080/?t=Xk8sP2mq…
```

Open that on the phone, then **Share → Add to Home Screen**. It opens
full-screen with an icon and remembers the token, so afterwards it is one tap
to `Sync + Generate Daily`. The token is stored on the phone only — it is
stripped from the address bar as soon as the page loads, so a screenshot of the
URL gives nothing away.

Set `WEB_TOKEN` in `.env` before relying on it. Leave it blank and the server
invents one at every restart, which locks the phone out after a reboot:

```powershell
py -c "import secrets; print(secrets.token_urlsafe(16))"
```

To have it come back by itself after a restart:

```powershell
.\install-task.ps1 -Server              # at logon, port 8080
.\install-task.ps1 -Server -Port 9000
```

### Reaching it from outside the office

**Do not port-forward it.** Pressing these buttons writes to Asana and pushes
to the tablet, so anything that can reach the server can do both. The token is
a lock on the cabinet, not a front door.

[Tailscale](https://tailscale.com) is the way in: free for personal use,
installed on the mini PC and the phone, and the mini PC gets a stable address
that works on the office wifi and on 5G alike without opening anything to the
internet. Use the Tailscale address in the phone's bookmark
(`http://minipc:8080`) and it keeps working from both.

### One machine, not two

If the laptop keeps its scheduled task as well, both machines will try to own
today's sheet — they race to archive and replace the same document, and a tick
read by one is invisible to the other. Pick the machine that stays on, run
`install-task.ps1` there, and run `install-task.ps1 -Remove` everywhere else.
The laptop keeps the window, which is safe: reading the tablet is the same
whoever asks.

> Design notes and what is deliberately left out: [NEXT.md](NEXT.md).

## Calendar via Power Automate

Use this when Outlook's publish setting is capped at **"Can view when I'm busy"**.
That strips titles at source — the feed genuinely does not contain them, so no
parsing recovers them. Power Automate reads the calendar through your own
delegated access instead, so it returns what you can see, and needs no admin
approval and no published link.

```
Power Automate (daily, before the sheet is built)
  Recurrence
  → Office 365 Outlook: Get calendar view of events (V3)
  → OneDrive for Business: Create file  →  calendar.json
        ↓ OneDrive syncs it to this machine
Daily Sheet
  reads the local file — no credential, no network call
```

### 1. Build the flow

At **make.powerautomate.com** → **Create** → **Scheduled cloud flow**.

| Step | Action | Settings |
|---|---|---|
| 1 | **Recurrence** | Every 1 day, 07:30, time zone **(UTC+01:00) Amsterdam, Berlin… ** |
| 2 | **Office 365 Outlook — Get calendar view of events (V3)** | Calendar: your own. Start: `addDays(utcNow(),-7)`. End: `addDays(utcNow(),14)`. Time zone: **(UTC+01:00) Amsterdam, Berlin…** |
| 3 | **OneDrive for Business — Create file** | Folder: `/Apps/DailySheet`. Name: `calendar.json`. Content: the **value** output of step 2 |

Set step 3 to overwrite (in newer designers, *Create file* replaces by default;
otherwise use **Update file**).

### 2. Point the sheet at the synced file

Find where OneDrive put it locally, then in `.env`:

```ini
CALENDAR_JSON=C:\Users\<you>\OneDrive - NPG\Apps\DailySheet\calendar.json
```

`CALENDAR_JSON` takes precedence over `ICS_URL` when both are set — the JSON
source exists because the ICS could not carry titles, so quietly falling back to
ICS would restore the problem it was added to solve. Leave `ICS_URL` in place or
remove it; it is ignored either way.

Press **Check connections**: the calendar row will read `calendar (Power
Automate JSON)` with a sample of real event titles.

### Shape of the file

Parsing is deliberately forgiving, so a hand-shaped flow still works. It accepts
a bare array or an object with `value` / `events` / `items`, and for each event
the usual spellings — `subject`/`title`/`name`, `start`/`startTime`,
`end`/`endTime`, `location` (string or `{displayName}`), `isAllDay`.

A timestamp carrying an offset is converted; a naive one is taken as Oslo local,
which is what the connector returns when the flow sets its time zone.

> If the file stops updating — the flow is turned off, OneDrive stops syncing —
> the calendar section reports how stale it is rather than rendering yesterday's
> schedule as if it were today's.

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
| 1 | **Today** — hour grid 08–18, and the ten tasks worth seeing first |
| 2–16 | **Week** — last week through thirteen ahead, with ‹ Prev / Next › and a This week jump |
| 5 | **Tasks** — Asana tasks assigned to you, High priority first, with H/M/L pickers and the New tasks box. Cards on the pipeline board are excluded — they appear on Projects |
| 6 | **Projects** — the pipeline board by section, with Active / Signed / Incoming totals |
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

65 tests, no network, no model calls. The round-trip tests render a real sheet,
draw pen ticks into it at the coordinates `layout.json` claims, read it back,
and assert the task store changed correctly — the whole product minus the
tablet.

## Not in v1

Room availability (no data path without Graph or IT approval), intra-day
refresh, editing task text by pen, writing to the work calendar. See SCOPE §7–8.
