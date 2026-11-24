"""Regression and integration tests for RepoPilot Fix One-Click Interactive Demo API.

Tests:
1. Demo initialization (idempotent, preconfigured repo, indexed fixture files, symbols).
2. Duplicate initialization (no duplicate records).
3. User and session isolation (tenancy and authorization boundaries).
4. Issue investigation and AST root-cause analysis without external GitHub calls.
5. Explicit human approval enforcement.
6. Actual verification execution and status reporting.
7. Docker unavailable behavior (fails closed, reports accurate offline state).
8. Evidence integrity and cryptographic binding.
9. Prevention of external GitHub writes (simulated PR preview).
10. Safe demo reset workflow.
"""

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from repopilot_api.demo import check_docker_daemon_status
from repopilot_api.dependencies import get_db
from repopilot_api.fixture_registry import (
    DEMO_CALC_COMMIT_SHA,
    DEMO_CALC_FILES,
    DEMO_CALC_FIX_PATCH,
    DEMO_CALC_TEST_CMD,
    ExecutionPolicyGuard,
    ModifiedFixtureError,
    UntrustedRepositoryError,
    get_default_fixture_registry,
)
from repopilot_api.github_pr_service import GitHubPullRequestService
from repopilot_api.main import app
from repopilot_api.models import (
    AuthSession,
    GitHubPullRequest,
    IndexedFile,
    IndexingJob,
    ProofReport,
    Repository,
    RepositoryAccess,
    User,
    VerificationRun,
)
from repopilot_api.proof_engine import ProofOfFixEngine
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    MockSandboxRunner,
    SandboxExecutionState,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofOfFixRequest,
    ProofVerdict,
    PullRequestCreateRequest,
    VerificationCreateRequest,
    VerificationJobStatus,
)
from repopilot_api.security import (
    SESSION_COOKIE_NAME,
    csrf_token_for_session,
    digest,
)
from repopilot_api.verification_service import (
    VerificationJobManager,
    VerificationSecurityError,
    VerificationStateError,
    compute_spec_hash,
)

# ==============================================================================
# Auth & Client Helpers
# ==============================================================================


def create_authenticated_user(db, origin="http://localhost:3000"):
    """Seed user and valid session with CSRF token and origin headers."""
    user = User()
    db.add(user)
    db.flush()

    raw_token = f"session-token-{user.id}"
    csrf = csrf_token_for_session(raw_token)
    session = AuthSession(
        user_id=user.id,
        token_hash=digest(raw_token),
        csrf_hash=digest(csrf),
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    db.add(session)
    db.commit()
    db.refresh(user)

    headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={raw_token}",
        "X-CSRF-Token": csrf,
        "Origin": origin,
    }
    return user, headers


def get_client(test_session_factory):
    def override_get_db():
        db = test_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, base_url="http://testserver")


# ==============================================================================
# Tests
# ==============================================================================


