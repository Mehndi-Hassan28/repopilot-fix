import httpx
import pytest
from fastapi import HTTPException

from repopilot_api import main
from repopilot_api.schemas import RepositoryImport

GITHUB_REPO = {
    "id": 12345,
    "owner": {"login": "octocat"},
    "name": "hello-world",
    "full_name": "octocat/hello-world",
    "html_url": "https://github.com/octocat/hello-world",
    "description": "A sample repository",
    "default_branch": "main",
    "stargazers_count": 42,
}


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
        first = main.import_repository(
            RepositoryImport(url="https://github.com/octocat/hello-world"), session
        )
        second = main.import_repository(
            RepositoryImport(url="http://github.com/octocat/hello-world/"), session
        )
        listing = main.list_repositories(session)
        detail = main.get_repository(first.id, session)

    assert first.id == second.id
    assert first.stars == 42
    assert len(listing) == 1
    assert detail.full_name == "octocat/hello-world"


def test_import_rejects_other_hosts():
    with pytest.raises(ValueError, match="public GitHub"):
        RepositoryImport(url="https://evil.example/octocat/hello-world")


def test_github_not_found_is_reported(monkeypatch, test_session):
    monkeypatch.setattr(main, "Client", MockGitHubClient)
    MockGitHubClient.status_code = 404
    with test_session() as session, pytest.raises(HTTPException) as error:
        main.import_repository(
            RepositoryImport(url="https://github.com/octocat/hello-world"), session
        )
    assert error.value.status_code == 404
    MockGitHubClient.status_code = 200


def test_github_rate_limit_is_reported(monkeypatch, test_session):
    monkeypatch.setattr(main, "Client", MockGitHubClient)
    MockGitHubClient.status_code = 403
    MockGitHubClient.headers = {"X-RateLimit-Remaining": "0", "Retry-After": "120"}
    with test_session() as session, pytest.raises(HTTPException) as error:
        main.import_repository(
            RepositoryImport(url="https://github.com/octocat/hello-world"), session
        )
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "120"
    MockGitHubClient.status_code = 200
    MockGitHubClient.headers = {}
