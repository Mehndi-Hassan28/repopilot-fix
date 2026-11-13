from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from repopilot_api import main, oauth
from repopilot_api.models import (
    AuthSession,
    ChatMessage,
    ChatSession,
    IndexedFile,
    IndexingJob,
    OAuthIdentity,
    OAuthState,
    Repository,
    RepositoryAccess,
    User,
)
from repopilot_api.oauth import auth_session, github_callback, github_login
from repopilot_api.schemas import ChatQuestion
from repopilot_api.security import (
    OAUTH_STATE_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AuthContext,
    csrf_token_for_session,
    digest,
    get_current_auth,
    require_csrf,
)


def request_for(path="/", method="GET", query=b"", cookie="", origin=None, csrf=None):
    headers = []
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if origin:
        headers.append((b"origin", origin.encode()))
    if csrf:
        headers.append((b"x-csrf-token", csrf.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "query_string": query,
            "headers": headers,
            "server": ("localhost", 8000),
            "client": ("localhost", 3000),
        }
    )


def add_user(db):
    user = User()
    db.add(user)
    db.flush()
    return user


def add_repo(db):
    repo = Repository(
        github_id=100,
        owner="org",
        name="sample",
        full_name="org/sample",
        url="https://github.com/org/sample",
        default_branch="main",
    )
    db.add(repo)
    db.flush()
    job = IndexingJob(repository_id=repo.id, commit_sha="a" * 40, status="completed")
    db.add(job)
    db.flush()
    file = IndexedFile(
        job_id=job.id,
        path="src/private.py",
        language="python",
        size_bytes=30,
        content="def secret_handler():\n    return 'private'\n",
    )
    db.add(file)
    db.flush()
    return repo, job, file


def auth_for(user):
    return AuthContext(user=user, session=None)


def add_access(db, repo, user, role="owner"):
    db.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role=role))
    db.flush()


def test_anonymous_requests_are_denied(test_session):
    with test_session() as db, pytest.raises(HTTPException) as error:
        get_current_auth(request_for(), db)
    assert error.value.status_code == 401


def test_repository_and_chat_records_are_isolated_by_user(test_session):
    with test_session() as db:
        owner = add_user(db)
        stranger = add_user(db)
        repo, job, _ = add_repo(db)
        add_access(db, repo, owner)
        session = ChatSession(
            repository_id=repo.id, owner_user_id=owner.id, commit_sha=job.commit_sha
        )
        db.add(session)
        db.flush()
        message = ChatMessage(
            session_id=session.id,
            role="assistant",
            content="private source detail",
            citations="[]",
        )
        db.add(message)
        db.flush()
        stranger_auth = auth_for(stranger)

        with pytest.raises(HTTPException) as repo_error:
            main.get_repository(repo.id, db, stranger_auth)
        with pytest.raises(HTTPException) as file_error:
            main.indexed_files(repo.id, "", db, stranger_auth)
        with pytest.raises(HTTPException) as history_error:
            main.chat_history(session.id, db, stranger_auth)
        with pytest.raises(HTTPException) as message_error:
            main.ask_chat(session.id, ChatQuestion(question="secret"), db, stranger_auth)
        with pytest.raises(HTTPException) as delete_error:
            main.delete_chat_session(session.id, db, stranger_auth)
        with pytest.raises(HTTPException) as clear_error:
            main.clear_chat_history(session.id, db, stranger_auth)

        assert {
            e.value.status_code
            for e in (
                repo_error,
                file_error,
                history_error,
                message_error,
                delete_error,
                clear_error,
            )
        } == {404}
        assert db.get(ChatSession, session.id) is not None
        assert db.get(ChatMessage, message.id) is not None


def test_authorized_clear_and_delete_are_persisted(test_session):
    with test_session() as db:
        user = add_user(db)
        repo, job, _ = add_repo(db)
        add_access(db, repo, user)
        session = ChatSession(
            repository_id=repo.id, owner_user_id=user.id, commit_sha=job.commit_sha
        )
        db.add(session)
        db.flush()
        db.add(
            ChatMessage(
                session_id=session.id,
                role="assistant",
                content="answer",
                citations="[]",
            )
        )
        db.flush()
        clear_response = main.clear_chat_history(session.id, db, auth_for(user))
        assert clear_response == {"cleared": 1}
        assert db.get(ChatSession, session.id) is not None
        assert db.query(ChatMessage).filter_by(session_id=session.id).count() == 0

        db.add(
            ChatMessage(
                session_id=session.id,
                role="user",
                content="delete this",
                citations="[]",
            )
        )
        db.flush()
        deleted_id = session.id
        main.delete_chat_session(session.id, db, auth_for(user))
        assert db.get(ChatSession, deleted_id) is None
        assert db.query(ChatMessage).filter_by(session_id=deleted_id).count() == 0


