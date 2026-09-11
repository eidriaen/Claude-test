"""The same buttons, on your phone.

A small HTTP server that runs the same `python -m daily_sheet ...` commands the
window (`dashboard.pyw`) runs, and streams their output back to a browser. The
CLI stays the single implementation -- this is a wrapper, exactly as the window
is, so the two cannot drift apart.

    python serve.py                 # prints the address to open

Run it on whichever machine owns the loop (the mini PC), reach it over
Tailscale, and add the page to your phone's home screen. README has the setup.

Not for the public internet: pressing these buttons writes to Asana and pushes
to the tablet. The token is checked on every request that does anything, but a
token is a lock on a cabinet, not a front door.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent

TOKEN_HEADER = "X-Daily-Token"
# Ends the streamed body. A run that dies mid-flight simply stops arriving, and
# without a marker the page could not tell that from a run that finished
# quietly -- so the last line always says how it ended.
EXIT_MARK = "\x1e"


@dataclass(frozen=True)
class Action:
    label: str
    args: list[str]
    hint: str


# The window's buttons, in the same order and with the same words.
ACTIONS: dict[str, Action] = {
    "generate": Action(
        "Sync + Generate Daily", ["generate"],
        "Reads your ticks, completes them in Asana, rebuilds today's sheet "
        "without them, pushes it back",
    ),
    "sync": Action(
        "Sync only", ["sync"],
        "Pushes ticks to Asana without touching the sheet",
    ),
    "tablet": Action(
        "What's on the tablet", ["tablet"],
        "Lists Daily/ and Archive/ — where your ticks live",
    ),
    "board": Action(
        "Check Asana board", ["board"],
        "Shows the sections and fields the Projects page reads",
    ),
    "doctor": Action(
        "Check connections", ["doctor"],
        "Tests rmapi, calendar, Asana and the API key",
    ),
}


def python_exe() -> list[str]:
    """The interpreter to run daily_sheet with.

    Same trick as the window: under pythonw.exe a child would be a GUI-subsystem
    process whose stdout we cannot read, so swap back to the console build.
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return [str(console)]
    return [str(exe)]


def load_token() -> tuple[str, bool]:
    """(token, was_generated).

    A missing WEB_TOKEN generates one for this session rather than starting
    unauthenticated -- the server triggers Asana writes and tablet pushes, so
    "no token yet" must not mean "no lock".
    """
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except ImportError:
        pass
    tok = os.getenv("WEB_TOKEN", "").strip() or env_file_value("WEB_TOKEN")
    return (tok, False) if tok else (secrets.token_urlsafe(12), True)


def env_file_value(key: str, path: Path | None = None) -> str:
    """Read one value straight out of .env, without python-dotenv.

    Nothing else in this file needs a package from requirements.txt, so the
    server has to run on a machine where `pip install` failed. Leaning on
    dotenv alone meant a missing library silently ignored the token in .env and
    generated a fresh one every restart -- which looks exactly like "the token
    is wrong" from the phone, with nothing on screen to say otherwise.
    """
    try:
        text = (path or HERE / ".env").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip().removeprefix("export ").strip() != key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value.strip()
    return ""


