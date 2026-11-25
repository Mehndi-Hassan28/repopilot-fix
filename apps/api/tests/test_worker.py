"""Tests for Milestone 3.6 Phase 2 — Verification WorkerLoop and Atomic Durable Processing."""

import asyncio
import json

import pytest
from sqlalchemy import select

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
    MockSandboxRunner,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofVerdict,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxExecutionState,
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
    """Mock sandbox runner that reproduces bug in baseline, passes in patched."""

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
            # Baseline execution: reproduces failure (exit 1)
            return await MockSandboxRunner(
                simulated_state=SandboxExecutionState.FAILED,
                simulated_exit_code=1,
                simulated_stdout="Ran 1 test in 0.01s\n\nFAILED (failures=1)\n",
            ).run(workspace_path, request)
        # Patched execution: passes (exit 0)
        return await MockSandboxRunner(
            simulated_state=SandboxExecutionState.PASSED,
            simulated_exit_code=0,
            simulated_stdout="Ran 1 test in 0.01s\n\nOK\n",
        ).run(workspace_path, request)


# ==============================================================================
# 1. Atomic Job Claiming
# ==============================================================================


def test_atomic_job_claiming(test_session):
    """Worker atomically claims a queued job, transitions to RUNNING, and logs audit."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

        # Claim the job
        claimed = VerificationJobManager.claim_next_job(db, worker_id="test-worker-1")
        assert claimed is not None
        assert claimed.id == run.id
        assert claimed.status == VerificationJobStatus.RUNNING.value

        # Verify STARTED audit event
        events = db.scalars(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run.id
            )
        ).all()
        started_events = [
            e for e in events if e.event_type == VerificationAuditEventType.STARTED.value
        ]
        assert len(started_events) == 1
        assert started_events[0].from_status == VerificationJobStatus.QUEUED.value
        assert started_events[0].to_status == VerificationJobStatus.RUNNING.value
        details = json.loads(started_events[0].details_json)
        assert details.get("worker_id") == "test-worker-1"

        # Attempting to claim again yields None
        second_claim = VerificationJobManager.claim_next_job(db, worker_id="test-worker-2")
        assert second_claim is None


# ==============================================================================
# 2. Two Workers Racing for the Same Job
# ==============================================================================


@pytest.mark.anyio
async def test_two_workers_racing_for_same_job(test_session):
    """When two concurrent workers race to claim the same job, only one succeeds."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db)
        add_access(db, repo, user)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    claimed_runs = []

    async def worker_attempt(worker_name: str):
        with test_session() as db:
            claimed = VerificationJobManager.claim_next_job(db, worker_id=worker_name)
            if claimed is not None:
                claimed_runs.append((worker_name, claimed.id))

    # Run both workers concurrently
    await asyncio.gather(
        worker_attempt("worker-alpha"),
        worker_attempt("worker-beta"),
    )

    # Exactly one worker must have claimed the single queued job
    assert len(claimed_runs) == 1
    winning_worker, winning_job_id = claimed_runs[0]
    assert winning_job_id == run.id
    assert winning_worker in ("worker-alpha", "worker-beta")

    # Verify database state shows exactly 1 STARTED audit event
    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.RUNNING.value
        events = db.scalars(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run.id,
                VerificationAuditEvent.event_type == VerificationAuditEventType.STARTED.value,
            )
        ).all()
        assert len(events) == 1


# ==============================================================================
# 3. Successful Mock Verification
# ==============================================================================


