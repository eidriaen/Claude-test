# Next: trigger the sync from a phone

The plan for running this on an always-on machine and driving it from a phone,
written down while it is fresh. Nothing here is built yet.

## The shape

```
mini PC at the office                     phone
  scheduled task, every 15 min      ┌─ browser, added to home screen
  → daily_sheet sync                │    the same buttons as the window
  08:00 → daily_sheet generate      │
                                    ▼
  serve.py  ── small HTTP server ── tap "Sync now"
      runs the same CLI, streams the log back
```

The window (`dashboard.pyw`) already shells out to `python -m daily_sheet ...`
and streams the output. A web version does exactly the same thing behind an
HTTP handler, so the two stay thin wrappers over one CLI rather than two
implementations that drift.

## Why a web page rather than a phone app

A page reaches every phone, needs no store, no signing, no install. Add it to
the home screen and it opens full-screen with an icon, which is as close to an
app as this needs to be. The buttons, the log pane and the colours carry over
from the window almost unchanged.

## Getting to it from outside the office

The server should **not** be port-forwarded to the internet. It triggers Asana
writes and tablet pushes, and anything that can reach it can do both.

**Tailscale** is the answer worth trying first: free for personal use, installs
on the mini PC and the phone, and gives the mini PC a stable address reachable
from anywhere without opening anything to the public internet. The phone sees
`http://minipc:8080` on the office network and on 5G alike.

Add a shared token in `.env` regardless, checked on every request — Tailscale
is the lock on the door, the token is the lock on the cabinet.

## What the mini PC needs

Everything the laptop has now, plus:

- **Left signed in.** The scheduled task runs only while a user is logged on,
  which is the trade for not storing a password. A mini PC that lives at the
  office and stays logged in suits this better than a laptop that travels.
- **rmapi paired as that user.** The token is per-user; pairing as someone else
  leaves the task authenticating as nobody.
- **OneDrive signed in and syncing**, or the calendar file never arrives.
- **Its own `.env`.** Same values as the laptop, copied by hand rather than
  committed.

Run both schedules there and take them off the laptop, or the two machines will
both try to own today's sheet.

## Open questions to settle when building

- **One machine or two?** If the laptop keeps its scheduled task, two machines
  race to archive and replace the same document. Simplest is for the mini PC to
  own the loop and the laptop to keep only the window, pointed at the mini PC.
- **Does the phone need the log?** Streaming it is easy and reassuring, but a
  single "done / failed, 3 completed" line may read better on a small screen.
- **Anything worth showing without a tap?** The phone could show what is on
  today's sheet, or the last run's result, on load. Useful, but it is a second
  thing to build and design — worth doing only if the buttons alone feel thin.

## What is deliberately not in the plan

**Exposing this to the internet properly** — TLS, a real auth flow, a hostname.
That is a genuinely different project, and Tailscale avoids needing any of it.

**A notification when the sync fails.** Tempting, but the failure modes so far
have all been setup-time, not run-time. Worth revisiting after the mini PC has
run unattended for a few weeks and we know what actually breaks.
