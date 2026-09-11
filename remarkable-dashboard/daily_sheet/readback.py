"""Read yesterday's pen marks back off the annotated PDF.

Checkboxes and priority boxes are decided by **ink density** inside the region
(SCOPE §4) — no OCR, no model call, fully deterministic. Only two things reach
Claude: a priority box that already tested positive for ink (transcribe one
digit) and the free-text regions (new tasks, notes).

All coordinates come from the sidecar layout.json written at render time, so
this module never needs to know the page design.
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .models import Region

# Ink thresholds. Calibrate against a real ticked page with `--calibrate`
# (SCOPE §6.4) and override via env if your pen/threshold differs.
DARK = 160                  # 0–255; below this a pixel counts as ink
CHECK_MIN = float(os.getenv("INK_CHECK_MIN", "0.035"))     # ≥3.5 % of the box inked = ticked
PRIORITY_MIN = float(os.getenv("INK_PRIORITY_MIN", "0.02"))
SPAN_MIN = float(os.getenv("INK_SPAN_MIN", "0.45"))   # mark must cross ~half the box
INSET = 7                   # px trimmed off each edge to exclude the printed border

MODEL = "claude-opus-5"


@dataclass
class Marks:
    """What the pen said on yesterday's sheet."""
    checked: list[str] = field(default_factory=list)          # region ids
    priorities: dict[str, int] = field(default_factory=dict)  # region id -> 1|2|3
    new_tasks: list[str] = field(default_factory=list)        # raw lines, unparsed
    notes: str = ""
    unreadable: list[Path] = field(default_factory=list)      # PNG strips to re-show
    ink: dict[str, float] = field(default_factory=dict)       # region id -> ratio (debug)


