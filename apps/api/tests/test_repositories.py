import httpx
import pytest
from fastapi import HTTPException

from repopilot_api import main
from repopilot_api.models import (
    CodeSymbol,
    IndexedFile,
    IndexingJob,
    Repository,
    RepositoryAccess,
    User,
)
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


def test_get_file_content_returns_file_and_symbols(test_session):
    with test_session() as session:
        auth = add_auth(session)
        repo = Repository(
            github_id=99999,
            owner="octocat",
            name="hello-world",
            full_name="octocat/hello-world",
            url="https://github.com/octocat/hello-world",
            default_branch="main",
            stars=10,
        )
        session.add(repo)
        session.flush()
        session.add(RepositoryAccess(repository_id=repo.id, user_id=auth.user.id, role="owner"))

        job = IndexingJob(repository_id=repo.id, commit_sha="c" * 40, status="completed")
        session.add(job)
        session.flush()

        content = "class Greeter:\n    def hello():\n        return 'hi'\n"
        file = IndexedFile(
            job_id=job.id,
            path="src/greeter.py",
            language="python",
            size_bytes=len(content.encode("utf-8")),
            content=content,
        )
        session.add(file)
        session.flush()

        session.add(CodeSymbol(file_id=file.id, kind="class", name="Greeter", line=1))
        session.add(CodeSymbol(file_id=file.id, kind="function", name="hello", line=2))
        session.commit()

        result = main.get_file_content(repo.id, "src/greeter.py", session, auth)
        assert result["path"] == "src/greeter.py"
        assert result["language"] == "python"
        assert result["content"] == content
        assert result["size_bytes"] == len(content.encode("utf-8"))
        assert len(result["symbols"]) == 2
        assert result["symbols"][0] == {"kind": "class", "name": "Greeter", "line": 1}
        assert result["symbols"][1] == {"kind": "function", "name": "hello", "line": 2}


def test_get_file_content_validates_empty_path(test_session):
    with test_session() as session:
        auth = add_auth(session)
        with pytest.raises(HTTPException) as error:
            main.get_file_content(1, "   ", session, auth)
        assert error.value.status_code == 422


def test_get_file_content_not_found_or_unindexed(test_session):
    with test_session() as session:
        auth = add_auth(session)
        repo = Repository(
            github_id=88888,
            owner="octocat",
            name="unindexed-repo",
            full_name="octocat/unindexed-repo",
            url="https://github.com/octocat/unindexed-repo",
            default_branch="main",
            stars=0,
        )
        session.add(repo)
        session.flush()
        session.add(RepositoryAccess(repository_id=repo.id, user_id=auth.user.id, role="owner"))
        session.commit()

        # Unindexed repo
        with pytest.raises(HTTPException) as error:
            main.get_file_content(repo.id, "missing.py", session, auth)
        assert error.value.status_code == 404
        assert "not indexed" in error.value.detail.lower()

        # Add job, but file is missing
        job = IndexingJob(repository_id=repo.id, commit_sha="b" * 40, status="completed")
        session.add(job)
        session.commit()

        with pytest.raises(HTTPException) as error:
            main.get_file_content(repo.id, "missing.py", session, auth)
        assert error.value.status_code == 404
        assert "not found" in error.value.detail.lower()


def test_get_file_content_forbidden_without_access(test_session):
    with test_session() as session:
        owner_auth = add_auth(session)
        other_auth = add_auth(session)
        repo = Repository(
            github_id=77777,
            owner="octocat",
            name="secret-repo",
            full_name="octocat/secret-repo",
            url="https://github.com/octocat/secret-repo",
            default_branch="main",
            stars=0,
        )
        session.add(repo)
        session.flush()
        session.add(
            RepositoryAccess(repository_id=repo.id, user_id=owner_auth.user.id, role="owner")
        )
        session.commit()

        with pytest.raises(HTTPException) as error:
            main.get_file_content(repo.id, "secret.py", session, other_auth)
        assert error.value.status_code == 404


def test_indexing_status_returns_completed_metadata(test_session):
    with test_session() as session:
        auth = add_auth(session)
        repo = Repository(
            github_id=65432,
            owner="octocat",
            name="indexed-repo",
            full_name="octocat/indexed-repo",
            url="https://github.com/octocat/indexed-repo",
            default_branch="main",
            stars=15,
        )
        session.add(repo)
        session.flush()
        session.add(RepositoryAccess(repository_id=repo.id, user_id=auth.user.id, role="owner"))

        job = IndexingJob(
            repository_id=repo.id,
            commit_sha="f" * 40,
            status="completed",
            file_count=2,
            error=None,
        )
        session.add(job)
        session.flush()

        f1 = IndexedFile(
            job_id=job.id,
            path="app.py",
            language="python",
            size_bytes=100,
            content="print('hi')",
        )
        f2 = IndexedFile(
            job_id=job.id,
            path="util.py",
            language="python",
            size_bytes=200,
            content="def fn(): pass",
        )
        session.add_all([f1, f2])
        session.flush()

        session.add(CodeSymbol(file_id=f2.id, kind="function", name="fn", line=1))
        session.commit()

        result = main.indexing_status(repo.id, session, auth)
        assert result["id"] == job.id
        assert result["status"] == "completed"
        assert result["commit_sha"] == "f" * 40
        assert result["file_count"] == 2
        assert result["languages"] == ["python"]
        assert result["symbol_count"] == 1
        assert result["error"] is None


def test_indexing_status_returns_404_when_no_job(test_session):
    with test_session() as session:
        auth = add_auth(session)
        repo = Repository(
            github_id=65433,
            owner="octocat",
            name="unindexed-job-repo",
            full_name="octocat/unindexed-job-repo",
            url="https://github.com/octocat/unindexed-job-repo",
            default_branch="main",
            stars=0,
        )
        session.add(repo)
        session.flush()
        session.add(RepositoryAccess(repository_id=repo.id, user_id=auth.user.id, role="owner"))
        session.commit()

        with pytest.raises(HTTPException) as error:
            main.indexing_status(repo.id, session, auth)
        assert error.value.status_code == 404
        assert "no indexing job found" in error.value.detail.lower()


def test_indexing_status_forbidden_without_access(test_session):
    with test_session() as session:
        owner_auth = add_auth(session)
        other_auth = add_auth(session)
        repo = Repository(
            github_id=65434,
            owner="octocat",
            name="private-repo",
            full_name="octocat/private-repo",
            url="https://github.com/octocat/private-repo",
            default_branch="main",
            stars=0,
        )
        session.add(repo)
        session.flush()
        session.add(
            RepositoryAccess(repository_id=repo.id, user_id=owner_auth.user.id, role="owner")
        )

        job = IndexingJob(repository_id=repo.id, commit_sha="e" * 40, status="completed")
        session.add(job)
        session.commit()

        with pytest.raises(HTTPException) as error:
            main.indexing_status(repo.id, session, other_auth)
        assert error.value.status_code == 404
