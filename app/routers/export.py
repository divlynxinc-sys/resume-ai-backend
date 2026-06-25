"""
Resume export endpoints.

`/resumes/export/pdf`  - renders template HTML (built on the frontend) to a real,
                          text + clickable-link PDF via headless Chromium.
`/resumes/export/docx` - builds a genuine Word document from structured content.

Both require an authenticated user. They are stateless (no DB write) and operate
on the payload the client sends, so they work for both saved and unsaved drafts.
"""
from __future__ import annotations

from urllib.parse import quote
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.security import get_current_user
from app.models.user import User
from app.utils.pdf_renderer import render_html_to_pdf, PdfRenderError
from app.utils.docx_builder import build_resume_docx

router = APIRouter(prefix="/resumes/export", tags=["Resume Export"])


def _safe_filename(name: Optional[str], default: str, ext: str) -> str:
    base = (name or default).strip() or default
    # Strip path separators / control chars; keep it simple and safe.
    base = "".join(c for c in base if c.isalnum() or c in " ._-()").strip() or default
    if base.lower().endswith(f".{ext}"):
        base = base[: -(len(ext) + 1)]
    return f"{base}.{ext}"


def _content_disposition(filename: str) -> str:
    # RFC 5987 so non-ASCII names survive.
    return f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"


class PdfExportRequest(BaseModel):
    html: str = Field(..., description="Fully-rendered resume template HTML")
    filename: Optional[str] = Field(default=None, description="Desired download filename")


class DocxExportRequest(BaseModel):
    content: dict = Field(..., description="Structured resume content (TemplateInput shape)")
    filename: Optional[str] = Field(default=None)


@router.post("/pdf")
def export_pdf(payload: PdfExportRequest, user: User = Depends(get_current_user)):
    try:
        pdf_bytes = render_html_to_pdf(payload.html)
    except PdfRenderError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the client
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"PDF render failed: {exc}")

    filename = _safe_filename(payload.filename, "Resume", "pdf")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(filename)},
    )


@router.post("/docx")
def export_docx(payload: DocxExportRequest, user: User = Depends(get_current_user)):
    try:
        docx_bytes = build_resume_docx(payload.content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"DOCX build failed: {exc}")

    filename = _safe_filename(payload.filename, "Resume", "docx")
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": _content_disposition(filename)},
    )
