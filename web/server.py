#!/usr/bin/env python3
"""
Quill — web UI for fill_form.py.

Runs on http://127.0.0.1:8000 by default. Serves a single-page frontend and
three endpoints:

    POST /api/inspect            upload a PDF -> page images + detected spots
    GET  /api/page/{token}/{n}   rendered page PNG
    POST /api/fill               token + entries -> filled PDF download

Bind is loopback-only. Do NOT expose to the internet — uploads are written
to a temp dir and there is no authentication.

Requires:
    pip3 install fastapi 'uvicorn[standard]' python-multipart pymupdf

Run:
    python3 server.py                 # http://127.0.0.1:8000
    QUILL_PORT=9000 python3 server.py # different port
"""

import os
import re
import secrets
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import fill_form  # noqa: E402

INDEX_HTML = Path(__file__).resolve().parent / "index.html"
RENDER_DPI = 144
SESSIONS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="Quill", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    if not INDEX_HTML.exists():
        raise HTTPException(500, f"index.html not found at {INDEX_HTML}")
    return HTMLResponse(INDEX_HTML.read_text())


@app.post("/api/inspect")
async def inspect(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "please upload a .pdf file")

    token = secrets.token_urlsafe(12)
    workdir = Path(tempfile.mkdtemp(prefix="quill-"))
    input_path = workdir / "input.pdf"
    with input_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        doc = fitz.open(input_path)
        pages = fill_form.detect(doc)
        for page in doc:
            page.get_pixmap(dpi=RENDER_DPI, alpha=False).save(workdir / f"p{page.number}.png")
    except Exception as e:  # corrupt / encrypted / not really a PDF
        shutil.rmtree(workdir, ignore_errors=True)
        raise HTTPException(400, f"could not read PDF: {e}")

    SESSIONS[token] = {"dir": workdir, "name": file.filename, "pages": len(doc)}
    return {
        "token": token,
        "filename": file.filename,
        "is_form": bool(doc.is_form_pdf),
        "pages": [
            {
                "index": p.index, "width": p.width, "height": p.height,
                "image": f"/api/page/{token}/{p.index}",
                "spots": [asdict(s) for s in p.spots],
            }
            for p in pages
        ],
    }


@app.get("/api/page/{token}/{n}")
async def page_image(token: str, n: int):
    sess = _session(token)
    path = sess["dir"] / f"p{n}.png"
    if not path.exists():
        raise HTTPException(404, "no such page")
    return FileResponse(path, media_type="image/png")


class Entry(BaseModel):
    page: int
    type: str = "text"
    rect: list[float]
    value: Any = None
    size: Optional[float] = None
    widget: Optional[str] = None


class FillRequest(BaseModel):
    token: str
    entries: list[Entry]
    flatten: bool = False
    size: float = 10.0
    dpi: int = 150


@app.post("/api/fill")
async def fill(req: FillRequest):
    sess = _session(req.token)
    if not (6 <= req.size <= 36):
        raise HTTPException(400, "size out of range (6-36)")
    if not (72 <= req.dpi <= 600):
        raise HTTPException(400, "dpi out of range (72-600)")

    doc = fitz.open(sess["dir"] / "input.pdf")
    n = fill_form.fill(doc, [e.model_dump() for e in req.entries], default_size=req.size)
    out_name = "filled-" + re.sub(r"[^\w.\- ]", "_", sess["name"])
    out_path = sess["dir"] / out_name
    if req.flatten:
        fill_form.flatten(doc, dpi=req.dpi).save(out_path)
    else:
        doc.save(out_path, garbage=3, deflate=True)

    return FileResponse(
        out_path, media_type="application/pdf", filename=out_name,
        headers={"X-Quill-Written": str(n)},
    )


def _session(token: str) -> dict[str, Any]:
    sess = SESSIONS.get(token)
    if not sess or not sess["dir"].exists():
        raise HTTPException(404, "session expired — upload the PDF again")
    return sess


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("QUILL_PORT", "8000"))
    print(f"\n  🖋  Quill — running at http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
