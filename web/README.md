# UMBRA

A web UI for [`redact_flatten.py`](../redact_flatten.py). Bold, single-page,
dark, dossier-style. Runs locally on loopback only.

![tag](https://img.shields.io/badge/pdfs-in%20the%20shadow-black)

## What it does

- Upload a PDF (drag-drop or click)
- Optionally override the phrase list for this run
- Pick output DPI, format (JPEG/PNG), JPEG quality, OCR DPI
- Click **Redact ▸**
- Download the redacted PDF; see the CLI log inline

Underneath: same `redact_flatten.py` you'd run from the command line — the
web server just wraps it as a subprocess.

## Install

```bash
pip3 install fastapi 'uvicorn[standard]' python-multipart
```

Requires the CLI's own deps too: `pymupdf`, `pytesseract`, `Pillow`, and
the `tesseract` binary (`brew install tesseract`). Those are covered by the
main [README](../README.md#install).

## Run

```bash
cd ~/Developer/pdf-redact/web
python3 server.py
```

Then open http://127.0.0.1:8000 in your browser.

Different port:
```bash
UMBRA_PORT=9000 python3 server.py
```

## Security notes

- **Loopback bind only** (`127.0.0.1`). Do not change to `0.0.0.0` without
  understanding the exposure — this endpoint runs arbitrary user-supplied
  filenames through a subprocess.
- Uploads land in `/private/var/folders/.../umbra-XXX/`. The subprocess reads
  them and writes the output next to the input. Temp dirs are not
  auto-purged — restart the server occasionally, or add a cleanup hook if
  you use it heavily.
- The endpoint is not authenticated. On your Mac this is fine (loopback
  can't be reached from other devices without extra plumbing). If you ever
  want remote access, put it behind a reverse proxy with auth.

## Files

```
web/
├── server.py       FastAPI app: /  (HTML)  +  POST /api/redact
├── index.html      Single-page frontend (no build step, no framework)
└── README.md       This file
```

## Design

The look — declassified dossier: near-black background, warm off-white
paper text, monospace labels for metadata, red "DECLASSIFIED" stamp,
solid black redaction bars as a decorative element. Feels like the file
came out of a filing cabinet, not a browser.

## Alias

If you'd like a shortcut:
```bash
echo "alias umbra='cd ~/Developer/pdf-redact/web && python3 server.py'" >> ~/.zshrc
source ~/.zshrc
umbra
```
