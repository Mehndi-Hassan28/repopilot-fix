"""Comprehensive unit and regression tests for Proof-of-Fix Verification Engine."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from repopilot_api.models import (
    IndexedFile,
    IndexingJob,
    ProofReport,
    Repository,
    User,
    VerificationRun,
)
from repopilot_api.proof_engine import (
    ProofOfFixEngine,
    TestOutputParser,
)
from repopilot_api.sandbox import (
    MockSandboxRunner,
    SandboxExecutionState,
    SandboxSecurityError,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofOfFixRequest,
    ProofVerdict,
    ProposedFix,
    VerificationStage,
)
from tests.fixtures.sandbox_fixtures import (
    CALC_FIX_PATCH,
    FAILING_FIXTURE_FILES,
    PASSING_FIXTURE_FILES,
)

SAMPLE_COMMIT_SHA = "a" * 40


def test_output_parser_unittest_success():
    stdout = "Ran 3 tests in 0.002s\n\nOK\n"
    metrics = TestOutputParser.parse(stdout, "")
    assert metrics.total == 3
    assert metrics.passed == 3
    assert metrics.failed == 0
    assert metrics.errors == 0
    assert "all passed" in metrics.summary


def test_output_parser_unittest_failure():
    stdout = "Ran 5 tests in 0.015s\n\nFAILED (failures=2, errors=1)\n"
    metrics = TestOutputParser.parse(stdout, "")
    assert metrics.total == 5
    assert metrics.failed == 2
    assert metrics.errors == 1
    assert metrics.passed == 2
    assert "2 failed, 1 errors" in metrics.summary


def test_output_parser_pytest_output():
    stdout = "==================== 3 passed, 1 failed in 0.12s ====================\n"
    metrics = TestOutputParser.parse(stdout, "")
    assert metrics.total == 4
    assert metrics.passed == 3
    assert metrics.failed == 1
    assert metrics.errors == 0


def test_output_parser_non_standard_fallback():
    stdout = "Custom test runner finished with code 0\n"
    metrics = TestOutputParser.parse(stdout, "")
    assert metrics.total is None
    assert metrics.passed is None
    assert metrics.failed is None
    assert "Custom test runner" in metrics.summary


@pytest.mark.anyio
async def test_proof_engine_verified_fix_scenario(tmp_path):
    """Reproduced bug in baseline (exit 1) followed by passing test in patched (exit 0)."""
    scratch = str(tmp_path / "scratch")

    call_count = 0

    class SequentialMockRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Baseline run: bug reproduced (fails)
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout="Ran 2 tests in 0.01s\n\nFAILED (failures=1)\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)
            else:
                # Patched run: fix verified (passes)
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.PASSED,
                    simulated_exit_code=0,
                    simulated_stdout="Ran 2 tests in 0.01s\n\nOK\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)

    runner = SequentialMockRunner(scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=10,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[
            PatchProposal(
                file_path="calc.py",
                diff=CALC_FIX_PATCH,
                explanation="Fix addition operator",
            )
        ],
        hypothesis="Bug caused by subtraction instead of addition",
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)

    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert result.confidence == "high"
    assert result.stage == VerificationStage.COMPLETED
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.patched_exit_code == 0
    assert result.evidence.changed_files == ["calc.py"]
    assert len(result.evidence.patch_hash) == 64
    assert result.evidence.baseline_metrics.failed == 1
    assert result.evidence.patched_metrics.failed == 0
    assert "Proof-of-Fix Verified" in result.summary


@pytest.mark.anyio
async def test_proof_engine_unreproduced_baseline_passes(tmp_path):
    """If baseline test passes (exit 0), fix cannot be proven (UNREPRODUCED)."""
    scratch = str(tmp_path / "scratch")

    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.PASSED,
        simulated_exit_code=0,
        simulated_stdout="Ran 2 tests in 0.01s\n\nOK\n",
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=11,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[
            PatchProposal(
                file_path="calc.py",
                diff=CALC_FIX_PATCH,
                explanation="Unnecessary patch",
            )
        ],
        files=PASSING_FIXTURE_FILES,
    )

    result = await engine.verify(req)

    assert result.verdict == ProofVerdict.UNREPRODUCED
    assert result.confidence == "low"
    assert result.evidence.baseline_exit_code == 0
    assert result.evidence.patched_exit_code is None  # Patched run skipped safely
    assert "Original bug could not be reproduced" in result.summary


@pytest.mark.anyio
async def test_proof_engine_fix_failed_patched_still_fails(tmp_path):
    """Baseline fails, but patched code still fails with exit 1 (FIX_FAILED)."""
    scratch = str(tmp_path / "scratch")

    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.FAILED,
        simulated_exit_code=1,
        simulated_stdout="Ran 2 tests in 0.01s\n\nFAILED (failures=1)\n",
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    wrong_diff = (
        "@@ -1,3 +1,3 @@\n"
        " def add(a: int, b: int) -> int:\n"
        '     """Add two numbers."""\n'
        "-    return a - b  # Bug: returns subtraction instead of addition\n"
        "+    return a * b\n"
    )

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=12,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[
            PatchProposal(
                file_path="calc.py",
                diff=wrong_diff,
                explanation="Wrong fix (multiplication)",
            )
        ],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)

    assert result.verdict == ProofVerdict.FIX_FAILED
    assert result.confidence == "high"
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.patched_exit_code == 1
    assert "Fix failed" in result.summary


@pytest.mark.anyio
async def test_proof_engine_regression_detected(tmp_path):
    """Baseline has 1 failure, but patched code causes 2 failures (REGRESSION_DETECTED)."""
    scratch = str(tmp_path / "scratch")

    call_count = 0

    class RegressionMockRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout="Ran 5 tests in 0.01s\n\nFAILED (failures=1)\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)
            else:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout="Ran 5 tests in 0.01s\n\nFAILED (failures=2)\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)

    runner = RegressionMockRunner(scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=13,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[
            PatchProposal(
                file_path="calc.py",
                diff=CALC_FIX_PATCH,
                explanation="Fix attempt with regression",
            )
        ],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)

    assert result.verdict == ProofVerdict.REGRESSION_DETECTED
    assert result.confidence == "high"
    assert "Regression detected" in result.summary
    assert result.evidence.baseline_metrics.failed == 1
    assert result.evidence.patched_metrics.failed == 2


@pytest.mark.anyio
async def test_proof_engine_invalid_patch_application(tmp_path):
    """Patch has syntax error or context conflict (PATCH_APPLICATION_ERROR)."""
    scratch = str(tmp_path / "scratch")

    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.FAILED,
        simulated_exit_code=1,
        simulated_stdout="Ran 1 test\n\nFAILED (failures=1)\n",
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    bad_patch = (
        "@@ -1,3 +1,3 @@\n"
        " def non_existent_function():\n"  # Context conflict!
        "-    pass\n"
        "+    return 1\n"
    )

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=14,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[
            PatchProposal(
                file_path="calc.py",
                diff=bad_patch,
                explanation="Conflict patch",
            )
        ],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)

    assert result.verdict == ProofVerdict.PATCH_APPLICATION_ERROR
    assert result.confidence == "low"
    assert "Patch application failed" in result.summary


@pytest.mark.anyio
async def test_proof_engine_rejects_unauthorized_test_command(tmp_path):
    """CommandValidator strictly blocks shell injection attempts or unauthorized commands."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    with pytest.raises(SandboxSecurityError, match="Unauthorized command entrypoint"):
        req = ProofOfFixRequest(
            repository_id=1,
            commit_sha=SAMPLE_COMMIT_SHA,
            issue_number=15,
            test_command=["bash", "-c", "echo 1"],
            patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
            files=FAILING_FIXTURE_FILES,
        )
        await engine.verify(req)


