from datetime import datetime, timedelta, timezone
from typing import Iterable, Set

from fastapi import Request
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.config import Roles
from app.database.connection import SessionLocal
from app.models.session_tracking import UserSessionLog
from app.models.user import User
from app.utils.auth_utils import decode_token


class UserSessionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, ignored_paths: Iterable[str] | None = None, idle_threshold_minutes: int = 1):
        super().__init__(app)
        self.ignored_paths: Set[str] = set(ignored_paths or [])
        self.idle_threshold = timedelta(minutes=idle_threshold_minutes)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path.rstrip("/") + "/"
        if self._is_ignored(path):
            return await call_next(request)

        authorization = request.headers.get("authorization") or request.headers.get("Authorization")
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()

        if not token:
            return await call_next(request)

        payload = decode_token(token)
        if not payload or payload.get("type") != "access":
            return await call_next(request)

        user_id = payload.get("sub")
        token_version = payload.get("tv")
        try:
            user_id_int = int(user_id)
        except Exception:
            return await call_next(request)

        db: Session = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id_int).first()
            if not user or int(user.token_version or 1) != token_version:
                return await call_next(request)

            client = request.client
            ip = client.host if client else None
            ua = request.headers.get("user-agent")
            now = datetime.now(timezone.utc)

            # Find existing session row grouped by (user_id, user_agent, ip)
            session = (
                db.query(UserSessionLog)
                .filter(
                    UserSessionLog.user_id == user.id,
                    UserSessionLog.user_agent == ua,
                    UserSessionLog.ip_address == ip,
                )
                .first()
            )
            if session:
                gap_ok = (now - session.last_seen) >= self.idle_threshold
                session.last_seen = now if gap_ok else now
                session.last_path = path
                session.updated_at = now
                db.add(session)
            else:
                session = UserSessionLog(
                    user_id=user.id,
                    ip_address=ip,
                    user_agent=ua,
                    last_path=path,
                    last_seen=now,
                    created_at=now,
                    updated_at=now,
                )
                db.add(session)
            db.commit()
        finally:
            db.close()

        return await call_next(request)

    def _is_ignored(self, normalized_path: str) -> bool:
        if normalized_path == "//":
            return True
        # Simple exact match on normalized paths like "/auth/login/"
        return normalized_path in self.ignored_paths

