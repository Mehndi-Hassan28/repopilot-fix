"""Tests for Milestone 3.6 Phase 3 & Phase 4 — Worker Sandbox, Recovery, and Safe Cleanup."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from repopilot_api.fixture_registry import (
    UntrustedRepositoryError,
)
from repopilot_api.models import (
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
    DockerSandboxRunner,
    ImageValidator,
    MockSandboxRunner,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxExecutionState,
    SandboxSecurityError,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofVerdict,
    VerificationAuditEventType,
    VerificationCreateRequest,
    VerificationJobStatus,
)
from repopilot_api.verification_service import (
    VerificationJobManager,
)
from repopilot_api.worker import WorkerLoop

SAMPLE_COMMIT_SHA = "a" * 40


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


def make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA, files=None):
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


def sample_create_payload(test_command=None, commit_sha=SAMPLE_COMMIT_SHA):
    return VerificationCreateRequest(
        commit_sha=commit_sha,
        issue_number=1,
        test_command=test_command or ["python", "-m", "unittest", "tests/test_calc.py"],
        patches=sample_patch(),
        hypothesis="Fix addition bug in calculator",
        auto_submit_approval=True,
    )


class StepRunner(MockSandboxRunner):
    """Mock runner that fails in baseline, passes in patched."""

    def __init__(self):
        super().__init__()
        self.call_count = 0
        self.executed_commands = []

    async def run(
        self, workspace_path: str, request: SandboxExecutionRequest
    ) -> SandboxExecutionResult:
        self.call_count += 1
        self.executed_commands.append(request.command)
        if self.call_count == 1:
            return await MockSandboxRunner(
                simulated_state=SandboxExecutionState.FAILED,
                simulated_exit_code=1,
                simulated_stdout="Ran 1 test in 0.01s\n\nFAILED (failures=1)\n",
            ).run(workspace_path, request)
        return await MockSandboxRunner(
            simulated_state=SandboxExecutionState.PASSED,
            simulated_exit_code=0,
            simulated_stdout="Ran 1 test in 0.01s\n\nOK\n",
        ).run(workspace_path, request)


# ==============================================================================
# PHASE 3: Bounded Concurrency & Global Execution Slots
# ==============================================================================


def test_global_execution_slot_enforcement(test_session):
    """Global execution slots restrict concurrent running jobs across all workers."""
    with test_session() as db:
        user = make_user(db)
        repo1 = make_repo(db, github_id=101, owner="acme", name="app")
        repo2 = make_repo(db, github_id=102, owner="acme", name="service")
        repo3 = make_repo(db, github_id=103, owner="acme", name="backend")
        add_access(db, repo1, user)
        add_access(db, repo2, user)
        add_access(db, repo3, user)
        make_index(db, repo1)
        make_index(db, repo2)
        make_index(db, repo3)

        run1, _ = VerificationJobManager.create_verification_request(
            db, repo1.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run1.id, user.id, auto_queue=True)

        run2, _ = VerificationJobManager.create_verification_request(
            db, repo2.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run2.id, user.id, auto_queue=True)

        run3, _ = VerificationJobManager.create_verification_request(
            db, repo3.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run3.id, user.id, auto_queue=True)

        # Max global concurrent = 2
        claim1 = VerificationJobManager.claim_next_job(
            db, worker_id="worker-1", max_global_concurrent=2
        )
        assert claim1 is not None
        assert claim1.id == run1.id

        claim2 = VerificationJobManager.claim_next_job(
            db, worker_id="worker-2", max_global_concurrent=2
        )
        assert claim2 is not None
        assert claim2.id == run2.id

        # Third claim fails because global limit (2) is reached
        claim3 = VerificationJobManager.claim_next_job(
            db, worker_id="worker-3", max_global_concurrent=2
        )
        assert claim3 is None

        # Releasing a slot (transition run1 to COMPLETED) frees a slot
        run1_db = db.get(VerificationRun, run1.id)
        run1_db.status = VerificationJobStatus.COMPLETED.value
        db.commit()

        # Now worker 3 can claim run3
        claim3_after = VerificationJobManager.claim_next_job(
            db, worker_id="worker-3", max_global_concurrent=2
        )
        assert claim3_after is not None
        assert claim3_after.id == run3.id


def test_per_repo_concurrency_slot(test_session):
    """Per-repository concurrency prevents multiple concurrent executions on same repo."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=201, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        run1, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run1.id, user.id, auto_queue=True)

        run2, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run2.id, user.id, auto_queue=True)

        # Claim 1 for repo
        c1 = VerificationJobManager.claim_next_job(
            db, worker_id="worker-1", max_repo_concurrent=1
        )
        assert c1 is not None
        assert c1.id == run1.id

        # Second job for same repo is held back
        c2 = VerificationJobManager.claim_next_job(
            db, worker_id="worker-2", max_repo_concurrent=1
        )
        assert c2 is None


