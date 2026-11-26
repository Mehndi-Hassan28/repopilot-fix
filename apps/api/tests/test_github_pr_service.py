"""Tests for secure GitHub PR creation, verification gating, and patch export."""

import json

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from repopilot_api.github_pr_service import (
    GitHubAuthError,
    GitHubPermissionError,
    GitHubPullRequestService,
    GitHubRateLimitError,
    GitHubVerificationPrerequisiteError,
)
from repopilot_api.models import (
    GitHubPullRequest,
    IndexedFile,
    IndexingJob,
    ProofReport,
    Repository,
    RepositoryAccess,
    User,
    VerificationAuditEvent,
    VerificationRun,
)
from repopilot_api.schemas import (
    ProofVerdict,
    PullRequestCreateRequest,
    VerificationJobStatus,
)
from repopilot_api.security import AuthContext
from repopilot_api.verification_router import create_pull_request
from repopilot_api.verification_service import compute_spec_hash


def _add_user(session) -> User:
    user = User()
    session.add(user)
    session.flush()
    return user


def _setup_verified_repo_and_run(session, role: str = "owner"):
    user = _add_user(session)
    auth = AuthContext(user=user, session=None)

    repo = Repository(
        github_id=123456,
        owner="test-owner",
        name="test-repo",
        full_name="test-owner/test-repo",
        url="https://github.com/test-owner/test-repo",
        default_branch="main",
        stars=10,
    )
    session.add(repo)
    session.flush()

    session.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role=role))

    job = IndexingJob(repository_id=repo.id, commit_sha="a" * 40, status="completed")
    session.add(job)
    session.flush()

    file_content = "def add(a, b):\n    return a - b\n"
    indexed_file = IndexedFile(
        job_id=job.id,
        path="calc.py",
        language="python",
        size_bytes=len(file_content.encode("utf-8")),
        content=file_content,
    )
    session.add(indexed_file)
    session.flush()

    diff_text = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )

    test_cmd = ["python", "-m", "unittest"]
    patch_hash = "fake-patch-hash-1234"
    spec_hash = compute_spec_hash("a" * 40, patch_hash, test_cmd, "sandbox:latest")

    run = VerificationRun(
        repository_id=repo.id,
        commit_sha="a" * 40,
        issue_number=42,
        user_id=user.id,
        status=VerificationJobStatus.COMPLETED.value,
        spec_hash=spec_hash,
        approved_spec_hash=spec_hash,
        approved_by_user_id=user.id,
        test_command=json.dumps(test_cmd),
        sandbox_image="sandbox:latest",
        patch_hash=patch_hash,
        patch_diff=diff_text,
        patches_json=json.dumps([{"file_path": "calc.py", "diff": diff_text}]),
        baseline_exit_code=1,
        patched_exit_code=0,
    )
    session.add(run)
    session.flush()

    report = ProofReport(
        verification_run_id=run.id,
        verdict=ProofVerdict.VERIFIED_FIX.value,
        hypothesis="Fix subtraction bug in calc.py",
        summary="Verified fix: test passed in patched sandbox with 0 regressions.",
        baseline_summary="Failed as expected in baseline.",
        patched_summary="Passed in patched sandbox.",
        confidence="high",
        evidence_json="{}",
    )
    session.add(report)
    session.commit()

    return user, auth, repo, run, report


