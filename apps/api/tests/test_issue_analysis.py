import httpx
import pytest
from fastapi import HTTPException

from repopilot_api import main
from repopilot_api.chat import build_issue_query
from repopilot_api.code_intelligence import GitHubSource
from repopilot_api.models import (
    IndexedFile,
    IndexingJob,
    Repository,
    RepositoryAccess,
    User,
)
from repopilot_api.schemas import IssueAnalyzeRequest
from repopilot_api.security import AuthContext


def add_repo(session, github_id=1, full_name="owner/repo"):
    parts = full_name.split("/")
    repo = Repository(
        github_id=github_id,
        owner=parts[0],
        name=parts[1],
        full_name=full_name,
        url=f"https://github.com/{full_name}",
        default_branch="main",
    )
    session.add(repo)
    session.flush()
    return repo


def add_index(session, repo, sha="a" * 40, status="completed"):
    job = IndexingJob(repository_id=repo.id, commit_sha=sha, status=status)
    session.add(job)
    session.flush()
    session.add(
        IndexedFile(
            job_id=job.id,
            path="src/auth.py",
            language="python",
            size_bytes=100,
            content=(
                "def authenticate(token):\n"
                "    if not token:\n"
                "        return False\n"
                "    return True\n"
            ),
        )
    )
    session.add(
        IndexedFile(
            job_id=job.id,
            path="src/utils.py",
            language="python",
            size_bytes=80,
            content="def format_user(name):\n    return f'User: {name}'\n",
        )
    )
    session.flush()
    return job


def add_auth(session, repo=None):
    user = User()
    session.add(user)
    session.flush()
    if repo:
        session.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner"))
        session.flush()
    return AuthContext(user=user, session=None)


def test_fetch_issue_success():
    class FakeClient:
        def get(self, url, headers=None):
            return httpx.Response(
                200,
                json={
                    "number": 42,
                    "title": "Fix token validation bug",
                    "body": "Authenticate fails when token is empty string.",
                    "state": "open",
                    "html_url": "https://github.com/owner/repo/issues/42",
                    "user": {"login": "octocat"},
                    "labels": [{"name": "bug"}, {"name": "security"}],
                },
                request=httpx.Request("GET", url),
            )

    source = GitHubSource(client=FakeClient())
    issue = source.fetch_issue("owner", "repo", 42)
    assert issue["number"] == 42
    assert issue["title"] == "Fix token validation bug"
    assert issue["body"] == "Authenticate fails when token is empty string."
    assert issue["state"] == "open"
    assert issue["author"] == "octocat"
    assert issue["labels"] == ["bug", "security"]
    assert issue["html_url"] == "https://github.com/owner/repo/issues/42"


def test_fetch_issue_validates_issue_number():
    source = GitHubSource(client=None)
    with pytest.raises(ValueError, match="positive integer"):
        source.fetch_issue("owner", "repo", 0)

    with pytest.raises(ValueError, match="positive integer"):
        source.fetch_issue("owner", "repo", -10)

    with pytest.raises(ValueError, match="positive integer"):
        source.fetch_issue("owner", "repo", True)


def test_fetch_issue_rejects_pull_request():
    class FakeClient:
        def get(self, url, headers=None):
            return httpx.Response(
                200,
                json={
                    "number": 15,
                    "title": "A pull request",
                    "body": "PR description",
                    "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/15"},
                },
                request=httpx.Request("GET", url),
            )

    source = GitHubSource(client=FakeClient())
    with pytest.raises(ValueError, match="pull request, not an issue"):
        source.fetch_issue("owner", "repo", 15)


def test_fetch_issue_handles_404_not_found():
    class FakeClient:
        def get(self, url, headers=None):
            return httpx.Response(404, request=httpx.Request("GET", url))

    source = GitHubSource(client=FakeClient())
    with pytest.raises(RuntimeError, match="Issue #999 not found on GitHub"):
        source.fetch_issue("owner", "repo", 999)


def test_fetch_issue_handles_rate_limits():
    class FakeClient:
        def get(self, url, headers=None):
            return httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "0"},
                request=httpx.Request("GET", url),
            )

    source = GitHubSource(client=FakeClient())
    with pytest.raises(RuntimeError, match="GitHub rate limit reached"):
        source.fetch_issue("owner", "repo", 1)


def test_fetch_issue_bounds_large_untrusted_content():
    huge_title = "A" * 1000
    huge_body = "B" * 20000

    class FakeClient:
        def get(self, url, headers=None):
            return httpx.Response(
                200,
                json={"number": 1, "title": huge_title, "body": huge_body},
                request=httpx.Request("GET", url),
            )

    source = GitHubSource(client=FakeClient())
    issue = source.fetch_issue("owner", "repo", 1)
    assert len(issue["title"]) == 500
    assert len(issue["body"]) == 10000


