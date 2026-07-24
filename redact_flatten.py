#!/usr/bin/env python3
"""
Redact one or more names in a PDF and flatten the whole document to an
image-only PDF. Flattening deletes all text/font metadata, so the redacted
name is truly gone (not just visually covered).

Handles two page types automatically:
  - Text-based pages   → uses PyMuPDF's search_for to find name locations,
                         covers with black rectangles, then rasterizes.
  - Scanned image pages → runs Tesseract OCR to find word-level bounding
                          boxes, matches names case-insensitively, covers
                          the raster with black rectangles, uses that as
                          the page content.

Requires:
    pip3 install pymupdf pytesseract pillow
    brew install tesseract

Usage:
    ./redact-flatten-pdf.py <input.pdf> <output.pdf> [options]

Options:
    --dpi 150            Raster DPI (default: 150). Higher = better OCR + larger files.
    --format jpeg|png    Raster format (default: jpeg — smaller files).
    --jpeg-quality 85    JPEG quality 1-100 (default: 85).
    --names ...          Names/strings to redact (default: NAMES list below).
    --ocr-dpi 300        DPI used for OCR pass on scanned pages (default: 300).
                         Kept separate from output DPI because OCR needs high resolution
                         to be accurate; output DPI can be lower to save file size.
"""

import argparse
import io
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.stderr.write("Missing PyMuPDF. Install with: pip3 install pymupdf\n")
    sys.exit(2)

try:
    from PIL import Image, ImageDraw
    import pytesseract
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


# Default phrase list: read from config/phrases.txt beside this script.
# If the file doesn't exist, exit with a friendly error (rather than silently
# using a hardcoded personal fallback — this is a standalone tool now).
DEFAULT_PHRASES_FILE = Path(__file__).resolve().parent / "config" / "phrases.txt"
FALLBACK_NAMES: list[str] = []


def load_phrases_from_file(path: Path) -> list[str]:
    """Read one phrase per line, skip blanks and comment lines starting with '#'."""
    phrases = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        phrases.append(line)
    return phrases


def has_text_layer(page: "fitz.Page") -> bool:
    """True if the page has any extractable text."""
    return len(page.get_text().strip()) > 0


def redact_text_page(page: "fitz.Page", names: list[str]) -> int:
    """Cover name matches on a text-based page. Returns match count."""
    count = 0
    for name in names:
        for rect in page.search_for(name):
            page.draw_rect(rect, color=(0, 0, 0), fill=(0, 0, 0), overlay=True)
            count += 1
    return count


MAX_MEGAPIXELS = 100  # PyMuPDF's pixmap ceiling; keeps memory sane


def _safe_dpi(page: "fitz.Page", requested_dpi: int) -> int:
    """Clamp requested DPI so the rasterized page stays under MAX_MEGAPIXELS."""
    w_pt, h_pt = page.rect.width, page.rect.height
    px_at_req = (w_pt * h_pt) * (requested_dpi / 72) ** 2
    if px_at_req <= MAX_MEGAPIXELS * 1_000_000:
        return requested_dpi
    # Solve for the DPI that lands exactly at MAX_MEGAPIXELS.
    safe = int(72 * (MAX_MEGAPIXELS * 1_000_000 / (w_pt * h_pt)) ** 0.5)
    return max(72, safe)  # never go below 72 DPI or OCR gets useless


