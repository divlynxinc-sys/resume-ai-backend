import anyio
from starlette.responses import JSONResponse

from app.utils.resume_upload import MAX_UPLOAD_REQUEST_BYTES, is_resume_upload, upload_error


class ResumeUploadIngress:
    def __init__(self, app):
        self.app = app
        self.slots = anyio.CapacityLimiter(3)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_resume_upload(scope["method"], scope["path"].rstrip("/")):
            await self.app(scope, receive, send)
            return

        length = dict(scope["headers"]).get(b"content-length", b"")
        if length.isdigit() and (len(length) > 9 or int(length) > MAX_UPLOAD_REQUEST_BYTES):
            await self._reject("upload_too_large", scope, receive, send)
            return

        try:
            self.slots.acquire_nowait()
        except anyio.WouldBlock:
            await self._reject("parser_busy", scope, receive, send)
            return

        try:
            body = bytearray()
            try:
                with anyio.fail_after(15):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > MAX_UPLOAD_REQUEST_BYTES:
                            await self._reject("upload_too_large", scope, receive, send)
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                response = JSONResponse(status_code=408, content={
                    "detail": {"code": "upload_incomplete", "message": "The upload did not finish. Try again."},
                })
                await response(scope, receive, send)
                return

            pending = True

            async def bounded_receive():
                nonlocal pending
                if not pending:
                    return await receive()
                pending = False
                buffered = bytes(body)
                body.clear()
                return {"type": "http.request", "body": buffered, "more_body": False}

            await self.app(scope, bounded_receive, send)
        finally:
            self.slots.release()

    @staticmethod
    async def _reject(code, scope, receive, send):
        error = upload_error(code)
        response = JSONResponse(status_code=error.status_code, headers=error.headers, content={"detail": error.detail})
        await response(scope, receive, send)