class MockTransport(httpx.BaseTransport):
    """Configurable mock transport for GitHub API responses."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.recorded_requests = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.recorded_requests.append(request)
        url_str = str(request.url)
        method = request.method.upper()

        key = (method, url_str)
        if key in self.routes:
            handler = self.routes[key]
            if callable(handler):
                return handler(request)
            status_code, json_data, headers = handler
            return httpx.Response(status_code, json=json_data, headers=headers or {})

        # Default fallbacks based on path patterns
        if method == "GET" and "/git/ref/heads/main" in url_str:
            return httpx.Response(200, json={"object": {"sha": "base12345678"}})
        if method == "GET" and "/git/ref/heads/repopilot" in url_str:
            return httpx.Response(404, json={"message": "Not Found"})
        if method == "POST" and "/git/refs" in url_str:
            return httpx.Response(201, json={"object": {"sha": "base12345678"}})
        if method == "GET" and "/contents/calc.py" in url_str:
            import base64

            content = "def add(a, b):\n    return a - b\n"
            b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
            return httpx.Response(200, json={"content": b64, "encoding": "base64"})
        if method == "POST" and "/git/blobs" in url_str:
            return httpx.Response(201, json={"sha": "blob12345678"})
        if method == "POST" and "/git/trees" in url_str:
            return httpx.Response(201, json={"sha": "tree12345678"})
        if method == "POST" and "/git/commits" in url_str:
            return httpx.Response(201, json={"sha": "newcommit12345678"})
        if method == "PATCH" and "/git/refs/heads/" in url_str:
            return httpx.Response(200, json={"object": {"sha": "newcommit12345678"}})
        if method == "GET" and "/pulls" in url_str:
            return httpx.Response(200, json=[])
        if method == "POST" and "/pulls" in url_str:
            return httpx.Response(
                201,
                json={
                    "number": 101,
                    "html_url": "https://github.com/test-owner/test-repo/pull/101",
                },
            )

        return httpx.Response(404, json={"message": "Not Found in Mock"})


def test_create_pr_success(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)

        mock_client = httpx.Client(transport=MockTransport())
        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")

        resp = GitHubPullRequestService.create_pull_request(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
            user_id=user.id,
            payload=payload,
            client=mock_client,
        )

        assert resp.success is True
        assert resp.pr_number == 101
        assert resp.pr_url == "https://github.com/test-owner/test-repo/pull/101"
        assert resp.status == "created"
        assert resp.base_branch == "main"

        # Check DB persistence
        pr_record = session.scalar(
            select(GitHubPullRequest).where(GitHubPullRequest.verification_run_id == run.id)
        )
        assert pr_record is not None
        assert pr_record.pr_number == 101
        assert pr_record.pr_url == "https://github.com/test-owner/test-repo/pull/101"

        # Check audit event
        audit = session.scalar(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run.id,
                VerificationAuditEvent.event_type == "pr_created",
            )
        )
        assert audit is not None


def test_create_pr_idempotency_returns_existing(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)

        # Pre-seed existing PR record
        session.add(
            GitHubPullRequest(
                repository_id=repo.id,
                verification_run_id=run.id,
                created_by_user_id=user.id,
                issue_number=run.issue_number,
                pr_number=55,
                pr_url="https://github.com/test-owner/test-repo/pull/55",
                branch_name="repopilot/fix-issue-42-1",
                base_branch="main",
                head_sha="commit123",
                patch_hash=run.patch_hash or "",
                title="Existing PR",
                body="Existing Body",
            )
        )
        session.commit()

        # Should return existing without making any GitHub API calls
        mock_client = httpx.Client(transport=MockTransport())
        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")

        resp = GitHubPullRequestService.create_pull_request(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
            user_id=user.id,
            payload=payload,
            client=mock_client,
        )

        assert resp.success is True
        assert resp.pr_number == 55
        assert resp.status == "already_exists"
        assert "already exists" in resp.message


def test_create_pr_rejects_unapproved_run(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)
        run.approved_spec_hash = None
        session.commit()

        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")
        with pytest.raises(GitHubVerificationPrerequisiteError) as exc_info:
            GitHubPullRequestService.create_pull_request(
                db=session,
                repository_id=repo.id,
                verification_id=run.id,
                user_id=user.id,
                payload=payload,
            )
        assert "without prior explicit human approval" in str(exc_info.value)


def test_create_pr_rejects_altered_specification(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)
        run.test_command = json.dumps(["python", "-m", "pytest", "--tampered"])
        session.commit()

        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")
        with pytest.raises(GitHubVerificationPrerequisiteError) as exc_info:
            GitHubPullRequestService.create_pull_request(
                db=session,
                repository_id=repo.id,
                verification_id=run.id,
                user_id=user.id,
                payload=payload,
            )
        assert "altered after approval" in str(exc_info.value)


def test_create_pr_rejects_failed_verdict(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)
        report.verdict = ProofVerdict.FIX_FAILED.value
        session.commit()

        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")
        with pytest.raises(GitHubVerificationPrerequisiteError) as exc_info:
            GitHubPullRequestService.create_pull_request(
                db=session,
                repository_id=repo.id,
                verification_id=run.id,
                user_id=user.id,
                payload=payload,
            )
        assert "Only verified fixes" in str(exc_info.value)


def test_create_pr_rejects_missing_token(test_session, monkeypatch):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_PR_TOKEN", raising=False)

        payload = PullRequestCreateRequest(github_token=None)
        with pytest.raises(GitHubAuthError) as exc_info:
            GitHubPullRequestService.create_pull_request(
                db=session,
                repository_id=repo.id,
                verification_id=run.id,
                user_id=user.id,
                payload=payload,
            )
        assert "Missing GitHub authorization token" in str(exc_info.value)


def test_create_pr_handles_rate_limit(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)

        routes = {
            ("GET", "https://api.github.com/repos/test-owner/test-repo/git/ref/heads/main"): (
                429,
                {"message": "API rate limit exceeded"},
                {"X-RateLimit-Remaining": "0"},
            )
        }
        mock_client = httpx.Client(transport=MockTransport(routes))
        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")

        with pytest.raises(GitHubRateLimitError) as exc_info:
            GitHubPullRequestService.create_pull_request(
                db=session,
                repository_id=repo.id,
                verification_id=run.id,
                user_id=user.id,
                payload=payload,
                client=mock_client,
            )
        assert "rate limit exceeded" in str(exc_info.value).lower()


def test_create_pr_handles_insufficient_permissions(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)

        routes = {
            ("GET", "https://api.github.com/repos/test-owner/test-repo/git/ref/heads/main"): (
                403,
                {"message": "Must have push access to repository"},
                {},
            )
        }
        mock_client = httpx.Client(transport=MockTransport(routes))
        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")

        with pytest.raises(GitHubPermissionError) as exc_info:
            GitHubPullRequestService.create_pull_request(
                db=session,
                repository_id=repo.id,
                verification_id=run.id,
                user_id=user.id,
                payload=payload,
                client=mock_client,
            )
        assert "Insufficient GitHub permissions" in str(exc_info.value)


def test_export_patch_success(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)

        data = GitHubPullRequestService.export_patch(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
            user_id=user.id,
        )

        assert data.patch_diff == run.patch_diff
        assert data.filename == "repopilot-fix-issue-42.patch"
        assert len(data.instructions) > 0
        assert any("git apply" in line for line in data.instructions)
        assert data.verdict == ProofVerdict.VERIFIED_FIX.value

        # Check audit event
        audit = session.scalar(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run.id,
                VerificationAuditEvent.event_type == "patch_exported",
            )
        )
        assert audit is not None


def test_get_pr_status_endpoints(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)

        # Before PR creation
        status_before = GitHubPullRequestService.get_pr_status(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
        )
        assert status_before.has_pr is False

        # Add PR record
        session.add(
            GitHubPullRequest(
                repository_id=repo.id,
                verification_run_id=run.id,
                created_by_user_id=user.id,
                issue_number=run.issue_number,
                pr_number=77,
                pr_url="https://github.com/test-owner/test-repo/pull/77",
                branch_name="repopilot/fix-issue-42-1",
                base_branch="main",
                head_sha="sha123",
                patch_hash="patch123",
                title="fix: title",
                body="fix: body",
            )
        )
        session.commit()

        status_after = GitHubPullRequestService.get_pr_status(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
        )
        assert status_after.has_pr is True
        assert status_after.pr_number == 77
        assert status_after.pr_url == "https://github.com/test-owner/test-repo/pull/77"


def test_router_create_pr_unauthorized_user(test_session):
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session, role="owner")
        other_user = _add_user(session)
        other_auth = AuthContext(user=other_user, session=None)

        with pytest.raises(HTTPException) as exc_info:
            create_pull_request(
                repository_id=repo.id,
                verification_id=run.id,
                payload=PullRequestCreateRequest(),
                db=session,
                auth=other_auth,
            )
        assert exc_info.value.status_code == 404


def test_create_pr_external_api_idempotency_when_pr_exists_on_github(test_session):
    """External API idempotency: reuses existing upstream PR and prevents duplicates."""
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)

        called_endpoints = []

        def tracking_handler(request):
            called_endpoints.append((request.method, str(request.url)))
            if request.method == "GET" and "/git/ref/heads/main" in str(request.url):
                return httpx.Response(200, json={"object": {"sha": "base12345678"}})
            if request.method == "GET" and "/git/ref/heads/repopilot" in str(request.url):
                return httpx.Response(200, json={"object": {"sha": "existing_branch_sha"}})
            if request.method == "GET" and "/pulls" in str(request.url):
                return httpx.Response(
                    200,
                    json=[
                        {
                            "number": 88,
                            "html_url": "https://github.com/test-owner/test-repo/pull/88",
                            "title": "Existing upstream PR",
                        }
                    ],
                )
            return httpx.Response(404, json={"message": "Not Found"})

        transport = httpx.MockTransport(tracking_handler)
        mock_client = httpx.Client(transport=transport)
        payload = PullRequestCreateRequest(github_token="ghp_test_token_123")

        resp = GitHubPullRequestService.create_pull_request(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
            user_id=user.id,
            payload=payload,
            client=mock_client,
        )

        assert resp.success is True
        assert resp.pr_number == 88
        assert resp.status == "already_exists"
        assert "Reused existing GitHub pull request" in resp.message

        methods = [m for m, _ in called_endpoints]
        assert "POST" not in methods


def test_branch_name_validation_allows_valid_and_blocks_invalid():
    """Branch validation allows valid branches with hyphens and blocks invalid characters."""
    valid_branches = [
        "fix/issue-1-power-calc",
        "repopilot/fix-issue-42-7",
        "main",
        "feat/calc_fix_v2",
        "patch-1",
    ]
    for b in valid_branches:
        req = PullRequestCreateRequest(custom_branch_name=b)
        assert req.custom_branch_name == b

    invalid_branches = [
        "-starts-with-dash",
        "/starts-with-slash",
        ".starts-with-dot",
        "ends-with-slash/",
        "ends-with-dot.",
        "branch.lock",
        "has space",
        "bad~tilde",
        "bad^caret",
        "bad:colon",
        "bad?question",
        "bad*star",
        "bad[bracket",
        "bad\\backslash",
        "bad..double-dot",
        "bad//double-slash",
        "bad@{brace",
        "@",
    ]
    for b in invalid_branches:
        with pytest.raises(Exception) as exc_info:
            PullRequestCreateRequest(custom_branch_name=b)
        assert "Invalid Git branch name" in str(exc_info.value)


def test_demo_repository_creates_simulated_pr_and_is_idempotent(test_session):
    """Internal demo repository creates simulated PR #101 safely with idempotency."""
    from repopilot_api.demo import (
        DEMO_FIXTURE_DESCRIPTION_PREFIX,
        DEMO_GITHUB_BASE_ID,
        DEMO_PROVENANCE_TAG_PREFIX,
    )

    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)
        # Transform repo into an internal demo repository fixture with exact provenance
        repo.owner = "repopilot"
        repo.name = f"demo-calc-{user.id}"
        repo.full_name = f"repopilot/demo-calc-{user.id}"
        repo.github_id = DEMO_GITHUB_BASE_ID + user.id
        repo.url = "https://github.com/repopilot/demo-calculator"
        repo.default_branch = "main"
        repo.description = (
            f"{DEMO_FIXTURE_DESCRIPTION_PREFIX} "
            f"{DEMO_PROVENANCE_TAG_PREFIX}{user.id}:demo-calculator]"
        )
        session.commit()

        payload = PullRequestCreateRequest(
            custom_title="Fix power calculation for negative base or zero exponent",
            custom_body="Automated PR description",
            custom_branch_name="fix/issue-1-power-calc",
        )

        # 1. First call creates simulated PR #101
        resp = GitHubPullRequestService.create_pull_request(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
            user_id=user.id,
            payload=payload,
        )

        assert resp.success is True
        assert resp.pr_number == 101
        assert resp.is_simulated is True
        assert resp.status == "created"
        assert resp.branch_name == "fix/issue-1-power-calc"
        assert f"repopilot/{repo.name}/pull/101" in (resp.pr_url or "")

        # Verify DB record
        pr_records = session.scalars(
            select(GitHubPullRequest).where(GitHubPullRequest.verification_run_id == run.id)
        ).all()
        assert len(pr_records) == 1
        assert pr_records[0].pr_number == 101

        # 2. Repeated call returns existing PR idempotently without duplicate records
        resp2 = GitHubPullRequestService.create_pull_request(
            db=session,
            repository_id=repo.id,
            verification_id=run.id,
            user_id=user.id,
            payload=payload,
        )

        assert resp2.success is True
        assert resp2.pr_number == 101
        assert resp2.is_simulated is True
        assert resp2.status == "already_exists"

        # Verify still exactly 1 record in DB
        pr_records_after = session.scalars(
            select(GitHubPullRequest).where(GitHubPullRequest.verification_run_id == run.id)
        ).all()
        assert len(pr_records_after) == 1


def test_guest_user_cannot_create_real_github_pr(test_session):
    """Guest users are forbidden from creating real GitHub PRs on external repositories."""
    with test_session() as session:
        user, auth, repo, run, report = _setup_verified_repo_and_run(session)
        user.is_guest = True
        session.commit()

        payload = PullRequestCreateRequest(
            custom_branch_name="fix/external-fix",
            github_token="ghp_mock_token_12345",
        )

        with pytest.raises(GitHubPermissionError) as exc_info:
            GitHubPullRequestService.create_pull_request(
                db=session,
                repository_id=repo.id,
                verification_id=run.id,
                user_id=user.id,
                payload=payload,
            )
        assert "Guest users cannot create external GitHub pull requests" in str(exc_info.value)