def redact_scanned_page(page: "fitz.Page", names: list[str], ocr_dpi: int) -> tuple[Image.Image, int, int]:
    """
    OCR a scanned page, cover any matching name locations, return the
    modified PIL Image, match count, and the effective DPI used.
    Case-insensitive matching.
    """
    effective_dpi = _safe_dpi(page, ocr_dpi)
    scale = fitz.Matrix(effective_dpi / 72, effective_dpi / 72)
    pix = page.get_pixmap(matrix=scale, alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")

    # Get word-level bounding boxes from Tesseract.
    tsv = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    words = tsv["text"]
    lefts, tops, widths, heights = tsv["left"], tsv["top"], tsv["width"], tsv["height"]

    # Build a lowercase name set. For multi-word names ("Roie Ab") we need to
    # match consecutive OCR words on the same line, so we handle those separately.
    single_word_names = {n.lower() for n in names if " " not in n}
    multi_word_names = [n.lower().split() for n in names if " " in n]

    draw = ImageDraw.Draw(img)
    count = 0

    # Single-word matches: cover each match individually.
    for i, w in enumerate(words):
        if not w:
            continue
        # Strip punctuation for fair matching (e.g. "Roie," should match "roie")
        stripped = re.sub(r"[^\w]", "", w).lower()
        if stripped in single_word_names:
            x, y, dw, dh = lefts[i], tops[i], widths[i], heights[i]
            draw.rectangle([x, y, x + dw, y + dh], fill=(0, 0, 0))
            count += 1

    # Multi-word matches: slide across the word list looking for the sequence.
    lower_words = [re.sub(r"[^\w]", "", w).lower() for w in words]
    for name_parts in multi_word_names:
        n = len(name_parts)
        for i in range(len(lower_words) - n + 1):
            if lower_words[i:i + n] == name_parts:
                # Cover the joined bounding box of the n consecutive words.
                x0 = min(lefts[i + k] for k in range(n))
                y0 = min(tops[i + k] for k in range(n))
                x1 = max(lefts[i + k] + widths[i + k] for k in range(n))
                y1 = max(tops[i + k] + heights[i + k] for k in range(n))
                draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
                count += 1

    return img, count, effective_dpi


def redact_and_flatten(
    input_path: Path,
    output_path: Path,
    names: list[str],
    dpi: int,
    fmt: str,
    jpeg_quality: int,
    ocr_dpi: int,
) -> None:
    if not input_path.exists():
        sys.exit(f"error: input not found: {input_path}")
    if output_path.exists():
        sys.exit(f"error: output already exists (won't overwrite): {output_path}")

    src = fitz.open(input_path)
    dst = fitz.open()
    total_matches = 0
    pages_with_matches = 0
    scanned_pages = 0
    scale = fitz.Matrix(dpi / 72, dpi / 72)

    for page_num, page in enumerate(src, start=1):
        page_matches = 0

        if has_text_layer(page):
            # Text-based path: cover via PyMuPDF then rasterize normally.
            page_matches = redact_text_page(page, names)
            pix = page.get_pixmap(matrix=scale, alpha=False)
            if fmt == "jpeg":
                img_bytes = pix.tobytes("jpeg", jpg_quality=jpeg_quality)
            else:
                img_bytes = pix.tobytes("png")
            page_type = "text"
        else:
            # Scanned path: OCR the raster, cover matches on the raster.
            if not OCR_AVAILABLE:
                sys.exit(
                    "error: page has no text layer (scanned PDF) and OCR deps are missing.\n"
                    "  install: pip3 install pytesseract pillow && brew install tesseract"
                )
            scanned_pages += 1
            img, page_matches, effective_ocr_dpi = redact_scanned_page(page, names, ocr_dpi)
            if effective_ocr_dpi != ocr_dpi:
                print(f"    (OCR DPI clamped from {ocr_dpi} to {effective_ocr_dpi} — page too large)")
            # Rescale to output DPI (OCR happens at effective_ocr_dpi, final output at dpi).
            if effective_ocr_dpi != dpi:
                w = int(img.width * dpi / effective_ocr_dpi)
                h = int(img.height * dpi / effective_ocr_dpi)
                # Also clamp output image to safe megapixel range
                out_mp = (w * h) / 1_000_000
                if out_mp > MAX_MEGAPIXELS:
                    shrink = (MAX_MEGAPIXELS / out_mp) ** 0.5
                    w = int(w * shrink)
                    h = int(h * shrink)
                img = img.resize((w, h), Image.LANCZOS)
            buf = io.BytesIO()
            if fmt == "jpeg":
                img.save(buf, "JPEG", quality=jpeg_quality)
            else:
                img.save(buf, "PNG")
            img_bytes = buf.getvalue()
            page_type = "scan"

        pix_w, pix_h = fitz.Pixmap(io.BytesIO(img_bytes)).width, fitz.Pixmap(io.BytesIO(img_bytes)).height
        img_page = dst.new_page(width=pix_w, height=pix_h)
        img_page.insert_image(img_page.rect, stream=img_bytes)

        if page_matches:
            pages_with_matches += 1
            total_matches += page_matches
            print(f"  page {page_num} ({page_type}): {page_matches} match(es) redacted")
        else:
            print(f"  page {page_num} ({page_type}): no matches")

    dst.save(output_path)
    dst.close()
    src.close()

    src_size = input_path.stat().st_size
    dst_size = output_path.stat().st_size
    print()
    print(f"Redacted {total_matches} occurrence(s) across {pages_with_matches} page(s).")
    if scanned_pages:
        print(f"Scanned pages OCR'd: {scanned_pages}")
    print(f"Input:  {input_path}  ({src_size/1024:.0f} KB)")
    print(f"Output: {output_path}  ({dst_size/1024:.0f} KB, image-flattened at {dpi} dpi)")


def main() -> None:
    p = argparse.ArgumentParser(description="Redact names in a PDF and flatten to image-only PDF.")
    p.add_argument("input", type=Path, help="Input PDF path.")
    p.add_argument("output", type=Path, help="Output PDF path (must not exist).")
    p.add_argument("--dpi", type=int, default=150, help="Output raster DPI (default: 150).")
    p.add_argument(
        "--format",
        choices=["jpeg", "png"],
        default="jpeg",
        help="Raster format (default: jpeg).",
    )
    p.add_argument("--jpeg-quality", type=int, default=85, help="JPEG quality 1-100 (default: 85).")
    p.add_argument("--ocr-dpi", type=int, default=300, help="DPI for OCR pass on scanned pages (default: 300).")
    p.add_argument(
        "--phrases-file",
        type=Path,
        default=DEFAULT_PHRASES_FILE,
        help=f"Path to phrases file (default: {DEFAULT_PHRASES_FILE}).",
    )
    p.add_argument(
        "--names",
        nargs="+",
        default=None,
        help="Explicit phrase list; overrides --phrases-file when provided.",
    )
    args = p.parse_args()

    # Resolve which phrase list to use.
    if args.names is not None:
        phrases = args.names
        source = "--names flag"
    elif args.phrases_file.exists():
        phrases = load_phrases_from_file(args.phrases_file)
        source = str(args.phrases_file)
    else:
        example = args.phrases_file.with_name("phrases.example.txt")
        hint = f"  cp {example} {args.phrases_file}" if example.exists() else ""
        sys.exit(
            f"error: no phrases file at {args.phrases_file}\n"
            f"  create one (see phrases.example.txt) or pass --names / --phrases-file.\n"
            f"{hint}"
        )

    if not phrases:
        sys.exit(f"error: no phrases to redact (source: {source} is empty).")

    print(f"Phrases ({len(phrases)}) from {source}:")
    for phrase in phrases:
        print(f"  - {phrase}")
    print()

    redact_and_flatten(
        args.input,
        args.output,
        phrases,
        args.dpi,
        args.format,
        args.jpeg_quality,
        args.ocr_dpi,
    )


if __name__ == "__main__":
    main()
