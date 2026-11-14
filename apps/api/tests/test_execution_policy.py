"""Negative security tests and verification policy enforcement tests for Milestone 3.6 Phase 1.

Verifies:
1. Untrusted fixture rejected (unknown code snapshot fails closed).
2. Modified fixture rejected (tampered file content fails SHA-256 integrity check).
3. Unapproved verification rejected (draft/pending runs cannot execute).
4. Specification mismatch rejected (post-approval alteration fails).
5. Unauthorized user rejected (unauthenticated, non-members, or readers rejected).
6. Untrusted repository rejected (unallowlisted repos blocked).
7. Unapproved test command rejected (unapproved entrypoint/args blocked).
8. Corrupted patch hash rejected (patch digest tampering blocked).
9. Missing metadata fails closed (missing indexing snapshot blocked).
10. Valid trusted fixture passes all policy checks and executes cleanly.
"""

from datetime import UTC, datetime
import json
import pytest

from repopilot_api.fixture_registry import (
    ExecutionPolicyGuard,
    FixtureRegistryError,
    ModifiedFixtureError,
    PolicyMetadataError,
    TrustedFixture,
    TrustedFixtureRegistry,
    UnauthorizedUserError,
    UntrustedFixtureError,
    UntrustedRepositoryError,
    compute_snapshot_hash,
    get_default_fixture_registry,
)
from repopilot_api.models import (
    AuthSession,
    IndexedFile,
    IndexingJob,
    Repository,
    RepositoryAccess,
    User,
    VerificationRun,
)
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    MockSandboxRunner,
)
from repopilot_api.schemas import (
    PatchProposal,
    VerificationCreateRequest,
    VerificationJobStatus,
)
from repopilot_api.security import AuthContext
from repopilot_api.verification_service import (
    VerificationJobManager,
    VerificationSecurityError,
    VerificationSpecificationMismatchError,
    VerificationStateError,
    compute_spec_hash,
)


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


# ==============================================================================
# 1. Untrusted Fixture Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_untrusted_fixture_rejected(test_session):
    """Repository snapshot with unknown/arbitrary files is strictly rejected."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")

        # Snapshot contains unknown arbitrary files not in the fixture registry
        untrusted_files = {
            "untrusted.py": "print('malicious arbitrary script')",
            "test_untrusted.py": "import unittest\nclass T(unittest.TestCase): pass",
        }
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA, files=untrusted_files)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        runner = MockSandboxRunner()
        with pytest.raises(UntrustedFixtureError) as exc_info:
            await VerificationJobManager.execute_job(db, run.id, runner=runner, user_id=user.id)

        assert "No approved trusted fixture found" in str(exc_info.value)
        # Runner was never called
        assert len(runner.executed_commands) == 0
        db.refresh(run)
        # Status did NOT transition to RUNNING
        assert run.status != VerificationJobStatus.RUNNING.value


# ==============================================================================
# 2. Modified Fixture Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_modified_fixture_rejected(test_session):
    """Tampering with an approved fixture's files triggers snapshot hash mismatch."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")

        # Start with standard calc files, but tamper with tests/test_calc.py
        tampered_files = {
            "src/calc.py": "def add(a, b):\n    return a - b\n",
            "tests/test_calc.py": (
                "import unittest\n"
                "# Injected unauthorized modification!\n"
                "from src.calc import add\n"
                "class TestCalc(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n"
            ),
        }
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA, files=tampered_files)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        runner = MockSandboxRunner()
        with pytest.raises(ModifiedFixtureError) as exc_info:
            await VerificationJobManager.execute_job(db, run.id, runner=runner, user_id=user.id)

        assert "snapshot content hash mismatch" in str(exc_info.value)
        assert len(runner.executed_commands) == 0
        db.refresh(run)
        assert run.status != VerificationJobStatus.RUNNING.value