def test_demo_initialization_and_idempotency(test_session, monkeypatch):
    """Phase 2: Safe, idempotent demo initialization creates preconfigured repo, index, symbols."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)
    get_headers = {"Cookie": headers["Cookie"]}

    # 1. Initial status before initialization
    res_status_before = client.get("/api/demo/status", headers=get_headers)
    assert res_status_before.status_code == 200
    data_status_before = res_status_before.json()
    assert data_status_before["initialized"] is False
    assert "docker_available" in data_status_before

    # 2. First Initialization Call
    res_init = client.post("/api/demo/initialize", headers=headers)
    assert res_init.status_code == 200
    data_init = res_init.json()

    assert data_init["repository"]["owner"] == "repopilot"
    assert f"demo-calc-{user.id}" in data_init["repository"]["name"]
    assert data_init["indexing_job"]["status"] == "completed"
    assert data_init["indexing_job"]["file_count"] == len(DEMO_CALC_FILES)
    assert data_init["indexing_job"]["commit_sha"] == DEMO_CALC_COMMIT_SHA
    assert data_init["issue"]["number"] == 1
    assert "power" in data_init["issue"]["title"].lower()
    assert data_init["test_command"] == DEMO_CALC_TEST_CMD

    repo_id = data_init["repository"]["id"]

    # Verify database records
    indexed_files = db.scalars(
        select(IndexedFile).where(IndexedFile.job_id == data_init["indexing_job"]["id"])
    ).all()
    assert len(indexed_files) == 2
    paths = {f.path for f in indexed_files}
    assert "src/calculator.py" in paths
    assert "tests/test_calculator.py" in paths

    # 3. Duplicate Initialization is strictly idempotent
    res_duplicate = client.post("/api/demo/initialize", headers=headers)
    assert res_duplicate.status_code == 200
    data_dup = res_duplicate.json()
    assert data_dup["repository"]["id"] == repo_id
    # Ensure no duplicate repositories created
    user_repos = db.scalars(
        select(RepositoryAccess).where(RepositoryAccess.user_id == user.id)
    ).all()
    assert len(user_repos) == 1

    # 4. Status endpoint reports initialized
    res_status_after = client.get("/api/demo/status", headers=get_headers)
    assert res_status_after.status_code == 200
    data_status_after = res_status_after.json()
    assert data_status_after["initialized"] is True
    assert data_status_after["repository_id"] == repo_id


def test_demo_user_and_session_isolation(test_session, monkeypatch):
    """Phase 2 & 5: Strict tenancy boundaries isolate demo repositories between different users."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user1, headers1 = create_authenticated_user(db)
    user2, headers2 = create_authenticated_user(db)

    # User 1 initializes demo
    res1 = client.post("/api/demo/initialize", headers=headers1)
    assert res1.status_code == 200
    repo1_id = res1.json()["repository"]["id"]

    # User 2 initializes demo
    res2 = client.post("/api/demo/initialize", headers=headers2)
    assert res2.status_code == 200
    repo2_id = res2.json()["repository"]["id"]

    # Repositories must be distinct
    assert repo1_id != repo2_id

    # User 1 CANNOT access User 2's demo repository
    res_unauth = client.get(
        f"/api/repositories/{repo2_id}/files",
        headers={"Cookie": headers1["Cookie"]},
    )
    assert res_unauth.status_code == 404

    # User 2 CANNOT access User 1's demo repository
    res_unauth2 = client.get(
        f"/api/repositories/{repo1_id}/files",
        headers={"Cookie": headers2["Cookie"]},
    )
    assert res_unauth2.status_code == 404


