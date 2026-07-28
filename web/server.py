#!/usr/bin/env python3
"""
Umbra — web UI for the pdf-redact CLI.

Runs on http://127.0.0.1:8000 by default. Serves a single-page frontend +
one POST endpoint that shells out to redact_flatten.py.

Bind is loopback-only. Do NOT expose to the internet — this endpoint writes
files to a temp dir and runs a subprocess with user-supplied args.

Requires:
    pip3 install fastapi 'uvicorn[standard]' python-multipart
    plus the CLI's own deps: pymupdf, pytesseract, Pillow, tesseract binary

Run:
    python3 server.py                 # http://127.0.0.1:8000
    UMBRA_PORT=9000 python3 server.py # different port
"""

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "redact_flatten.py"
INDEX_HTML = Path(__file__).resolve().parent / "index.html"

app = FastAPI(title="Umbra", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    if not INDEX_HTML.exists():
        raise HTTPException(500, f"index.html not found at {INDEX_HTML}")
    return HTMLResponse(INDEX_HTML.read_text())


@app.post("/api/redact")
async def redact(
    file: UploadFile = File(...),
    names: Optional[str] = Form(None),
    dpi: int = Form(150),
    fmt: str = Form("jpeg"),
    jpeg_quality: int = Form(85),
    ocr_dpi: int = Form(300),
):
    """
    Accept a PDF + options, run redact_flatten.py, return the redacted PDF.

    `names` is a newline- or comma-separated list. If omitted, the CLI's
    default (phrases.txt or built-in fallback) is used.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "please upload a .pdf file")
    if fmt not in ("jpeg", "png"):
        raise HTTPException(400, "format must be 'jpeg' or 'png'")
    if not (72 <= dpi <= 600):
        raise HTTPException(400, "dpi out of range (72-600)")
    if not (1 <= jpeg_quality <= 100):
        raise HTTPException(400, "jpeg_quality out of range (1-100)")

    workdir = Path(tempfile.mkdtemp(prefix="umbra-"))
    input_path = workdir / "input.pdf"
    output_path = workdir / f"redacted-{file.filename}"

    # Persist the uploaded PDF
    with input_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # Build the CLI invocation
    cmd = [
        sys.executable,
        str(CLI),
        str(input_path),
        str(output_path),
        "--dpi", str(dpi),
        "--format", fmt,
        "--jpeg-quality", str(jpeg_quality),
        "--ocr-dpi", str(ocr_dpi),
    ]

    parsed_names = _parse_names(names)
    if parsed_names:
        cmd.extend(["--names", *parsed_names])

    # Run the CLI subprocess. Capture stdout/stderr for the client.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout_bytes, _ = await proc.communicate()
    log = stdout_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0 or not output_path.exists():
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(
            500,
            f"redact_flatten.py exited {proc.returncode}\n\n{log}",
        )

    # Include the CLI log in a response header for the UI to show.
    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename=output_path.name,
        headers={"X-Umbra-Log": _sanitize_header(log)},
        background=None,  # keep the file on disk so the download can complete
    )


def _parse_names(names_field: Optional[str]) -> list[str]:
    """Accept commas or newlines as separators."""
    if not names_field:
        return []
    raw = names_field.replace("\r", "\n").replace(",", "\n").split("\n")
    return [n.strip() for n in raw if n.strip()]


def _sanitize_header(s: str) -> str:
    """HTTP headers can't hold newlines or non-ASCII cleanly. Fold + strip."""
    return s.replace("\r", " ").replace("\n", " | ").encode("ascii", "ignore").decode("ascii")[:4000]


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("UMBRA_PORT", "8000"))
    print(f"\n  🕶  Umbra — running at http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
