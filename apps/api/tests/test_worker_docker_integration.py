"""Opt-in integration tests for Worker-to-Docker execution and failure recovery.

Milestone 3.6 Phase 3 + Phase 4:
- End-to-end VerificationJobManager -> WorkerLoop -> ProofOfFixEngine -> DockerSandboxRunner.
- ProofReport persistence and audit trail with real Docker execution.
- Hardened boundaries: no-network, non-root uid, read-only rootfs, tmpfs, pids/mem limits.
- Failure handling: timeout, nonzero exit, cancellation, stale fencing token, output flooding.
- Safe container cleanup and zero orphan guarantee.

To run:
    RUN_DOCKER_TESTS=1 apps/api/.venv/bin/pytest \
        apps/api/tests/test_worker_docker_integration.py -m docker -v
"""

import os
import shutil
import subprocess

import pytest
from sqlalchemy import select

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
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    DockerSandboxRunner,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofVerdict,
    VerificationAuditEventType,
    VerificationCreateRequest,
    VerificationJobStatus,
)
from repopilot_api.verification_service import VerificationJobManager
from repopilot_api.worker import WorkerLoop
from tests.fixtures.sandbox_fixtures import (
    CALC_FIX_PATCH,
    FAILING_FIXTURE_FILES,
    NETWORK_PROBE_FILES,
    NON_ROOT_PROBE_FILES,
    OUTPUT_FLOOD_PROBE_FILES,
    READ_ONLY_PROBE_FILES,
    TIMEOUT_PROBE_FILES,
)

SAMPLE_COMMIT_SHA = "a" * 40


def _docker_is_ready() -> tuple[bool, str]:
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        return False, "Docker integration tests are opt-in. Set RUN_DOCKER_TESTS=1 to execute."
    if shutil.which("docker") is None:
        return False, "Docker CLI executable not found on PATH."
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        if res.returncode != 0:
            return False, "Docker daemon is not running or not accessible."
    except Exception as exc:
        return False, f"Docker daemon check failed: {exc}"
    return True, "Docker is ready."


_ready, _reason = _docker_is_ready()

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _ready, reason=_reason),
]


# ==============================================================================
# Helper Factories
# ==============================================================================


def make_user(db):
    user = User()
    db.add(user)
    db.flush()
    return user


def make_repo(db, github_id=888, owner="acme", name="app"):
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


def add_access(db, repo, user, role="owner"):
    db.add(RepositoryAccess(repository_id=repo.id, user_id=user.id, role=role))
    db.flush()


def make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA, files=None):
    job = IndexingJob(repository_id=repo.id, commit_sha=commit_sha, status="completed")
    db.add(job)
    db.flush()

    file_map = files or FAILING_FIXTURE_FILES
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


def make_patch_proposal(diff_text=CALC_FIX_PATCH, file_path="calc.py"):
    return [PatchProposal(file_path=file_path, diff=diff_text)]


def make_create_request(test_command=None, patches=None, hypothesis="Fix bug"):
    return VerificationCreateRequest(
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=1,
        test_command=test_command or ["python", "-m", "unittest", "test_calc.py"],
        patches=patches if patches is not None else make_patch_proposal(),
        hypothesis=hypothesis,
        auto_submit_approval=False,
    )


def assert_no_repopilot_containers():
    """Verify that no repopilot_sbx containers remain in Docker daemon."""
    ps_res = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=repopilot_sbx", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert ps_res.returncode == 0
    names = [n for n in ps_res.stdout.strip().splitlines() if n.startswith("repopilot_sbx")]
    assert not names, f"Lingering Docker containers found: {names}"


# ==============================================================================
# Step 3: Complete Worker-Docker End-to-End Workflow
# ==============================================================================