def test_demo_issue_investigation_and_patch_generation(test_session, monkeypatch):
    """Phase 3: Demo issue analysis generates grounded AST citations without GitHub calls."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    # Initialize demo
    res_init = client.post("/api/demo/initialize", headers=headers)
    repo_id = res_init.json()["repository"]["id"]

    # Analyze issue #1 on demo repo
    res_analyze = client.post(
        f"/api/repositories/{repo_id}/issues/analyze",
        json={"issue_number": 1},
        headers=headers,
    )
    assert res_analyze.status_code == 200
    analysis = res_analyze.json()

    # Verify provenance and mock distinction
    assert analysis["is_mock"] is True
    assert analysis["provider"] == "mock"
    assert "power" in analysis["analysis"]["root_cause_hypothesis"].lower()
    assert len(analysis["evidence"]) > 0
    assert "calculator.py" in analysis["evidence"][0]["path"]

    patches = analysis["analysis"]["proposed_fix"]["patches"]
    assert len(patches) > 0
    assert "return base ** exponent" in patches[0]["diff"]

    patches = analysis["analysis"]["proposed_fix"]["patches"]
    assert len(patches) > 0
    assert "return base ** exponent" in patches[0]["diff"]


def test_demo_approval_enforcement_and_unapproved_rejection(test_session):
    """Phase 3 & 5: Approval gating strictly prevents unapproved or tampered specifications."""
    db = test_session()
    user, _ = create_authenticated_user(db)

    # Seed demo repo
    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        default_branch="main",
    )
    db.add(repo)
    db.flush()
    db.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner"))

    job = IndexingJob(
        repository_id=repo.id,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        status="completed",
        file_count=2,
    )
    db.add(job)
    db.flush()

    for path, content in DEMO_CALC_FILES.items():
        db.add(
            IndexedFile(
                job_id=job.id,
                path=path,
                language="python",
                size_bytes=len(content.encode("utf-8")),
                content=content,
            )
        )
    db.commit()

    # Create verification request in pending_approval state
    create_req = VerificationCreateRequest(
        commit_sha=DEMO_CALC_COMMIT_SHA,
        issue_number=1,
        test_command=DEMO_CALC_TEST_CMD,
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)],
        hypothesis="Fix power calculation",
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
        auto_submit_approval=True,
    )

    run, is_new = VerificationJobManager.create_verification_request(
        db=db,
        repository_id=repo.id,
        user_id=user.id,
        payload=create_req,
    )
    assert is_new is True
    assert run.status == VerificationJobStatus.PENDING_APPROVAL.value
    assert run.approved_spec_hash is None

    # Attempt execution before approval MUST fail
    import anyio

    async def try_unapproved():
        with pytest.raises((VerificationSecurityError, VerificationStateError)) as exc_info:
            await VerificationJobManager.execute_job(db=db, run_id=run.id, user_id=user.id)
        assert (
            "approval" in str(exc_info.value).lower()
            or "state" in str(exc_info.value).lower()
        )

    anyio.run(try_unapproved)

    # Approve with explicit specification hash
    approved_run = VerificationJobManager.approve_verification(
        db=db,
        run_id=run.id,
        user_id=user.id,
        expected_spec_hash=run.spec_hash,
        auto_queue=True,
    )
    assert approved_run.status == VerificationJobStatus.QUEUED.value
    assert approved_run.approved_spec_hash == run.spec_hash


@pytest.mark.anyio
async def test_demo_verification_execution_and_status(test_session, tmp_path):
    """Phase 4: Dual-sandbox verification executes and records actual before/after test evidence."""
    db = test_session()
    user, _ = create_authenticated_user(db)
    scratch = str(tmp_path / "demo_scratch")

    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        default_branch="main",
    )
    db.add(repo)
    db.flush()
    db.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner"))

    job = IndexingJob(
        repository_id=repo.id,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        status="completed",
        file_count=2,
    )
    db.add(job)
    db.flush()
    for path, content in DEMO_CALC_FILES.items():
        db.add(
            IndexedFile(
                job_id=job.id,
                path=path,
                language="python",
                size_bytes=len(content.encode("utf-8")),
                content=content,
            )
        )
    db.commit()

    # Sequential runner: step 1 baseline failure (exit 1), step 2 patched success (exit 0)
    class DemoSequenceRunner(MockSandboxRunner):
        def __init__(self, steps):
            super().__init__(scratch_root=scratch, image=DEFAULT_SANDBOX_IMAGE)
            self.steps = steps
            self.count = 0

        async def run(self, ws, req):
            idx = min(self.count, len(self.steps) - 1)
            self.count += 1
            code, out, err = self.steps[idx]
            st = SandboxExecutionState.PASSED if code == 0 else SandboxExecutionState.FAILED
            return await MockSandboxRunner(
                simulated_state=st,
                simulated_exit_code=code,
                simulated_stdout=out,
                simulated_stderr=err,
                scratch_root=self.scratch_root,
                image=self.image,
            ).run(ws, req)

    mock_runner = DemoSequenceRunner(
        steps=[
            (
                1,
                "FAIL: test_power_positive\nAssertionError: 6 != 8\n"
                "FAIL: test_power_zero\nAssertionError: 0 != 1\nFAILED (failures=2)\n",
                "",
            ),
            (0, "Ran 6 tests in 0.002s\n\nOK\n", ""),
        ]
    )

    engine = ProofOfFixEngine(runner=mock_runner, scratch_root=scratch)
    req = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        issue_number=1,
        test_command=DEMO_CALC_TEST_CMD,
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)],
        hypothesis="Fix power operator bug in calculator.py",
        user_id=user.id,
        files=DEMO_CALC_FILES,
    )

    result = await engine.verify(req, db=db)

    # 1. Baseline reproduced failure
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.baseline_metrics.failed == 2

    # 2. Patched passed
    assert result.evidence.patched_exit_code == 0
    assert result.evidence.patched_metrics.passed == 6
    assert result.evidence.patched_metrics.failed == 0

    # 3. Verdict
    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert result.confidence == "high"


def test_demo_prevention_of_external_github_writes(test_session):
    """Phase 5: Demo repo PR creation is simulated locally; never calls external GitHub APIs."""
    db = test_session()
    user, _ = create_authenticated_user(db)

    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        default_branch="main",
    )
    db.add(repo)
    db.flush()
    db.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner"))

    patch_hash = "70e3ac85ad38065f147bb8898b564ebec4b4e61d4efc8c9988f83bc8f5945269"
    spec_hash = compute_spec_hash(
        DEMO_CALC_COMMIT_SHA, patch_hash, DEMO_CALC_TEST_CMD, DEFAULT_SANDBOX_IMAGE
    )

    run = VerificationRun(
        repository_id=repo.id,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        issue_number=1,
        user_id=user.id,
        status=VerificationJobStatus.COMPLETED.value,
        patch_diff=DEMO_CALC_FIX_PATCH,
        patch_hash=patch_hash,
        test_command=json.dumps(DEMO_CALC_TEST_CMD),
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
        approved_by_user_id=user.id,
        approved_spec_hash=spec_hash,
    )
    db.add(run)
    db.flush()

    report = ProofReport(
        verification_run_id=run.id,
        verdict=ProofVerdict.VERIFIED_FIX.value,
        hypothesis="Fix power operator bug",
        summary="Verified fix in dual-sandbox",
        baseline_summary="Ran 6 tests: 2 failed, 4 passed",
        patched_summary="Ran 6 tests: OK (all passed)",
        confidence="high",
        evidence_json=json.dumps(
            {
                "commit_sha": DEMO_CALC_COMMIT_SHA,
                "patch_hash": patch_hash,
                "approved_spec_hash": spec_hash,
            }
        ),
    )
    db.add(report)
    db.commit()

    # Call create_pull_request with NO token (simulated demo)
    pr_resp = GitHubPullRequestService.create_pull_request(
        db=db,
        repository_id=repo.id,
        verification_id=run.id,
        user_id=user.id,
        payload=PullRequestCreateRequest(
            github_token=None,  # No token provided
            custom_title="fix(calculator): resolve power operator multiplication bug",
        ),
    )

    assert pr_resp.success is True
    assert pr_resp.pr_number == 101
    assert "https://github.com/repopilot/" in pr_resp.pr_url
    assert pr_resp.status == "created"
    assert pr_resp.is_simulated is True

    # Verify PR record saved in database
    db_pr = db.scalar(
        select(GitHubPullRequest).where(GitHubPullRequest.verification_run_id == run.id)
    )
    assert db_pr is not None
    assert db_pr.pr_number == 101

    # Idempotent second call
    pr_dup = GitHubPullRequestService.create_pull_request(
        db=db,
        repository_id=repo.id,
        verification_id=run.id,
        user_id=user.id,
        payload=PullRequestCreateRequest(),
    )
    assert pr_dup.success is True
    assert pr_dup.status == "already_exists"


def test_demo_docker_unavailable_status_detection(monkeypatch):
    """Phase 4 & 5: When Docker is unavailable, status accurately reflects offline state."""
    # Simulate missing docker CLI
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    is_ok, msg = check_docker_daemon_status()
    assert is_ok is False
    assert "executable not found" in msg

    # Simulate failing docker info
    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/docker")

    class FakeProc:
        returncode = 1
        stderr = b"Cannot connect to the Docker daemon at unix:///var/run/docker.sock"

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeProc())
    is_ok2, msg2 = check_docker_daemon_status()
    assert is_ok2 is False
    assert "not accessible" in msg2


def test_demo_reset_flow(test_session, monkeypatch):
    """Phase 2 & 5: Reset endpoint clears prior runs and returns clean state."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    # Initialize
    res1 = client.post("/api/demo/initialize", headers=headers)
    assert res1.status_code == 200
    repo_id = res1.json()["repository"]["id"]
    assert repo_id is not None

    # Reset
    res_reset = client.post("/api/demo/reset", headers=headers)
    assert res_reset.status_code == 200
    data_reset = res_reset.json()
    assert data_reset["initialized"] is True
    assert data_reset["repository_id"] is not None
    assert data_reset["has_active_run"] is False