# ==============================================================================
# 3. Unapproved Verification Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_unapproved_verification_rejected(test_session):
    """Verification runs in DRAFT or PENDING_APPROVAL cannot be executed."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA)

        # 1. Draft run
        payload = sample_create_payload()
        payload.auto_submit_approval = False
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, payload
        )
        assert run.status == VerificationJobStatus.DRAFT.value

        with pytest.raises(VerificationStateError) as exc_info:
            await VerificationJobManager.execute_job(db, run.id, runner=MockSandboxRunner())
        assert "Cannot execute verification in state 'draft'" in str(exc_info.value)

        # 2. Pending Approval run
        VerificationJobManager.submit_for_approval(db, run.id, user.id)
        assert run.status == VerificationJobStatus.PENDING_APPROVAL.value

        with pytest.raises(VerificationStateError) as exc_info:
            await VerificationJobManager.execute_job(db, run.id, runner=MockSandboxRunner())
        assert "Cannot execute verification in state 'pending_approval'" in str(exc_info.value)

        # 3. Force state to QUEUED but with no approved_spec_hash
        run.status = VerificationJobStatus.QUEUED.value
        run.approved_spec_hash = None
        db.commit()

        with pytest.raises(VerificationSecurityError) as exc_info:
            await VerificationJobManager.execute_job(db, run.id, runner=MockSandboxRunner())
        assert "No execution without human approval" in str(exc_info.value)


# ==============================================================================
# 4. Specification Mismatch Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_specification_mismatch_rejected(test_session):
    """Altering test_command, commit_sha, or patch after approval fails spec verification."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        # Maliciously modify the test command in the database post-approval
        run.test_command = json.dumps(["python", "-m", "unittest", "tests/injected.py"])
        db.commit()

        with pytest.raises(VerificationSpecificationMismatchError) as exc_info:
            await VerificationJobManager.execute_job(db, run.id, runner=MockSandboxRunner())

        assert "specification altered after approval" in str(exc_info.value).lower()
        db.refresh(run)
        assert run.status == VerificationJobStatus.FAILED.value
        assert "Specification mismatch" in (run.error_message or "")


# ==============================================================================
# 5. Unauthorized User Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_unauthorized_user_rejected(test_session):
    """Users with no repository access or cross-tenant access cannot execute verification."""
    with test_session() as db:
        owner = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, owner, role="owner")
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, owner.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, owner.id)

        # 1. Explicit unauthorized user without any repository access record
        unauthorized_user = make_user(db)
        with pytest.raises(UnauthorizedUserError) as exc_info:
            await VerificationJobManager.execute_job(
                db, run.id, runner=MockSandboxRunner(), user_id=unauthorized_user.id
            )
        assert "has no authorization record" in str(exc_info.value)

        # 2. User with access only to a completely different repository (cross-tenant access attempt)
        other_repo = make_repo(db, github_id=998, owner="other-org", name="other-repo")
        cross_tenant_user = make_user(db)
        add_access(db, other_repo, cross_tenant_user, role="owner")

        with pytest.raises(UnauthorizedUserError) as exc_info:
            await VerificationJobManager.execute_job(
                db, run.id, runner=MockSandboxRunner(), user_id=cross_tenant_user.id
            )
        assert "has no authorization record" in str(exc_info.value)

        # 3. Revoked access: approver's repository access was deleted before execution
        access = db.get(RepositoryAccess, (repo.id, owner.id))
        db.delete(access)
        db.commit()

        with pytest.raises(UnauthorizedUserError) as exc_info:
            await VerificationJobManager.execute_job(
                db, run.id, runner=MockSandboxRunner(), user_id=None
            )
        assert "has no authorization record" in str(exc_info.value)


# ==============================================================================
# 6. Untrusted Repository Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_untrusted_repository_rejected(test_session):
    """Repositories not in the server-controlled trusted allowlist are rejected."""
    with test_session() as db:
        user = make_user(db)
        # Create repo with an untrusted owner/name
        repo = make_repo(db, github_id=12345, owner="evil-org", name="untrusted-repo")
        add_access(db, repo, user, role="owner")
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        with pytest.raises(UntrustedRepositoryError) as exc_info:
            await VerificationJobManager.execute_job(
                db, run.id, runner=MockSandboxRunner(), user_id=user.id
            )

        assert "not in the trusted repository allowlist" in str(exc_info.value)
        db.refresh(run)
        assert run.status != VerificationJobStatus.RUNNING.value


