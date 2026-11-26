"""Targeted PostgreSQL Integration & Concurrency Tests for RepoPilot Fix.

Milestone 3.6 Phase 3 + 4 Validation:
- Strictly executes on repopilot_scratch only.
- Real PostgreSQL advisory locking (pg_advisory_xact_lock).
- Real concurrent worker claiming across independent sessions.
- Real global and per-repo slot bounds.
- Real heartbeat, lease expiration, crash recovery, and fencing.
- Exact cleanup of only test-generated rows; preserves all pre-existing rows.
"""

import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from repopilot_api.fixture_registry import (
    TrustedFixture,
    get_default_fixture_registry,
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
from repopilot_api.sandbox import DEFAULT_SANDBOX_IMAGE, MockSandboxRunner
from repopilot_api.schemas import (
    PatchProposal,
    VerificationAuditEventType,
    VerificationCreateRequest,
    VerificationJobStatus,
)
from repopilot_api.verification_service import (
    CLAIM_SLOT_ADVISORY_LOCK_ID,
    VerificationJobManager,
)
from repopilot_api.worker import WorkerLoop

SAMPLE_COMMIT_SHA = "b" * 40


def get_scratch_db_url() -> str:
    """Retrieve and validate the repopilot_scratch database URL safely."""
    raw_url = os.getenv("DATABASE_URL", "")
    if not raw_url and os.path.exists(".env"):
        for line in open(".env"):
            if line.startswith("DATABASE_URL="):
                raw_url = line.strip().split("=", 1)[1].strip("\"'")
                break
    parsed = urlparse(raw_url)
    db_name = parsed.path.lstrip("/")
    if db_name != "repopilot_scratch":
        raise RuntimeError(f"Target database must be 'repopilot_scratch', but got '{db_name}'.")
    return raw_url


def _postgres_is_ready() -> tuple[bool, str]:
    """Check if PostgreSQL integration tests are explicitly enabled and targeted at scratch."""
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        return (
            False,
            "PostgreSQL integration tests are opt-in. Set RUN_POSTGRES_TESTS=1 to execute.",
        )
    try:
        url = get_scratch_db_url()
        parsed = urlparse(url)
        db_name = parsed.path.lstrip("/")
        if db_name != "repopilot_scratch":
            return False, f"Target database must be 'repopilot_scratch', got '{db_name}'."
    except Exception as exc:
        return False, f"PostgreSQL scratch connection check failed: {exc}"
    return True, "PostgreSQL scratch is ready."


_ready, _reason = _postgres_is_ready()

pytestmark = pytest.mark.skipif(not _ready, reason=_reason)


@pytest.fixture(scope="module")
def pg_engine():
    """Create a scoped PostgreSQL engine bound exclusively to repopilot_scratch."""
    url = get_scratch_db_url()
    engine = create_engine(url, pool_size=5, max_overflow=5)
    with engine.connect() as conn:
        current_db = conn.scalar(text("SELECT current_database()"))
        assert current_db == "repopilot_scratch", f"Unexpected database: {current_db}"
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def pg_session_factory(pg_engine):
    """SessionFactory bound to repopilot_scratch."""
    return sessionmaker(bind=pg_engine, expire_on_commit=False)


@pytest.fixture
def pg_test_ctx(pg_session_factory):
    """Fixture providing isolated test record creation and guaranteed cleanup."""
    prefix = f"pgval_{uuid.uuid4().hex[:8]}"
    created_runs: set[int] = set()
    created_users: set[int] = set()
    created_repos: set[int] = set()
    created_jobs: set[int] = set()

    with pg_session_factory() as db:
        initial_vrun_ids = set(db.scalars(select(VerificationRun.id)).all())

    def _make_env(db: Session):
        user = User()
        db.add(user)
        db.flush()
        created_users.add(user.id)

        repo_suffix = uuid.uuid4().hex[:6]
        owner_name = f"org_{prefix}_{repo_suffix}"
        repo_name = f"repo_{prefix}_{repo_suffix}"
        repo = Repository(
            github_id=int(uuid.uuid4().int % 10000000),
            owner=owner_name,
            name=repo_name,
            full_name=f"{owner_name}/{repo_name}",
            url=f"https://github.com/{owner_name}/{repo_name}",
            default_branch="main",
        )
        db.add(repo)
        db.flush()
        created_repos.add(repo.id)

        access = RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner")
        db.add(access)
        db.flush()

        registry = get_default_fixture_registry()
        registry.register_trusted_repository(owner_name, repo_name)

        job = IndexingJob(repository_id=repo.id, commit_sha=SAMPLE_COMMIT_SHA, status="completed")
        db.add(job)
        db.flush()
        created_jobs.add(job.id)

        calc_test = (
            "import unittest\n"
            "from src.calc import add\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n"
        )
        file_map = {
            "src/calc.py": "def add(a, b):\n    return a + b\n",
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
        db.commit()

        registry.register_fixture(
            TrustedFixture.create(
                fixture_id=f"fix_{prefix}_{repo_suffix}",
                name="PostgreSQL Integration Calculator Fixture",
                description="Fixture for PostgreSQL scratch integration tests",
                repository_owner=owner_name,
                repository_name=repo_name,
                commit_sha=SAMPLE_COMMIT_SHA,
                files=file_map,
                approved_test_commands=[
                    ["python", "-m", "unittest", "tests/test_calc.py"],
                ],
                sandbox_image=DEFAULT_SANDBOX_IMAGE,
                allowed_commit_shas={SAMPLE_COMMIT_SHA},
            )
        )

        return user, repo, job

    def _make_queued_run(db: Session, user: User, repo: Repository) -> VerificationRun:
        diff_text = (
            "--- a/src/calc.py\n"
            "+++ b/src/calc.py\n"
            "@@ -2,1 +2,1 @@\n"
            "-    return a - b\n"
            "+    return a + b\n"
        )
        req = VerificationCreateRequest(
            commit_sha=SAMPLE_COMMIT_SHA,
            issue_number=1,
            test_command=["python", "-m", "unittest", "tests/test_calc.py"],
            patches=[PatchProposal(file_path="src/calc.py", diff=diff_text)],
            hypothesis=f"Test hypothesis for {prefix}",
            auto_submit_approval=True,
        )
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, req)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        created_runs.add(run.id)
        return run

    ctx = {
        "session_factory": pg_session_factory,
        "prefix": prefix,
        "make_env": _make_env,
        "make_queued_run": _make_queued_run,
        "created_runs": created_runs,
        "registry": get_default_fixture_registry(),
    }

    try:
        yield ctx
    finally:
        with pg_session_factory() as db:
            if created_runs:
                db.execute(
                    delete(ProofReport).where(ProofReport.verification_run_id.in_(created_runs))
                )
                db.execute(
                    delete(VerificationAuditEvent).where(
                        VerificationAuditEvent.verification_run_id.in_(created_runs)
                    )
                )
                db.execute(delete(VerificationRun).where(VerificationRun.id.in_(created_runs)))
            if created_jobs:
                db.execute(delete(IndexedFile).where(IndexedFile.job_id.in_(created_jobs)))
                db.execute(delete(IndexingJob).where(IndexingJob.id.in_(created_jobs)))
            if created_repos:
                db.execute(
                    delete(RepositoryAccess).where(
                        RepositoryAccess.repository_id.in_(created_repos)
                    )
                )
                db.execute(delete(Repository).where(Repository.id.in_(created_repos)))
            if created_users:
                db.execute(delete(User).where(User.id.in_(created_users)))
            db.commit()

            final_vrun_ids = set(db.scalars(select(VerificationRun.id)).all())
            assert initial_vrun_ids.issubset(final_vrun_ids), (
                "Pre-existing verification runs were modified!"
            )


# ==============================================================================
# POSTGRESQL INTEGRATION TESTS
# ==============================================================================


def test_pg_advisory_xact_lock_serialization(pg_session_factory):
    """Test 1: Transaction-scoped PostgreSQL advisory lock serializes concurrent sessions."""
    lock_id = CLAIM_SLOT_ADVISORY_LOCK_ID
    conn1_locked = threading.Event()
    conn2_proceed = threading.Event()
    conn2_result = [None]

    def _worker2():
        conn1_locked.wait()
        with pg_session_factory() as s2:
            # try_advisory_xact_lock returns false while conn1 holds the xact lock
            acquired = s2.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
            )
            conn2_result[0] = acquired
            conn2_proceed.set()

    t2 = threading.Thread(target=_worker2)
    t2.start()

    with pg_session_factory() as s1:
        s1.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
        conn1_locked.set()
        conn2_proceed.wait()
        assert conn2_result[0] is False, (
            "Session 2 should NOT acquire advisory lock while held by 1"
        )
        s1.commit()

    t2.join()

    # Once s1 committed, s3 should acquire lock freely
    with pg_session_factory() as s3:
        acquired_now = s3.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
        )
        assert acquired_now is True, "Advisory lock should be released after transaction commit"
        s3.commit()


