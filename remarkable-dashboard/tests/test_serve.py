"""The phone server.

Two things matter here and neither is cosmetic: nothing runs without the token,
and the request names a button rather than a command line. The rest of the
tests pin the streaming contract the page depends on -- output arrives line by
line and the last line says how the run ended, because without that marker a
dropped connection is indistinguishable from a quiet success.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

import serve
from serve import ACTIONS, EXIT_MARK, TOKEN_HEADER, Runner


TOKEN = "s3cret-token"


@pytest.fixture
def server(monkeypatch):
    """A real server on a real socket, with the CLI replaced by echo."""
    # The point is the HTTP layer, not the pipeline -- so run something that
    # prints a couple of lines and exits, and keep the command shape identical.
    monkeypatch.setattr(serve, "python_exe", lambda: [_PY])
    monkeypatch.setattr(serve.Handler, "runner", Runner())

    httpd = serve.serve("127.0.0.1", 0, TOKEN, quiet=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


_PY = "python-stub"       # replaced below by the real interpreter + a -c script


@pytest.fixture(autouse=True)
def fake_cli(monkeypatch):
    """Make `python -m daily_sheet <action>` print and exit without doing work."""
    import subprocess
    import sys

    real = subprocess.Popen

    def fake(cmd, **kw):
        assert cmd[1:3] == ["-m", "daily_sheet"], cmd
        action = cmd[3]
        script = (
            "import sys;"
            f"print('running {action}');"
            "print('[ok] Completed 2');"
            f"sys.exit({1 if action == 'doctor' else 0})"
        )
        return real([sys.executable, "-c", script], **kw)

    monkeypatch.setattr(subprocess, "Popen", fake)


def get(url, token=TOKEN, method="GET"):
    req = urllib.request.Request(url, method=method)
    if token is not None:
        req.add_header(TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_page_needs_no_token(server):
    """The shell is not secret -- it is what asks for the token."""
    status, body = get(server + "/", token=None)
    assert status == 200
    assert "Daily Sheet" in body
    for action in ACTIONS:
        assert f'data-action="{action}"' in body


def test_status_refuses_without_token(server):
    assert get(server + "/status", token=None)[0] == 401
    assert get(server + "/status", token="wrong")[0] == 401


def test_run_refuses_without_token(server):
    """A missing token must not merely hide the buttons -- it must not run."""
    assert get(server + "/run/generate", token=None, method="POST")[0] == 401
    assert get(server + "/run/generate", token="wrong", method="POST")[0] == 401


def test_unknown_action_is_rejected(server):
    """The URL names a button, never a command line."""
    for bad in ("rm", "generate;rm", "../doctor"):
        status, _ = get(f"{server}/run/{bad}", method="POST")
        assert status in (404, 400), bad


def test_run_streams_output_and_exit_code(server):
    status, body = get(server + "/run/sync", method="POST")
    assert status == 200
    assert "running sync" in body
    assert body.endswith(EXIT_MARK + "0\n")


def test_failure_is_reported_not_swallowed(server):
    _, body = get(server + "/run/doctor", method="POST")
    assert body.endswith(EXIT_MARK + "1\n")


def test_status_carries_the_last_run(server):
    get(server + "/run/sync", method="POST")
    status, body = get(server + "/status")
    assert status == 200
    last = json.loads(body)["last"]
    assert last["action"] == "sync"
    assert last["ok"] is True
    assert "Completed 2" in last["summary"]


def test_second_run_is_refused_while_one_is_going(server):
    """Two generates would race to archive and replace the same document."""
    runner = serve.Handler.runner
    runner._lock.acquire()
    runner.current = "Sync + Generate Daily"
    try:
        status, body = get(server + "/run/generate", method="POST")
    finally:
        runner.current = ""
        runner._lock.release()
    assert status == 409
    assert "still running" in json.loads(body)["error"]


def test_generated_token_when_env_has_none(monkeypatch, tmp_path):
    """No WEB_TOKEN must not mean no lock."""
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(serve, "HERE", tmp_path)
    monkeypatch.delenv("WEB_TOKEN", raising=False)
    token, generated = serve.load_token()
    assert generated and len(token) >= 12


def test_env_is_read_without_dotenv_installed(monkeypatch, tmp_path):
    """A failed `pip install` must not silently ignore the token in .env.

    It did, and from the phone it looked identical to typing the token wrong:
    the server invented a new one every restart and said nothing.
    """
    (tmp_path / ".env").write_text(
        "# comment\nASANA_PAT=abc\nWEB_TOKEN = from-the-file \nOTHER=x\n",
        encoding="utf-8")
    monkeypatch.setattr(serve, "HERE", tmp_path)
    monkeypatch.delenv("WEB_TOKEN", raising=False)

    import builtins
    real_import = builtins.__import__

    def no_dotenv(name, *a, **kw):
        if name == "dotenv":
            raise ImportError("no dotenv")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_dotenv)
    token, generated = serve.load_token()
    assert (token, generated) == ("from-the-file", False)


@pytest.mark.parametrize("line, want", [
    ('WEB_TOKEN="quoted"', "quoted"),
    ("WEB_TOKEN='single'", "single"),
    ("export WEB_TOKEN=exported", "exported"),
    ("WEB_TOKEN=", ""),
    ("#WEB_TOKEN=commented", ""),
    ("WEB_TOKEN_OTHER=near-miss", ""),
])
def test_env_file_parsing(tmp_path, line, want):
    (tmp_path / ".env").write_text(line + "\n", encoding="utf-8")
    assert serve.env_file_value("WEB_TOKEN", tmp_path / ".env") == want


def test_token_is_trimmed_on_the_way_in(server):
    """Phones paste with a trailing space more often than anyone admits."""
    assert get(server + "/status", token=TOKEN + " ")[0] == 200


def test_summary_prefers_the_result_line():
    tail = ["2026-09-11T08:00:01  Completed 3 · Added 1", "2026-09-11T08:00:02  wrote layout.json"]
    assert serve.summarise(tail, True).startswith("Completed 3")