@pytest.mark.anyio
async def test_worker_successful_mock_verification(test_session):
    """Worker claims and cleanly executes approved verification using mock sandbox."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    runner = StepRunner()
    worker = WorkerLoop(
        session_factory=test_session,
        runner=runner,
        worker_id="test-worker",
    )

    # Run one job through worker
    processed = await worker.run_once()
    assert processed is True
    assert len(runner.executed_commands) == 2  # Baseline + Patched

    # Verify database record updated to COMPLETED
    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.COMPLETED.value
        assert run_db.baseline_exit_code == 1
        assert run_db.patched_exit_code == 0
        assert run_db.duration_seconds > 0

        # Verify ProofReport was created
        report = db.scalar(
            select(ProofReport).where(ProofReport.verification_run_id == run.id)
        )
        assert report is not None
        assert report.verdict == ProofVerdict.VERIFIED_FIX.value
        assert report.confidence == "high"

        # Verify audit trail contains STARTED and COMPLETED in order
        events = db.scalars(
            select(VerificationAuditEvent)
            .where(VerificationAuditEvent.verification_run_id == run.id)
            .order_by(VerificationAuditEvent.id.asc())
        ).all()
        types = [e.event_type for e in events]
        assert types == [
            VerificationAuditEventType.CREATED.value,
            VerificationAuditEventType.SUBMITTED_FOR_APPROVAL.value,
            VerificationAuditEventType.APPROVED.value,
            VerificationAuditEventType.QUEUED.value,
            VerificationAuditEventType.STARTED.value,
            VerificationAuditEventType.COMPLETED.value,
        ]


# ==============================================================================
# 4. Unauthorized Job Rejection (Fail Closed)
# ==============================================================================


@pytest.mark.anyio
async def test_worker_unauthorized_job_rejection(test_session):
    """Worker rejects queued job if user lacks authorization, transitioning to FAILED."""
    with test_session() as db:
        owner = make_user(db)
        unauthorized_user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, owner)
        make_index(db, repo)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, owner.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, owner.id, auto_queue=True)

        # Tamper user authorization: assign job approved_by to unauthorized user
        run.approved_by_user_id = unauthorized_user.id
        db.commit()

    runner = StepRunner()
    worker = WorkerLoop(session_factory=test_session, runner=runner)

    processed = await worker.run_once()
    assert processed is True
    # Crucial security check: NO sandbox commands should be executed!
    assert runner.call_count == 0

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.FAILED.value
        assert "has no authorization record" in run_db.error_message

        # Verify FAILED audit event exists with policy_violation flag
        fail_event = db.scalar(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run.id,
                VerificationAuditEvent.event_type == VerificationAuditEventType.FAILED.value,
            )
        )
        assert fail_event is not None
        details = json.loads(fail_event.details_json)
        assert details.get("policy_violation") is True


# ==============================================================================
# 5. Specification Tampering Detection
# ==============================================================================


@pytest.mark.anyio
async def test_worker_specification_tampering_rejected(test_session):
    """Worker rejects execution if specification was tampered after approval."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

        # Tamper with test command after approval
        run.test_command = json.dumps(["python", "-m", "unittest", "tampered_test.py"])
        db.commit()

    runner = StepRunner()
    worker = WorkerLoop(session_factory=test_session, runner=runner)

    processed = await worker.run_once()
    assert processed is True
    assert runner.call_count == 0  # No sandbox execution

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.FAILED.value
        assert "altered after approval" in run_db.error_message


# ==============================================================================
# 6. Cancellation: Queued and In-Flight
# ==============================================================================


@pytest.mark.anyio
async def test_worker_cancellation_while_queued(test_session):
    """Cancelled job is not picked up by worker."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

        # Cancel while queued
        await VerificationJobManager.cancel_verification(
            db, run.id, user.id, reason="User cancelled prior to execution"
        )

    runner = StepRunner()
    worker = WorkerLoop(session_factory=test_session, runner=runner)

    claimed = await worker.run_once()
    assert claimed is False
    assert runner.call_count == 0


@pytest.mark.anyio
async def test_worker_cancellation_inflight(test_session):
    """Cancellation requested while job is in-flight cleanly transitions to CANCELLED."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    class SlowBaselineRunner(MockSandboxRunner):
        def __init__(self):
            super().__init__()
            self.started_event = asyncio.Event()

        async def run(self, workspace_path, request):
            self.started_event.set()
            # Give cancellation a chance to arrive
            await asyncio.sleep(0.1)
            return await MockSandboxRunner(
                simulated_state=SandboxExecutionState.FAILED,
                simulated_exit_code=1,
                simulated_stdout="Ran 1 test\n\nFAILED\n",
            ).run(workspace_path, request)

    runner = SlowBaselineRunner()
    worker = WorkerLoop(session_factory=test_session, runner=runner)

    async def cancel_when_started():
        await runner.started_event.wait()
        with test_session() as db:
            await VerificationJobManager.cancel_verification(
                db, run.id, user.id, reason="Cancelled during baseline test"
            )

    await asyncio.gather(
        worker.run_once(),
        cancel_when_started(),
    )

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.CANCELLED.value
        events = db.scalars(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run.id,
                VerificationAuditEvent.event_type == VerificationAuditEventType.CANCELLED.value,
            )
        ).all()
        assert len(events) >= 1


# ==============================================================================
# 7. Worker Exceptions Handled Safely
# ==============================================================================


