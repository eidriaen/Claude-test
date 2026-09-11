"""Upload replace-semantics. rmapi itself isn't available in CI, so we drive
Rmapi._run with a fake that reproduces its actual failure text.

What matters here: a same-day re-run must not fail, and must not silently
destroy a sheet that may already carry unread pen marks.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from daily_sheet.config import load_config
from daily_sheet.remarkable import Rmapi, RmapiError

EXISTS = ("rmapi put failed: ERROR: main.go:86: Error:  entry already exists "
          "(use --force to recreate, --content-only to replace content)")


@pytest.fixture
def rm(tmp_path):
    cfg = load_config()
    obj = Rmapi(cfg)
    obj.calls = []
    return obj


def _stub(rm, *, put_fails_until_forced=False, put_fails_always=False, mv_fails=False):
    """Replace _run with a recorder that mimics rmapi's behaviour."""
    def fake(*args, check=True, cwd=None):
        rm.calls.append(args)
        cmd = args[0]
        if cmd == "mkdir":
            return None
        if cmd == "mv" and mv_fails:
            raise RmapiError("rmapi mv failed: entry already exists")
        if cmd == "put":
            forced = "--force" in args
            if put_fails_always and not forced:
                raise RmapiError(EXISTS)
            if put_fails_until_forced and not forced and len(
                    [c for c in rm.calls if c[0] == "put"]) == 1:
                raise RmapiError(EXISTS)
        return None
    rm._run = fake


def _puts(rm):
    return [c for c in rm.calls if c[0] == "put"]


def test_clean_upload_does_not_touch_anything_else(rm, tmp_path):
    _stub(rm)
    pdf = tmp_path / "Daily Sheet — 2026-09-11.pdf"
    pdf.write_bytes(b"%PDF")
    assert rm.upload(pdf, "Daily") == "uploaded"
    assert len(_puts(rm)) == 1
    assert not [c for c in rm.calls if c[0] == "mv"], "nothing to archive on a first push"
    # rmapi names the document after the path it is handed, and does not strip a
    # Windows directory -- passing an absolute path produced a document called
    # "C:\\...\\out\\Daily Sheet - <date>" that nothing could find by name.
    assert _puts(rm)[0][1] == pdf.name, "must upload by bare filename, not a path"


def test_same_day_rerun_archives_the_old_sheet_then_uploads(rm, tmp_path):
    _stub(rm, put_fails_until_forced=True)
    pdf = tmp_path / "Daily Sheet — 2026-09-11.pdf"
    pdf.write_bytes(b"%PDF")

    result = rm.upload(pdf, "Daily")

    assert "archived" in result
    mv = [c for c in rm.calls if c[0] == "mv"]
    assert mv, "the existing sheet must be moved, not overwritten"
    assert mv[0][1] == "Daily/Daily Sheet — 2026-09-11"
    assert mv[0][2] == "Daily/Archive"
    assert "--force" not in sum((list(c) for c in _puts(rm)), []), \
        "force destroys pen marks; archiving first must be preferred"


def test_falls_back_to_force_only_when_archiving_fails(rm, tmp_path):
    _stub(rm, put_fails_always=True, mv_fails=True)
    pdf = tmp_path / "Daily Sheet — 2026-09-11.pdf"
    pdf.write_bytes(b"%PDF")

    result = rm.upload(pdf, "Daily")

    assert "could not archive" in result, "the caller must learn a sheet was overwritten"
    assert any("--force" in c for c in _puts(rm))


def test_unrelated_put_failure_is_not_swallowed(rm, tmp_path):
    def fake(*args, check=True, cwd=None):
        rm.calls.append(args)
        if args[0] == "put":
            raise RmapiError("rmapi put failed: connection refused")
        return None
    rm._run = fake
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")

    with pytest.raises(RmapiError, match="connection refused"):
        rm.upload(pdf, "Daily")
    assert len(_puts(rm)) == 1, "a network error must not trigger a force retry"
