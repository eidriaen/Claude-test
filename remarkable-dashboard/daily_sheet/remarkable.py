"""reMarkable cloud I/O via rmapi.

Everything the tablet needs goes through this one module so it can be swapped
or stubbed when firmware breaks rmapi (see SCOPE §6.2). `RmapiError` is raised
for anything the caller should treat as a hard failure; `available()` and
`find()` return falsy instead of raising so the daily loop can degrade.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config

TIMEOUT = 180  # rmapi uploads on a slow line can take a while


class RmapiError(RuntimeError):
    pass


@dataclass
class Rmapi:
    cfg: Config

    # -- plumbing -------------------------------------------------------
    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = [self.cfg.rmapi_bin, *args]
        try:
            # rmapi emits UTF-8. Without an explicit encoding Python decodes with
            # the locale codepage (cp1252 on a Norwegian Windows), which mangles
            # the em-dash in "Daily Sheet — <date>" — and since find() matches the
            # name exactly, read-back would never locate yesterday's sheet.
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                               encoding="utf-8", errors="replace")
        except FileNotFoundError as e:
            raise RmapiError(f"{self.cfg.rmapi_bin} not found on PATH — see README") from e
        except subprocess.TimeoutExpired as e:
            raise RmapiError(f"rmapi timed out after {TIMEOUT}s: {' '.join(args)}") from e
        if check and p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip().splitlines()
            raise RmapiError(f"rmapi {' '.join(args)} failed: {err[-1] if err else p.returncode}")
        return p

    def found(self) -> bool:
        """True if the binary exists. On Windows RMAPI_BIN is usually a full
        path to a loose rmapi.exe rather than something on PATH, so accept both."""
        bin_ = self.cfg.rmapi_bin
        if shutil.which(bin_):
            return True
        p = Path(bin_)
        return p.is_file() or p.with_suffix(".exe").is_file()

    def status(self) -> tuple[bool, str]:
        """(usable, reason). Separates 'not installed' from 'not paired' so the
        log tells you which of the two to go and fix."""
        if not self.found():
            return False, (f"{self.cfg.rmapi_bin} not found — install rmapi and set "
                           f"RMAPI_BIN in .env to its full path")
        try:
            if self._run("ls", check=False).returncode != 0:
                return False, "rmapi is installed but not paired — run it once to enter a code from my.remarkable.com"
        except RmapiError as exc:
            return False, str(exc)
        return True, ""

    def available(self) -> bool:
        return self.status()[0]

    # -- folders --------------------------------------------------------
    def ensure_folder(self, path: str) -> None:
        """mkdir -p. rmapi errors when the folder exists, which is fine."""
        parts, sofar = path.strip("/").split("/"), ""
        for part in parts:
            sofar = f"{sofar}/{part}" if sofar else part
            self._run("mkdir", sofar, check=False)

    def ls(self, folder: str) -> list[str]:
        p = self._run("ls", folder, check=False)
        if p.returncode != 0:
            return []
        names = []
        for line in p.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("["):      # rmapi prints "[d] name" / "[f] name"
                if line.startswith("[f]") or line.startswith("[d]"):
                    names.append(line[3:].strip())
                continue
            names.append(line)
        return [n for n in names if n]

    def find(self, folder: str, name: str) -> str | None:
        """Full path of `name` in `folder`, or None."""
        return f"{folder}/{name}" if name in self.ls(folder) else None

    # -- documents ------------------------------------------------------
    def upload(self, pdf: Path, folder: str) -> str:
        """Upload, replacing any same-named document already on the tablet.

        Re-running on a day that already has a sheet (a manual run, or the
        scheduler retrying) otherwise dies on 'entry already exists'. We move
        the old document to Archive rather than overwriting it, because it may
        already carry pen marks that have not been read back yet — losing those
        silently is the one outcome worth some trouble to avoid. Only if the
        move fails do we force, and the caller gets told which happened.
        """
        self.ensure_folder(folder)
        try:
            self._run("put", str(pdf), folder)
            return "uploaded"
        except RmapiError as exc:
            if "already exists" not in str(exc).lower():
                raise

        existing = f"{folder}/{pdf.stem}"
        archived = False
        try:
            self.move(existing, self.cfg.archive_folder)
            archived = True
        except RmapiError:
            pass        # name already taken in Archive, or mv unsupported

        try:
            self._run("put", str(pdf), folder)
            return "replaced (previous sheet archived)" if archived else "replaced"
        except RmapiError as exc:
            if "already exists" not in str(exc).lower():
                raise
            self._run("put", "--force", str(pdf), folder)
            return "overwritten (could not archive the previous sheet)"

    def move(self, src: str, dst_folder: str) -> None:
        self.ensure_folder(dst_folder)
        self._run("mv", src, dst_folder)

    def download_annotated(self, doc_path: str, dest_dir: Path) -> Path:
        """Download the document with pen marks baked in, as a PDF.

        rmapi's `geta` ('get annotated') writes <name>.pdf into cwd, so we run
        it from dest_dir and return whatever landed.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        before = set(dest_dir.glob("*.pdf"))
        try:
            p = subprocess.run(
                [self.cfg.rmapi_bin, "geta", doc_path],
                capture_output=True, text=True, timeout=TIMEOUT, cwd=dest_dir,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise RmapiError(f"rmapi geta failed: {e}") from e
        new = set(dest_dir.glob("*.pdf")) - before
        if not new:
            err = (p.stderr or p.stdout or "").strip()
            raise RmapiError(f"rmapi geta produced no PDF for {doc_path}: {err[:200]}")
        return new.pop()