def lan_address() -> str:
    """This machine's address on the network, for the URL we print.

    Opens a UDP socket to a routable address -- nothing is sent, it just makes
    the OS pick the interface it would route out of, which is the one the phone
    can reach. Falls back to the hostname.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostname()
    finally:
        s.close()


class Busy(Exception):
    """Something is already running."""


class Runner:
    """One run at a time, as in the window.

    Two overlapping `generate`s would race to archive and replace the same
    document on the tablet, so a second press waits for the first rather than
    doubling up.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = ""
        self.last: dict = {}

    @property
    def busy(self) -> bool:
        return bool(self.current)

    def stream(self, key: str):
        """Yield the run's output line by line, ending with the exit marker."""
        action = ACTIONS[key]
        if not self._lock.acquire(blocking=False):
            raise Busy(self.current or "another run")
        self.current = action.label
        started = datetime.now()
        tail: list[str] = []
        code = 1
        try:
            cmd = python_exe() + ["-m", "daily_sheet", *action.args]
            try:
                p = subprocess.Popen(
                    cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
            except OSError as exc:
                yield f"Could not run the command: {exc}\n"
            else:
                for line in p.stdout:                      # type: ignore[union-attr]
                    if line.strip():
                        tail.append(line.strip())
                        del tail[:-6]
                    yield line
                code = p.wait()
            self.last = {
                "action": key,
                "label": action.label,
                "at": started.strftime("%H:%M"),
                "ok": code == 0,
                "seconds": round((datetime.now() - started).total_seconds(), 1),
                "summary": summarise(tail, code == 0),
            }
            yield f"{EXIT_MARK}{code}\n"
        finally:
            self.current = ""
            self._lock.release()


def summarise(tail: list[str], ok: bool) -> str:
    """One line for the phone, picked from the last few lines of output.

    Prefers the CLI's own summary line over the literal last line, which is
    often a trailing detail rather than the result.
    """
    for line in reversed(tail):
        text = strip_stamp(line)
        if any(w in text for w in ("Completed", "pushed", "Pushed", "[FAIL]", "[ok]")):
            return text[:120]
    if tail:
        return strip_stamp(tail[-1])[:120]
    return "done" if ok else "failed"


def strip_stamp(line: str) -> str:
    """Drop the ISO timestamp the CLI prefixes; the page shows live output."""
    if len(line) > 20 and line[4] == "-" and line[10] == "T" and "  " in line:
        return line.split("  ", 1)[1]
    return line


def sheet_status() -> dict:
    """What is sitting in out/ for today, so the page says something on load."""
    today = date.today().isoformat()
    hits = sorted((HERE / "out").glob(f"*{today}*.pdf"))
    if not hits:
        return {"sheet": "", "built": "", "today": today}
    newest = max(hits, key=lambda p: p.stat().st_mtime)
    built = datetime.fromtimestamp(newest.stat().st_mtime)
    return {"sheet": newest.name, "built": built.strftime("%H:%M"), "today": today}


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0: the connection closes at the end of the body, which is how the
    # browser knows a streamed run is over. The exit marker is what tells it
    # whether the run actually finished.
    protocol_version = "HTTP/1.0"
    server_version = "DailySheet"

    token = ""
    token_source = "env"          # env | generated
    started = ""
    runner = Runner()

    # -- plumbing -------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:        # noqa: A003
        if self.server.quiet:                              # type: ignore[attr-defined]
            return
        sys.stderr.write(f"{self.address_string()}  {fmt % args}\n")

    def authorised(self) -> bool:
        # Trimmed: a token pasted on a phone arrives with a trailing space often
        # enough that not trimming reads as "the token is wrong".
        given = self.headers.get(TOKEN_HEADER, "").strip()
        return bool(given) and hmac.compare_digest(given, self.token)

    def send(self, status: HTTPStatus, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: HTTPStatus, payload: dict) -> None:
        self.send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    # -- routes ---------------------------------------------------------
    def do_GET(self) -> None:                              # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            # The page itself holds nothing secret -- it is a shell that asks
            # for the token and stores it, so a home-screen icon never has to
            # carry the token in its URL.
            self.send(HTTPStatus.OK, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/status":
            if not self.authorised():
                self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "bad token"})
                return
            self.send_json(HTTPStatus.OK, {
                "sheet": sheet_status(),
                "last": self.runner.last,
                "running": self.runner.current,
                "host": socket.gethostname(),
                "now": datetime.now().strftime("%H:%M"),
            })
        elif path == "/hello":
            # Deliberately unauthenticated, and deliberately says nothing about
            # the token's value. It exists so a phone that is being refused can
            # tell "I have a stale token" from "the server is minting a new one
            # every restart" -- from the phone, those looked identical.
            self.send_json(HTTPStatus.OK, {
                "token_source": self.token_source,
                "started": self.started,
                "host": socket.gethostname(),
            })
        elif path == "/manifest.webmanifest":
            self.send(HTTPStatus.OK, MANIFEST.encode(), "application/manifest+json")
        elif path == "/icon.svg":
            self.send(HTTPStatus.OK, ICON.encode(), "image/svg+xml")
        elif path == "/favicon.ico":
            self.send(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "no such page"})

    def do_POST(self) -> None:                             # noqa: N802
        # urlsplit, not urlparse: urlparse strips ";params" off the last path
        # segment, so "/run/generate;anything" would resolve to a real action.
        path = urlsplit(self.path).path
        if not path.startswith("/run/"):
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "no such page"})
            return
        if not self.authorised():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "bad token"})
            return

        key = path[len("/run/"):]
        # An allow-list, not a pass-through: the request names a button, never
        # a command line.
        if key not in ACTIONS:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": f"unknown action {key!r}"})
            return

        try:
            stream = self.runner.stream(key)
            first = next(stream)
        except Busy as exc:
            self.send_json(HTTPStatus.CONFLICT, {"error": f"{exc} is still running"})
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            for line in (first, *stream):
                self.wfile.write(line.encode("utf-8", "replace"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The phone locked or the page was closed. The run keeps going --
            # stopping half way through a push is worse than losing the log.
            for _ in stream:
                pass


ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="12" fill="#1c1c1a"/>'
    '<rect x="14" y="12" width="36" height="40" rx="3" fill="#f4f4f2"/>'
    '<path d="M20 24h24M20 32h24M20 40h14" stroke="#1c1c1a" stroke-width="3" '
    'stroke-linecap="round"/></svg>'
)

MANIFEST = json.dumps({
    "name": "Daily Sheet",
    "short_name": "Daily Sheet",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#f4f4f2",
    "theme_color": "#1c1c1a",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
})


def _buttons_html() -> str:
    rows = []
    for i, (key, a) in enumerate(ACTIONS.items()):
        cls = "btn primary" if i == 0 else "btn"
        rows.append(
            f'<button class="{cls}" data-action="{key}">'
            f'<span class="label">{a.label}</span>'
            f'<span class="hint">{a.hint}</span></button>'
        )
    return "\n".join(rows)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Daily Sheet">
<meta name="theme-color" content="#f4f4f2">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" href="/icon.svg" type="image/svg+xml">
<title>Daily Sheet</title>
<style>
:root{--bg:#f4f4f2;--card:#fff;--ink:#1c1c1a;--muted:#6b6b66;--rule:#d8d8d4;
      --good:#1f7a3d;--bad:#a8322a;--busy:#8a6d1f;}
@media (prefers-color-scheme:dark){
  :root{--bg:#17171a;--card:#212126;--ink:#ececea;--muted:#9a9a95;--rule:#34343a;
        --good:#5fbf80;--bad:#e2796f;--busy:#d8b45c;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:16px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
     padding:max(16px,env(safe-area-inset-top)) 16px calc(24px + env(safe-area-inset-bottom));
     max-width:640px;margin:0 auto;-webkit-text-size-adjust:100%}
h1{font-size:20px;margin:0}
header{display:flex;align-items:baseline;justify-content:space-between;gap:12px}
.date{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--rule);border-radius:12px;
      padding:12px 14px;margin:14px 0}
.card .big{font-size:15px}
.card .sub{color:var(--muted);font-size:13px;margin-top:2px}
.btn{display:block;width:100%;text-align:left;background:var(--card);color:var(--ink);
     border:1px solid var(--rule);border-radius:12px;padding:14px;margin-bottom:10px;
     font:inherit;-webkit-tap-highlight-color:transparent}
.btn:active{transform:scale(.995)}
.btn .label{display:block;font-size:17px;font-weight:600}
.btn .hint{display:block;color:var(--muted);font-size:13px;margin-top:3px}
.btn.primary{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.btn.primary .hint{color:var(--bg);opacity:.72}
.btn[disabled]{opacity:.45}
#pill{font-size:13px;color:var(--muted)}
#pill.good{color:var(--good)}#pill.bad{color:var(--bad)}#pill.busy{color:var(--busy)}
#logwrap{margin-top:6px}
#logwrap summary{color:var(--muted);font-size:13px;cursor:pointer;padding:6px 0}
#log{background:var(--card);border:1px solid var(--rule);border-radius:12px;
     padding:10px 12px;max-height:46vh;overflow:auto;white-space:pre-wrap;
     word-break:break-word;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
#log div.good{color:var(--good)}#log div.bad{color:var(--bad)}#log div.busy{color:var(--busy)}
#log div.muted{color:var(--muted)}
#gate{display:none}
#tokenbox{width:100%;padding:12px;font:inherit;border-radius:10px;
     border:1px solid var(--rule);background:var(--card);color:var(--ink)}
.go{margin-top:10px;background:var(--ink);color:var(--bg);border:0;border-radius:10px;
    padding:12px 16px;font:inherit;font-weight:600}
footer{color:var(--muted);font-size:12px;margin-top:18px;display:flex;
       justify-content:space-between;gap:10px}
footer button{background:none;border:0;color:var(--muted);font:inherit;
       text-decoration:underline;padding:0}
</style>
</head>
<body>
<header>
  <h1>Daily Sheet</h1>
  <span id="pill"></span>
</header>
<div class="date" id="today"></div>

<section id="gate" class="card">
  <div class="big">Enter the access token</div>
  <div class="sub" id="gatehint">WEB_TOKEN from the server's .env. Stored on this phone only.</div>
  <form id="gateform" style="margin-top:10px">
    <input type="text" id="tokenbox" autocomplete="off" autocapitalize="off"
           autocorrect="off" spellcheck="false" placeholder="token" enterkeyhint="go">
    <button class="go" type="submit">Unlock</button>
  </form>
</section>

<main id="app" hidden>
  <div class="card" id="statecard">
    <div class="big" id="sheetline">…</div>
    <div class="sub" id="lastline"></div>
  </div>

  __BUTTONS__

  <details id="logwrap">
    <summary>Log</summary>
    <div id="log"></div>
  </details>

  <footer>
    <span id="host"></span>
    <button id="forget">Forget token</button>
  </footer>
</main>

<script>
const KEY = 'daily-sheet-token';
const url = new URL(location.href);
let token = (url.searchParams.get('t') || localStorage.getItem(KEY) || '').trim();
if (url.searchParams.get('t')) {
  localStorage.setItem(KEY, token);
  history.replaceState({}, '', url.pathname);   // keep it out of the address bar
}

const $ = (id) => document.getElementById(id);
const app = $('app'), gate = $('gate'), log = $('log'), pill = $('pill');
let running = false;

$('today').textContent = new Date().toLocaleDateString(undefined,
  {weekday:'long', day:'numeric', month:'long', year:'numeric'});

function setPill(text, cls) { pill.textContent = text; pill.className = cls || ''; }

function colour(line) {
  const l = line.toLowerCase();
  if (l.includes('[fail]') || l.includes('error') || l.includes('failed')) return 'bad';
  if (l.includes('[warn]') || l.startsWith('warn')) return 'busy';
  if (l.includes('[ok]') || l.includes('pushed') || l.includes('completed')) return 'good';
  return '';
}

function append(line, cls) {
  const at = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const d = document.createElement('div');
  d.className = cls !== undefined ? cls : colour(line);
  // The CLI stamps every line with an ISO time; the page is live, so drop it.
  d.textContent = line.replace(/^\\d{4}-\\d\\d-\\d\\dT[\\d:.]+\\s\\s+/, '') || '\\u00a0';
  log.appendChild(d);
  if (at) log.scrollTop = log.scrollHeight;
}

function showGate(why) {
  gate.style.display = 'block';
  app.hidden = true;
  if (why) setPill(why, 'bad');
  // Show the rejected token rather than hiding it behind a saved value that
  // cannot be seen or cleared -- being locked out with no control over what
  // the page keeps sending is worse than showing your own token back to you.
  if (token) { $('tokenbox').value = token; localStorage.removeItem(KEY); }
  $('tokenbox').focus();
  fetch('/hello').then(r => r.json()).then(s => {
    $('gatehint').textContent = s.token_source === 'generated'
      ? 'This server started at ' + s.started + ' without a WEB_TOKEN in .env, so it '
        + 'made one up — and it changes at every restart. Use the link it printed, '
        + 'or set WEB_TOKEN in .env and restart it.'
      : "WEB_TOKEN from the server's .env, on " + s.host + '. Stored on this phone only.';
  }).catch(() => {});
}

$('gateform').addEventListener('submit', (e) => {
  e.preventDefault();
  token = $('tokenbox').value.trim();
  if (!token) return;
  localStorage.setItem(KEY, token);
  refresh();
});

$('forget').addEventListener('click', () => {
  localStorage.removeItem(KEY);
  token = '';
  showGate('');
});

// keepPill: a run has just finished and said how it went; the refresh that
// follows must not overwrite that verdict with the idle state.
async function refresh(keepPill) {
  if (!token) { showGate(''); return; }
  let r;
  try {
    r = await fetch('/status', {headers: {'X-Daily-Token': token}});
  } catch (e) {
    setPill('server unreachable', 'bad');
    return;
  }
  if (r.status === 401) { showGate('wrong token'); return; }
  gate.style.display = 'none';
  app.hidden = false;
  const s = await r.json();
  $('sheetline').textContent = s.sheet.sheet
      ? s.sheet.sheet.replace(/\\.pdf$/, '')
      : 'No sheet built for today yet';
  $('lastline').textContent = [
      s.sheet.built ? 'built ' + s.sheet.built : '',
      s.last && s.last.label
        ? s.last.label + ' at ' + s.last.at + ' — ' + s.last.summary
        : ''
  ].filter(Boolean).join(' · ');
  $('host').textContent = s.host;
  if (keepPill) return;
  if (s.running) setPill(s.running + '…', 'busy');
  else if (!running) setPill(s.last && s.last.ok === false ? 'last run failed' : '',
                             s.last && s.last.ok === false ? 'bad' : '');
}

function buttons(disabled) {
  document.querySelectorAll('.btn').forEach(b => b.disabled = disabled);
}

async function run(action, label) {
  if (running) return;
  running = true;
  buttons(true);
  $('logwrap').open = true;
  setPill(label + '…', 'busy');
  append('');
  append(label + '…', 'busy');
  let finished = null;
  try {
    const res = await fetch('/run/' + action, {
      method: 'POST', headers: {'X-Daily-Token': token}
    });
    if (res.status === 401) { showGate('wrong token'); return; }
    if (res.status === 409) {
      append((await res.json()).error, 'busy');
      return;
    }
    const handle = (chunk) => {
      for (const line of chunk.split('\\n')) {
        if (line.startsWith('\\u001e')) { finished = line.slice(1).trim(); continue; }
        if (line !== '') append(line);
      }
    };
    if (res.body && res.body.getReader) {
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream: true});
        const parts = buf.split('\\n');
        buf = parts.pop();
        handle(parts.join('\\n'));
      }
      if (buf) handle(buf);
    } else {
      handle(await res.text());          // older Safari: no streaming, one blob
    }
  } catch (e) {
    append('Lost the connection to the server: ' + e, 'bad');
  } finally {
    running = false;
    buttons(false);
    if (finished === '0') setPill('done', 'good');
    else if (finished !== null) setPill('finished with errors', 'bad');
    else setPill('connection dropped', 'bad');
    refresh(true);
  }
}

