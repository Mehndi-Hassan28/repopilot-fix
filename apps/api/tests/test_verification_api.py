"""Tests for Milestone 3.4 — Secure Verification API, Human Approval, and Durable Job Management."""

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select

from repopilot_api.models import (
    AuthSession,
    IndexedFile,
    IndexingJob,
    ProofReport,
    Repository,
    RepositoryAccess,
    User,
    VerificationAuditEvent,
    VerificationRun,
)
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    MockSandboxRunner,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofVerdict,
    VerificationApprovalRequest,
    VerificationCancelRequest,
    VerificationCreateRequest,
    VerificationJobStatus,
    VerificationRejectionRequest,
)
from repopilot_api.security import AuthContext
from repopilot_api.verification_router import (
    approve_verification,
    cancel_verification,
    create_verification,
    get_verification,
    get_verification_audit_events,
    get_verification_report,
    list_verifications,
    queue_verification,
    reject_verification,
    submit_verification,
)
from repopilot_api.verification_service import (
    VerificationConcurrencyError,
    VerificationJobManager,
    VerificationSecurityError,
    VerificationSpecificationMismatchError,
    VerificationStateError,
    check_rate_limit,
    compute_spec_hash,
)


def make_user(db):
    user = User()
    db.add(user)
    db.flush()
    return user


def make_repo(db, github_id=999, owner="acme", name="app"):
    repo = Repository(
        github_id=github_id,
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        url=f"https://github.com/{owner}/{name}",
        default_branch="main",
    )
    db.add(repo)
    db.flush()
    return repo


def make_index(db, repo, commit_sha="a" * 40, files=None):
    job = IndexingJob(repository_id=repo.id, commit_sha=commit_sha, status="completed")
    db.add(job)
    db.flush()

    calc_test = (
        "import unittest\n"
        "from src.calc import add\n"
        "class TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n"
    )
    file_map = files or {
        "src/calc.py": "def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": calc_test,
    }
    for path, content in file_map.items():
        db.add(
            IndexedFile(
                job_id=job.id,
                path=path,
                language="python",
                size_bytes=len(content.encode()),
                content=content,
            )
        )
    db.flush()
    return job


def make_auth(user):
    session = AuthSession(
        user_id=user.id,
        token_hash="h",
        csrf_hash="c",
        expires_at=datetime.now(UTC),
    )
    return AuthContext(user=user, session=session)


def add_access(db, repo, user, role="owner"):
    db.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role=role))
    db.flush()


def sample_patch():
    diff_text = (
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -2,1 +2,1 @@\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )
    return [PatchProposal(file_path="src/calc.py", diff=diff_text)]


def sample_create_payload(auto_submit=True, idempotency_key=None):
    return VerificationCreateRequest(
        commit_sha="a" * 40,
        issue_number=1,
        test_command=["python", "-m", "unittest", "tests/test_calc.py"],
        patches=sample_patch(),
        hypothesis="Fixed inverted subtraction in add()",
        auto_submit_approval=auto_submit,
        idempotency_key=idempotency_key,
    )


# ==============================================================================
# 1. Authentication & Cross-User Authorization
# ==============================================================================


def test_cross_user_repository_access_denied(test_session):
    """User without access to the repository cannot create or view verifications."""
    with test_session() as db:
        owner = make_user(db)
        stranger = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, owner)

        owner_auth = make_auth(owner)
        stranger_auth = make_auth(stranger)

        resp = Response()
        # Owner succeeds
        run_read = create_verification(
            repo.id, sample_create_payload(), resp, None, db, owner_auth
        )
        assert run_read.id is not None

        # Stranger is denied with 404 (server-side authorization protection)
        with pytest.raises(HTTPException) as exc:
            create_verification(
                repo.id, sample_create_payload(), resp, None, db, stranger_auth
            )
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            get_verification(repo.id, run_read.id, db, stranger_auth)
        assert exc.value.status_code == 404

        with pytest.raises(HTTPException) as exc:
            list_verifications(repo.id, None, 50, 0, db, stranger_auth)
        assert exc.value.status_code == 404


