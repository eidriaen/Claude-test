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
rmapi mv "Daily/Daily Sheet — 2026-09-11" "Daily/Archived dailies"
```

If `geta` fails, read-back won't work and the run will log it and still ship the
sheet — you just lose the pen loop until it's fixed.

## The window

Double-click **`Daily Sheet.bat`** (or `dashboard.pyw` directly). Five buttons:

| Button | What it does |
|---|---|
| **Sync + Generate Daily** | The whole cycle: reads your ticks, completes them in Asana, rebuilds today's sheet without them, pushes it back. One sheet per day — this replaces today's rather than adding another |
| **Sync only** | Pushes ticks to Asana without rebuilding the sheet |
| **What's on the tablet** | Lists `Daily/` and `Daily/Archived dailies/`, flagging which sheet sync will read |
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

Open that on the phone. To install it, tap **Home screen** at the bottom of the
page *first*, then **Share → Add to Home Screen** — that route carries the
token into the installed app, which otherwise gets its own storage on iOS and
asks for the token all over again.

It opens full-screen with an icon, so afterwards it is one tap to
`Sync + Generate Daily`. On a normal open the token is stripped from the
address bar, so a screenshot of the URL gives nothing away.

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

## Setting up the machine that runs it

### What to buy

| | |
|---|---|
| **Windows 11 Pro** | not Home — Home cannot *host* Remote Desktop, and that is how you sign into OneDrive on a machine with no monitor |
| **16 GB RAM, NVMe SSD** | the job is seconds of Python every quarter of an hour; what makes a cheap mini PC feel slow is 8 GB and eMMC storage, not Windows |
| **Wired ethernet** | one less thing to reconnect after a power cut |

There is no terminal-only Windows that works here. Server Core and the IoT
builds drop the desktop shell, and OneDrive's sync client needs it — and
OneDrive is the only reason this wants Windows at all. Going properly headless
means rebuilding the calendar path, which is the thing the mini PC exists to
avoid.

### Installing

Four steps, and only one of them is yours to think about.

1. **Put two files in a folder** on the new machine — a USB stick is fine:
   [`Setup.bat`](Setup.bat), and **the `.env` from a machine that already
   works**. (No `.env` yet? Skip it; you will be asked to fill one in.)
2. **Double-click `Setup.bat`.** It asks Windows for administrator rights
   itself, downloads everything else, and offers to pair rmapi when it gets
   there.
3. **Fill in anything it says is missing** — it lists exactly which keys.
4. **Run it**: `py -m daily_sheet generate`, or press the button.

The `.env` next to `Setup.bat` is copied in, and nothing in it is overwritten:
your Asana token, API key and phone token come across as they are. What the
machine can work out for itself it fills in — where rmapi ended up, a
`WEB_TOKEN` if you have none, and where OneDrive put `calendar.json` on *this*
machine rather than the one the file came from.

It finishes by running `doctor`, so the last thing on screen is the state of
every connection rather than a claim that it worked.

Or, if you prefer a line in PowerShell:

```powershell
irm https://raw.githubusercontent.com/eidriaen/Claude-test/ReMarkable-dashboard/remarkable-dashboard/setup.ps1 -OutFile "$env:PUBLIC\setup.ps1"; & "$env:PUBLIC\setup.ps1"
```

Roughly ten minutes later the machine has:

| | |
|---|---|
| **Python 3.12 + the packages** | reportlab, Pillow, numpy, pypdfium2 |
| **Git + the project** | cloned to `C:\Claude-test`, on this branch |
| **rmapi** | the right build for the architecture, in `C:\tools` |
| **Tailscale** | installed, waiting for `tailscale up` |
| **Scheduled tasks** | sheet at 08:00, ticks read every 15 min, phone server at logon |
| **SSH and Remote Desktop** | on, so it never needs a keyboard again |
| **Claude Code** | with Node, so changes get made *on* the machine over SSH |
| **`.env`** | created from the template with a fixed `WEB_TOKEN` |
| **Power** | never sleeps on mains |

Options: `-Root D:\somewhere`, `-At 07:30`, `-SyncEvery 10`, `-Port 9000`,
`-NoTailscale`, `-NoRdp`, `-NoSsh`, `-NoTasks`, `-NoClaudeCode`. Pass them
straight to the `.bat` — `Setup.bat -At 07:30` works.

Re-running it is safe: it pulls the latest code, leaves an existing `.env`
alone, and re-registers the tasks. A step that fails is listed at the end
rather than stopping the ones after it.

### The part no script can do

Four things need a human once, and the script prints them as a checklist:

1. **Paste your secrets into `.env`** — `ASANA_PAT`, `CALENDAR_JSON`,
   `ANTHROPIC_API_KEY`. Fine over SSH.
2. **Pair rmapi** — `C:\tools\rmapi.exe`, then a code from
   [my.remarkable.com](https://my.remarkable.com/device/desktop/connect). Fine
   over SSH. Check the page shows the right account before copying the code.
3. **Sign into OneDrive** — needs a screen, so use Remote Desktop or plug a
   monitor in once. Without it the calendar file never arrives.
4. **Turn on automatic logon** — scheduled tasks and OneDrive both need a
   logged-on session, and a headless machine has none after a power cut. Use
   [Sysinternals Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon),
   which keeps the password in LSA rather than in the registry in clear text.

### Getting onto it

From the laptop, double-click **`Connect to Mini PC.bat`**. It asks for the
address once, remembers it, and drops you into a PowerShell prompt on the mini
PC. Windows has had an SSH client built in since Windows 10 1809, so there is
usually nothing to install — and if the feature is off, the script turns it on.

```powershell
.\connect.ps1                                  # the saved machine
.\connect.ps1 -Target minipc -User adrian      # a different one
.\connect.ps1 -Command "py -m daily_sheet doctor"   # one command, then out
.\connect.ps1 -Forget                          # ask me again next time
```

The address lives in `%LOCALAPPDATA%\DailySheet\minipc.txt`, not in the repo.
No password is ever stored — SSH asks, or uses your key.

Once you are on:

```powershell
cd C:\Claude-test\remarkable-dashboard
py -m daily_sheet doctor      # is everything connected?
claude                        # change something, with the files right there
```

From a phone, any SSH app does the same — [Termius](https://termius.com) and
Blink are the usual ones on iOS.

### Getting to it from outside

Use **Tailscale**. `tailscale up` on the mini PC and the app on your phone, and
both the page and SSH work from anywhere, over an encrypted link, with nothing
exposed.

**Don't port-forward this to the internet.** Two reasons, and the second is the
one that matters: the machine holds your Asana token, your reMarkable
credentials and an SSH server; and `serve.py` speaks plain HTTP, so a forwarded
port sends your access token across the internet in clear text on every tap.
Anyone on the path can read it and then push to your tablet and write to your
Asana. Making that safe means a real hostname, a certificate and a reverse
proxy in front — a different project, and Tailscale removes the need for all of
it.

## Running it on a cloud server

For when no machine at home or in the office is reliably on. Everything here
runs on Linux — reportlab renders the sheet, rmapi has a Linux build and pairs
headlessly, Asana and Anthropic are plain HTTPS.

```bash
git clone -b ReMarkable-dashboard https://github.com/eidriaen/Claude-test.git
cd Claude-test/remarkable-dashboard
sudo ./deploy/install.sh
```

That installs to `/opt/daily-sheet` with its own virtualenv, sets the machine's
timezone to Europe/Oslo, and registers three systemd units:

| Unit | What |
|---|---|
| `daily-sheet-generate.timer` | 08:00 daily, `Persistent=true` so a reboot at 07:59 does not cost you the day's sheet |
| `daily-sheet-sync.timer` | every 15 minutes, reads ticks into Asana |
| `daily-sheet-server.service` | the phone page on :8080, restarted if it dies |

The timezone step is not cosmetic: `OnCalendar` follows the system clock, and a
cloud host is UTC by default — an 08:00 timer would fire at 10:00 Oslo in
summer, which is a schedule that still looks plausible.

```bash
systemctl list-timers 'daily-sheet*'
journalctl -u daily-sheet-generate -n 50
sudo -u $USER /opt/daily-sheet/.venv/bin/python -m daily_sheet doctor
```

### The calendar is the hard part

The calendar arrives through OneDrive's **desktop sync client**, which needs a
Windows desktop session. A cloud box has none, so something has to relay
`calendar.json` out of Power Automate to somewhere the server can fetch:

| Route | Needs admin or premium? | Catch |
|---|---|---|
| **Dropbox / Google Drive connector** — a second *Create file* in the same flow | No, both are standard | A DLP policy may block business and personal connectors in one flow; then use a second flow |
| **OneDrive share link** the server downloads | No | The tenant likely blocks anonymous links — the same policy that caps ICS at free/busy |
| **HTTP POST to the server** | Premium connector | Cleanest if you have it |
| **Microsoft Graph** | Admin consent | The proper answer, and the wall this hit in the first place |

Try Dropbox first: one extra action, and a personal API token takes two minutes
with no admin anywhere.

### Before you do this

A cloud server puts your Asana token, reMarkable credentials, Anthropic key and
a copy of your work calendar on a rented machine on the public internet.
Everything today lives on hardware you hold. That is a real change, and worth
deciding deliberately rather than by drift.

Keep `serve.py` off the open internet regardless — it speaks plain HTTP, so a
forwarded port puts the access token on the wire in clear text. Install
Tailscale on the server and reach it that way, exactly as with the mini PC.

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

One sheet per day. Re-running on the same day refreshes that one document —
your ticks are read off it first, so nothing written is lost. When the date
rolls over, yesterday's sheet is **moved** into `Daily/Archived dailies`, never
deleted, and a sheet that cannot be archived is never replaced: the push fails
and says why instead. Change the folder name with `REMARKABLE_ARCHIVE` in
`.env`.

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