def test_pg_two_connections_concurrent_claims_global_concurrency(pg_test_ctx):
    """Test 2: Two concurrent PostgreSQL connections claiming jobs enforce global limit = 1."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user1, repo1, _ = pg_test_ctx["make_env"](db)
        user2, repo2, _ = pg_test_ctx["make_env"](db)
        pg_test_ctx["make_queued_run"](db, user1, repo1)
        pg_test_ctx["make_queued_run"](db, user2, repo2)

    claim_results = [None, None]

    def _claim_worker(worker_idx, worker_id):
        with session_factory() as s:
            claim = VerificationJobManager.claim_next_job(
                s, worker_id=worker_id, max_global_concurrent=1
            )
            claim_results[worker_idx] = claim.id if claim is not None else None

    t1 = threading.Thread(target=_claim_worker, args=(0, "worker_pg_1"))
    t2 = threading.Thread(target=_claim_worker, args=(1, "worker_pg_2"))

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    claimed_ids = [r for r in claim_results if r is not None]
    assert len(claimed_ids) == 1, (
        f"Global concurrency = 1 failed: exactly 1 worker must claim, got {claim_results}"
    )
    assert None in claim_results, "The second concurrent worker must receive None"


def test_pg_per_repo_concurrency(pg_test_ctx):
    """Test 3: Per-repository concurrency restricts concurrent runs on the same repository."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        run1 = pg_test_ctx["make_queued_run"](db, user, repo)
        pg_test_ctx["make_queued_run"](db, user, repo)

    with session_factory() as db1:
        # Worker 1 claims job 1
        claim1 = VerificationJobManager.claim_next_job(
            db1, worker_id="worker_repo_1", max_global_concurrent=5, max_repo_concurrent=1
        )
        assert claim1 is not None and claim1.id == run1.id

        with session_factory() as db2:
            # Worker 2 tries to claim job on the same repo -> returns None
            claim2 = VerificationJobManager.claim_next_job(
                db2, worker_id="worker_repo_2", max_global_concurrent=5, max_repo_concurrent=1
            )
            assert claim2 is None, "Per-repo limit = 1 should prevent second claim on same repo"