def test_unauthorized_approval_denied(test_session):
    """Users without owner or collaborator roles cannot approve verifications."""
    with test_session() as db:
        owner = make_user(db)
        stranger = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, owner, role="owner")

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, owner.id, sample_create_payload()
        )

        stranger_auth = make_auth(stranger)
        with pytest.raises(HTTPException) as exc:
            approve_verification(
                repo.id, run.id, VerificationApprovalRequest(), db, stranger_auth
            )
        assert exc.value.status_code == 404


# ==============================================================================
# 2. Immutable Specification & Specification Mismatch
# ==============================================================================


def test_specification_hash_computation():
    """Specification hash is deterministic, canonical, and changes with any parameter."""
    sha = "a" * 40
    patch_hash = "f" * 64
    cmd = ["pytest", "tests/"]
    img = DEFAULT_SANDBOX_IMAGE

    h1 = compute_spec_hash(sha, patch_hash, cmd, img)
    h2 = compute_spec_hash(sha, patch_hash, cmd, img)
    assert h1 == h2

    # Modified test command changes spec hash
    h3 = compute_spec_hash(sha, patch_hash, ["pytest", "tests/", "-v"], img)
    assert h1 != h3

    # Modified patch hash changes spec hash
    h4 = compute_spec_hash(sha, "0" * 64, cmd, img)
    assert h1 != h4


def test_approval_specification_mismatch_rejected(test_session):
    """Approval fails if expected_spec_hash does not match current specification."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )

        auth = make_auth(user)
        # Approval with mismatched expected_spec_hash
        with pytest.raises(HTTPException) as exc:
            approve_verification(
                repo.id,
                run.id,
                VerificationApprovalRequest(expected_spec_hash="wrong_hash"),
                db,
                auth,
            )
        assert exc.value.status_code == 409
        assert "Specification hash mismatch" in exc.value.detail


def test_tampered_specification_blocked_before_execution(test_session):
    """If a specification is tampered with after approval, execution is strictly blocked."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        # Maliciously alter test command in database
        run.test_command = json.dumps(["python", "-m", "unittest", "tests/malicious.py"])
        db.commit()

        # Attempting execution must raise specification mismatch
        with pytest.raises(VerificationSpecificationMismatchError):
            import asyncio

            asyncio.run(
                VerificationJobManager.execute_job(db, run.id, runner=MockSandboxRunner())
            )

        db.refresh(run)
        assert run.status == VerificationJobStatus.FAILED.value
        assert "Specification mismatch" in run.error_message


# ==============================================================================
# 3. Idempotency & Duplicate Submissions
# ==============================================================================


def test_idempotent_duplicate_submission(test_session):
    """Duplicate submission with same Idempotency-Key returns existing run without duplicate."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        auth = make_auth(user)

        resp1 = Response()
        first = create_verification(
            repo.id,
            sample_create_payload(),
            resp1,
            idempotency_key="unique_request_123",
            db=db,
            auth=auth,
        )
        assert resp1.status_code == 201

        resp2 = Response()
        second = create_verification(
            repo.id,
            sample_create_payload(),
            resp2,
            idempotency_key="unique_request_123",
            db=db,
            auth=auth,
        )
        # Idempotent replay: status 200, identical ID
        assert resp2.status_code == 200
        assert first.id == second.id
        assert first.spec_hash == second.spec_hash

        total_runs = (
            db.query(VerificationRun)
            .filter(VerificationRun.repository_id == repo.id)
            .count()
        )
        assert total_runs == 1


# ==============================================================================
# 4. State Machine & Invalid Transitions
# ==============================================================================


def test_human_approval_state_lifecycle(test_session):
    """Full lifecycle: DRAFT -> PENDING_APPROVAL -> APPROVED -> QUEUED."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        auth = make_auth(user)

        # 1. Create in DRAFT
        resp = Response()
        run = create_verification(
            repo.id, sample_create_payload(auto_submit=False), resp, None, db, auth
        )
        assert run.status == VerificationJobStatus.DRAFT.value

        # 2. Submit for approval -> PENDING_APPROVAL
        submitted = submit_verification(repo.id, run.id, db, auth)
        assert submitted.status == VerificationJobStatus.PENDING_APPROVAL.value

        # Cannot re-submit if not DRAFT
        with pytest.raises(HTTPException) as exc:
            submit_verification(repo.id, run.id, db, auth)
        assert exc.value.status_code == 409

        # 3. Approve with auto_queue=False -> APPROVED
        approved = approve_verification(
            repo.id,
            run.id,
            VerificationApprovalRequest(auto_queue=False),
            db,
            auth,
        )
        assert approved.status == VerificationJobStatus.APPROVED.value
        assert approved.approved_by_user_id == user.id
        assert approved.approved_spec_hash is not None

        # 4. Queue -> QUEUED
        queued = queue_verification(repo.id, run.id, db, auth)
        assert queued.status == VerificationJobStatus.QUEUED.value


