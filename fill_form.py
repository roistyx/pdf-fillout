#!/usr/bin/env python3
"""
fill_form.py — fill out PDF forms, including flat ones with no form fields.

Two subcommands:

  detect  INPUT.pdf                       -> JSON description of fillable spots
  fill    INPUT.pdf OUTPUT.pdf --data F   -> write values onto the PDF

"Fillable spots" are found three ways, in priority order:
  1. Real AcroForm widgets (text fields, checkboxes, radios) if the PDF has any.
  2. Underscore runs in the text layer ("__________") -> text fields.
     Thin horizontal drawn lines (>= 60 pt wide) are treated the same way.
  3. Small square vector drawings (8-24 pt) -> checkboxes.

Each detected spot gets a best-guess label from nearby text so a UI (or you)
can tell "Parent phone number" from "Date of Birth" without looking.

The --data file for `fill` is JSON:

    {
      "entries": [
        {"page": 0, "type": "text",  "rect": [x0, y0, x1, y1], "value": "Jane Doe", "size": 10},
        {"page": 1, "type": "check", "rect": [x0, y0, x1, y1], "value": true},
        {"page": 0, "type": "text",  "rect": [x0, y0, x1, y1], "value": "…", "widget": "FieldName"}
      ]
    }

Coordinates are PDF points, origin top-left (PyMuPDF convention). If an entry
carries a "widget" name the value is written into that AcroForm field
instead of being drawn. `--flatten` rasterizes the result so the values
cannot be edited or lifted back out.

Requires: pip3 install pymupdf
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Iterable, Optional

import fitz  # PyMuPDF

# ----------------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------------

MIN_UNDERSCORES = 3
MIN_LINE_WIDTH = 60.0       # drawn horizontal lines narrower than this are heading underlines
MAX_LINE_HEIGHT = 2.5
BOX_MIN, BOX_MAX = 8.0, 24.0  # checkbox side length range (pt)
ROW_TOLERANCE = 9.0         # vertical distance to count as "same row"
LABEL_MAX_GAP = 260.0       # how far left/up we look for a label


@dataclass
class Spot:
    id: str
    page: int
    type: str            # "text" | "check"
    rect: list[float]    # [x0, y0, x1, y1]
    label: str = ""
    group: str = ""      # e.g. the question a checkbox belongs to
    widget: Optional[str] = None
    value: object = None


@dataclass
class PageInfo:
    index: int
    width: float
    height: float
    spots: list[Spot] = field(default_factory=list)


def _text_lines(page: fitz.Page) -> list[tuple[fitz.Rect, str]]:
    """Every text line as (bbox, text), with underscore runs removed so a label
    like "Student Name ______" yields just the label's own bbox."""
    grouped: dict[tuple[int, int], list] = {}
    for x0, y0, x1, y1, word, bno, lno, _w in page.get_text("words"):
        if set(word) <= set("_"):
            continue
        grouped.setdefault((bno, lno), []).append((fitz.Rect(x0, y0, x1, y1), word))
    out = []
    for words in grouped.values():
        r = fitz.Rect(words[0][0])
        for wr, _ in words[1:]:
            r |= wr
        out.append((r, " ".join(w for _, w in words)))
    return out


def _strip_underscores(text: str) -> str:
    return text.replace("_", "").strip(" :")


def _same_row(a: fitz.Rect, b: fitz.Rect) -> bool:
    ca = (a.y0 + a.y1) / 2
    cb = (b.y0 + b.y1) / 2
    return abs(ca - cb) <= ROW_TOLERANCE


def _label_left_of(rect: fitz.Rect, lines, left_bound: float) -> str:
    """Nearest text on the same row, to the left, not crossing `left_bound`."""
    best, best_dx = "", 1e9
    for lb, text in lines:
        if not _same_row(lb, rect) or lb.x1 > rect.x0 + 2 or lb.x0 < left_bound - 2:
            continue
        dx = rect.x0 - lb.x1
        if dx < best_dx:
            best, best_dx = text, dx
    if best and best_dx <= LABEL_MAX_GAP:
        return _strip_underscores(best)
    return ""


def _label_right_of(rect: fitz.Rect, lines, right_bound: float) -> str:
    best, best_dx = "", 1e9
    for lb, text in lines:
        if not _same_row(lb, rect) or lb.x0 < rect.x1 - 2 or lb.x1 > right_bound + 2:
            continue
        dx = lb.x0 - rect.x1
        if dx < best_dx:
            best, best_dx = text, dx
    if best and best_dx <= 40:
        return _strip_underscores(best)
    return ""