def test_pg_heartbeat_renewal(pg_test_ctx):
    """Test 4: Heartbeat renewal updates lease_expires_at in PostgreSQL."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        pg_test_ctx["make_queued_run"](db, user, repo)
        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker_hb")
        assert claimed is not None
        run_id = claimed.id
        initial_lease = claimed.lease_expires_at
        initial_token = claimed.fencing_token

    time.sleep(0.05)

    with session_factory() as db:
        success = VerificationJobManager.heartbeat(
            db,
            run_id=run_id,
            worker_id="worker_hb",
            fencing_token=initial_token,
            lease_duration_seconds=60,
        )
        assert success is True

        refreshed = db.get(VerificationRun, run_id)
        assert refreshed.lease_expires_at > initial_lease
        assert refreshed.last_heartbeat_at is not None


def test_pg_lease_expiration_and_recovery(pg_test_ctx):
    """Test 5: Lease expiration triggers safe crash recovery to FAILED in PostgreSQL."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        pg_test_ctx["make_queued_run"](db, user, repo)
        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker_expired")
        assert claimed is not None
        run_id = claimed.id

        # Manually backdate lease into the past
        past_time = datetime.now(UTC) - timedelta(seconds=20)
        db.execute(
            text("UPDATE verification_runs SET lease_expires_at = :past WHERE id = :id"),
            {"past": past_time, "id": run_id},
        )
        db.commit()

    with session_factory() as db:
        recovered = VerificationJobManager.reconcile_stale_jobs(db)
        assert run_id in recovered

        reconciled_run = db.get(VerificationRun, run_id)
        assert reconciled_run.status == VerificationJobStatus.FAILED.value
        assert "worker lease expired" in (reconciled_run.error_message or "")

        events = db.scalars(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run_id
            )
        ).all()
        assert any(e.event_type == VerificationAuditEventType.RECOVERED.value for e in events)