def test_build_issue_query_filters_urls_and_bounds():
    query = build_issue_query(
        title="Fix authentication in auth.py",
        body="See https://example.com/docs for details. Token is validated incorrectly.",
        max_chars=200,
    )
    assert "https://example.com" not in query
    assert "Fix authentication in auth.py" in query
    assert "Token is validated incorrectly" in query

    # Long body bounded
    long_query = build_issue_query("Title", "word " * 500, max_chars=50)
    assert len(long_query) <= 50

    # Empty body
    assert build_issue_query("Only Title", "") == "Only Title"


def test_issue_analysis_endpoint_success(test_session, monkeypatch):
    def fake_fetch_issue(self, owner, repo, issue_number):
        return {
            "number": issue_number,
            "title": "Fix authenticate token check",
            "body": "The authenticate function fails on invalid tokens.",
            "state": "open",
            "html_url": f"https://github.com/{owner}/{repo}/issues/{issue_number}",
            "author": "tester",
            "labels": ["bug"],
        }

    monkeypatch.setattr(GitHubSource, "fetch_issue", fake_fetch_issue)

    with test_session() as db:
        repo = add_repo(db, github_id=101, full_name="acme/service")
        auth = add_auth(db, repo)
        job = add_index(db, repo)

        result = main.analyze_issue(
            repository_id=repo.id,
            payload=IssueAnalyzeRequest(issue_number=12),
            db=db,
            auth=auth,
        )

    assert result["repository_id"] == repo.id
    assert result["commit_sha"] == job.commit_sha
    assert result["issue_number"] == 12
    assert result["issue_title"] == "Fix authenticate token check"
    assert len(result["evidence"]) >= 1
    top = result["evidence"][0]
    assert top["path"] == "src/auth.py"
    assert top["start_line"] >= 1
    assert "authenticate" in top["content"]


def test_issue_analysis_endpoint_enforces_repository_isolation(test_session):
    with test_session() as db:
        repo = add_repo(db, github_id=202, full_name="user1/private-repo")
        # User 2 has no access to repo
        auth_user_2 = add_auth(db, repo=None)

        with pytest.raises(HTTPException) as exc:
            main.analyze_issue(
                repository_id=repo.id,
                payload=IssueAnalyzeRequest(issue_number=1),
                db=db,
                auth=auth_user_2,
            )
        assert exc.value.status_code == 404
        assert "Repository not found" in exc.value.detail


def test_issue_analysis_requires_completed_indexing(test_session):
    with test_session() as db:
        repo = add_repo(db, github_id=303, full_name="user/unindexed")
        auth = add_auth(db, repo)
        # No completed index job
        add_index(db, repo, status="running")

        with pytest.raises(HTTPException) as exc:
            main.analyze_issue(
                repository_id=repo.id,
                payload=IssueAnalyzeRequest(issue_number=5),
                db=db,
                auth=auth,
            )
        assert exc.value.status_code == 409
        assert "Index this repository before analyzing issues" in exc.value.detail


def test_issue_analysis_endpoint_handles_pull_request(test_session, monkeypatch):
    def fake_fetch_pr(self, owner, repo, issue_number):
        raise ValueError(f"Issue #{issue_number} is a pull request, not an issue.")

    monkeypatch.setattr(GitHubSource, "fetch_issue", fake_fetch_pr)

    with test_session() as db:
        repo = add_repo(db, github_id=404, full_name="user/sample")
        auth = add_auth(db, repo)
        add_index(db, repo)

        with pytest.raises(HTTPException) as exc:
            main.analyze_issue(
                repository_id=repo.id,
                payload=IssueAnalyzeRequest(issue_number=10),
                db=db,
                auth=auth,
            )
        assert exc.value.status_code == 422
        assert "pull request" in exc.value.detail


def test_issue_analysis_endpoint_handles_github_errors(test_session, monkeypatch):
    with test_session() as db:
        repo = add_repo(db, github_id=505, full_name="user/errors")
        auth = add_auth(db, repo)
        add_index(db, repo)

        # 404 Not Found
        monkeypatch.setattr(
            GitHubSource,
            "fetch_issue",
            lambda self, o, r, n: (_ for _ in ()).throw(
                RuntimeError("Issue #99 not found on GitHub")
            ),
        )
        with pytest.raises(HTTPException) as exc_404:
            main.analyze_issue(
                repository_id=repo.id,
                payload=IssueAnalyzeRequest(issue_number=99),
                db=db,
                auth=auth,
            )
        assert exc_404.value.status_code == 404
        assert "was not found on GitHub" in exc_404.value.detail

        # Rate Limit
        monkeypatch.setattr(
            GitHubSource,
            "fetch_issue",
            lambda self, o, r, n: (_ for _ in ()).throw(
                RuntimeError("GitHub rate limit reached")
            ),
        )
        with pytest.raises(HTTPException) as exc_429:
            main.analyze_issue(
                repository_id=repo.id,
                payload=IssueAnalyzeRequest(issue_number=1),
                db=db,
                auth=auth,
            )
        assert exc_429.value.status_code == 429
        assert "rate limit reached" in exc_429.value.detail