@pytest.mark.anyio
async def test_proof_engine_timeout_handling(tmp_path):
    """Runner timeout yields TIMEOUT verdict and low confidence."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.TIMED_OUT,
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=16,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)

    assert result.verdict == ProofVerdict.TIMEOUT
    assert result.confidence == "low"
    assert "timed out" in result.summary.lower()


@pytest.mark.anyio
async def test_proof_engine_sandbox_infrastructure_error(tmp_path):
    """Runner infrastructure error yields ERROR verdict."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.ERROR,
        simulated_error="Docker daemon socket failed",
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=17,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)

    assert result.verdict == ProofVerdict.ERROR
    assert result.confidence == "low"
    assert "infrastructure error" in result.summary


@pytest.mark.anyio
async def test_proof_engine_database_persistence_integration(tmp_path, test_session):
    """Verify that VerificationRun and ProofReport are correctly populated in DB."""
    scratch = str(tmp_path / "scratch")

    call_count = 0

    class DBSequentialMockRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout="Ran 2 tests in 0.01s\n\nFAILED (failures=1)\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)
            else:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.PASSED,
                    simulated_exit_code=0,
                    simulated_stdout="Ran 2 tests in 0.01s\n\nOK\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)

    runner = DBSequentialMockRunner(scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    with test_session() as session:
        user = User(disabled=False)
        session.add(user)
        session.flush()

        repo = Repository(
            github_id=88801,
            owner="org",
            name="proj",
            full_name="org/proj",
            url="https://github.com/org/proj",
            default_branch="main",
            stars=10,
        )
        session.add(repo)
        session.flush()

        req = ProofOfFixRequest(
            repository_id=repo.id,
            commit_sha=SAMPLE_COMMIT_SHA,
            issue_number=42,
            user_id=user.id,
            test_command=["python", "-m", "unittest", "test_calc.py"],
            patches=ProposedFix(
                explanation="Fix addition",
                patches=[
                    PatchProposal(
                        file_path="calc.py",
                        diff=CALC_FIX_PATCH,
                        explanation="Add operator",
                    )
                ],
            ),
            hypothesis="Subtraction instead of addition",
            files=FAILING_FIXTURE_FILES,
        )

        result = await engine.verify(req, db=session)

        assert result.verdict == ProofVerdict.VERIFIED_FIX
        assert result.verification_run_id is not None
        assert result.proof_report_id is not None

        # Query VerificationRun
        vrun = session.scalar(
            select(VerificationRun).where(VerificationRun.id == result.verification_run_id)
        )
        assert vrun is not None
        assert vrun.status == "passed"
        assert vrun.baseline_exit_code == 1
        assert vrun.patched_exit_code == 0
        assert vrun.issue_number == 42
        assert vrun.user_id == user.id

        # Query ProofReport
        report = session.scalar(select(ProofReport).where(ProofReport.id == result.proof_report_id))
        assert report is not None
        assert report.verdict == "verified_fix"
        assert report.confidence == "high"
        assert "Proof-of-Fix Verified" in report.summary
        assert "calc.py" in report.evidence_json


@pytest.mark.anyio
async def test_proof_engine_db_failure_resilience(tmp_path):
    """If database persistence encounters an error, in-memory result is still safely returned."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.PASSED,
        simulated_exit_code=0,
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    # Mock DB that throws on commit
    mock_db = MagicMock()
    mock_db.commit.side_effect = Exception("DB Connection Lost")

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=99,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        files=PASSING_FIXTURE_FILES,
    )

    # Must NOT raise unhandled DB error
    result = await engine.verify(req, db=mock_db)
    assert result.verdict == ProofVerdict.UNREPRODUCED
    assert result.evidence.baseline_exit_code == 0


@pytest.mark.anyio
async def test_proof_engine_files_resolved_from_indexing_job(tmp_path, test_session):
    """When request.files is None, engine loads files directly from IndexingJob/IndexedFile."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.PASSED,
        simulated_exit_code=0,
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    with test_session() as session:
        repo = Repository(
            github_id=88802,
            owner="org2",
            name="proj2",
            full_name="org2/proj2",
            url="https://github.com/org2/proj2",
            default_branch="main",
            stars=1,
        )
        session.add(repo)
        session.flush()

        job = IndexingJob(
            repository_id=repo.id,
            commit_sha=SAMPLE_COMMIT_SHA,
            status="completed",
        )
        session.add(job)
        session.flush()

        f1 = IndexedFile(
            job_id=job.id,
            path="calc.py",
            language="python",
            size_bytes=len(PASSING_FIXTURE_FILES["calc.py"]),
            content=PASSING_FIXTURE_FILES["calc.py"],
        )
        f2 = IndexedFile(
            job_id=job.id,
            path="test_calc.py",
            language="python",
            size_bytes=len(PASSING_FIXTURE_FILES["test_calc.py"]),
            content=PASSING_FIXTURE_FILES["test_calc.py"],
        )
        session.add_all([f1, f2])
        session.commit()

        req = ProofOfFixRequest(
            repository_id=repo.id,
            commit_sha=SAMPLE_COMMIT_SHA,
            issue_number=1,
            test_command=["python", "-m", "unittest", "test_calc.py"],
            patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
            files=None,  # Loaded from DB!
        )

        result = await engine.verify(req, db=session)
        assert result.verdict == ProofVerdict.UNREPRODUCED
        assert result.evidence.baseline_exit_code == 0


def test_output_parser_extracts_failed_test_ids_and_classifies_failure():
    # Unittest failure
    unittest_out = (
        "FAIL: test_add (test_calc.TestCalc)\nAssertionError: 3 != -1\n\nFAILED (failures=1)\n"
    )
    m1 = TestOutputParser.parse(unittest_out, "", exit_code=1)
    assert m1.failed == 1
    assert m1.failure_type == "assertion_failure"
    assert "test_add (test_calc.TestCalc)" in m1.failed_test_ids

    # Pytest failure
    pytest_out = (
        "FAILED test_calc.py::TestCalc::test_add - AssertionError: assert 3 == -1\n"
        "1 failed in 0.05s\n"
    )
    m2 = TestOutputParser.parse(pytest_out, "", exit_code=1)
    assert m2.failed == 1
    assert m2.failure_type == "assertion_failure"
    assert "test_calc.py::TestCalc::test_add" in m2.failed_test_ids

    # Collection error
    err_out = (
        "ERROR collecting tests/test_calc.py\nModuleNotFoundError: No module named 'missing'\n"
    )
    m3 = TestOutputParser.parse(err_out, "", exit_code=2)
    assert m3.failure_type in ("collection_error", "import_error")


@pytest.mark.anyio
async def test_proof_engine_baseline_collection_error_not_bug_reproduction(tmp_path):
    """If baseline fails due to collection/import error, it is NOT bug reproduction."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.FAILED,
        simulated_exit_code=2,
        simulated_stdout=(
            "ERROR collecting tests/test_calc.py\nModuleNotFoundError: No module 'dep'\n"
        ),
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=20,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)
    assert result.verdict == ProofVerdict.ERROR
    assert result.confidence == "low"
    assert "collection_error" in result.summary or "import_error" in result.summary
    assert result.evidence.patched_exit_code is None  # Patched run skipped


@pytest.mark.anyio
async def test_proof_engine_inconclusive_when_metrics_unparseable(tmp_path):
    """If exit code is 1 in base and 0 in patch, but output is unparseable, return INCONCLUSIVE."""
    scratch = str(tmp_path / "scratch")

    call_count = 0

    class UnparseableRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout="Custom non-standard test output that cannot be parsed\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)
            else:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.PASSED,
                    simulated_exit_code=0,
                    simulated_stdout="Custom non-standard test output success\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)

    runner = UnparseableRunner(scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=21,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)
    assert result.verdict == ProofVerdict.INCONCLUSIVE
    assert result.confidence == "low"
    assert "could not be verified" in result.summary


@pytest.mark.anyio
async def test_proof_engine_detects_new_failing_test_same_count(tmp_path):
    """Detect a new failing test even when the total number of failures stays unchanged."""
    scratch = str(tmp_path / "scratch")

    call_count = 0

    class SwappedFailureRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Baseline: test_add fails
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout=(
                        "FAIL: test_add\n"
                        "AssertionError: 1 != 2\n\n"
                        "Ran 3 tests in 0.01s\n\n"
                        "FAILED (failures=1)\n"
                    ),
                    scratch_root=scratch,
                ).run(workspace_path, request)
            else:
                # Patched: test_add is fixed, BUT test_subtract fails! (Total failures still 1!)
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout=(
                        "FAIL: test_subtract\n"
                        "AssertionError: 0 != 5\n\n"
                        "Ran 3 tests in 0.01s\n\n"
                        "FAILED (failures=1)\n"
                    ),
                    scratch_root=scratch,
                ).run(workspace_path, request)

    runner = SwappedFailureRunner(scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=22,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        files=FAILING_FIXTURE_FILES,
    )

    result = await engine.verify(req)
    assert result.verdict == ProofVerdict.REGRESSION_DETECTED
    assert result.confidence == "high"
    assert "test_subtract" in result.evidence.new_failed_test_ids
    assert "test_add" in result.evidence.resolved_test_ids
    assert "Regression detected" in result.summary


@pytest.mark.anyio
async def test_proof_engine_persistence_status_flags(tmp_path, test_session):
    """Ensure is_persisted is True on successful DB write and False on DB failure or missing DB."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.PASSED,
        simulated_exit_code=0,
        simulated_stdout="Ran 1 test\n\nOK\n",
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=23,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        files=PASSING_FIXTURE_FILES,
    )

    # 1. Without DB session
    res_no_db = await engine.verify(req, db=None)
    assert res_no_db.is_persisted is False
    assert "No database session" in res_no_db.persistence_error

    # 2. With failing DB commit
    mock_db = MagicMock()
    mock_db.commit.side_effect = Exception("Disk full")
    res_fail_db = await engine.verify(req, db=mock_db)
    assert res_fail_db.is_persisted is False
    assert "Database persistence failed" in res_fail_db.persistence_error
    assert any(
        "Evidence was not durably persisted" in r for r in res_fail_db.evidence.uncertainty_reasons
    )

    # 3. With real working DB session
    with test_session() as session:
        repo = Repository(
            github_id=88803,
            owner="org3",
            name="proj3",
            full_name="org3/proj3",
            url="https://github.com/org3/proj3",
            default_branch="main",
            stars=1,
        )
        session.add(repo)
        session.flush()

        req.repository_id = repo.id
        res_ok_db = await engine.verify(req, db=session)
        assert res_ok_db.is_persisted is True
        assert res_ok_db.persistence_error is None
        assert res_ok_db.verification_run_id is not None
        assert res_ok_db.proof_report_id is not None


