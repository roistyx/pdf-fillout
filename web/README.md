# QUILL

A web UI for [`fill_form.py`](../fill_form.py). Single page, no build
step, runs locally on loopback only.

## What it does

- Upload a PDF (drag-drop or click)
- Pages are rendered; detected blanks and checkboxes are overlaid as inputs
- Type straight on the page, or in the sidebar field list (two-way synced)
- Click any empty spot on a page to add free text; hover it and hit × to remove
- **Fill from profile** drops your saved name / phone / address entries
  into matching fields; **Edit profile** manages them (saved to
  `config/profile.json`, gitignored)
- Pick font size and whether to flatten (rasterize) the output
- **Fill ▸** → download the filled PDF

## Install

```bash
pip3 install fastapi 'uvicorn[standard]' python-multipart pymupdf
```

## Run

```bash
cd ~/Developer/PDF-fillout/web
python3 server.py                  # http://127.0.0.1:8000
QUILL_PORT=9000 python3 server.py  # different port
```

## Where things go

- The uploaded PDF and the filled output live in a temp dir
  (`/private/var/folders/.../quill-*`); the original in your Downloads
  folder is untouched.
- **Download** saves `filled-<original name>.pdf` through the browser.
- Typed values live only in the page; reload and they are gone. Use the
  profile for anything you want to keep.
- The profile is written to `config/profile.json` at the repo root.

## API

| Method | Path                     | Purpose                                      |
|--------|--------------------------|----------------------------------------------|
| POST   | `/api/inspect`           | multipart `file` → `{token, pages[...]}`     |
| GET    | `/api/page/{token}/{n}`  | rendered PNG of page `n` (144 dpi)           |
| POST   | `/api/fill`              | `{token, entries, flatten, size}` → PDF      |
| GET    | `/api/profile`           | autofill entries from `config/profile.json`  |
| PUT    | `/api/profile`           | replace the profile file                      |

Sessions live in memory and their temp dirs (`quill-*`) are not purged —
restart the server occasionally.

## Security notes

- **Loopback bind only** (`127.0.0.1`). Uploads are written to a temp dir
  and there is no authentication; do not expose this.
- The frontend loads no external scripts, fonts, or analytics. Every
  request goes to the local server.

## Files

```
web/
├── server.py    FastAPI app: /  (HTML) + the API above
├── index.html   single-page frontend (no build step, no framework)
└── README.md    this file
```
