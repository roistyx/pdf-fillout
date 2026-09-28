#!/usr/bin/env python3
"""
Quill — web UI for fill_form.py.

Runs on http://127.0.0.1:8000 by default. Serves a single-page frontend and
three endpoints:

    POST /api/inspect            upload a PDF -> page images + detected spots
    GET  /api/page/{token}/{n}   rendered page PNG
    POST /api/fill               token + entries -> filled PDF download
    GET  /api/profile            local autofill profile (config/profile.json)
    PUT  /api/profile            replace the profile

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

import json

import fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
import fill_form  # noqa: E402

INDEX_HTML = Path(__file__).resolve().parent / "index.html"
PROFILE_PATH = REPO_ROOT / "config" / "profile.json"
PROFILE_KINDS = ("name", "phone", "email", "address", "date", "language", "other")
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


class ProfileEntry(BaseModel):
    label: str = Field(max_length=120)
    kind: str = "other"
    value: str = Field(max_length=500)


class Profile(BaseModel):
    entries: list[ProfileEntry] = Field(default_factory=list, max_length=200)


@app.get("/api/profile")
async def get_profile():
    if not PROFILE_PATH.exists():
        return {"entries": [], "path": str(PROFILE_PATH)}
    try:
        data = json.loads(PROFILE_PATH.read_text())
        prof = Profile.model_validate(data)
    except Exception as e:
        raise HTTPException(500, f"profile.json is not valid: {e}")
    return {"entries": [e.model_dump() for e in prof.entries], "path": str(PROFILE_PATH)}


@app.put("/api/profile")
async def put_profile(prof: Profile):
    cleaned = []
    for e in prof.entries:
        if e.kind not in PROFILE_KINDS:
            raise HTTPException(400, f"unknown kind {e.kind!r}")
        if e.label.strip() or e.value.strip():
            cleaned.append(ProfileEntry(label=e.label.strip(), kind=e.kind, value=e.value.strip()))
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"entries": [e.model_dump() for e in cleaned]}, indent=2) + "\n")
    tmp.replace(PROFILE_PATH)
    return {"entries": [e.model_dump() for e in cleaned], "path": str(PROFILE_PATH)}


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