def test_demo_trust_cannot_be_spoofed_with_matching_name(test_session):
    """Review Item 1: Imported external repository cannot spoof demo trust via owner/name string."""
    registry = get_default_fixture_registry()

    # 1. Real server-controlled fixture repo (github_id >= 990_000_000)
    server_fixture_repo = Repository(
        id=101,
        github_id=990000001,
        owner="repopilot",
        name="demo-calc-1",
        full_name="repopilot/demo-calc-1",
        url="https://github.com/repopilot/demo-calculator",
        default_branch="main",
        stars=10,
    )
    assert (
        registry.is_repository_trusted(
            server_fixture_repo.owner,
            server_fixture_repo.name,
            github_id=server_fixture_repo.github_id,
        )
        is True
    )

    # 2. External imported repository with matching owner/name but real external github_id
    spoofed_imported_repo = Repository(
        id=102,
        github_id=12345678,  # Real external GitHub ID (< 990_000_000)
        owner="repopilot",
        name="demo-calculator",
        full_name="repopilot/demo-calculator",
        url="https://github.com/repopilot/demo-calculator",
        default_branch="main",
        stars=10,
    )
    # Must NOT be trusted as a demo fixture
    assert (
        registry.is_repository_trusted(
            spoofed_imported_repo.owner,
            spoofed_imported_repo.name,
            github_id=spoofed_imported_repo.github_id,
        )
        is False
    )

    # Policy validator must fail closed with UntrustedRepositoryError
    with pytest.raises(UntrustedRepositoryError) as exc_info:
        ExecutionPolicyGuard.validate_repository_trust(spoofed_imported_repo, registry)
    assert "not in the trusted repository allowlist" in str(exc_info.value)