def test_pg_stale_worker_fencing(pg_test_ctx):
    """Test 6: Monotonically increasing fencing_token fences stale workers in PostgreSQL."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        pg_test_ctx["make_queued_run"](db, user, repo)
        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker_stale")
        assert claimed is not None
        run_id = claimed.id
        old_token = claimed.fencing_token

        # Simulate another process or admin bumping fencing token
        db.execute(
            text("UPDATE verification_runs SET fencing_token = fencing_token + 1 WHERE id = :id"),
            {"id": run_id},
        )
        db.commit()

    with session_factory() as db:
        # Heartbeat with old token must be rejected
        ok = VerificationJobManager.heartbeat(
            db, run_id=run_id, worker_id="worker_stale", fencing_token=old_token
        )
        assert ok is False, "Heartbeat with stale fencing token must fail"


@pytest.mark.anyio
async def test_pg_cross_process_cancellation(pg_test_ctx):
    """Test 7: Cross-process cancellation updates state and increments fencing token."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        pg_test_ctx["make_queued_run"](db, user, repo)
        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker_cancel")
        assert claimed is not None
        run_id = claimed.id
        old_token = claimed.fencing_token

    # Another session cancels the run
    with session_factory() as db:
        cancelled = await VerificationJobManager.cancel_verification(
            db, run_id=run_id, user_id=user.id, reason="User cancelled test"
        )
        assert cancelled.status == VerificationJobStatus.CANCELLED.value
        assert cancelled.fencing_token == old_token + 1

    with session_factory() as db:
        # Worker attempting heartbeat fails because status is CANCELLED and token is bumped
        heartbeat_res = VerificationJobManager.heartbeat(
            db, run_id=run_id, worker_id="worker_cancel", fencing_token=old_token
        )
        assert heartbeat_res is False


def test_pg_recovery_idempotency(pg_test_ctx):
    """Test 8: Crash recovery is strictly idempotent across multiple passes."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        pg_test_ctx["make_queued_run"](db, user, repo)
        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker_idem")
        assert claimed is not None
        run_id = claimed.id

        # Backdate lease to expired
        db.execute(
            text("UPDATE verification_runs SET lease_expires_at = :past WHERE id = :id"),
            {"past": datetime.now(UTC) - timedelta(seconds=15), "id": run_id},
        )
        db.commit()

    with session_factory() as db:
        pass1 = VerificationJobManager.reconcile_stale_jobs(db)
        assert run_id in pass1

        pass2 = VerificationJobManager.reconcile_stale_jobs(db)
        assert run_id not in pass2, "Second pass must be a no-op for already recovered job"


@pytest.mark.anyio
async def test_pg_atomic_proof_report_persistence(pg_test_ctx):
    """Test 9: Atomic proof report and completed status persistence in PostgreSQL."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        run = pg_test_ctx["make_queued_run"](db, user, repo)
        run_id = run.id

    worker = WorkerLoop(
        session_factory=session_factory,
        runner=MockSandboxRunner(),
        fixture_registry=pg_test_ctx["registry"],
        worker_id="worker_atomic_pg",
    )
    processed = await worker.run_once()
    assert processed is True

    with session_factory() as db:
        completed_run = db.get(VerificationRun, run_id)
        assert completed_run.status == VerificationJobStatus.COMPLETED.value
        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run_id))
        assert report is not None
        assert report.verdict is not None


@pytest.mark.anyio
async def test_pg_lost_ownership_rollback(pg_test_ctx):
    """Test 10: Worker detecting lost ownership in Session 2 rolls back and discards report."""
    session_factory = pg_test_ctx["session_factory"]

    with session_factory() as db:
        user, repo, _ = pg_test_ctx["make_env"](db)
        pg_test_ctx["make_queued_run"](db, user, repo)
        claimed = VerificationJobManager.claim_next_job(db, worker_id="worker_lost_pg")
        assert claimed is not None
        run_id = claimed.id
        token = claimed.fencing_token

    # Use a custom runner that cancels or bumps token midway through execution
    class InterruptedRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            with session_factory() as s:
                r = s.get(VerificationRun, run_id)
                r.fencing_token = token + 1
                r.status = VerificationJobStatus.CANCELLED.value
                s.commit()
            return await super().run(workspace_path, request)

    worker = WorkerLoop(
        session_factory=session_factory,
        runner=InterruptedRunner(),
        fixture_registry=pg_test_ctx["registry"],
        worker_id="worker_lost_pg",
    )
    await worker.process_job(run_id, fencing_token=token)

    with session_factory() as db:
        final_run = db.get(VerificationRun, run_id)
        assert final_run.status == VerificationJobStatus.CANCELLED.value
        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run_id))
        assert report is None, "Report must NOT be persisted when worker lost ownership"