document.querySelectorAll('.btn').forEach(b => {
  b.addEventListener('click', () =>
    run(b.dataset.action, b.querySelector('.label').textContent));
});

if (token) refresh(); else showGate('');
document.addEventListener('visibilitychange', () => {
  if (!document.hidden && !running && token) refresh();
});
</script>
</body>
</html>
""".replace("__BUTTONS__", _buttons_html())


def serve(host: str, port: int, token: str, quiet: bool = False,
          generated: bool = False) -> ThreadingHTTPServer:
    Handler.token = token
    Handler.token_source = "generated" if generated else "env"
    Handler.started = datetime.now().strftime("%H:%M")
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.quiet = quiet                                    # type: ignore[attr-defined]
    httpd.daemon_threads = True
    return httpd


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="serve.py", description="Drive the daily sheet from a phone.")
    p.add_argument("--host", default="0.0.0.0",
                   help="interface to bind (default: all, so the phone can reach it)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--quiet", action="store_true", help="don't log requests")
    args = p.parse_args(argv)

    token, generated = load_token()
    httpd = serve(args.host, args.port, token, args.quiet, generated)

    where = lan_address() if args.host in ("0.0.0.0", "") else args.host
    print(f"Daily Sheet server on http://{where}:{args.port}")
    if generated:
        # Say which of the two it is. "No WEB_TOKEN" alone sent someone hunting
        # for a typo in a file that did not exist.
        env = HERE / ".env"
        print()
        if env.exists():
            print(f"There is a .env here but no WEB_TOKEN= line in it ({env}).")
        else:
            print(f"There is no .env here yet ({env}).")
        print("So this run made a token up:")
        print(f"    WEB_TOKEN={token}")
        print("Put that line in .env or it changes every restart.")
    print()
    print("On the phone, open:")
    print(f"    http://{where}:{args.port}/?t={token}")
    print("then Share -> Add to Home Screen. Ctrl-C here to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