# --- rasterising ------------------------------------------------------------
def page_images(pdf: Path, size: tuple[int, int]) -> list[Image.Image]:
    """Render every page to exactly `size` so layout.json coordinates line up."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf))
    out = []
    for i in range(len(doc)):
        page = doc[i]
        scale = size[0] / page.get_width()
        img = page.render(scale=scale, grayscale=True).to_pil().convert("L")
        if img.size != size:
            img = img.resize(size, Image.LANCZOS)
        out.append(img)
    return out


def ink_ratio(img: Image.Image, r: Region, inset: int = INSET) -> float:
    """Fraction of pixels inside the region (minus its border) that are ink."""
    box = (r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset)
    if box[2] <= box[0] or box[3] <= box[1]:
        return 0.0
    a = np.asarray(img.crop(box), dtype=np.uint8)
    return float((a < DARK).mean()) if a.size else 0.0


def ink_span(img: Image.Image, r: Region, inset: int = INSET) -> float:
    """How far the ink reaches across the region, as a fraction of its size.

    Density alone can't tell a tick from a resting pen nib: a small dense blob
    scores the same as a stroke. A deliberate mark *crosses* the box, so we take
    the larger of the ink bounding box's width and height fractions.
    """
    box = (r.x0 + inset, r.y0 + inset, r.x1 - inset, r.y1 - inset)
    if box[2] <= box[0] or box[3] <= box[1]:
        return 0.0
    a = np.asarray(img.crop(box), dtype=np.uint8) < DARK
    if not a.any():
        return 0.0
    rows, cols = np.any(a, axis=1), np.any(a, axis=0)
    h_frac = (np.flatnonzero(rows)[-1] - np.flatnonzero(rows)[0] + 1) / a.shape[0]
    w_frac = (np.flatnonzero(cols)[-1] - np.flatnonzero(cols)[0] + 1) / a.shape[1]
    return float(max(h_frac, w_frac))


def is_marked(img: Image.Image, r: Region, min_ratio: float = CHECK_MIN) -> bool:
    """A box counts as marked only if the ink is both dense enough and spans it."""
    return ink_ratio(img, r) >= min_ratio and ink_span(img, r) >= SPAN_MIN


def form_checked(pdf: Path) -> set[str]:
    """Ids whose AcroForm checkbox the viewer actually ticked.

    The sheet carries a real form checkbox over every drawn box. Most e-ink
    readers ignore PDF forms entirely, in which case this finds nothing and ink
    density decides as before -- but where a viewer does honour them, a tap is
    recorded state rather than something inferred from pixels, so it wins.
    """
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    out: set[str] = set()
    try:
        doc = pdfium.PdfDocument(str(pdf))
        doc.init_forms()
    except Exception:  # noqa: BLE001 — no form layer is the normal case
        return out

    CHECKBOX = 2
    try:
        for page in doc:
            for i in range(raw.FPDFPage_GetAnnotCount(page)):
                annot = raw.FPDFPage_GetAnnot(page, i)
                if raw.FPDFAnnot_GetFormFieldType(doc.formenv, annot) != CHECKBOX:
                    continue
                if not raw.FPDFAnnot_IsChecked(doc.formenv, annot):
                    continue
                length = raw.FPDFAnnot_GetFormFieldName(doc.formenv, annot, None, 0)
                buf = ctypes.create_string_buffer(length * 2)
                raw.FPDFAnnot_GetFormFieldName(
                    doc.formenv, annot,
                    ctypes.cast(buf, ctypes.POINTER(ctypes.c_ushort)), length)
                name = buf.raw.decode("utf-16-le").rstrip("\x00")
                if name.startswith("cb_"):
                    out.add(name[3:])
    except Exception:  # noqa: BLE001
        return out
    return out


def crop(img: Image.Image, r: Region, pad: int = 4) -> Image.Image:
    return img.crop((max(r.x0 - pad, 0), max(r.y0 - pad, 0),
                     min(r.x1 + pad, img.width), min(r.y1 + pad, img.height)))


# --- Claude transcription ---------------------------------------------------
def _png_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _ask_claude(api_key: str, img: Image.Image, prompt: str, max_tokens: int = 1000) -> str:
    from anthropic import Anthropic

    msg = Anthropic(api_key=api_key).messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": _png_b64(img)}},
            {"type": "text", "text": prompt},
        ]}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


DIGIT_PROMPT = (
    "This is a small box from a paper planner with a handwritten digit in it. "
    "Reply with just that digit: 1, 2 or 3. If it is blank, ambiguous, or anything "
    "other than 1/2/3, reply exactly: none"
)

TASKS_PROMPT = (
    "This is the 'New tasks' box from a handwritten paper planner. Transcribe it, "
    "one task per line, in reading order. Rules:\n"
    "- Output only the transcribed lines, nothing else. No numbering, no commentary.\n"
    "- Keep any trailing ! or !! exactly as written — they carry meaning.\n"
    "- Ignore the printed ruled lines and any empty lines.\n"
    "- If a line is written but you genuinely cannot read it, output the single "
    "token [UNREADABLE] on its own line in that position.\n"
    "- The handwriting may be Norwegian or English; keep the original language and spelling.\n"
    "If the box is entirely empty, output nothing at all."
)

NOTES_PROMPT = (
    "This is a handwritten notes page from a paper planner. Transcribe it to plain "
    "Markdown, preserving line breaks, bullets and indentation as written. The text "
    "may be Norwegian or English — keep the original language. Output only the "
    "transcription. If the page is blank, output nothing at all."
)


# --- main entry -------------------------------------------------------------
def read_marks(pdf: Path, layout_path: Path, api_key: str, debug_dir: Path | None = None) -> Marks:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    size = tuple(layout["page_size"])
    regions = [Region.from_json(d) for d in layout["regions"]]
    images = page_images(pdf, size)
    marks = Marks()

    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    # 1. checkboxes — pure ink density.
    # A task can carry a checkbox on more than one page (a top-3 task appears on
    # Today *and* on its own page), so ticking either must still count once.
    seen: set[str] = set()

    # A viewer that honours PDF forms gives us exact state; take it first, then
    # let ink decide the rest. Ticking with the pen and tapping the same box
    # must still count once, hence the shared `seen`.
    for rid in sorted(form_checked(pdf)):
        if rid not in seen:
            seen.add(rid)
            marks.checked.append(rid)
    if seen:
        marks.ink["_form_fields_read"] = len(seen)

    for r in (r for r in regions if r.kind == "check"):
        if r.page - 1 >= len(images):
            continue
        img = images[r.page - 1]
        marks.ink[f"check:{r.id}:p{r.page}"] = round(ink_ratio(img, r), 4)
        if is_marked(img, r) and r.id not in seen:
            seen.add(r.id)
            marks.checked.append(r.id)

    # 2. priority boxes — ink gate first, then one digit through Claude
    for r in (r for r in regions if r.kind == "priority"):
        if r.page - 1 >= len(images):
            continue
        img = images[r.page - 1]
        ratio = ink_ratio(img, r)
        marks.ink[f"prio:{r.id}"] = round(ratio, 4)
        if ratio < PRIORITY_MIN or not api_key:
            continue
        region_img = crop(img, r, pad=6).resize((160, 160), Image.LANCZOS)
        if debug_dir:
            region_img.save(debug_dir / f"prio-{r.id}.png")
        try:
            answer = _ask_claude(api_key, region_img, DIGIT_PROMPT, max_tokens=8)
        except Exception:  # noqa: BLE001 — a failed digit must not sink the run
            continue
        digit = re.search(r"[123]", answer)
        if digit and "none" not in answer.lower():
            marks.priorities[r.id] = int(digit.group())

    # 3. free-text regions — straight to Claude
    for r in (r for r in regions if r.kind in ("newtasks", "notes")):
        if r.page - 1 >= len(images) or not api_key:
            continue
        img = images[r.page - 1]
        if ink_ratio(img, r, inset=12) < 0.002:      # visibly blank, skip the call
            continue
        region_img = crop(img, r)
        if debug_dir:
            region_img.save(debug_dir / f"{r.kind}.png")
        prompt = TASKS_PROMPT if r.kind == "newtasks" else NOTES_PROMPT
        try:
            text = _ask_claude(api_key, region_img, prompt, max_tokens=2000)
        except Exception:  # noqa: BLE001
            continue
        if r.kind == "notes":
            marks.notes = text
        else:
            marks.new_tasks = _split_task_lines(text, region_img, r, marks)

    return marks


def _split_task_lines(text: str, region_img: Image.Image, r: Region, marks: Marks) -> list[str]:
    """Split the transcription into lines, turning [UNREADABLE] into image strips."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    good: list[str] = []
    lh = r.line_height or max(1, (r.y1 - r.y0) // 6)
    for i, line in enumerate(lines):
        if "[UNREADABLE]" in line.upper():
            top = min(i * lh, max(region_img.height - lh, 0))
            marks.unreadable.append(_save_strip(region_img.crop((0, top, region_img.width, min(top + lh, region_img.height)))))
        else:
            good.append(line)
    return good


_STRIP_DIR: Path | None = None


def set_strip_dir(path: Path) -> None:
    """Where unreadable-line PNGs are written (set by the CLI per run)."""
    global _STRIP_DIR
    _STRIP_DIR = path
    path.mkdir(parents=True, exist_ok=True)


def _save_strip(img: Image.Image) -> Path:
    base = _STRIP_DIR or Path("out/strips")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"unreadable-{len(list(base.glob('unreadable-*.png')))}.png"
    img.save(path)
    return path