@pytest.mark.anyio
async def test_worker_exception_handled_safely(test_session):
    """Unexpected exception in sandbox runner transitions job to FAILED without crashing worker."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    class BrokenRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            raise RuntimeError("Unexpected disk I/O error during mock execution")

    worker = WorkerLoop(session_factory=test_session, runner=BrokenRunner())
    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.FAILED.value
        assert "Unexpected disk I/O error" in run_db.error_message


# ==============================================================================
# 8. Graceful Shutdown
# ==============================================================================


@pytest.mark.anyio
async def test_worker_graceful_shutdown_idle(test_session):
    """Idle worker stops cleanly when stop() is called."""
    worker = WorkerLoop(session_factory=test_session, poll_interval=0.05)

    async def trigger_stop():
        await asyncio.sleep(0.02)
        worker.stop()

    run_task = asyncio.create_task(worker.run())
    stop_task = asyncio.create_task(trigger_stop())

    await asyncio.gather(run_task, stop_task)
    assert worker.is_running is False
    assert worker.current_job_id is None


@pytest.mark.anyio
async def test_worker_graceful_shutdown_during_job(test_session):
    """Worker asked to stop during job execution finishes current job and exits cleanly."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    class SleepRunner(StepRunner):
        async def run(self, workspace_path, request):
            await asyncio.sleep(0.05)
            return await super().run(workspace_path, request)

    runner = SleepRunner()
    worker = WorkerLoop(session_factory=test_session, runner=runner, poll_interval=0.05)

    async def trigger_stop():
        await asyncio.sleep(0.02)
        worker.stop()

    await asyncio.gather(
        worker.run(),
        trigger_stop(),
    )

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        # In-flight job reached COMPLETED (never left in RUNNING!)
        assert run_db.status == VerificationJobStatus.COMPLETED.value
    assert worker.is_running is False


# ==============================================================================
# 9. Audit Consistency
# ==============================================================================


@pytest.mark.anyio
async def test_worker_audit_consistency(test_session):
    """All state transitions have coherent, chronological audit events with valid from/to states."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    worker = WorkerLoop(session_factory=test_session, runner=StepRunner())
    await worker.run_once()

    with test_session() as db:
        events = db.scalars(
            select(VerificationAuditEvent)
            .where(VerificationAuditEvent.verification_run_id == run.id)
            .order_by(VerificationAuditEvent.id.asc())
        ).all()

        assert len(events) >= 5

        # Check chronological continuity of status transitions
        # CREATED: to=draft or pending_approval
        # SUBMITTED: draft -> pending_approval
        # APPROVED: pending_approval -> approved
        # QUEUED: approved -> queued
        # STARTED: queued -> running
        # COMPLETED: running -> completed
        for i in range(1, len(events)):
            curr = events[i]
            prev = events[i - 1]
            if curr.from_status and prev.to_status:
                assert curr.from_status == prev.to_status, (
                    f"Transition broken between event {prev.event_type} "
                    f"({prev.to_status}) and {curr.event_type} ({curr.from_status})"
                )


# ==============================================================================
# 10. Docker Execution Restriction Guard
# ==============================================================================


@pytest.mark.anyio
async def test_worker_blocks_docker_when_disabled(test_session, monkeypatch):
    """Worker rejects execution if runner is DockerSandboxRunner and allow_docker is false."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "false")

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo)

        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)

    class DockerSandboxRunner(MockSandboxRunner):
        pass

    worker = WorkerLoop(session_factory=test_session, runner=DockerSandboxRunner())
    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.FAILED.value
        assert "Real Docker execution is disabled" in run_db.error_message


# ==============================================================================
# 11. Worker CLI Entrypoint
# ==============================================================================


def test_worker_cli_entrypoint(test_session, monkeypatch):
    """Worker CLI entrypoint runs correctly with --once flag."""
    import sys

    from repopilot_api.worker import main

    monkeypatch.setattr("repopilot_api.worker.SessionLocal", test_session)
    monkeypatch.setattr(
        sys, "argv", ["repopilot_worker", "--once", "--poll-interval", "0.01"]
    )
    # With empty queue, main() finishes cleanly
    main()


@pytest.mark.anyio
async def test_worker_persists_approved_spec_hash_in_evidence(test_session):
    """Regression test: Worker must persist approved_spec_hash into report evidence_json."""
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
        run_appr = VerificationJobManager.approve_verification(
            db, run.id, user.id, auto_queue=True
        )
        expected_spec = run_appr.approved_spec_hash
        assert expected_spec is not None

    runner = StepRunner()
    worker = WorkerLoop(session_factory=test_session, runner=runner)
    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run.id)
        assert run_db.status == VerificationJobStatus.COMPLETED.value
        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run.id))
        assert report is not None
        ev = json.loads(report.evidence_json)
        assert ev.get("approved_spec_hash") == expected_spec