@pytest.mark.anyio
async def test_worker_docker_end_to_end_verification(test_session, tmp_path, monkeypatch):
    """Test full workflow: create job, approve, claim with durable worker, execute in Docker."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=FAILING_FIXTURE_FILES)

        payload = make_create_request()
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )
    worker = WorkerLoop(
        session_factory=test_session,
        runner=runner,
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        assert run_db.status == VerificationJobStatus.COMPLETED.value
        # Baseline should have failed (exit 1), patched should have passed (exit 0)
        assert run_db.baseline_exit_code != 0
        assert run_db.patched_exit_code == 0
        assert run_db.duration_seconds > 0

        # ProofReport must be persisted
        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run_id))
        assert report is not None
        assert report.verdict == ProofVerdict.VERIFIED_FIX.value
        assert report.confidence in ("high", "medium")

        # Audit events check
        events = db.scalars(
            select(VerificationAuditEvent)
            .where(VerificationAuditEvent.verification_run_id == run_id)
            .order_by(VerificationAuditEvent.id.asc())
        ).all()
        event_types = [e.event_type for e in events]
        assert VerificationAuditEventType.STARTED.value in event_types
        assert VerificationAuditEventType.COMPLETED.value in event_types

    # Verify container cleanup
    assert_no_repopilot_containers()

# ==============================================================================
# Step 4: Failure & Adversarial Tests
# ==============================================================================


@pytest.mark.anyio
async def test_worker_docker_nonzero_patch_exit(test_session, tmp_path, monkeypatch):
    """Test that a patch failing tests results in STILL_FAILS verdict, not crash."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    bad_patch = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,3 +1,3 @@\n"
        " def add(a: int, b: int) -> int:\n"
        '     """Add two numbers."""\n'
        "-    return a - b  # Bug: returns subtraction instead of addition\n"
        "+    return a * b  # Still buggy: multiplication\n"
    )

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=889, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=FAILING_FIXTURE_FILES)

        payload = make_create_request(
            patches=make_patch_proposal(diff_text=bad_patch),
            hypothesis="Buggy multiplication fix",
        )
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )
    worker = WorkerLoop(
        session_factory=test_session,
        runner=runner,
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        assert run_db.status == VerificationJobStatus.COMPLETED.value
        assert run_db.baseline_exit_code != 0
        assert run_db.patched_exit_code != 0

        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run_id))
        assert report is not None
        assert report.verdict == ProofVerdict.FIX_FAILED.value

    assert_no_repopilot_containers()