def _label_above(rect: fitz.Rect, lines, used: set[str]) -> str:
    """Nearest text line above and to the left (a question/heading)."""
    best, best_dy = "", 1e9
    for lb, text in lines:
        if text in used or lb.y1 > rect.y0 + ROW_TOLERANCE or lb.x0 > rect.x1:
            continue
        dy = rect.y0 - lb.y1
        if 0 <= dy < best_dy and dy <= 120:
            best, best_dy = text, dy
    return _strip_underscores(best)


def detect(doc: fitz.Document) -> list[PageInfo]:
    pages: list[PageInfo] = []
    counter = 0
    for pno, page in enumerate(doc):
        info = PageInfo(pno, page.rect.width, page.rect.height)
        lines = _text_lines(page)

        # --- 1. real widgets ---------------------------------------------
        widgets = list(page.widgets())
        if widgets:
            for w in widgets:
                t = w.field_type
                if t in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                    kind, val = "check", bool(w.field_value) and w.field_value != "Off"
                elif t in (fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX):
                    kind, val = "text", w.field_value or ""
                else:
                    continue
                counter += 1
                info.spots.append(Spot(
                    id=f"s{counter}", page=pno, type=kind,
                    rect=[round(v, 2) for v in w.rect],
                    label=w.field_label or w.field_name or "",
                    widget=w.field_name, value=val,
                ))
            pages.append(info)
            continue

        # --- 2. underscore runs + drawn lines -----------------------------
        text_rects: list[fitz.Rect] = []
        for x0, y0, x1, y1, word, *_ in page.get_text("words"):
            if len(word) >= MIN_UNDERSCORES and set(word) <= set("_"):
                text_rects.append(fitz.Rect(x0, y0, x1, y1))

        drawings = page.get_drawings()
        for d in drawings:
            r = d["rect"]
            if r.height <= MAX_LINE_HEIGHT and r.width >= MIN_LINE_WIDTH:
                # skip if it duplicates an underscore run already found
                if any(abs(r.y1 - t.y1) < 4 and abs(r.x0 - t.x0) < 10 for t in text_rects):
                    continue
                # skip underlines: text sitting directly on top of the line
                if any(lb.x0 < r.x1 and lb.x1 > r.x0 and -8 <= r.y0 - lb.y1 <= 4 for lb, _ in lines):
                    continue
                # give it a typing box: 14 pt tall, sitting on the line
                text_rects.append(fitz.Rect(r.x0, r.y1 - 14, r.x1, r.y1 + 1))

        # --- 3. checkboxes -------------------------------------------------
        box_rects: list[fitz.Rect] = []
        for d in drawings:
            r = d["rect"]
            if BOX_MIN <= r.width <= BOX_MAX and BOX_MIN <= r.height <= BOX_MAX \
                    and abs(r.width - r.height) <= 3 and len(d["items"]) <= 5:
                # the same box is often drawn twice (border + shadow); keep one
                if any(abs(r.x0 - b.x0) < 6 and abs(r.y0 - b.y0) < 6 for b in box_rects):
                    continue
                box_rects.append(r)

        # --- labels --------------------------------------------------------
        text_rects.sort(key=lambda r: (round(r.y0 / ROW_TOLERANCE), r.x0))
        prev_label, prev_n = "", 0
        for r in text_rects:
            same_row_left = [t for t in text_rects if t is not r and _same_row(t, r) and t.x1 <= r.x0]
            left_bound = max((t.x1 for t in same_row_left), default=0.0)
            label = _label_left_of(r, lines, left_bound)
            if not label:
                # continuation line (address line 2, second child, …)
                prev_n += 1
                label = f"{prev_label} ({prev_n + 1})" if prev_label else "Blank"
            else:
                prev_label, prev_n = label, 0
            counter += 1
            info.spots.append(Spot(
                id=f"s{counter}", page=pno, type="text",
                rect=[round(v, 2) for v in r], label=label, value="",
            ))

        used_labels: set[str] = set()
        box_rects.sort(key=lambda r: (round(r.y0 / ROW_TOLERANCE), r.x0))
        box_spots: list[Spot] = []
        for r in box_rects:
            next_box_x = min((b.x0 for b in box_rects if _same_row(b, r) and b.x0 > r.x1), default=page.rect.width)
            label = _label_right_of(r, lines, next_box_x)
            used_labels.add(label)
            counter += 1
            box_spots.append(Spot(
                id=f"s{counter}", page=pno, type="check",
                rect=[round(v, 2) for v in r], label=label or "Checkbox", value=False,
            ))
        # group: the question text above/left (excluding the option labels)
        question_lines = [(lb, t) for lb, t in lines if t not in used_labels]
        for s in box_spots:
            r = fitz.Rect(s.rect)
            # options in one run (same row, or stacked closely) share a question
            anchor = next((a for a in box_spots if a is not s and (
                (_same_row(fitz.Rect(a.rect), r) and a.rect[0] < r.x0) or
                (abs(a.rect[0] - r.x0) < 6 and 0 < r.y0 - a.rect[3] < 30))), None)
            if anchor and anchor.group:
                s.group = anchor.group
                continue
            s.group = _label_left_of(r, question_lines, 0.0) or _label_above(r, lines, used_labels)
        info.spots.extend(box_spots)

        info.spots.sort(key=lambda s: (round(s.rect[1] / ROW_TOLERANCE), s.rect[0]))
        pages.append(info)
    return pages