# ==============================================================================
# PHASE 4: Heartbeat & Fencing
# ==============================================================================


def test_heartbeat_renewal(test_session):
    """Heartbeat updates last_heartbeat_at and advances lease_expires_at."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

        claimed = VerificationJobManager.claim_next_job(
            db, worker_id="worker-alpha", lease_duration_seconds=10
        )
        assert claimed is not None
        initial_heartbeat = claimed.last_heartbeat_at
        initial_expiry = claimed.lease_expires_at
        fencing_token = claimed.fencing_token

        # Perform heartbeat renewal
        success = VerificationJobManager.heartbeat(
            db,
            run_id=claimed.id,
            worker_id="worker-alpha",
            fencing_token=fencing_token,
            lease_duration_seconds=60,
        )
        assert success is True

        db.refresh(claimed)
        assert claimed.lease_expires_at > initial_expiry
        assert claimed.last_heartbeat_at >= initial_heartbeat


def test_stale_worker_fenced_by_token(test_session):
    """Worker with outdated fencing token is rejected during heartbeat and result persist."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker-alpha")
        stale_token = claimed.fencing_token

        # Simulate another worker or recovery incrementing fencing_token
        claimed.fencing_token = stale_token + 1
        db.commit()

        # Original worker's heartbeat fails because fencing token changed
        renewed = VerificationJobManager.heartbeat(
            db,
            run_id=claimed.id,
            worker_id="worker-alpha",
            fencing_token=stale_token,
        )
        assert renewed is False


# ==============================================================================
# PHASE 4: Crash Recovery & Idempotency
# ==============================================================================


def test_crash_recovery_expired_leases(test_session):
    """Crash recovery reconciles stale RUNNING jobs whose leases expired to FAILED."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

        claimed = VerificationJobManager.claim_next_job(db, worker_id="crashed-worker")

        # Simulate expired lease from crashed worker (5 minutes in past)
        past_time = datetime.now(UTC) - timedelta(minutes=5)
        claimed.lease_expires_at = past_time
        claimed.last_heartbeat_at = past_time
        db.commit()

        # Run recovery
        recovered = VerificationJobManager.reconcile_stale_jobs(db)
        assert claimed.id in recovered

        db.refresh(claimed)
        assert claimed.status == VerificationJobStatus.FAILED.value
        assert "lease expired" in claimed.error_message

        # Audit event of type RECOVERED is recorded
        audit = db.scalar(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == claimed.id,
                VerificationAuditEvent.event_type == VerificationAuditEventType.RECOVERED.value,
            )
        )
        assert audit is not None
        assert audit.from_status == VerificationJobStatus.RUNNING.value
        assert audit.to_status == VerificationJobStatus.FAILED.value


def test_crash_recovery_idempotency(test_session):
    """Reconciling stale jobs is completely idempotent; subsequent runs are no-ops."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker-crash")
        claimed.lease_expires_at = datetime.now(UTC) - timedelta(minutes=10)
        db.commit()

        # First recovery run reconciles job
        first_pass = VerificationJobManager.reconcile_stale_jobs(db)
        assert len(first_pass) == 1

        # Second recovery run sees 0 stale jobs and makes no duplicate audits
        second_pass = VerificationJobManager.reconcile_stale_jobs(db)
        assert len(second_pass) == 0

        # Verify only 1 RECOVERED audit event exists
        audits = db.scalars(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run.id,
                VerificationAuditEvent.event_type == VerificationAuditEventType.RECOVERED.value,
            )
        ).all()
        assert len(audits) == 1


