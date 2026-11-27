import json
import re
import sys
from contextlib import suppress
from pathlib import Path
from subprocess import DEVNULL, PIPE

import anyio
from fastapi import HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as FormUpload

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
WORKER_TIMEOUT_SECONDS = 8
MAX_RESULT_BYTES = 2 * 1024 * 1024
_parser_slots = anyio.CapacityLimiter(1)

ERRORS = {
    "unsupported_upload": (400, "Upload a PDF or DOCX file."),
    "invalid_multipart": (400, "Upload one resume file at a time."),
    "upload_too_large": (413, "The file is larger than 10 MB."),
    "unreadable_document": (422, "The document is invalid or protected. Try a readable PDF or DOCX."),
    "document_limit_exceeded": (422, "The document exceeds the supported resume limits."),
    "no_extractable_text": (422, "We could not extract text. Use a text-based PDF or DOCX."),
    "parse_timeout": (422, "The document took too long to read. Try a shorter resume."),
    "parser_busy": (503, "Resume import is busy. Try again in a moment."),
    "parser_unavailable": (503, "Resume import is temporarily unavailable."),
}


def upload_error(code: str) -> HTTPException:
    status, message = ERRORS[code]
    headers = {"Retry-After": "2"} if code == "parser_busy" else None
    return HTTPException(status, detail={"code": code, "message": message}, headers=headers)


async def _write_input(stream, data: bytes) -> None:
    with suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
        await stream.send(data)
        await stream.aclose()


async def _run_worker(data: bytes, extension: str, include_review: bool) -> dict:
    process = None
    try:
        with anyio.fail_after(WORKER_TIMEOUT_SECONDS):
            args = [sys.executable, "-m", "app.utils.resume_parse_worker", extension]
            if include_review:
                args.append("review")
            process = await anyio.open_process(
                args,
                stdin=PIPE, stdout=PIPE, stderr=DEVNULL,
            )
            output = bytearray()
            exceeded_output = False
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(_write_input, process.stdin, data)
                async for chunk in process.stdout:
                    if len(output) + len(chunk) > MAX_RESULT_BYTES:
                        exceeded_output = True
                        tasks.cancel_scope.cancel()
                        break
                    output.extend(chunk)
            if exceeded_output:
                raise upload_error("document_limit_exceeded")
            code = await process.wait()
        if code < 0:
            raise upload_error("document_limit_exceeded")
        if code or not output:
            raise upload_error("parser_unavailable")
        result = json.loads(output)
        if "error" in result:
            raise upload_error(result["error"] if result["error"] in ERRORS else "parser_unavailable")
        if not isinstance(result.get("content"), dict):
            raise upload_error("parser_unavailable")
        return result["content"]
    except TimeoutError as exc:
        raise upload_error("parse_timeout") from exc
    except (OSError, ValueError) as exc:
        raise upload_error("parser_unavailable") from exc
    finally:
        if process is not None:
            with anyio.CancelScope(shield=True):
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
                await process.aclose()


async def _parse_in_worker(data: bytes, extension: str, include_review: bool = False) -> dict:
    try:
        _parser_slots.acquire_nowait()
    except anyio.WouldBlock as exc:
        raise upload_error("parser_busy") from exc
    try:
        return await _run_worker(data, extension, include_review)
    finally:
        _parser_slots.release()


async def _validate_form(request: Request) -> None:
    form = await request.form()
    files = [value for _, value in form.multi_items() if isinstance(value, FormUpload)]
    if len(files) != 1:
        raise upload_error("invalid_multipart")


def parse_resume_upload(file: UploadFile, request: Request, *, include_review: bool = False) -> dict:
    anyio.from_thread.run(_validate_form, request)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise upload_error("unsupported_upload")
    try:
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        file.file.close()
    if len(data) > MAX_UPLOAD_BYTES:
        raise upload_error("upload_too_large")
    if not data:
        raise upload_error("unreadable_document")
    return anyio.from_thread.run(_parse_in_worker, data, extension, include_review)


def is_resume_upload(method: str, path: str) -> bool:
    return (method == "POST" and path in {"/resumes/parse-upload", "/resumes/from-upload"}
            or method == "PATCH" and re.fullmatch(r"/resumes/[^/]+/from-upload", path) is not None)