def test_demo_content_tampering_rejected_by_policy(test_session):
    """Review Item 1: Fixture content tampering fails closed even with valid server identity."""
    db = test_session()
    user, _ = create_authenticated_user(db)
    registry = get_default_fixture_registry()

    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        default_branch="main",
        stars=42,
    )
    db.add(repo)
    db.flush()
    db.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner"))

    patch_hash = "70e3ac85ad38065f147bb8898b564ebec4b4e61d4efc8c9988f83bc8f5945269"
    spec_hash = compute_spec_hash(
        DEMO_CALC_COMMIT_SHA, patch_hash, DEMO_CALC_TEST_CMD, DEFAULT_SANDBOX_IMAGE
    )

    run = VerificationRun(
        repository_id=repo.id,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        issue_number=1,
        user_id=user.id,
        status=VerificationJobStatus.APPROVED.value,
        patch_diff=DEMO_CALC_FIX_PATCH,
        patch_hash=patch_hash,
        test_command=json.dumps(DEMO_CALC_TEST_CMD),
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
        approved_by_user_id=user.id,
        approved_spec_hash=spec_hash,
    )
    db.add(run)
    db.commit()

    # Tampered file content (injected code)
    tampered_files = dict(DEMO_CALC_FILES)
    tampered_files["src/calculator.py"] = "import os; os.system('echo malicious')\n"

    with pytest.raises(ModifiedFixtureError) as exc_info:
        ExecutionPolicyGuard.validate_all(
            db=db,
            run=run,
            files=tampered_files,
            registry=registry,
        )
    err = str(exc_info.value)
    assert "snapshot integrity check failed" in err or "mismatch" in err