# ==============================================================================
# 7. Unapproved Test Command Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_unapproved_test_command_rejected(test_session):
    """Test commands not explicitly approved for the fixture are rejected."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA)

        # Command is syntactically safe unittest, but not in fixture's approved list
        unapproved_cmd = ["python", "-m", "unittest", "tests/unapproved_calc.py"]
        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload(test_command=unapproved_cmd)
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        with pytest.raises(FixtureRegistryError) as exc_info:
            await VerificationJobManager.execute_job(
                db, run.id, runner=MockSandboxRunner(), user_id=user.id
            )

        assert "is not approved for fixture" in str(exc_info.value)
        db.refresh(run)
        assert run.status != VerificationJobStatus.RUNNING.value


# ==============================================================================
# 8. Corrupted Patch Hash Rejected
# ==============================================================================


@pytest.mark.anyio
async def test_corrupted_patch_hash_rejected(test_session):
    """Tampering with patch_hash or patch_diff fails integrity verification."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        # Corrupt patch_hash in database
        run.patch_hash = "0" * 64
        # Also update approved_spec_hash so spec check passes to reach patch check
        test_cmd = json.loads(run.test_command)
        run.approved_spec_hash = compute_spec_hash(
            run.commit_sha, run.patch_hash, test_cmd, run.sandbox_image
        )
        run.spec_hash = run.approved_spec_hash
        db.commit()

        with pytest.raises(FixtureRegistryError) as exc_info:
            await VerificationJobManager.execute_job(
                db, run.id, runner=MockSandboxRunner(), user_id=user.id
            )

        assert "Patch hash mismatch" in str(exc_info.value)
        db.refresh(run)
        assert run.status != VerificationJobStatus.RUNNING.value


# ==============================================================================
# 9. Missing Metadata Fails Closed
# ==============================================================================


@pytest.mark.anyio
async def test_missing_metadata_fails_closed(test_session):
    """Verification with missing indexing job or files snapshot fails closed."""
    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")
        # Notice: make_index is NOT called; no indexed files exist!

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        with pytest.raises(PolicyMetadataError) as exc_info:
            await VerificationJobManager.execute_job(
                db, run.id, runner=MockSandboxRunner(), user_id=user.id
            )

        assert "No completed indexing job found" in str(exc_info.value)
        db.refresh(run)
        assert run.status != VerificationJobStatus.RUNNING.value


# ==============================================================================
# 10. Valid Trusted Fixture Passes All Checks and Executes
# ==============================================================================


@pytest.mark.anyio
async def test_trusted_fixture_passes_policy_and_executes(test_session):
    """Approved verification matching trusted fixture executes cleanly."""
    from repopilot_api.schemas import SandboxExecutionState, ProofVerdict

    class StepRunner(MockSandboxRunner):
        def __init__(self):
            super().__init__()
            self.call_count = 0
            self.executed_commands = []

        async def run(self, workspace_path, request):
            self.call_count += 1
            self.executed_commands.append(request.command)
            if self.call_count == 1:
                # Baseline reproduces failure (exit 1)
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout="Ran 1 test in 0.01s\n\nFAILED (failures=1)\n",
                ).run(workspace_path, request)
            # Patched passes (exit 0)
            return await MockSandboxRunner(
                simulated_state=SandboxExecutionState.PASSED,
                simulated_exit_code=0,
                simulated_stdout="Ran 1 test in 0.01s\n\nOK\n",
            ).run(workspace_path, request)

    with test_session() as db:
        user = make_user(db)
        repo = make_repo(db, owner="acme", name="app")
        add_access(db, repo, user, role="owner")
        make_index(db, repo, commit_sha=SAMPLE_COMMIT_SHA)

        run, _ = VerificationJobManager.create_verification_request(
            db, repo.id, user.id, sample_create_payload()
        )
        VerificationJobManager.approve_verification(db, run.id, user.id)

        runner = StepRunner()
        result = await VerificationJobManager.execute_job(
            db, run.id, runner=runner, user_id=user.id
        )

        assert result is not None
        assert result.verdict == ProofVerdict.VERIFIED_FIX
        db.refresh(run)
        assert run.status == VerificationJobStatus.COMPLETED.value
        assert len(runner.executed_commands) == 2  # Baseline + Patched