@pytest.mark.anyio
async def test_proof_engine_identical_baseline_and_patched_environment(tmp_path):
    """Verify baseline and patched runs execute identical commands, envs, and base files."""
    scratch = str(tmp_path / "scratch")
    captured_requests = []

    class CapturingMockRunner(MockSandboxRunner):
        async def run(self, workspace_path, request):
            captured_requests.append((workspace_path, request))
            if len(captured_requests) == 1:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.FAILED,
                    simulated_exit_code=1,
                    simulated_stdout=(
                        "FAIL: test_calc\nAssertionError\n\nRan 1 test\n\nFAILED (failures=1)\n"
                    ),
                    scratch_root=scratch,
                ).run(workspace_path, request)
            else:
                return await MockSandboxRunner(
                    simulated_state=SandboxExecutionState.PASSED,
                    simulated_exit_code=0,
                    simulated_stdout="Ran 1 test in 0.01s\n\nOK\n",
                    scratch_root=scratch,
                ).run(workspace_path, request)

    runner = CapturingMockRunner(scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=1,
        commit_sha=SAMPLE_COMMIT_SHA,
        issue_number=24,
        test_command=["python", "-m", "unittest", "test_calc.py"],
        patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
        environment={"DEBUG_MODE": "1"},
        timeout_seconds=15,
        files=FAILING_FIXTURE_FILES,
    )

    await engine.verify(req)
    assert len(captured_requests) == 2
    base_ws, base_req = captured_requests[0]
    patch_ws, patch_req = captured_requests[1]

    # Both must use exact same command, environment, and timeout
    assert base_req.command == patch_req.command == ["python", "-m", "unittest", "test_calc.py"]
    assert base_req.environment == patch_req.environment == {"DEBUG_MODE": "1"}
    assert base_req.timeout_seconds == patch_req.timeout_seconds == 15
    assert base_ws != patch_ws  # Isolated separate workspaces