def test_reject_verification_flow(test_session):
    """Rejecting verification transitions to REJECTED."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        auth = make_auth(user)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        assert run.status == VerificationJobStatus.PENDING_APPROVAL.value

        rejected = reject_verification(
            repo.id,
            run.id,
            VerificationRejectionRequest(reason="Test command looks suspicious"),
            db,
            auth,
        )
        assert rejected.status == VerificationJobStatus.REJECTED.value
        assert rejected.rejection_reason == "Test command looks suspicious"

        # Cannot approve or reject already rejected run
        with pytest.raises(HTTPException) as exc:
            approve_verification(repo.id, run.id, VerificationApprovalRequest(), db, auth)
        assert exc.value.status_code == 409


def test_cannot_execute_unapproved_verification(test_session):
    """Attempting execution without prior approval is blocked."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload(auto_submit=False)
        )
        assert run.status == VerificationJobStatus.DRAFT.value

        with pytest.raises(VerificationStateError):
            import asyncio

            asyncio.run(
                VerificationJobManager.execute_job(db, run.id, runner=MockSandboxRunner())
            )


# ==============================================================================
# 5. Cancellation
# ==============================================================================


@pytest.mark.anyio
async def test_cancellation_before_execution(test_session):
    """Cancellation in PENDING_APPROVAL or QUEUED state updates status and audit log."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        auth = make_auth(user)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        cancelled = await cancel_verification(
            repo.id,
            run.id,
            VerificationCancelRequest(reason="Author withdrew patch"),
            db,
            auth,
        )
        assert cancelled.status == VerificationJobStatus.CANCELLED.value
        assert cancelled.cancellation_reason == "Author withdrew patch"
        assert cancelled.cancelled_by_user_id == user.id


@pytest.mark.anyio
async def test_cancellation_of_terminal_run_denied(test_session):
    """Terminal verification runs cannot be cancelled."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        auth = make_auth(user)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        run.status = VerificationJobStatus.COMPLETED.value
        db.commit()

        with pytest.raises(HTTPException) as exc:
            await cancel_verification(repo.id, run.id, VerificationCancelRequest(), db, auth)
        assert exc.value.status_code == 409


# ==============================================================================
# 6. Restart Recovery Strategy
# ==============================================================================