# ==============================================================================
# PHASE 4: Cross-Process Cancellation via Durable State
# ==============================================================================


@pytest.mark.anyio
async def test_cross_process_cancellation_fences_worker(test_session):
    """If another process cancels a job in DB, in-flight worker cannot overwrite with COMPLETED."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    class CancellingRunner(StepRunner):
        async def run(self, workspace_path, request):
            # Simulate external process cancelling job while worker is in runner
            with test_session() as db:
                await VerificationJobManager.cancel_verification(
                    db, run.id, user.id, reason="External user cancelled run"
                )
            return await super().run(workspace_path, request)

    runner = CancellingRunner()
    worker = WorkerLoop(session_factory=test_session, runner=runner)

    await worker.run_once()

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        # Job must stay CANCELLED, NOT overwritten to COMPLETED!
        assert run_db.status == VerificationJobStatus.CANCELLED.value

        # No ProofReport should have been persisted for the cancelled job
        report = db.scalar(
            select(ProofReport).where(ProofReport.verification_run_id == run.id)
        )
        assert report is None


# ==============================================================================
# PHASE 4: Safe Scoped Container Cleanup Logic
# ==============================================================================


@pytest.mark.anyio
async def test_safe_container_cleanup_strictly_checks_ownership():
    """safe_cleanup_containers never touches unrelated containers and verifies labels."""
    # Simulated docker ps output containing:
    # 1. Unrelated MongoDB container (must NEVER be touched)
    # 2. Container without repopilot prefix (must be skipped)
    # 3. Verified RepoPilot container matching run_id (safe to remove)
    mock_ps_output = (
        b"mongo_123\tmongodb\tapp=database\n"
        b"other_456\tsome_app\trepopilot.creator=repopilot_fix\n"
        b"c_good_789\trepopilot_sbx_abc123\trepopilot.managed=true,repopilot.creator=repopilot_fix,repopilot.verification_run_id=42,repopilot.worker_id=worker-1\n"
    )

    removed_calls = []

    async def mock_subprocess_exec(*args, **kwargs):
        cmd = args[0]
        subcmd = args[1]
        proc = AsyncMock()

        if cmd == "docker" and subcmd == "ps":
            proc.communicate = AsyncMock(return_value=(mock_ps_output, b""))
            proc.returncode = 0
        elif cmd == "docker" and subcmd == "rm":
            container_id = args[3]
            removed_calls.append(container_id)
            proc.wait = AsyncMock(return_value=0)
            proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        removed = await DockerSandboxRunner.safe_cleanup_containers(run_id=42, worker_id="worker-1")

        # Only the verified container c_good_789 should be removed!
        assert removed == ["c_good_789"]
        assert removed_calls == ["c_good_789"]
        # Mongo and unrelated containers must NEVER be in removed_calls!
        assert "mongo_123" not in removed_calls
        assert "other_456" not in removed_calls


# ==============================================================================
# PHASE 3: Security & Image Hardening
# ==============================================================================


def test_sandbox_image_digest_validation():
    """ImageValidator enforces immutable @sha256 digest when required."""
    valid_pinned = DEFAULT_SANDBOX_IMAGE
    assert ImageValidator.validate(valid_pinned, require_digest=True) == valid_pinned

    # Mutable tag without digest fails when digest required
    with pytest.raises(SandboxSecurityError, match="must be pinned by an immutable @sha256"):
        ImageValidator.validate("python:3.11-slim", require_digest=True)

    # Disallowed characters / flags fail
    with pytest.raises(SandboxSecurityError, match="cannot start with a flag"):
        ImageValidator.validate("--privileged", require_digest=False)


def test_docker_runner_arguments_hardening():
    """DockerSandboxRunner constructs hardened, non-root, read-only arguments with labels."""
    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        memory_limit="256m",
        cpu_limit="0.5",
        pids_limit=50,
        worker_id="test-worker-99",
    )

    args = runner.build_docker_args(
        container_name="repopilot_sbx_test123",
        workspace_path="/tmp/repopilot_sandbox/test123",
        env={"TEST_VAR": "safe_val"},
        command=["python", "-m", "unittest", "tests/test_calc.py"],
        labels={"repopilot.creator": "repopilot_fix", "repopilot.verification_run_id": "99"},
    )

    # Verify security flags
    assert "--network" in args and args[args.index("--network") + 1] == "none"
    assert "--read-only" in args
    assert "--cap-drop" in args and args[args.index("--cap-drop") + 1] == "ALL"
    assert (
        "--security-opt" in args
        and args[args.index("--security-opt") + 1] == "no-new-privileges"
    )
    assert "--memory" in args and args[args.index("--memory") + 1] == "256m"
    assert "--cpus" in args and args[args.index("--cpus") + 1] == "0.5"
    assert "--pids-limit" in args and args[args.index("--pids-limit") + 1] == "50"
    assert "--label" in args
    assert "repopilot.creator=repopilot_fix" in args
    assert "repopilot.verification_run_id=99" in args
    assert "-v" in args and "/tmp/repopilot_sandbox/test123:/workspace:ro" in args


def test_untrusted_repository_rejected_fail_closed(test_session, monkeypatch):
    """Untrusted repository execution is strictly rejected by ExecutionPolicyGuard."""
    from repopilot_api.fixture_registry import (
        ExecutionPolicyGuard,
        get_default_fixture_registry,
    )

    with test_session() as db:
        user = make_user(db)
        # Create an unapproved repository (not in TrustedFixtureRegistry)
        untrusted_repo = make_repo(db, github_id=9999, owner="evil-org", name="malicious-repo")
        add_access(db, untrusted_repo, user)

        with pytest.raises(UntrustedRepositoryError, match="not in the trusted"):
            ExecutionPolicyGuard.validate_repository_trust(
                untrusted_repo, get_default_fixture_registry()
            )


# ==============================================================================
# PHASE 3: Deterministic Evidence & Infrastructure Failure Distinction
# ==============================================================================


@pytest.mark.anyio
async def test_infrastructure_failure_distinguished_from_test_failure(test_session):
    """Infrastructure/syntax errors yield ERROR verdict rather than UNREPRODUCED or VERIFIED_FIX."""
    from repopilot_api.proof_engine import ProofOfFixEngine
    from repopilot_api.schemas import ProofOfFixRequest

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

    # Simulated syntax error in test code
    class SyntaxErrorRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            return await MockSandboxRunner(
                simulated_state=SandboxExecutionState.FAILED,
                simulated_exit_code=1,
                simulated_stdout="SyntaxError: invalid syntax in test file\n",
            ).run(workspace_path, request)

    engine = ProofOfFixEngine(runner=SyntaxErrorRunner())
    req = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=1,
        test_command=["python", "-m", "unittest", "tests/test_calc.py"],
        patches=sample_patch(),
        files={"src/calc.py": "def add(a, b): return a + b\n"},
    )

    result = await engine.verify(req)
    assert result.verdict == ProofVerdict.ERROR
    assert "syntax_error" in result.summary or "SyntaxError" in result.summary


# ==============================================================================
# SEC AUDIT REMEDIATION TESTS (SEC-01 through SEC-06)
# ==============================================================================


def test_sec01_atomic_global_concurrency_and_advisory_lock(test_session):
    """SEC-01: Global concurrency defaults to 1, slot check enforces bounds,

    and pg_advisory_xact_lock is called with CLAIM_SLOT_ADVISORY_LOCK_ID under PostgreSQL.
    """
    from repopilot_api.verification_service import (
        CLAIM_SLOT_ADVISORY_LOCK_ID,
        MAX_GLOBAL_CONCURRENT_JOBS,
        VerificationJobManager,
    )

    assert MAX_GLOBAL_CONCURRENT_JOBS == 1
    assert CLAIM_SLOT_ADVISORY_LOCK_ID == 742918471928471928

    with test_session() as db:
        user = make_user(db)
        repo1 = make_repo(db, github_id=101, owner="org1", name="repo1")
        repo2 = make_repo(db, github_id=102, owner="org2", name="repo2")
        add_access(db, repo1, user)
        add_access(db, repo2, user)
        make_index(db, repo1)
        make_index(db, repo2)

        run1, _ = VerificationJobManager.create_verification_request(
            db, repo1.id, user.id, sample_create_payload()
        )
        run2, _ = VerificationJobManager.create_verification_request(
            db, repo2.id, user.id, sample_create_payload()
        )

        VerificationJobManager.approve_verification(db, run1.id, user.id, auto_queue=True)
        VerificationJobManager.approve_verification(db, run2.id, user.id, auto_queue=True)

        # Worker 1 claims job 1
        claimed1 = VerificationJobManager.claim_next_job(
            db, worker_id="worker-1", max_global_concurrent=1
        )
        assert claimed1 is not None
        assert claimed1.id == run1.id
        assert claimed1.status == VerificationJobStatus.RUNNING.value

        # Worker 2 tries to claim while job 1 is RUNNING with valid lease -> slot limit reached (1)
        claimed2 = VerificationJobManager.claim_next_job(
            db, worker_id="worker-2", max_global_concurrent=1
        )
        assert claimed2 is None

        # Verify advisory lock execution logic on PostgreSQL dialect
        mock_bind = type(
            "MockBind",
            (),
            {
                "dialect": type("MockDialect", (), {"name": "postgresql"})(),
                "url": "postgresql://test:test@localhost/test",
            },
        )()
        executed_stmts = []
        original_execute = db.execute

        def spy_execute(statement, *args, **kwargs):
            executed_stmts.append((str(statement), args, kwargs))
            return original_execute(statement, *args, **kwargs)

        with (
            patch("repopilot_api.verification_service.has_lease_columns", return_value=True),
            patch.object(db, "get_bind", return_value=mock_bind),
            patch.object(db, "execute", side_effect=spy_execute),
        ):
            try:
                VerificationJobManager.claim_next_job(
                    db, worker_id="worker-3", max_global_concurrent=2
                )
            except Exception:
                # SQLite underlying execute may fail with postgresql query syntax
                pass

            # Assert advisory lock SQL was dispatched
            assert any("pg_advisory_xact_lock" in s[0] for s in executed_stmts)


@pytest.mark.anyio
async def test_sec02_atomic_persistence_rollback_on_fencing_or_cancellation(test_session):
    """SEC-02: Worker detects lost ownership (fencing token bump or cancellation) before

    final persistence, rolls back atomic transaction, and discards stale completed result.
    """
    from repopilot_api.verification_service import VerificationJobManager

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker-fenced")
        assert claimed is not None
        run_id = claimed.id
        original_token = claimed.fencing_token

    # Use a runner that simulates external cancellation/fencing midway through execution
    class InterruptedRunner(MockSandboxRunner):
        def __init__(self, session_factory):
            super().__init__()
            self.session_factory = session_factory

        async def run(self, workspace_path, request):
            with self.session_factory() as db:
                r = db.get(VerificationRun, run_id)
                r.fencing_token = original_token + 1
                r.status = VerificationJobStatus.CANCELLED.value
                db.commit()
            return await MockSandboxRunner().run(workspace_path, request)

    worker = WorkerLoop(
        session_factory=test_session,
        runner=InterruptedRunner(test_session),
        worker_id="worker-fenced",
    )

    # Process job: Worker will complete the mock run, but Session 2 persistence MUST detect
    # fencing token mismatch and rollback without overwriting CANCELLED to COMPLETED!
    await worker.process_job(run_id, fencing_token=original_token)

    with test_session() as db:
        final_run = db.get(VerificationRun, run_id)
        assert final_run.status == VerificationJobStatus.CANCELLED.value
        # ProofReport must NOT have been saved
        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run_id))
        assert report is None
        # No COMPLETED audit event
        events = db.scalars(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run_id
            )
        ).all()
        assert not any(e.event_type == VerificationAuditEventType.COMPLETED.value for e in events)


@pytest.mark.anyio
async def test_sec03_container_ownership_propagates_integer_run_id(test_session):
    """SEC-03: Propagate actual database integer verification_run_id through SandboxExecutionRequest

    and Docker labels; safe_cleanup_containers uses exact ownership matching.
    """
    captured_requests = []

    class CapturingRunner(StepRunner):
        async def run(self, workspace_path, request):
            captured_requests.append(request)
            return await super().run(workspace_path, request)

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    worker = WorkerLoop(
        session_factory=test_session,
        runner=CapturingRunner(),
        worker_id="worker-sec03",
    )
    claimed = await worker.run_once()
    assert claimed is True
    assert len(captured_requests) >= 2  # Baseline and patched
    for req in captured_requests:
        assert req.verification_run_id == run_id
        assert req.worker_id == "worker-sec03"

    # Test DockerSandboxRunner args generation with integer ID
    docker_runner = DockerSandboxRunner(worker_id="worker-sec03")
    args = docker_runner.build_docker_args(
        container_name="repopilot_test",
        workspace_path="/tmp/fake",
        env={},
        command=["python"],
        labels={
            "repopilot.verification_run_id": str(run_id),
            "repopilot.worker_id": "worker-sec03",
        },
    )
    assert f"repopilot.verification_run_id={run_id}" in args
    assert "repopilot.worker_id=worker-sec03" in args

    # Test Docker safe_cleanup_containers ownership filtering with simulated process output
    simulated_ps_output = (
        f"cid_other\tunrelated_db\tapp=database\n"
        f"cid_other_run\trepopilot_sbx_999\trepopilot.creator=repopilot_fix,repopilot.verification_run_id=999,repopilot.worker_id=worker-sec03\n"
        f"cid_target\trepopilot_sbx_{run_id}\trepopilot.creator=repopilot_fix,repopilot.verification_run_id={run_id},repopilot.worker_id=worker-sec03\n"
    ).encode()

    mock_ps_proc = AsyncMock()
    mock_ps_proc.communicate.return_value = (simulated_ps_output, b"")
    mock_ps_proc.returncode = 0

    mock_rm_proc = AsyncMock()
    mock_rm_proc.wait.return_value = 0
    mock_rm_proc.returncode = 0

    async def mock_subprocess_exec(*cmd_args, **kwargs):
        if cmd_args[0] == "docker" and cmd_args[1] == "ps":
            return mock_ps_proc
        if cmd_args[0] == "docker" and cmd_args[1] == "rm":
            return mock_rm_proc
        raise ValueError(f"Unexpected command: {cmd_args}")

    with patch("asyncio.create_subprocess_exec", side_effect=mock_subprocess_exec):
        removed = await DockerSandboxRunner.safe_cleanup_containers(
            run_id=run_id, worker_id="worker-sec03"
        )
        # cid_other and cid_other_run must NOT be removed; ONLY cid_target must be removed!
        assert removed == ["cid_target"]


def test_sec04_legacy_running_jobs_grace_period_and_reconciliation(test_session):
    """SEC-04: Pre-migration RUNNING jobs with NULL lease_expires_at are bounded by grace period

    and safely reconciled without blocking global slots indefinitely.
    """
    from repopilot_api.verification_service import (
        LEGACY_JOB_GRACE_SECONDS,
        VerificationJobManager,
    )

    now = datetime.now(UTC)

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        # Legacy active job within grace period (updated 60s ago)
        active_legacy = VerificationRun(
            repository_id=repo.id,
            commit_sha=SAMPLE_COMMIT_SHA,
            issue_number=1,
            user_id=user.id,
            status=VerificationJobStatus.RUNNING.value,
            lease_expires_at=None,
            updated_at=now - timedelta(seconds=60),
            sandbox_image=DEFAULT_SANDBOX_IMAGE,
            test_command="[]",
        )
        # Legacy abandoned job older than grace period (updated 600s ago)
        stale_legacy = VerificationRun(
            repository_id=repo.id,
            commit_sha=SAMPLE_COMMIT_SHA,
            issue_number=2,
            user_id=user.id,
            status=VerificationJobStatus.RUNNING.value,
            lease_expires_at=None,
            updated_at=now - timedelta(seconds=LEGACY_JOB_GRACE_SECONDS + 300),
            sandbox_image=DEFAULT_SANDBOX_IMAGE,
            test_command="[]",
        )
        db.add_all([active_legacy, stale_legacy])
        db.commit()
        db.refresh(active_legacy)
        db.refresh(stale_legacy)

        active_id = active_legacy.id
        stale_id = stale_legacy.id

        # Queued job waiting
        queued_run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, queued_run.id, user.id, auto_queue=True)

        # 1. With max_global_concurrent=1, active_legacy (within grace) counts toward slot
        claimed = VerificationJobManager.claim_next_job(db, max_global_concurrent=1)
        assert claimed is None

        # 2. Run standard reconciliation: only stale_legacy (older than grace) is reconciled
        reconciled = VerificationJobManager.reconcile_stale_jobs(db, reconcile_legacy=False)
        assert stale_id in reconciled
        assert active_id not in reconciled

        db.refresh(stale_legacy)
        assert stale_legacy.status == VerificationJobStatus.FAILED.value
        assert "legacy unleased run reconciled" in (stale_legacy.error_message or "")

        # 3. Controlled deployment reconciliation with reconcile_legacy=True reconciles all legacy
        reconciled_all = VerificationJobManager.reconcile_stale_jobs(db, reconcile_legacy=True)
        assert active_id in reconciled_all
        db.refresh(active_legacy)
        assert active_legacy.status == VerificationJobStatus.FAILED.value

        # 4. Idempotency: Calling reconcile again reconciles nothing
        assert VerificationJobManager.reconcile_stale_jobs(db, reconcile_legacy=True) == []

        # 5. Now slot is free, queued job can be claimed
        claimed_after = VerificationJobManager.claim_next_job(db, max_global_concurrent=1)
        assert claimed_after is not None
        assert claimed_after.id == queued_run.id


@pytest.mark.anyio
async def test_sec05_fail_closed_schema_check(test_session):
    """SEC-05: Missing migration 0007 lease columns fail closed with VerificationSchemaError

    across claim_next_job, worker.run_once(), and worker.run() startup.
    """
    from repopilot_api.verification_service import (
        VerificationJobManager,
        VerificationSchemaError,
        has_lease_columns,
    )

    with test_session() as db:
        # has_lease_columns returns False when bind is None
        mock_db = type("MockDB", (), {"get_bind": lambda *args, **kwargs: None})()
        assert has_lease_columns(mock_db) is False

        # When schema check fails, claim_next_job raises VerificationSchemaError
        with patch("repopilot_api.verification_service.has_lease_columns", return_value=False):
            with pytest.raises(
                VerificationSchemaError, match="missing required durable worker lease"
            ):
                VerificationJobManager.claim_next_job(db)

    # WorkerLoop fail-closed behavior
    worker = WorkerLoop(session_factory=test_session)
    with (
        patch("repopilot_api.verification_service.has_lease_columns", return_value=False),
        patch("repopilot_api.worker.has_lease_columns", return_value=False),
    ):
        # run_once must fail-closed
        with pytest.raises(VerificationSchemaError):
            await worker.run_once()

        # run() startup must fail-closed
        with pytest.raises(VerificationSchemaError):
            await worker.run()


def test_sec06_composite_index_and_migration_definition():
    """SEC-06: VerificationRun model and Migration 0007 include composite index

    ix_verification_runs_status_lease_expires_at for (status, lease_expires_at).
    """
    import importlib.util
    import inspect
    from pathlib import Path

    mig_path = (
        Path(__file__).resolve().parent.parent
        / "migrations"
        / "versions"
        / "0007_verification_worker_leases.py"
    )
    assert mig_path.exists(), f"Migration file not found at {mig_path}"

    spec = importlib.util.spec_from_file_location("mig_0007", mig_path)
    assert spec is not None and spec.loader is not None
    mig_0007 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig_0007)

    # Check migration upgrade and downgrade source text
    upgrade_source = inspect.getsource(mig_0007.upgrade)
    downgrade_source = inspect.getsource(mig_0007.downgrade)
    assert "ix_verification_runs_status_lease_expires_at" in upgrade_source
    assert "ix_verification_runs_status_lease_expires_at" in downgrade_source
    assert "ix_verification_runs_lease_expires_at" in upgrade_source
    assert "ix_verification_runs_lease_expires_at" in downgrade_source

    # Check VerificationRun model __table_args__
    table_args = getattr(VerificationRun, "__table_args__", ())
    index_names = [arg.name for arg in table_args if hasattr(arg, "name")]
    assert "ix_verification_runs_status_lease_expires_at" in index_names