def test_legacy_unowned_repository_and_chat_stay_inaccessible(test_session):
    with test_session() as db:
        user = add_user(db)
        repo, job, _ = add_repo(db)
        session = ChatSession(repository_id=repo.id, commit_sha=job.commit_sha)
        db.add(session)
        db.flush()
        with pytest.raises(HTTPException) as repo_error:
            main.get_repository(repo.id, db, auth_for(user))
        with pytest.raises(HTTPException) as chat_error:
            main.chat_history(session.id, db, auth_for(user))
    assert repo_error.value.status_code == chat_error.value.status_code == 404


def test_csrf_requires_allowed_origin_and_session_token(test_session, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    with test_session() as db:
        user = add_user(db)
        raw_token = "opaque-test-session"
        csrf = csrf_token_for_session(raw_token)
        session = AuthSession(
            user_id=user.id,
            token_hash=digest(raw_token),
            csrf_hash=digest(csrf),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        context = AuthContext(user=user, session=session)
        assert require_csrf(
            request_for(method="POST", origin="http://localhost:3000", csrf=csrf), context
        ) == context
        with pytest.raises(HTTPException) as bad_token:
            require_csrf(
                request_for(method="POST", origin="http://localhost:3000", csrf="wrong"),
                context,
            )
        with pytest.raises(HTTPException) as bad_origin:
            require_csrf(
                request_for(method="POST", origin="https://attacker.invalid", csrf=csrf),
                context,
            )
    assert bad_token.value.status_code == bad_origin.value.status_code == 403


def test_oauth_state_is_browser_bound_single_use_and_redirect_is_fixed(
    test_session, monkeypatch
):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id-test")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret-test")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/github/callback"
    )
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with test_session() as db:
        login = github_login(db)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        cookie = SimpleCookie()
        cookie.load(login.headers["set-cookie"])
        state_cookie = cookie[OAUTH_STATE_COOKIE_NAME].value
        assert state == state_cookie
        assert cookie[OAUTH_STATE_COOKIE_NAME]["httponly"]
        assert cookie[OAUTH_STATE_COOKIE_NAME]["samesite"].lower() == "lax"
        assert parse_qs(urlparse(login.headers["location"]).query)["scope"] == ["read:user"]

        wrong_request = request_for(
            "/api/auth/github/callback",
            query=f"state={state}&code=mock-code&next=https://attacker.invalid".encode(),
            cookie=f"{OAUTH_STATE_COOKIE_NAME}=wrong-state",
        )
        with pytest.raises(HTTPException) as state_error:
            github_callback(wrong_request, db)
        assert state_error.value.status_code == 400

        class OAuthResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        monkeypatch.setattr(
            oauth.httpx,
            "post",
            lambda *args, **kwargs: OAuthResponse({"access_token": "mock-private-token"}),
        )
        monkeypatch.setattr(
            oauth.httpx,
            "get",
            lambda *args, **kwargs: OAuthResponse({"id": 456, "login": "safe-user"}),
        )
        callback_request = request_for(
            "/api/auth/github/callback",
            query=f"state={state}&code=mock-code".encode(),
            cookie=f"{OAUTH_STATE_COOKIE_NAME}={state}",
        )
        callback = github_callback(callback_request, db)
        assert callback.headers["location"] == "http://localhost:3000/"
        assert "mock-private-token" not in callback.headers["location"]
        session_cookie = SimpleCookie()
        session_cookie.load(callback.headers["set-cookie"])
        assert session_cookie[SESSION_COOKIE_NAME]["httponly"]
        assert session_cookie[SESSION_COOKIE_NAME]["samesite"].lower() == "lax"
        session_token = session_cookie[SESSION_COOKIE_NAME].value
        user_auth = get_current_auth(
            request_for(cookie=f"{SESSION_COOKIE_NAME}={session_token}"), db
        )
        public_session = auth_session(
            request_for(cookie=f"{SESSION_COOKIE_NAME}={session_token}"), db, user_auth
        )
        assert public_session["authenticated"] is True
        assert public_session["user"]["login"] == "safe-user"
        assert "mock-private-token" not in str(public_session)
        assert db.query(OAuthIdentity).count() == 1
        assert db.query(AuthSession).count() == 1

        with pytest.raises(HTTPException) as replay_error:
            github_callback(callback_request, db)
    assert replay_error.value.status_code == 400


def test_oauth_state_expires(test_session, monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id-test")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret-test")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/github/callback"
    )
    with test_session() as db:
        login = github_login(db)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        state_row = db.get(OAuthState, digest(state))
        state_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.flush()
        request = request_for(
            "/api/auth/github/callback",
            query=f"state={state}&code=mock-code".encode(),
            cookie=f"{OAUTH_STATE_COOKIE_NAME}={state}",
        )
        with pytest.raises(HTTPException) as error:
            github_callback(request, db)
    assert error.value.status_code == 400
    assert "expired" in error.value.detail


def test_oauth_rejects_unapproved_frontend_redirect(monkeypatch):
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client-id-test")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "client-secret-test")
    monkeypatch.setenv(
        "GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/api/auth/github/callback"
    )
    monkeypatch.setenv("FRONTEND_URL", "https://attacker.invalid")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(HTTPException) as error:
        oauth._frontend_redirect_url()
    assert error.value.status_code == 503
    assert "allowed application origin" in error.value.detail