def test_demo_reset_cancels_active_runs_and_deletes_pull_requests(test_session, monkeypatch):
    """Review Item 3: Reset cancels active runs and deletes linked PR records without error."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    # Initialize
    res_init = client.post("/api/demo/initialize", headers=headers)
    assert res_init.status_code == 200
    repo_id = res_init.json()["repository"]["id"]

    # Create active verification run (status: running)
    patch_hash = "70e3ac85ad38065f147bb8898b564ebec4b4e61d4efc8c9988f83bc8f5945269"
    spec_hash = compute_spec_hash(
        DEMO_CALC_COMMIT_SHA, patch_hash, DEMO_CALC_TEST_CMD, DEFAULT_SANDBOX_IMAGE
    )
    vrun = VerificationRun(
        repository_id=repo_id,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        issue_number=1,
        user_id=user.id,
        status="running",
        patch_diff=DEMO_CALC_FIX_PATCH,
        patch_hash=patch_hash,
        test_command=json.dumps(DEMO_CALC_TEST_CMD),
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
        approved_by_user_id=user.id,
        approved_spec_hash=spec_hash,
    )
    db.add(vrun)
    db.flush()

    # Add simulated pull request record
    pr_record = GitHubPullRequest(
        repository_id=repo_id,
        verification_run_id=vrun.id,
        created_by_user_id=user.id,
        issue_number=1,
        pr_number=101,
        pr_url="https://github.com/repopilot/demo-calculator/pull/101",
        branch_name="repopilot/fix-issue-1",
        base_branch="main",
        head_sha="mock_sha_" + "a" * 24,
        patch_hash=patch_hash,
        title="fix: resolve power bug",
        body="Automated fix",
    )
    db.add(pr_record)
    db.commit()
    pr_id = pr_record.id
    vrun_id = vrun.id

    # Perform Reset
    res_reset = client.post("/api/demo/reset", headers=headers)
    assert res_reset.status_code == 200
    assert res_reset.json()["initialized"] is True

    # Verify old PR and run are deleted
    db.expire_all()
    assert db.get(GitHubPullRequest, pr_id) is None
    assert db.get(VerificationRun, vrun_id) is None


def test_demo_concurrent_users_isolated(test_session, monkeypatch):
    """Review Item 3: Multiple users initializing and resetting demo cannot affect each other."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()

    user_a, headers_a = create_authenticated_user(db)
    user_b, headers_b = create_authenticated_user(db)

    # User A initializes
    res_a = client.post("/api/demo/initialize", headers=headers_a)
    assert res_a.status_code == 200
    repo_a_id = res_a.json()["repository"]["id"]

    # User B initializes
    res_b = client.post("/api/demo/initialize", headers=headers_b)
    assert res_b.status_code == 200
    repo_b_id = res_b.json()["repository"]["id"]

    assert repo_a_id != repo_b_id

    # User A resets demo
    res_a_reset = client.post("/api/demo/reset", headers=headers_a)
    assert res_a_reset.status_code == 200

    # User B's repository is completely untouched
    status_b = client.get("/api/demo/status", headers=headers_b)
    assert status_b.status_code == 200
    assert status_b.json()["repository_id"] == repo_b_id
