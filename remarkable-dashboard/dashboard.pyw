"""Buttons instead of commands.

Saved as .pyw so Windows launches it with pythonw and no console window
appears behind it. Every action shells out to `python -m daily_sheet ...` in a
worker thread and streams the output into the log pane, so the window stays
responsive and you can see what is happening rather than waiting on a frozen
box. Nothing here reimplements the pipeline -- it only presses the same buttons
you would press from a terminal.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import font as tkfont
from tkinter import ttk

HERE = Path(__file__).resolve().parent

# Muted palette -- this sits open all day, so nothing should shout.
BG      = "#f4f4f2"
CARD    = "#ffffff"
INK     = "#1c1c1a"
MUTED   = "#6b6b66"
RULE    = "#d8d8d4"
GOOD    = "#1f7a3d"
BAD     = "#a8322a"
BUSY    = "#8a6d1f"


def python_exe() -> list[str]:
    """The interpreter to run daily_sheet with.

    sys.executable under pythonw.exe would spawn a GUI-subsystem child whose
    stdout we cannot read, so swap back to the console build when we find it.
    """
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.exists():
            return [str(console)]
    return [str(exe)]


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.q: queue.Queue[tuple[str, str]] = queue.Queue()
        self.running = False
        self.buttons: list[ttk.Button] = []

        root.title("reMarkable Daily Sheet")
        root.geometry("760x560")
        root.minsize(620, 440)
        root.configure(bg=BG)

        self._fonts()
        self._style()
        self._build()

        self.root.after(80, self._drain)
        self.log("Ready. Tick boxes on the sheet with your pen, then press Sync now.\n", MUTED)

    # -- chrome ---------------------------------------------------------
    def _fonts(self) -> None:
        base = "Segoe UI" if sys.platform == "win32" else "Helvetica"
        self.f_title = tkfont.Font(family=base, size=16, weight="bold")
        self.f_body = tkfont.Font(family=base, size=10)
        self.f_btn = tkfont.Font(family=base, size=11)
        self.f_hint = tkfont.Font(family=base, size=9)
        self.f_mono = tkfont.Font(family="Consolas" if sys.platform == "win32" else "Menlo", size=9)

    def _style(self) -> None:
        s = ttk.Style()
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=CARD, relief="flat")
        s.configure("TLabel", background=BG, foreground=INK, font=self.f_body)
        s.configure("Muted.TLabel", background=BG, foreground=MUTED, font=self.f_hint)
        s.configure("Title.TLabel", background=BG, foreground=INK, font=self.f_title)
        s.configure("Big.TButton", font=self.f_btn, padding=(12, 12))
        s.configure("TButton", font=self.f_body, padding=(8, 6))

    def _build(self) -> None:
        pad = {"padx": 18}

        head = ttk.Frame(self.root)
        head.pack(fill="x", pady=(16, 4), **pad)
        ttk.Label(head, text="reMarkable Daily Sheet", style="Title.TLabel").pack(side="left")
        self.status = ttk.Label(head, text="", style="Muted.TLabel")
        self.status.pack(side="right")

        ttk.Label(self.root, text=date.today().strftime("%A %d %B %Y"),
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 12), **pad)

        # Primary actions, in the order they get used.
        grid = ttk.Frame(self.root)
        grid.pack(fill="x", **pad)
        for i in (0, 1):
            grid.columnconfigure(i, weight=1, uniform="b")

        self._button(grid, 0, 0, "Sync + Generate Daily",
                     "Reads your ticks → completes them in Asana → rebuilds "
                     "today's sheet without them → pushes it back",
                     lambda: self.run(["generate"], "Syncing and rebuilding"), span=2)
        self._button(grid, 1, 0, "Sync only",
                     "Pushes ticks to Asana without touching the sheet",
                     lambda: self.run(["sync"], "Reading your ticks"))
        self._button(grid, 1, 1, "Check Asana board",
                     "Shows the sections and fields the Projects page reads",
                     lambda: self.run(["board"], "Reading the board"))
        self._button(grid, 2, 0, "What's on the tablet",
                     "Lists Daily/ and Archive/ — where your ticks live",
                     lambda: self.run(["tablet"], "Listing"))
        self._button(grid, 2, 1, "Check connections",
                     "Tests rmapi, calendar, Asana and the API key",
                     lambda: self.run(["doctor"], "Checking"))

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", pady=(14, 6), **pad)
        ttk.Button(bar, text="Clear log", command=self.clear).pack(side="right")
        ttk.Button(bar, text="Open folder", command=self.open_folder).pack(side="right", padx=(0, 8))

        wrap = tk.Frame(self.root, bg=RULE, bd=0, highlightthickness=1, highlightbackground=RULE)
        wrap.pack(fill="both", expand=True, pady=(0, 16), **pad)
        self.out = tk.Text(wrap, wrap="word", font=self.f_mono, bg=CARD, fg=INK,
                           bd=0, padx=12, pady=10, insertbackground=INK, height=10)
        sb = ttk.Scrollbar(wrap, command=self.out.yview)
        self.out.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y")
        self.out.pack(side="left", fill="both", expand=True)
        for tag, colour in (("good", GOOD), ("bad", BAD), ("muted", MUTED), ("busy", BUSY)):
            self.out.tag_configure(tag, foreground=colour)
        self.out.tag_configure("bold", font=tkfont.Font(
            family=self.f_mono.cget("family"), size=9, weight="bold"))

    def _button(self, parent: ttk.Frame, r: int, c: int, label: str, hint: str, cmd,
                span: int = 1) -> None:
        cell = ttk.Frame(parent)
        cell.grid(row=r, column=c, columnspan=span, sticky="ew",
                  padx=(0, 10) if c == 0 and span == 1 else (0, 0), pady=(0, 10))
        b = ttk.Button(cell, text=label, style="Big.TButton", command=cmd)
        b.pack(fill="x")
        ttk.Label(cell, text=hint, style="Muted.TLabel").pack(anchor="w", pady=(3, 0))
        self.buttons.append(b)

    # -- logging --------------------------------------------------------
    def log(self, text: str, colour: str | None = None, bold: bool = False) -> None:
        tag = {GOOD: "good", BAD: "bad", MUTED: "muted", BUSY: "busy"}.get(colour or "", "")
        tags = tuple(t for t in (tag, "bold" if bold else "") if t)
        self.out.configure(state="normal")
        self.out.insert("end", text, tags)
        self.out.see("end")
        self.out.configure(state="disabled")

    def clear(self) -> None:
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.configure(state="disabled")

    def open_folder(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(HERE)                                  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(HERE)])
            else:
                subprocess.Popen(["xdg-open", str(HERE)])
        except OSError as exc:
            self.log(f"Could not open the folder: {exc}\n", BAD)

    # -- running --------------------------------------------------------
    def run(self, args: list[str], label: str) -> None:
        if self.running:
            return
        self.running = True
        for b in self.buttons:
            b.state(["disabled"])
        self.status.configure(text=f"{label}…", foreground=BUSY)
        self.log(f"\n{label}…\n", BUSY, bold=True)
        threading.Thread(target=self._worker, args=(args,), daemon=True).start()

    def _worker(self, args: list[str]) -> None:
        cmd = python_exe() + ["-m", "daily_sheet", *args]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            p = subprocess.Popen(
                cmd, cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=flags,
            )
            for line in p.stdout:                                    # type: ignore[union-attr]
                self.q.put(("line", line))
            p.wait()
            self.q.put(("done", str(p.returncode)))
        except Exception as exc:                                     # noqa: BLE001
            self.q.put(("line", f"Could not run the command: {exc}\n"))
            self.q.put(("done", "1"))

    def _drain(self) -> None:
        """Pump worker output into the widget from the Tk thread."""
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self.log(self._tidy(payload), self._colour(payload))
                else:
                    self._finished(payload == "0")
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    @staticmethod
    def _tidy(line: str) -> str:
        """Drop the ISO timestamp the CLI prefixes -- the log pane is live."""
        if len(line) > 20 and line[4] == "-" and line[10] == "T" and "  " in line:
            return line.split("  ", 1)[1]
        return line

    @staticmethod
    def _colour(line: str) -> str | None:
        low = line.lower()
        if "[fail]" in low or "error" in low or "failed" in low:
            return BAD
        if "[warn]" in low or low.startswith("warn"):
            return BUSY
        if "[ok]" in low or "pushed" in low or "completed" in low:
            return GOOD
        return None

    def _finished(self, ok: bool) -> None:
        self.running = False
        for b in self.buttons:
            b.state(["!disabled"])
        self.status.configure(text="Done" if ok else "Finished with errors",
                              foreground=GOOD if ok else BAD)


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
