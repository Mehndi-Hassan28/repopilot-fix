"""GitHub OAuth login with one-time browser-bound state and opaque server sessions."""

import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from repopilot_api.dependencies import get_db
from repopilot_api.models import AuthSession, OAuthIdentity, OAuthState, User
from repopilot_api.security import (
    OAUTH_STATE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AuthContext,
    configured_origins,
    cookie_secure,
    csrf_token_for_session,
    digest,
    get_optional_auth,
    require_csrf,
    session_ttl_seconds,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])
OAUTH_STATE_TTL_SECONDS = 600
OAUTH_HTTP_TIMEOUT_SECONDS = 10.0


def _oauth_configuration() -> tuple[str, str, str]:
    client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
    client_secret = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GITHUB_OAUTH_REDIRECT_URI", "").strip()
    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            503, "GitHub OAuth requires server-side client ID, secret, and callback URL."
        )
    parsed = urlparse(redirect_uri)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(503, "GitHub OAuth callback URL configuration is invalid.")
    if parsed.path.rstrip("/") != "/api/auth/github/callback":
        raise HTTPException(503, "GitHub OAuth callback URL path is invalid.")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise HTTPException(503, "GitHub OAuth callback URL must use HTTPS.")
    return client_id, client_secret, redirect_uri


def _frontend_redirect_url() -> str:
    target = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    parsed = urlparse(target)
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or origin not in configured_origins()
    ):
        raise HTTPException(503, "Frontend redirect URL must match an allowed application origin.")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise HTTPException(503, "Frontend redirect URL must use HTTPS.")
    return origin + "/"


@router.get("/github/login")
def github_login(db: Session = Depends(get_db)):
    client_id, _, redirect_uri = _oauth_configuration()
    state = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.execute(delete(OAuthState).where(OAuthState.expires_at <= now))
    db.add(
        OAuthState(
            state_hash=digest(state),
            created_at=now,
            expires_at=now + timedelta(seconds=OAUTH_STATE_TTL_SECONDS),
        )
    )
    db.commit()
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "read:user",
            "state": state,
        }
    )
    response = RedirectResponse(
        f"https://github.com/login/oauth/authorize?{query}", status_code=302
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE_NAME,
        state,
        max_age=OAUTH_STATE_TTL_SECONDS,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/api/auth/github",
    )
    return response


@router.get("/github/callback")
def github_callback(
    request: Request,
    db: Session = Depends(get_db),
):
    client_id, client_secret, redirect_uri = _oauth_configuration()
    state = request.query_params.get("state", "")
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE_NAME, "")
    if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise HTTPException(400, "OAuth state validation failed.")
    consumed_state = db.execute(
        delete(OAuthState)
        .where(
            OAuthState.state_hash == digest(state),
            OAuthState.expires_at > datetime.now(UTC),
        )
        .returning(OAuthState.state_hash)
    ).scalar_one_or_none()
    db.commit()
    if consumed_state is None:
        raise HTTPException(400, "OAuth state is invalid, expired, or already used.")

    try:
        token_response = httpx.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": request.query_params.get("code", ""),
                "redirect_uri": redirect_uri,
                "state": state,
            },
            headers={"Accept": "application/json"},
            timeout=OAUTH_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        token_response.raise_for_status()
        access_token = token_response.json().get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("missing access token")
        user_response = httpx.get(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "RepoPilot-Fix",
            },
            timeout=OAUTH_HTTP_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
        user_response.raise_for_status()
        github_user = user_response.json()
        subject = github_user.get("id")
        login = github_user.get("login")
        if not isinstance(subject, int) or not isinstance(login, str) or not login:
            raise ValueError("invalid GitHub user profile")
    except (httpx.HTTPError, ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(502, "GitHub sign-in could not be completed.") from exc

    identity = db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.provider == "github",
            OAuthIdentity.provider_subject == str(subject),
        )
    )
    if identity is None:
        user = User()
        db.add(user)
        db.flush()
        identity = OAuthIdentity(
            user_id=user.id,
            provider="github",
            provider_subject=str(subject),
            login=login,
        )
        db.add(identity)
    else:
        identity.login = login
        user = db.get(User, identity.user_id)
        if user is None or user.disabled:
            raise HTTPException(403, "This account is unavailable.")

    session_token = secrets.token_urlsafe(32)
    csrf = csrf_token_for_session(session_token)
    ttl = session_ttl_seconds()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=digest(session_token),
            csrf_hash=digest(csrf),
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )
    )
    db.commit()

    response = RedirectResponse(_frontend_redirect_url(), status_code=303)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=ttl,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        OAUTH_STATE_COOKIE_NAME,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/api/auth/github",
    )
    return response


@router.get("/session")
def auth_session(
    request: Request,
    db: Session = Depends(get_db),
    auth: AuthContext | None = Depends(get_optional_auth),
):
    if auth is None:
        return {"authenticated": False, "user": None, "csrf_token": None}
    identity = db.scalar(
        select(OAuthIdentity).where(
            OAuthIdentity.user_id == auth.user.id,
            OAuthIdentity.provider == "github",
        )
    )
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    return {
        "authenticated": True,
        "user": {"id": auth.user.id, "login": identity.login if identity else "github-user"},
        "csrf_token": csrf_token_for_session(token),
    }


@router.post("/logout")
def logout(
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    db: Session = Depends(get_db),
):
    auth.session.revoked_at = datetime.now(UTC)
    db.commit()
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return {"authenticated": False}