@pytest.mark.anyio
async def test_proof_engine_duplicate_verification_requests(tmp_path, test_session):
    """Duplicate verification runs for same repo/issue are tracked as separate immutable records."""
    scratch = str(tmp_path / "scratch")
    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.PASSED,
        simulated_exit_code=0,
        simulated_stdout="Ran 1 test in 0.01s\n\nOK\n",
        scratch_root=scratch,
    )
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    with test_session() as session:
        repo = Repository(
            github_id=88804,
            owner="org4",
            name="proj4",
            full_name="org4/proj4",
            url="https://github.com/org4/proj4",
            default_branch="main",
            stars=1,
        )
        session.add(repo)
        session.flush()

        req = ProofOfFixRequest(
            repository_id=repo.id,
            commit_sha=SAMPLE_COMMIT_SHA,
            issue_number=25,
            test_command=["python", "-m", "unittest", "test_calc.py"],
            patches=[PatchProposal(file_path="calc.py", diff=CALC_FIX_PATCH, explanation="x")],
            files=PASSING_FIXTURE_FILES,
        )

        res1 = await engine.verify(req, db=session)
        res2 = await engine.verify(req, db=session)

        assert res1.run_id != res2.run_id
        assert res1.verification_run_id != res2.verification_run_id
        assert res1.proof_report_id != res2.proof_report_id