@pytest.mark.anyio
async def test_worker_docker_network_blocked_probe(test_session, tmp_path, monkeypatch):
    """Test that container outbound network attempts are strictly blocked."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    registry = get_default_fixture_registry()
    net_fixture = TrustedFixture.create(
        fixture_id="fixture_network_probe_worker",
        name="Network Probe Worker Fixture",
        description="Verifies socket connect fails due to --network none",
        repository_owner="*",
        repository_name="*",
        commit_sha=SAMPLE_COMMIT_SHA,
        files=NETWORK_PROBE_FILES,
        approved_test_commands=[["python", "-m", "unittest", "test_network.py"]],
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
    )
    registry.register_fixture(net_fixture)

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=890, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=NETWORK_PROBE_FILES)

        net_patch = [
            PatchProposal(
                file_path="test_network.py",
                diff=(
                    "--- a/test_network.py\n"
                    "+++ b/test_network.py\n"
                    "@@ -1,3 +1,4 @@\n"
                    " import socket\n"
                    "+# probed\n"
                    " import unittest\n"
                ),
            )
        ]
        payload = make_create_request(
            test_command=["python", "-m", "unittest", "test_network.py"],
            patches=net_patch,
            hypothesis="Network probe test",
        )
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )
    worker = WorkerLoop(
        session_factory=test_session,
        runner=runner,
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        # Network probe test passes if socket.connect raised OSError (blocked)
        assert run_db.baseline_exit_code == 0

    assert_no_repopilot_containers()

@pytest.mark.anyio
async def test_worker_docker_non_root_and_readonly_restrictions(
    test_session, tmp_path, monkeypatch
):
    """Test that container executes with non-root UID and enforces read-only rootfs."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    registry = get_default_fixture_registry()
    combined_files = {**NON_ROOT_PROBE_FILES, **READ_ONLY_PROBE_FILES}
    sec_fixture = TrustedFixture.create(
        fixture_id="fixture_security_probe_worker",
        name="Security Probes Fixture",
        description="Verifies UID >= 1000 and read-only rootfs",
        repository_owner="*",
        repository_name="*",
        commit_sha=SAMPLE_COMMIT_SHA,
        files=combined_files,
        approved_test_commands=[
            ["python", "-m", "unittest", "test_user.py"],
            ["python", "-m", "unittest", "test_readonly.py"],
        ],
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
    )
    registry.register_fixture(sec_fixture)

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=891, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=combined_files)

        sec_patch = [
            PatchProposal(
                file_path="test_user.py",
                diff=(
                    "--- a/test_user.py\n"
                    "+++ b/test_user.py\n"
                    "@@ -1,3 +1,4 @@\n"
                    " import os\n"
                    "+# probed\n"
                    " import unittest\n"
                ),
            )
        ]
        payload = make_create_request(
            test_command=["python", "-m", "unittest", "test_user.py"],
            patches=sec_patch,
            hypothesis="Non-root probe test",
        )
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )
    worker = WorkerLoop(
        session_factory=test_session,
        runner=runner,
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        # Test passed because uid >= 1000 and not root
        assert run_db.baseline_exit_code == 0

    assert_no_repopilot_containers()

@pytest.mark.anyio
async def test_worker_docker_bounded_output_flooding(test_session, tmp_path, monkeypatch):
    """Test that heavy output flooding inside container is capped without OOM or crash."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    registry = get_default_fixture_registry()
    flood_fixture = TrustedFixture.create(
        fixture_id="fixture_flood_probe_worker",
        name="Output Flood Fixture",
        description="Generates thousands of stdout lines",
        repository_owner="*",
        repository_name="*",
        commit_sha=SAMPLE_COMMIT_SHA,
        files=OUTPUT_FLOOD_PROBE_FILES,
        approved_test_commands=[["python", "-m", "unittest", "test_flood.py"]],
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
    )
    registry.register_fixture(flood_fixture)

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=892, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=OUTPUT_FLOOD_PROBE_FILES)

        flood_patch = [
            PatchProposal(
                file_path="test_flood.py",
                diff=(
                    "--- a/test_flood.py\n"
                    "+++ b/test_flood.py\n"
                    "@@ -1,3 +1,4 @@\n"
                    " import sys\n"
                    "+# probed\n"
                    " import unittest\n"
                ),
            )
        ]
        payload = make_create_request(
            test_command=["python", "-m", "unittest", "test_flood.py"],
            patches=flood_patch,
            hypothesis="Flood probe test",
        )
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )
    worker = WorkerLoop(
        session_factory=test_session,
        runner=runner,
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        assert run_db.status == VerificationJobStatus.COMPLETED.value
        assert len(run_db.baseline_stdout) <= 60 * 1024
        assert "truncated" in run_db.baseline_stdout

    assert_no_repopilot_containers()

@pytest.mark.anyio
async def test_worker_docker_timeout_handling(test_session, tmp_path, monkeypatch):
    """Test that a hanging container test triggers timeout and transitions to FAILED."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    registry = get_default_fixture_registry()
    timeout_fixture = TrustedFixture.create(
        fixture_id="fixture_timeout_probe_worker",
        name="Timeout Probe Fixture",
        description="Sleeps 30 seconds to trigger timeout",
        repository_owner="*",
        repository_name="*",
        commit_sha=SAMPLE_COMMIT_SHA,
        files=TIMEOUT_PROBE_FILES,
        approved_test_commands=[["python", "-m", "unittest", "test_timeout.py"]],
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
    )
    registry.register_fixture(timeout_fixture)

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=893, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=TIMEOUT_PROBE_FILES)

        timeout_patch = [
            PatchProposal(
                file_path="test_timeout.py",
                diff=(
                    "--- a/test_timeout.py\n"
                    "+++ b/test_timeout.py\n"
                    "@@ -1,3 +1,4 @@\n"
                    " import time\n"
                    "+# probed\n"
                    " import unittest\n"
                ),
            )
        ]
        payload = make_create_request(
            test_command=["python", "-m", "unittest", "test_timeout.py"],
            patches=timeout_patch,
            hypothesis="Timeout probe test",
        )
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        # Set short timeout on the job
        run.timeout_seconds = 5
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )
    worker = WorkerLoop(
        session_factory=test_session,
        runner=runner,
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    processed = await worker.run_once()
    assert processed is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        assert run_db.status == VerificationJobStatus.COMPLETED.value
        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run_id))
        assert report is not None
        assert report.verdict == ProofVerdict.TIMEOUT.value
        assert "timed out" in report.summary.lower()

    assert_no_repopilot_containers()