def test_restart_recovery_reaps_stale_running_jobs(test_session):
    """Jobs left in RUNNING status during a process crash/restart are recovered to FAILED."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)

        # Simulate 2 jobs that were RUNNING when server crashed
        run1 = VerificationRun(
            repository_id=repo.id,
            commit_sha="a" * 40,
            issue_number=1,
            user_id=user.id,
            status=VerificationJobStatus.RUNNING.value,
            sandbox_image=DEFAULT_SANDBOX_IMAGE,
        )
        run2 = VerificationRun(
            repository_id=repo.id,
            commit_sha="b" * 40,
            issue_number=2,
            user_id=user.id,
            status=VerificationJobStatus.RUNNING.value,
            sandbox_image=DEFAULT_SANDBOX_IMAGE,
        )
        db.add_all([run1, run2])
        db.commit()

        recovered_count = VerificationJobManager.recover_stale_jobs(db)
        assert recovered_count == 2

        db.refresh(run1)
        db.refresh(run2)
        assert run1.status == VerificationJobStatus.FAILED.value
        assert "server restart" in run1.error_message.lower()
        assert run2.status == VerificationJobStatus.FAILED.value

        events = (
            db.query(VerificationAuditEvent)
            .filter(VerificationAuditEvent.verification_run_id == run1.id)
            .all()
        )
        assert len(events) == 1
        assert events[0].event_type == "recovered"


# ==============================================================================
# 7. Concurrency Limits
# ==============================================================================


@pytest.mark.anyio
async def test_concurrency_limit_enforcement(test_session, monkeypatch):
    """When repo or global running concurrency limit is reached, new execution is rejected."""
    monkeypatch.setenv("MAX_REPO_CONCURRENT_JOBS", "1")

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)

        # Existing running job
        running_job = VerificationRun(
            repository_id=repo.id,
            commit_sha="a" * 40,
            issue_number=1,
            user_id=user.id,
            status=VerificationJobStatus.RUNNING.value,
            sandbox_image=DEFAULT_SANDBOX_IMAGE,
        )
        db.add(running_job)
        db.commit()

        # New approved job
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        with pytest.raises(VerificationConcurrencyError) as exc:
            await VerificationJobManager.execute_job(db, run.id, runner=MockSandboxRunner())
        assert "Concurrency limit reached" in str(exc.value)

        db.refresh(run)
        # Stays in QUEUED state
        assert run.status == VerificationJobStatus.QUEUED.value


# ==============================================================================
# 8. Decoupled Job Lifecycle vs Proof-of-Fix Verdict
# ==============================================================================


@pytest.mark.anyio
async def test_decoupled_job_status_and_proof_verdict(test_session):
    """When tests fail in patched sandbox (FIX_FAILED), job state is still COMPLETED."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        # Configure mock runner where baseline fails (exit 1) and patched also fails (exit 1)
        from repopilot_api.schemas import SandboxExecutionState

        class BothFailRunner(MockSandboxRunner):
            async def run(self, workspace_path, request):
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout="Ran 2 tests in 0.01s\n\nFAILED (failures=1)\n",
                ).run(workspace_path, request)

        mock_runner = BothFailRunner()

        result = await VerificationJobManager.execute_job(db, run.id, runner=mock_runner)

        # Job lifecycle status is COMPLETED (job successfully ran to conclusion)
        db.refresh(run)
        assert run.status == VerificationJobStatus.COMPLETED.value

        # Proof report verdict is FIX_FAILED
        assert result.verdict == ProofVerdict.FIX_FAILED

        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run.id))
        assert report is not None
        assert report.verdict == "fix_failed"


# ==============================================================================
# 9. Docker Execution Restriction in Normal API Flow
# ==============================================================================


@pytest.mark.anyio
async def test_docker_execution_disabled_in_normal_api(test_session, monkeypatch):
    """Real Docker execution is disabled unless explicitly enabled via environment."""
    from repopilot_api.sandbox import DockerSandboxRunner

    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "false")

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        fake_docker_runner = DockerSandboxRunner(image=DEFAULT_SANDBOX_IMAGE)
        with pytest.raises(VerificationSecurityError) as exc:
            await VerificationJobManager.execute_job(db, run.id, runner=fake_docker_runner)
        assert "Real Docker execution is disabled" in str(exc.value)


# ==============================================================================
# 10. Audit Trail & Report Endpoints
# ==============================================================================


def test_audit_events_endpoint(test_session):
    """Audit events endpoint returns full lifecycle history."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        auth = make_auth(user)

        run = create_verification(
            repo.id, sample_create_payload(auto_submit=False), Response(), None, db, auth
        )
        submit_verification(repo.id, run.id, db, auth)
        approve_verification(repo.id, run.id, VerificationApprovalRequest(), db, auth)

        events = get_verification_audit_events(repo.id, run.id, db, auth)
        event_types = [e.event_type for e in events]
        assert "created" in event_types
        assert "submitted_for_approval" in event_types
        assert "approved" in event_types


def test_get_report_returns_404_when_not_ready(test_session):
    """Calling /report on a run that has not yet generated a report returns 404."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        auth = make_auth(user)

        run = create_verification(repo.id, sample_create_payload(), Response(), None, db, auth)
        with pytest.raises(HTTPException) as exc:
            get_verification_report(repo.id, run.id, db, auth)
        assert exc.value.status_code == 404
        assert "No Proof-of-Fix report found" in exc.value.detail


def test_rate_limiter_blocks_excessive_traffic():
    """Rate limiter enforces request limits."""
    key = "test_user_rate_limit"
    for _ in range(5):
        assert check_rate_limit(key, max_requests=5, window_seconds=10) is True
    # 6th request is blocked
    assert check_rate_limit(key, max_requests=5, window_seconds=10) is False
