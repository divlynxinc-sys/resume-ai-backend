from io import BytesIO

import anyio
import pytest
from docx import Document
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.middleware.resume_upload import ResumeUploadIngress
from app.utils import resume_upload


def test_ingress_limit_applies_to_uploads_only(monkeypatch):
    monkeypatch.setattr("app.middleware.resume_upload.MAX_UPLOAD_REQUEST_BYTES", 32)
    app = FastAPI()
    app.add_middleware(ResumeUploadIngress)

    @app.post("/resumes/parse-upload")
    @app.post("/ordinary")
    async def echo_size(request: Request):
        return {"size": len(await request.body())}

    client = TestClient(app)
    assert client.post("/resumes/parse-upload", content=b"valid").json() == {"size": 5}
    response = client.post("/resumes/parse-upload", content=iter([b"x" * 20, b"y" * 20]))
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "upload_too_large"
    assert client.post("/ordinary", content=b"x" * 40).json() == {"size": 40}


def test_worker_process_parses_normal_document_and_sanitizes_errors():
    doc = Document()
    doc.add_paragraph("Synthetic Candidate")
    doc.add_heading("Summary")
    doc.add_paragraph("This document is a test fixture.")
    output = BytesIO()
    doc.save(output)

    data = anyio.run(resume_upload._parse_in_worker, output.getvalue(), ".docx")
    assert data["info"]["full_name"] == "Synthetic Candidate"
    with pytest.raises(HTTPException) as bad:
        anyio.run(resume_upload._parse_in_worker, b"invalid document", ".pdf")
    assert bad.value.detail["code"] == "unreadable_document"


def test_capacity_rejects_new_job_and_can_be_reused(monkeypatch):
    async def check():
        active = anyio.Event()
        completed = anyio.Event()

        async def fixture_worker(*_args):
            active.set()
            await completed.wait()
            return {}

        monkeypatch.setattr(resume_upload, "_run_worker", fixture_worker)
        async with anyio.create_task_group() as group:
            group.start_soon(resume_upload._parse_in_worker, b"local fixture", ".docx")
            await active.wait()
            with pytest.raises(HTTPException) as blocked:
                await resume_upload._parse_in_worker(b"local fixture", ".docx")
            assert blocked.value.detail["code"] == "parser_busy"
            completed.set()
        assert await resume_upload._parse_in_worker(b"local fixture", ".docx") == {}

    anyio.run(check)
