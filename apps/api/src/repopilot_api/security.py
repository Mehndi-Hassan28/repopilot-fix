"""Opaque database-backed sessions and CSRF enforcement."""

import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from repopilot_api.dependencies import get_db
from repopilot_api.models import AuthSession, User

SESSION_COOKIE_NAME = "repopilot_session"
OAUTH_STATE_COOKIE_NAME = "repopilot_oauth_state"
CSRF_HEADER_NAME = "X-CSRF-Token"
DEFAULT_SESSION_TTL_SECONDS = 60 * 60 * 24 * 7


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_token_for_session(token: str) -> str:
    return hmac.new(token.encode("utf-8"), b"repopilot-csrf-v1", hashlib.sha256).hexdigest()


def cookie_secure() -> bool:
    return os.getenv("COOKIE_SECURE", "true").lower() in {"true", "1", "yes"}


def session_ttl_seconds() -> int:
    try:
        configured = int(os.getenv("SESSION_TTL_SECONDS", str(DEFAULT_SESSION_TTL_SECONDS)))
        return min(max(configured, 300), 30 * 86400)
    except ValueError:
        return DEFAULT_SESSION_TTL_SECONDS


def configured_origins() -> set[str]:
    return {
        origin.strip().rstrip("/")
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip() and origin.strip() != "*"
    }


def _resolve_auth(request: Request, db: Session) -> AuthContext:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token or len(token) > 256:
        raise HTTPException(401, "Authentication required.")
    row = db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(
            AuthSession.token_hash == digest(token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > datetime.now(UTC),
            User.disabled.is_(False),
        )
    ).first()
    if row is None:
        raise HTTPException(401, "Authentication required.")
    return AuthContext(user=row[1], session=row[0])


def get_current_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    return _resolve_auth(request, db)


def get_optional_auth(request: Request, db: Session = Depends(get_db)) -> AuthContext | None:
    try:
        return _resolve_auth(request, db)
    except HTTPException as exc:
        if exc.status_code == 401:
            return None
        raise


def require_csrf(
    request: Request, auth: AuthContext = Depends(get_current_auth)
) -> AuthContext:
    origin = request.headers.get("origin", "").rstrip("/")
    supplied = request.headers.get(CSRF_HEADER_NAME, "")
    if origin not in configured_origins() or not supplied:
        raise HTTPException(403, "CSRF validation failed.")
    if not hmac.compare_digest(digest(supplied), auth.session.csrf_hash):
        raise HTTPException(403, "CSRF validation failed.")
    return auth
