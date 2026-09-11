# Trigger the sync from a phone

The design behind `serve.py`, and the parts of it still to do. Setup
instructions live in [README](README.md#the-same-buttons-on-your-phone); this is
the reasoning.

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

The window (`dashboard.pyw`) shells out to `python -m daily_sheet ...` and
streams the output. The server does exactly the same thing behind an HTTP
handler, so the two stay thin wrappers over one CLI rather than two
implementations that drift.

## Why a web page rather than a phone app

A page reaches every phone, needs no store, no signing, no install. Add it to
the home screen and it opens full-screen with an icon, which is as close to an
app as this needs to be. The buttons, the log pane and the colours carry over
from the window almost unchanged.

## Decisions made while building it

**Both the one-liner and the log.** The open question was which to show. The
card at the top carries the result — *Sync only at 08:14 — Completed 3* — and
the log sits under a collapsed `Log` toggle that opens itself while a run is
going. Reading nothing while a push is in flight is worse than reading too
much, and collapsing it costs nothing.

**The page says something before you tap.** It loads with today's sheet name
and when it was built, so the common question (*did this morning's run
happen?*) is answered without pressing anything.

**The token is never in a URL.** The page itself is not secret — it is a shell
that asks for the token and keeps it in the phone's storage. The `?t=` link the
server prints is a convenience for the first open, and the page strips it from
the address bar immediately. Every endpoint that does anything checks the token
with a constant-time compare.

**No token in `.env` means a generated one, not an open door.** The server
makes one up per restart and prints it. That keeps the first run one command
long without ever running unauthenticated — and `install-task.ps1 -Server`
warns, because a token nobody can read is the same as being locked out.

**The URL names a button, never a command line.** Actions are an allow-list;
arguments are fixed in the server. Nothing from the request reaches a shell.

**One run at a time.** Two overlapping `generate`s would race to archive and
replace the same document, so a second press gets a 409 and a plain message
rather than a queue.

**A dropped connection does not stop the run.** Phones lock mid-push. The
server keeps reading the process to the end and only the log is lost. The
stream's last line carries the exit code, so the page can tell "finished with
errors" from "the connection died" instead of guessing.

## What the mini PC needs

Everything the laptop has now, plus:

- **Left signed in.** The scheduled task runs only while a user is logged on,
  which is the trade for not storing a password. A mini PC that lives at the
  office and stays logged in suits this better than a laptop that travels.
- **rmapi paired as that user.** The token is per-user; pairing as someone else
  leaves the task authenticating as nobody.
- **OneDrive signed in and syncing**, or the calendar file never arrives.
- **Its own `.env`.** Same values as the laptop, copied by hand rather than
  committed — plus its own `WEB_TOKEN`.

Run both schedules there and take them off the laptop
(`install-task.ps1 -Remove`), or the two machines will both try to own today's
sheet.

## Still to do

- **Tailscale on the mini PC and the phone**, so the bookmark works off the
  office network. Nothing in the code depends on it; it is setup, once.
- **Try it from a locked phone on 5G.** Streaming over a long-lived HTTP
  response is the part most likely to behave differently there than on wifi.

## What is deliberately not in the plan

**Exposing this to the internet properly** — TLS, a real auth flow, a hostname.
That is a genuinely different project, and Tailscale avoids needing any of it.

**A notification when the sync fails.** Tempting, but the failure modes so far
have all been setup-time, not run-time. Worth revisiting after the mini PC has
run unattended for a few weeks and we know what actually breaks.

**Editing the sheet from the phone.** The phone presses buttons; the pen is
still the only way anything gets marked. Adding a second input path would mean
two sources of truth for the same tick.