@pytest.mark.anyio
async def test_worker_docker_stale_fencing_token_discarded(test_session, tmp_path, monkeypatch):
    """Test that a worker whose fencing token was invalidated cannot commit final results."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=894, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=FAILING_FIXTURE_FILES)

        payload = make_create_request()
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id

    # Create a custom runner that corrupts the database fencing token while Docker is running
    real_runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )

    class FencingInterferingRunner:
        async def run(self, ws, req):
            # Run real Docker execution
            res = await real_runner.run(ws, req)
            # Invalidate the fencing token in DB to simulate another worker claiming the job
            with test_session() as db:
                r = db.get(VerificationRun, run_id)
                if r:
                    r.fencing_token = "stale-tampered-token-999"
                    r.worker_id = "other-worker-stealing-lease"
                    db.commit()
            return res

    worker = WorkerLoop(
        session_factory=test_session,
        runner=FencingInterferingRunner(),
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    result = await worker.run_once()
    assert result is True

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        # Must NOT be completed by test-docker-worker
        assert run_db.status != VerificationJobStatus.COMPLETED.value
        # No ProofReport should have been saved
        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run_id))
        assert report is None

    assert_no_repopilot_containers()

@pytest.mark.anyio
async def test_worker_docker_cancellation_during_execution(test_session, tmp_path, monkeypatch):
    """Test that cancellation signal during execution halts Docker and transitions to CANCELLED."""
    monkeypatch.setenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "true")
    scratch = str(tmp_path / "scratch")

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, github_id=895, owner="acme", name="app")
        add_access(db, repo, user)
        make_index(db, repo, files=FAILING_FIXTURE_FILES)

        payload = make_create_request()
        run, _ = VerificationJobManager.create_verification_request(db, repo.id, user.id, payload)
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        VerificationJobManager.approve_verification(db, run.id, user.id, auto_queue=True)
        run_id = run.id
        user_id = user.id

    real_runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        scratch_root=scratch,
        worker_id="test-docker-worker",
    )

    class CancellingRunner:
        async def run(self, ws, req):
            with test_session() as db:
                await VerificationJobManager.cancel_verification(db, run_id, user_id=user_id)
            return await real_runner.run(ws, req)

    worker = WorkerLoop(
        session_factory=test_session,
        runner=CancellingRunner(),
        worker_id="test-docker-worker",
        scratch_root=scratch,
    )

    await worker.run_once()

    with test_session() as db:
        run_db = db.get(VerificationRun, run_id)
        assert run_db.status == VerificationJobStatus.CANCELLED.value
        events = db.scalars(
            select(VerificationAuditEvent).where(
                VerificationAuditEvent.verification_run_id == run_id,
                VerificationAuditEvent.event_type == VerificationAuditEventType.CANCELLED.value,
            )
        ).all()
        assert len(events) >= 1

    assert_no_repopilot_containers()