# ----------------------------------------------------------------------------
# Filling
# ----------------------------------------------------------------------------

FONT = "helv"
CHECK_FONT = "zadb"     # ZapfDingbats — "4" is a check mark


def _fit_size(text: str, width: float, size: float, min_size: float = 6.0) -> float:
    while size > min_size and fitz.get_text_length(text, fontname=FONT, fontsize=size) > width:
        size -= 0.5
    return size


def fill(doc: fitz.Document, entries: Iterable[dict], default_size: float = 10.0) -> int:
    written = 0
    for e in entries:
        page = doc[int(e["page"])]
        kind = e.get("type", "text")
        value = e.get("value")
        rect = fitz.Rect(e["rect"])

        if e.get("widget"):
            for w in page.widgets():
                if w.field_name == e["widget"]:
                    if kind == "check":
                        w.field_value = bool(value)
                    else:
                        w.field_value = "" if value is None else str(value)
                    w.update()
                    written += 1
            continue

        if kind == "check":
            if not value:
                continue
            side = min(rect.width, rect.height)
            size = side * 0.85
            tw = fitz.get_text_length("4", fontname=CHECK_FONT, fontsize=size)
            x = rect.x0 + (rect.width - tw) / 2
            y = rect.y1 - (rect.height - size * 0.7) / 2
            page.insert_text((x, y), "4", fontname=CHECK_FONT, fontsize=size, color=(0, 0, 0))
            written += 1
            continue

        text = "" if value is None else str(value).strip()
        if not text:
            continue
        size = _fit_size(text, rect.width - 4, float(e.get("size") or default_size))
        # baseline a hair above the underline; underscore glyph sits ~3pt above bbox bottom
        baseline = rect.y1 - max(3.0, rect.height * 0.25)
        page.insert_text((rect.x0 + 2, baseline), text, fontname=FONT, fontsize=size, color=(0, 0, 0))
        written += 1
    return written


def flatten(doc: fitz.Document, dpi: int = 150, fmt: str = "jpeg", quality: int = 85) -> fitz.Document:
    out = fitz.open()
    for page in doc:
        pix = page.get_pixmap(dpi=dpi, alpha=False)
        img = pix.tobytes("png") if fmt == "png" else pix.tobytes("jpeg", jpg_quality=quality)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, stream=img)
    return out


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _cmd_detect(args) -> int:
    doc = fitz.open(args.input)
    pages = detect(doc)
    payload = {
        "file": str(args.input),
        "is_form": bool(doc.is_form_pdf),
        "pages": [
            {"index": p.index, "width": p.width, "height": p.height,
             "spots": [asdict(s) for s in p.spots]}
            for p in pages
        ],
    }
    if args.pretty:
        for p in pages:
            print(f"--- page {p.index + 1}")
            for s in p.spots:
                loc = f"({s.rect[0]:.0f},{s.rect[1]:.0f})"
                tag = f"[{s.group}] " if s.group else ""
                print(f"  {s.id:>4}  {s.type:5}  {loc:>10}  {tag}{s.label}")
    else:
        json.dump(payload, sys.stdout, indent=2)
        print()
    return 0


def _cmd_fill(args) -> int:
    data = json.loads(Path(args.data).read_text())
    entries = data["entries"] if isinstance(data, dict) else data
    doc = fitz.open(args.input)
    n = fill(doc, entries, default_size=args.size)
    if args.flatten:
        out = flatten(doc, dpi=args.dpi)
        out.save(args.output)
    else:
        doc.save(args.output, garbage=3, deflate=True)
    print(f"wrote {n} value(s) -> {args.output}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("detect", help="list fillable spots as JSON")
    d.add_argument("input")
    d.add_argument("--pretty", action="store_true", help="human-readable table instead of JSON")
    d.set_defaults(fn=_cmd_detect)

    f = sub.add_parser("fill", help="write values onto the PDF")
    f.add_argument("input")
    f.add_argument("output")
    f.add_argument("--data", required=True, help="JSON file with an 'entries' list")
    f.add_argument("--size", type=float, default=10.0, help="default font size (pt)")
    f.add_argument("--flatten", action="store_true", help="rasterize output (non-editable)")
    f.add_argument("--dpi", type=int, default=150, help="raster DPI when --flatten")
    f.set_defaults(fn=_cmd_fill)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
