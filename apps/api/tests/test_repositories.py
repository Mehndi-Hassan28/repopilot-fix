import httpx
import pytest
from fastapi import HTTPException

from repopilot_api import main
from repopilot_api.models import RepositoryAccess, User
from repopilot_api.schemas import RepositoryImport
from repopilot_api.security import AuthContext

GITHUB_REPO = {
    "id": 12345,
    "owner": {"login": "octocat"},
    "name": "hello-world",
    "full_name": "octocat/hello-world",
    "html_url": "https://github.com/octocat/hello-world",
    "description": "A sample repository",
    "default_branch": "main",
    "stargazers_count": 42,
    "private": False,
}


def add_auth(session):
    user = User()
    session.add(user)
    session.flush()
    return AuthContext(user=user, session=None)


class MockGitHubClient:
    status_code = 200
    headers = {}
    payload = GITHUB_REPO

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url, **kwargs):
        assert url == "https://api.github.com/repos/octocat/hello-world"
        return httpx.Response(self.status_code, headers=self.headers, json=self.payload)


def test_import_is_idempotent_and_can_be_listed(monkeypatch, test_session):
    monkeypatch.setattr(main, "Client", MockGitHubClient)
    with test_session() as session:
        auth = add_auth(session)
        first = main.import_repository(
            RepositoryImport(url="https://github.com/octocat/hello-world"), session, auth
        )
        second = main.import_repository(
            RepositoryImport(url="http://github.com/octocat/hello-world/"), session, auth
        )
        listing = main.list_repositories(session, auth)
        detail = main.get_repository(first.id, session, auth)
        access = session.get(RepositoryAccess, (first.id, auth.user.id))

    assert first.id == second.id
    assert first.stars == 42
    assert len(listing) == 1
    assert detail.full_name == "octocat/hello-world"
    assert access.role == "owner"


def test_import_rejects_other_hosts():
    with pytest.raises(ValueError, match="public GitHub"):
        RepositoryImport(url="https://evil.example/octocat/hello-world")


def test_github_not_found_is_reported(monkeypatch, test_session):
    monkeypatch.setattr(main, "Client", MockGitHubClient)
    MockGitHubClient.status_code = 404
    with test_session() as session, pytest.raises(HTTPException) as error:
        auth = add_auth(session)
        main.import_repository(
            RepositoryImport(url="https://github.com/octocat/hello-world"), session, auth
        )
    assert error.value.status_code == 404
    MockGitHubClient.status_code = 200


def test_github_rate_limit_is_reported(monkeypatch, test_session):
    monkeypatch.setattr(main, "Client", MockGitHubClient)
    MockGitHubClient.status_code = 403
    MockGitHubClient.headers = {"X-RateLimit-Remaining": "0", "Retry-After": "120"}
    with test_session() as session, pytest.raises(HTTPException) as error:
        auth = add_auth(session)
        main.import_repository(
            RepositoryImport(url="https://github.com/octocat/hello-world"), session, auth
        )
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "120"
    MockGitHubClient.status_code = 200
    MockGitHubClient.headers = {}


def test_private_repository_import_is_rejected(monkeypatch, test_session):
    monkeypatch.setattr(main, "Client", MockGitHubClient)
    original = MockGitHubClient.payload
    MockGitHubClient.payload = {**GITHUB_REPO, "private": True}
    try:
        with test_session() as session, pytest.raises(HTTPException) as error:
            auth = add_auth(session)
            main.import_repository(
                RepositoryImport(url="https://github.com/octocat/hello-world"), session, auth
            )
        assert error.value.status_code == 403
    finally:
        MockGitHubClient.payload = original
