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
from sqlalchemy.exc import IntegrityError

from repopilot_api.demo import check_docker_daemon_status, is_internal_demo_repository
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
    AuthContext,
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
    provenance_tag = f"[provenance:internal_fixture:{user.id}:demo-calculator]"
    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        description=f"Deterministic Calculator Demo Fixture for RepoPilot Fix {provenance_tag}",
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

    provenance_tag = f"[provenance:internal_fixture:{user.id}:demo-calculator]"
    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        description=f"Deterministic Calculator Demo Fixture for RepoPilot Fix {provenance_tag}",
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

    provenance_tag = f"[provenance:internal_fixture:{user.id}:demo-calculator]"
    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        description=f"Deterministic Calculator Demo Fixture for RepoPilot Fix {provenance_tag}",
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

    provenance_tag = f"[provenance:internal_fixture:{user.id}:demo-calculator]"
    repo = Repository(
        github_id=990000000 + user.id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        description=f"Deterministic Calculator Demo Fixture for RepoPilot Fix {provenance_tag}",
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
    """Review Item 2: Reset rejects active running runs (409) and cleanly deletes cancelled runs."""
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

    # 1. Reset MUST be rejected while verification run is actively running in Docker
    res_running = client.post("/api/demo/reset", headers=headers)
    assert res_running.status_code == 409
    assert "actively executing in Docker" in res_running.json()["detail"]

    # Verify run and PR were NOT deleted
    assert db.get(VerificationRun, vrun_id) is not None
    assert db.get(GitHubPullRequest, pr_id) is not None

    # 2. Cancel the run (simulating worker cancellation or user cancel)
    vrun.status = "cancelled"
    db.commit()

    # 3. Reset now succeeds cleanly
    res_reset = client.post("/api/demo/reset", headers=headers)
    assert res_reset.status_code == 200
    assert res_reset.json()["initialized"] is True

    # Verify old PR and run are deleted
    db.expire_all()
    assert db.get(GitHubPullRequest, pr_id) is None
    assert db.get(VerificationRun, vrun_id) is None


def test_external_import_cannot_acquire_demo_provenance(test_session, monkeypatch):
    """Review Item 1: External repository imports cannot acquire internal demo provenance."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    # 1. Reject import of exact demo fixture URL
    res_1 = client.post(
        "/api/repositories/import",
        json={"url": "https://github.com/repopilot/demo-calculator"},
        headers=headers,
    )
    assert res_1.status_code == 400
    assert "cannot acquire internal demo fixture provenance" in res_1.json()["detail"]

    # 2. Reject import of per-user demo name pattern
    res_2 = client.post(
        "/api/repositories/import",
        json={"url": "https://github.com/repopilot/demo-calc-1"},
        headers=headers,
    )
    assert res_2.status_code == 400
    assert "cannot acquire internal demo fixture provenance" in res_2.json()["detail"]


def test_demo_provenance_full_canonical_validation(test_session, monkeypatch):
    """Review Item 1: Provenance requires exact identity, ownership, and canonical content."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    # Initialize demo
    res = client.post("/api/demo/initialize", headers=headers)
    assert res.status_code == 200
    repo_id = res.json()["repository"]["id"]
    repo = db.get(Repository, repo_id)
    assert repo is not None

    # Verify canonical content passes
    assert is_internal_demo_repository(db, repo, user_id=user.id, verify_content=True) is True

    # Other user cannot claim provenance
    assert not is_internal_demo_repository(
        db, repo, user_id=user.id + 999, verify_content=True
    )

    # Tamper with an indexed file in database
    job = db.scalar(
        select(IndexingJob)
        .where(IndexingJob.repository_id == repo.id)
        .order_by(IndexingJob.id.desc())
    )
    file_record = db.scalar(
        select(IndexedFile).where(
            IndexedFile.job_id == job.id,
            IndexedFile.path == "src/calculator.py",
        )
    )
    assert file_record is not None
    file_record.content = "malicious_override = True\n"
    db.commit()

    # Tampered content fails canonical validation
    assert is_internal_demo_repository(db, repo, user_id=user.id, verify_content=True) is False


def test_concurrent_demo_initialization_thread_safe(test_session, monkeypatch):
    """Review Item 3: Concurrent initialization is safe and creates no duplicate repos."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    db = test_session()
    user, headers = create_authenticated_user(db)
    session_obj = db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
    auth = AuthContext(user=user, session=session_obj)

    from repopilot_api.demo import initialize_demo

    # 1. Verify idempotency and lock safety across multiple initialize calls
    res_1 = initialize_demo(reset=False, db=db, auth=auth)
    res_2 = initialize_demo(reset=False, db=db, auth=auth)
    assert res_1.repository.id == res_2.repository.id

    # 2. Verify duplicate DB creation attempt triggers unique constraint and rolls back safely
    duplicate_repo = Repository(
        github_id=res_1.repository.github_id,
        owner="repopilot",
        name=f"demo-calc-{user.id}",
        full_name=f"repopilot/demo-calc-{user.id}",
        url="https://github.com/repopilot/demo-calculator",
        default_branch="main",
    )
    db.add(duplicate_repo)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()

    # 3. Post-rollback recovery returns clean existing repository without creating duplicate
    res_3 = initialize_demo(reset=False, db=db, auth=auth)
    assert res_3.repository.id == res_1.repository.id

    # 4. HTTP client entry point confirms idempotency
    client = get_client(test_session)
    res_client_1 = client.post("/api/demo/initialize", headers=headers)
    assert res_client_1.status_code == 200
    res_client_2 = client.post("/api/demo/initialize", headers=headers)
    assert res_client_2.status_code == 200
    assert res_client_1.json()["repository"]["id"] == res_client_2.json()["repository"]["id"]

    # Exactly one repository exists for this user in DB
    repos = db.scalars(
        select(Repository).where(Repository.name == f"demo-calc-{user.id}")
    ).all()
    assert len(repos) == 1


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


def test_unauthenticated_demo_status_docker_capability(test_session, monkeypatch):
    """Regression: Guest without a session gets 200 OK with accurate Docker capability."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)

    # Completely unauthenticated request (no Cookie, no Authorization header)
    res = client.get("/api/demo/status")
    assert res.status_code == 200
    data = res.json()
    assert data["initialized"] is False
    assert "docker_available" in data
    assert "docker_status_message" in data
    assert data["repository_id"] is None


def test_guest_session_initialization_and_reuse(test_session, monkeypatch):
    """Regression: POST /api/auth/guest initializes secure guest session and is idempotent."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)

    # 1. Invalid origin is rejected
    res_invalid_origin = client.post("/api/auth/guest", headers={"Origin": "http://malicious.com"})
    assert res_invalid_origin.status_code == 403

    # 2. Valid origin initializes guest session
    res_guest = client.post("/api/auth/guest", headers={"Origin": "http://localhost:3000"})
    assert res_guest.status_code == 200
    data = res_guest.json()
    assert data["authenticated"] is True
    assert data["user"]["is_guest"] is True
    guest_id = data["user"]["id"]
    assert data["user"]["login"] == f"guest-{guest_id}"
    csrf_token = data["csrf_token"]
    assert csrf_token is not None and len(csrf_token) == 64

    # Cookie is returned with HttpOnly and SameSite=lax
    assert SESSION_COOKIE_NAME in res_guest.cookies
    session_cookie = res_guest.cookies[SESSION_COOKIE_NAME]

    guest_headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={session_cookie}",
        "Origin": "http://localhost:3000",
        "X-CSRF-Token": csrf_token,
    }

    # 3. GET /api/auth/session reports authenticated guest
    res_session = client.get("/api/auth/session", headers={"Cookie": guest_headers["Cookie"]})
    assert res_session.status_code == 200
    sess_data = res_session.json()
    assert sess_data["authenticated"] is True
    assert sess_data["user"]["id"] == guest_id
    assert sess_data["user"]["is_guest"] is True

    # 4. Calling POST /api/auth/guest again with existing session reuses it
    res_reuse = client.post("/api/auth/guest", headers=guest_headers)
    assert res_reuse.status_code == 200
    reuse_data = res_reuse.json()
    assert reuse_data["user"]["id"] == guest_id
    assert reuse_data["user"]["is_guest"] is True


def test_guest_demo_complete_workflow_and_isolation(test_session, monkeypatch):
    """Regression: Guest demo session initializes, steps through workflow, and isolates data."""
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()

    # Create an authenticated GitHub user with their own repo
    github_user, gh_headers = create_authenticated_user(db)
    res_gh_init = client.post("/api/demo/initialize", headers=gh_headers)
    assert res_gh_init.status_code == 200
    gh_repo_id = res_gh_init.json()["repository"]["id"]

    # 1. Guest acquires a guest session
    res_guest_auth = client.post("/api/auth/guest", headers={"Origin": "http://localhost:3000"})
    assert res_guest_auth.status_code == 200
    guest_data = res_guest_auth.json()
    guest_id = guest_data["user"]["id"]
    guest_cookie = res_guest_auth.cookies[SESSION_COOKIE_NAME]
    guest_csrf = guest_data["csrf_token"]

    guest_headers = {
        "Cookie": f"{SESSION_COOKIE_NAME}={guest_cookie}",
        "Origin": "http://localhost:3000",
        "X-CSRF-Token": guest_csrf,
    }

    # 2. Before init, status reports uninitialized for guest
    res_status = client.get("/api/demo/status", headers={"Cookie": guest_headers["Cookie"]})
    assert res_status.status_code == 200
    assert res_status.json()["initialized"] is False

    # 3. Guest CANNOT access GitHub user's demo repo
    res_leak = client.get(
        f"/api/repositories/{gh_repo_id}/files",
        headers={"Cookie": guest_headers["Cookie"]},
    )
    assert res_leak.status_code == 404

    # 4. Guest initializes demo
    res_init = client.post("/api/demo/initialize", headers=guest_headers)
    assert res_init.status_code == 200
    guest_init = res_init.json()
    guest_repo_id = guest_init["repository"]["id"]
    assert guest_init["repository"]["name"] == f"demo-calc-{guest_id}"
    assert guest_repo_id != gh_repo_id

    # 5. Guest status now reports initialized
    res_status_after = client.get("/api/demo/status", headers={"Cookie": guest_headers["Cookie"]})
    assert res_status_after.status_code == 200
    assert res_status_after.json()["initialized"] is True
    assert res_status_after.json()["repository_id"] == guest_repo_id

    # 6. Guest runs issue analysis (Stage 3 & 4)
    res_analyze = client.post(
        f"/api/repositories/{guest_repo_id}/issues/analyze",
        json={"issue_number": 1},
        headers=guest_headers,
    )
    assert res_analyze.status_code == 200
    analysis = res_analyze.json()
    assert analysis["is_mock"] is True
    assert "power" in analysis["analysis"]["root_cause_hypothesis"].lower()

    # 7. GitHub user CANNOT access Guest user's demo repo
    res_gh_probe = client.get(
        f"/api/repositories/{guest_repo_id}/files",
        headers={"Cookie": gh_headers["Cookie"]},
    )
    assert res_gh_probe.status_code == 404

    # 8. Unauthenticated mutation attempts fail closed
    res_unauth_init = client.post("/api/demo/initialize")
    assert res_unauth_init.status_code in {401, 403}

    res_unauth_reset = client.post("/api/demo/reset")
    assert res_unauth_reset.status_code in {401, 403}

    # 9. Guest resets demo: only guest repo is reset, GitHub user repo remains untouched
    res_reset = client.post("/api/demo/reset", headers=guest_headers)
    assert res_reset.status_code == 200
    assert res_reset.json()["initialized"] is True
    assert res_reset.json()["has_active_run"] is False

    res_gh_status = client.get("/api/demo/status", headers={"Cookie": gh_headers["Cookie"]})
    assert res_gh_status.status_code == 200
    assert res_gh_status.json()["initialized"] is True
    assert res_gh_status.json()["repository_id"] == gh_repo_id


def test_stage5_and_stage7_verification_consistency(test_session, monkeypatch):
    """Regression test for Stage 5 and Stage 7 verification consistency.

    Verifies:
    1. Demo init provides authoritative pinned image and unittest test command.
    2. Submitting prefix 'python:3.11-slim' is canonicalized to fixture's pinned digest.
    3. Submitting an unapproved test command (e.g. pytest) fails closed with 422.
    4. Submitting an unallowlisted/arbitrary image fails closed with 422.
    5. Spec hash uses the canonical pinned image.
    6. Tampering with execution spec after approval triggers specification mismatch error.
    7. Evidence report retains authoritative canonical image.
    """
    import hashlib
    import tempfile

    import anyio

    from repopilot_api.verification_service import VerificationSpecificationMismatchError

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    # 1. Initialize demo
    res_init = client.post("/api/demo/initialize", headers=headers)
    assert res_init.status_code == 200
    init_data = res_init.json()
    repo_id = init_data["repository"]["id"]

    assert init_data["test_command"] == DEMO_CALC_TEST_CMD
    assert init_data["sandbox_image"] == DEFAULT_SANDBOX_IMAGE
    assert "@sha256:" in init_data["sandbox_image"]

    # 2. Reject unapproved test command (e.g. pytest) during execution via policy guard
    from repopilot_api.fixture_registry import FixtureRegistryError
    res_bad_cmd = client.post(
        f"/api/repositories/{repo_id}/verifications",
        json={
            "commit_sha": DEMO_CALC_COMMIT_SHA,
            "issue_number": 1,
            "test_command": ["python", "-m", "unittest", "tests/unapproved_test.py"],
            "patches": [{"file_path": "src/calculator.py", "diff": DEMO_CALC_FIX_PATCH}],
            "hypothesis": "Test invalid command rejection",
            "sandbox_image": DEFAULT_SANDBOX_IMAGE,
            "auto_submit_approval": True,
        },
        headers=headers,
    )
    assert res_bad_cmd.status_code == 201
    bad_run_id = res_bad_cmd.json()["id"]
    client.post(
        f"/api/repositories/{repo_id}/verifications/{bad_run_id}/approve",
        json={"expected_spec_hash": res_bad_cmd.json()["spec_hash"]},
        headers=headers,
    )
    db.expire_all()

    async def try_unapproved_cmd():
        with pytest.raises(FixtureRegistryError) as exc_info:
            await VerificationJobManager.execute_job(db=db, run_id=bad_run_id, user_id=user.id)
        assert "not approved for fixture" in str(exc_info.value).lower()

    anyio.run(try_unapproved_cmd)

    # 3. Reject arbitrary unapproved image for fixture
    res_bad_img = client.post(
        f"/api/repositories/{repo_id}/verifications",
        json={
            "commit_sha": DEMO_CALC_COMMIT_SHA,
            "issue_number": 1,
            "test_command": DEMO_CALC_TEST_CMD,
            "patches": [{"file_path": "src/calculator.py", "diff": DEMO_CALC_FIX_PATCH}],
            "hypothesis": "Test arbitrary image rejection",
            "sandbox_image": "debian:bullseye",
        },
        headers=headers,
    )
    assert res_bad_img.status_code == 422
    assert "does not match fixture" in res_bad_img.json()["detail"].lower()

    # 4. Canonicalize unpinned tag prefix 'python:3.11-slim' to authoritative digest
    res_create = client.post(
        f"/api/repositories/{repo_id}/verifications",
        json={
            "commit_sha": DEMO_CALC_COMMIT_SHA,
            "issue_number": 1,
            "test_command": DEMO_CALC_TEST_CMD,
            "patches": [{"file_path": "src/calculator.py", "diff": DEMO_CALC_FIX_PATCH}],
            "hypothesis": "Fix power calculation with canonical image",
            "sandbox_image": "python:3.11-slim",
            "auto_submit_approval": True,
        },
        headers=headers,
    )
    assert res_create.status_code == 201
    run_data = res_create.json()
    run_id = run_data["id"]
    assert run_data["sandbox_image"] == DEFAULT_SANDBOX_IMAGE
    assert run_data["status"] == "pending_approval"

    # Verify spec hash was computed with canonical image
    patch_hash = hashlib.sha256(DEMO_CALC_FIX_PATCH.strip().encode("utf-8")).hexdigest()
    expected_hash = compute_spec_hash(
        DEMO_CALC_COMMIT_SHA, patch_hash, DEMO_CALC_TEST_CMD, DEFAULT_SANDBOX_IMAGE
    )
    assert run_data["spec_hash"] == expected_hash

    # 5. Approve specification
    res_approve = client.post(
        f"/api/repositories/{repo_id}/verifications/{run_id}/approve",
        json={"expected_spec_hash": expected_hash, "comment": "Approved consistent spec"},
        headers=headers,
    )
    assert res_approve.status_code == 200
    approved_data = res_approve.json()
    assert approved_data["status"] == "queued"
    assert approved_data["approved_spec_hash"] == expected_hash

    # 6. Tamper check: modifying run in DB invalidates approval and rejects execution
    run_record = db.get(VerificationRun, run_id)
    original_image = run_record.sandbox_image
    tampered_digest = "0" * 64
    run_record.sandbox_image = f"python:3.10-slim@sha256:{tampered_digest}"
    db.commit()

    async def try_tampered():
        with pytest.raises(VerificationSpecificationMismatchError):
            await VerificationJobManager.execute_job(db=db, run_id=run_id, user_id=user.id)

    anyio.run(try_tampered)

    # Restore canonical image and re-approve
    run_record.sandbox_image = original_image
    run_record.status = "pending_approval"
    run_record.approved_spec_hash = None
    db.commit()

    res_reapprove = client.post(
        f"/api/repositories/{repo_id}/verifications/{run_id}/approve",
        json={"expected_spec_hash": expected_hash, "comment": "Fresh approval after spec fix"},
        headers=headers,
    )
    assert res_reapprove.status_code == 200
    db.expire_all()

    # 7. Execute with MockSandboxRunner
    class ConsistentMockRunner(MockSandboxRunner):
        def __init__(self, scratch: str):
            super().__init__(scratch_root=scratch, image=DEFAULT_SANDBOX_IMAGE)
            self._call_count = 0

        async def run(self, workspace_path: str, request):
            self._call_count += 1
            if self._call_count == 1:
                self.simulated_exit_code = 1
                self.simulated_stdout = "FAILED (failures=2)\nRan 6 tests in 0.05s"
            else:
                self.simulated_exit_code = 0
                self.simulated_stdout = "Ran 6 tests in 0.04s\n\nOK\n"
            return await super().run(workspace_path, request)

    with tempfile.TemporaryDirectory() as scratch_dir:
        mock_runner = ConsistentMockRunner(scratch_dir)

        async def run_exec():
            return await VerificationJobManager.execute_job(
                db=db,
                run_id=run_id,
                runner=mock_runner,
                scratch_root=scratch_dir,
                user_id=user.id,
            )

        result = anyio.run(run_exec)
        assert result.verdict == ProofVerdict.VERIFIED_FIX
        assert result.evidence.sandbox_image == DEFAULT_SANDBOX_IMAGE
        assert result.evidence.test_command == DEMO_CALC_TEST_CMD


def test_unverified_verdict_blocks_pr_and_patch_export(test_session, monkeypatch):
    """Ensure PR creation and patch export are blocked if verification is not VERIFIED_FIX."""
    from repopilot_api.models import ProofReport, VerificationRun

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    res_init = client.post("/api/demo/initialize", headers=headers)
    assert res_init.status_code == 200
    repo_id = res_init.json()["repository"]["id"]

    res_create = client.post(
        f"/api/repositories/{repo_id}/verifications",
        json={
            "commit_sha": DEMO_CALC_COMMIT_SHA,
            "issue_number": 1,
            "test_command": DEMO_CALC_TEST_CMD,
            "patches": [{"file_path": "src/calculator.py", "diff": DEMO_CALC_FIX_PATCH}],
            "hypothesis": "Test unverified gating",
            "sandbox_image": DEFAULT_SANDBOX_IMAGE,
            "auto_submit_approval": True,
        },
        headers=headers,
    )
    assert res_create.status_code == 201
    run_id = res_create.json()["id"]

    # Approve
    client.post(
        f"/api/repositories/{repo_id}/verifications/{run_id}/approve",
        json={"expected_spec_hash": res_create.json()["spec_hash"]},
        headers=headers,
    )

    # Simulate completed run with UNREPRODUCED verdict
    run = db.get(VerificationRun, run_id)
    run.status = "completed"
    report = ProofReport(
        verification_run_id=run.id,
        verdict="unreproduced",
        hypothesis="Original bug not reproducible",
        summary="Baseline passed without reproducing bug",
        baseline_summary="Passed",
        patched_summary="Pending",
        confidence="low",
        evidence_json="{}",
    )
    db.add(report)
    db.commit()

    # PR creation must be blocked
    res_pr = client.post(
        f"/api/repositories/{repo_id}/verifications/{run_id}/create-pr",
        json={"custom_title": "Blocked PR"},
        headers=headers,
    )
    assert res_pr.status_code == 400
    assert "Only verified fixes ('verified_fix') qualify for PR creation" in res_pr.json()["detail"]

    # Patch export must be blocked
    res_export = client.get(
        f"/api/repositories/{repo_id}/verifications/{run_id}/export-patch",
        headers=headers,
    )
    assert res_export.status_code == 400
    assert "Only verified fixes qualify for verified patch export" in res_export.json()["detail"]


@pytest.mark.docker
def test_demo_real_docker_verification_e2e(test_session, monkeypatch):
    """Execute end-to-end demo verification workflow using real Docker containers."""
    import anyio

    from repopilot_api.sandbox import check_docker_daemon_status

    docker_ok, reason = check_docker_daemon_status()
    if not docker_ok:
        pytest.skip(f"Docker daemon unavailable: {reason}")

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    client = get_client(test_session)
    db = test_session()
    user, headers = create_authenticated_user(db)

    # Step 1: Initialize demo repository
    res_init = client.post("/api/demo/initialize", headers=headers)
    assert res_init.status_code == 200
    repo_id = res_init.json()["repository"]["id"]

    # Step 2: Create verification run
    res_create = client.post(
        f"/api/repositories/{repo_id}/verifications",
        json={
            "commit_sha": DEMO_CALC_COMMIT_SHA,
            "issue_number": 1,
            "test_command": DEMO_CALC_TEST_CMD,
            "patches": [{"file_path": "src/calculator.py", "diff": DEMO_CALC_FIX_PATCH}],
            "hypothesis": "Fix power operator bug from multiplication to exponentiation",
            "sandbox_image": DEFAULT_SANDBOX_IMAGE,
            "auto_submit_approval": True,
        },
        headers=headers,
    )
    assert res_create.status_code == 201
    run_id = res_create.json()["id"]
    spec_hash = res_create.json()["spec_hash"]

    # Step 3: Human operator approval
    res_approve = client.post(
        f"/api/repositories/{repo_id}/verifications/{run_id}/approve",
        json={"expected_spec_hash": spec_hash, "comment": "Operator verified approval"},
        headers=headers,
    )
    assert res_approve.status_code == 200

    # Step 4: Execute real Docker verification (uses auto-detected DockerSandboxRunner)
    async def do_execute():
        return await VerificationJobManager.execute_job(db=db, run_id=run_id, user_id=user.id)

    result = anyio.run(do_execute)

    # Assert exact deterministic evidence-based outcomes
    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert result.evidence is not None
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.patched_exit_code == 0
    assert result.evidence.baseline_metrics.total == 6
    assert result.evidence.baseline_metrics.failed == 2
    assert result.evidence.baseline_metrics.passed == 4
    assert result.evidence.patched_metrics.total == 6
    assert result.evidence.patched_metrics.failed == 0
    assert result.evidence.patched_metrics.passed == 6
    assert len(result.evidence.resolved_test_ids) == 2
    assert len(result.evidence.new_failed_test_ids) == 0

    # Step 5: Check Report API
    res_report = client.get(
        f"/api/repositories/{repo_id}/verifications/{run_id}/report",
        headers=headers,
    )
    assert res_report.status_code == 200
    assert res_report.json()["verdict"] == "verified_fix"

    # Step 6: Export Patch succeeds for verified fix
    res_export = client.get(
        f"/api/repositories/{repo_id}/verifications/{run_id}/export-patch",
        headers=headers,
    )
    assert res_export.status_code == 200
    assert "return base ** exponent" in res_export.json()["patch_diff"]

    # Step 7: Simulated PR succeeds for verified fix
    res_pr = client.post(
        f"/api/repositories/{repo_id}/verifications/{run_id}/create-pr",
        json={"custom_title": "Fix power calculation in calculator"},
        headers=headers,
    )
    assert res_pr.status_code == 200
    assert res_pr.json()["pr_number"] == 101
    assert res_pr.json()["success"] is True



