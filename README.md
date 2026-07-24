# pdf-redact

Redact names (or arbitrary phrases) from a PDF and flatten it to an image-only
PDF. Flattening deletes the text layer entirely — the redacted phrase is
literally gone, not just visually covered.

Handles both text-based PDFs (via PyMuPDF's `search_for`) and scanned image
PDFs (via Tesseract OCR) automatically. You don't specify which type; the
script detects and picks the right path per-page.

## Install

**System deps** (macOS):
```bash
brew install tesseract
```

**Python deps**:
```bash
pip3 install -r requirements.txt
```

## Setup

Copy the example phrase list and edit with your own identifiers:
```bash
cp config/phrases.example.txt config/phrases.txt
$EDITOR config/phrases.txt
```

`config/phrases.txt` is gitignored — your real values stay local.

## Usage

Basic:
```bash
python3 redact_flatten.py input.pdf output.pdf
```

Override phrase list for one document:
```bash
python3 redact_flatten.py input.pdf output.pdf --names "Some Corp" "Case #12345"
```

Higher-quality output for print:
```bash
python3 redact_flatten.py input.pdf output.pdf --dpi 300 --jpeg-quality 95
```

Lossless output for line art / technical diagrams:
```bash
python3 redact_flatten.py input.pdf output.pdf --format png
```

See all options: `python3 redact_flatten.py --help`

## Optional: shell alias

```bash
echo "alias redact-pdf='python3 $HOME/Developer/pdf-redact/redact_flatten.py'" >> ~/.zshrc
source ~/.zshrc
```

Then just:
```bash
redact-pdf input.pdf output.pdf
```

## How it works

For each page:
1. **Check for a text layer.** If present → PyMuPDF's `search_for` finds
   phrase occurrences and covers each match with a black rectangle at PDF
   coordinates. Then rasterize.
2. **If no text layer (scan)** → OCR the rasterized page with Tesseract to
   get word-level bounding boxes. Match phrases (case-insensitive,
   punctuation-stripped, multi-word supported). Draw black rectangles on the
   raster.
3. Insert the rasterized image as the sole content of the output PDF page.
   No fonts, no text, no metadata.

Text-based pages: fast (seconds), high accuracy, small output.
Scanned pages: slower (10–60s per page), decent accuracy, larger output.

## Caveats

**Case sensitivity differs by path.**
Text-based: exact case (`search_for("Roie")` won't match `roie`).
OCR: case-insensitive with punctuation stripped.
For text-based PDFs, add every case variant to your phrases file.

**OCR is not perfect.**
Small fonts, stylized text, low-res scans may not be recognized. Very large
pages are auto-clamped to ≤100 megapixels so OCR runs at a lower DPI than
requested — printed at bottom of output. If a phrase appears in the PDF but
isn't caught, options: raise `--ocr-dpi`, add a variant to the phrases list,
or manually cover with an image editor.

**Redaction covers rectangles, not context.**
`"Roie"` matches all standalone occurrences of the word. If your name appears
inside `"Roiebot"` or a filename, that gets partially covered. Add specific
patterns to the phrases list rather than trying to be too broad.

**The output is bigger than the input.**
Flattening a 2 KB text PDF produces a ~90 KB image-flattened PDF. A 1 MB
document typically becomes 2–4 MB. Cost of no text layer.

## Files

```
pdf-redact/
├── redact_flatten.py            Main script
├── config/
│   ├── phrases.example.txt      Template (versioned)
│   └── phrases.txt              Your real list (gitignored)
├── requirements.txt             Python deps
├── .gitignore
└── README.md
```

## License

Personal tool; no license granted. Feel free to fork and adapt for your own use.
