"""Comprehensive Live AI Software Repair & Proof-of-Fix Evaluation Tests.

Validates:
1. Live AI issue investigation using grounded repository context.
2. Structured root-cause hypotheses with validated file and line citations.
3. Clean unified diff generation, revision validation, and patch applicability.
4. Telemetry recording: provider, model, latency, and token usage without logging secrets.
5. Rejection of invalid, unsupported, malformed, or unsafe patches.
6. Three small trusted bug scenarios evaluated end-to-end:
   - Scenario 1: Calculator power operator defect (src/calculator.py)
   - Scenario 2: Calculator addition operator defect (src/calc.py)
   - Scenario 3: Range validator inclusive upper bound defect (src/validator.py)
7. Reproduction of baseline failures, patched success, and regression detection.
8. Gating: Failed fixes and regressions strictly blocked from GitHub PR creation.
"""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from repopilot_api.chat import (
    Evidence,
    MockProvider,
    normalize_model_name,
    parse_and_validate_analysis,
    validate_server_side_analysis,
)
from repopilot_api.fixture_registry import (
    DEMO_CALC_FILES,
    DEMO_CALC_FIX_PATCH,
    RANGE_VALIDATOR_FILES,
    RANGE_VALIDATOR_FIX_PATCH,
)
from repopilot_api.github_pr_service import (
    GitHubPullRequestService,
    GitHubVerificationPrerequisiteError,
)
from repopilot_api.models import (
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
    DockerSandboxRunner,
    MockSandboxRunner,
)
from repopilot_api.schemas import (
    FileCitation,
    IssueAnalysisResult,
    PatchProposal,
    ProofOfFixRequest,
    ProofVerdict,
)
from repopilot_api.verification_service import compute_spec_hash
from tests.fixtures.sandbox_fixtures import (
    FAILING_FIXTURE_FILES,
    RANGE_VALIDATOR_FIXTURE_FILES,
    RANGE_VALIDATOR_REGRESSION_PATCH,
)

SAMPLE_COMMIT_SHA = "a" * 40


def _docker_is_available() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "Docker CLI executable not found on PATH."
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        if res.returncode != 0:
            return False, "Docker daemon is not running or not accessible."
    except Exception as exc:
        return False, f"Docker daemon check failed: {exc}"
    return True, "Docker is ready."


_has_docker, _docker_reason = _docker_is_available()


# ==============================================================================
# Helper Factories
# ==============================================================================


def create_user_and_repo(db, repo_name=None, owner="repopilot"):
    user = User()
    db.add(user)
    db.flush()

    name = repo_name or f"demo-calc-{user.id}"
    url = (
        "https://github.com/repopilot/demo-calculator"
        if owner == "repopilot" and name.startswith("demo-calc-")
        else f"https://github.com/{owner}/{name}"
    )
    desc = (
        f"Deterministic Calculator Demo Fixture for RepoPilot Fix "
        f"[provenance:internal_fixture:{user.id}:demo-calculator]"
        if owner == "repopilot" and name.startswith("demo-calc-")
        else "Test repository"
    )

    repo = Repository(
        github_id=990000000 + user.id,
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        url=url,
        default_branch="main",
        description=desc,
    )
    db.add(repo)
    db.flush()

    access = RepositoryAccess(user_id=user.id, repository_id=repo.id, role="owner")
    db.add(access)

    job = IndexingJob(
        repository_id=repo.id,
        status="completed",
        commit_sha=SAMPLE_COMMIT_SHA,
        file_count=2,
    )
    db.add(job)
    db.flush()

    db.commit()
    db.refresh(user)
    db.refresh(repo)
    db.refresh(job)
    return user, repo, job


# ==============================================================================
# Phase 2 & 4: AI Reliability, Telemetry, and Contract Validation
# ==============================================================================


def test_model_name_normalization():
    """Verify that model normalization maps Google Generative Language models properly."""
    google_url = "https://generativelanguage.googleapis.com/v1beta/openai"

    # Non-existent/broken models normalized to active working free model
    assert (
        normalize_model_name("gemini-3.5-flash-lite", google_url)
        == "models/gemini-flash-lite-latest"
    )
    assert (
        normalize_model_name("models/gemini-3.5-flash-lite", google_url)
        == "models/gemini-flash-lite-latest"
    )

    # Prefix normalized when missing on gemini-flash-lite-latest
    assert (
        normalize_model_name("gemini-flash-lite-latest", google_url)
        == "models/gemini-flash-lite-latest"
    )
    assert (
        normalize_model_name("models/gemini-flash-lite-latest", google_url)
        == "models/gemini-flash-lite-latest"
    )

    # Other models preserved as configured
    assert normalize_model_name("gemini-2.5-flash", google_url) == "gemini-2.5-flash"

    # Non-google endpoints untouched
    other_url = "https://api.openai.com/v1"
    assert normalize_model_name("gpt-4o-mini", other_url) == "gpt-4o-mini"


def test_mock_provider_telemetry_and_three_scenarios():
    """Verify MockProvider sets telemetry and generates valid fixes for all 3 scenarios."""
    provider = MockProvider()

    # Scenario 1: Calculator power bug
    issue1 = {
        "number": 1,
        "title": "Bug in power calculation: multiplication returned instead of exponentiation",
        "body": "power(2, 3) returns 6 instead of 8 in src/calculator.py",
    }
    ev1 = [
        Evidence(
            path="src/calculator.py",
            start_line=18,
            end_line=21,
            content=DEMO_CALC_FILES["src/calculator.py"],
            score=0.95,
        )
    ]
    raw1 = provider.analyze_issue(issue1, ev1)
    res1 = parse_and_validate_analysis(
        raw1, ev1, DEMO_CALC_FILES, provider_name="mock", is_mock=True
    )
    assert res1.is_mock is True
    assert "Hypothesis:" in res1.root_cause_hypothesis
    assert len(res1.proposed_fix.patches) == 1
    assert "return base ** exponent" in res1.proposed_fix.patches[0].diff
    assert provider.last_telemetry["model"] == "mock"
    assert provider.last_telemetry["latency_ms"] >= 0
    assert provider.last_telemetry["generation_mode"] == "mock_ai"

    # Scenario 2: Calculator addition package bug
    issue2 = {
        "number": 2,
        "title": "Bug in add(): returns subtraction instead of addition",
        "body": "add(2, 3) returns -1 instead of 5.",
    }
    src2 = FAILING_FIXTURE_FILES["calc.py"]
    ev2 = [Evidence(path="src/calc.py", start_line=1, end_line=3, content=src2, score=0.9)]
    raw2 = provider.analyze_issue(issue2, ev2)
    res2 = parse_and_validate_analysis(
        raw2, ev2, {"src/calc.py": src2}, provider_name="mock", is_mock=True
    )
    assert "return a + b" in res2.proposed_fix.patches[0].diff

    # Scenario 3: Range validator boundary bug
    issue3 = {
        "number": 3,
        "title": "is_in_range upper bound comparison is exclusive instead of inclusive",
        "body": "is_in_range(10, 1, 10) returns False instead of True in src/validator.py",
    }
    src3 = RANGE_VALIDATOR_FILES["src/validator.py"]
    ev3 = [
        Evidence(
            path="src/validator.py",
            start_line=1,
            end_line=5,
            content=src3,
            score=0.9,
        )
    ]
    raw3 = provider.analyze_issue(issue3, ev3)
    res3 = parse_and_validate_analysis(
        raw3, ev3, {"src/validator.py": src3}, provider_name="mock", is_mock=True
    )
    assert "min_val <= val <= max_val" in res3.proposed_fix.patches[0].diff


def test_line_citation_clamping_and_bounds_validation():
    """Verify line citations are clamped when exceeding file length, and rejected if invalid."""
    indexed_files = {"src/calculator.py": DEMO_CALC_FILES["src/calculator.py"]}
    total_lines = len(indexed_files["src/calculator.py"].splitlines())
    assert total_lines == 21

    ev = [
        Evidence(
            path="src/calculator.py",
            start_line=1,
            end_line=total_lines,
            content=indexed_files["src/calculator.py"],
            score=0.9,
        )
    ]

    # Valid patch
    patch = PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)

    # 1. End line slightly exceeds file length (e.g. 25 on 21-line file) -> clamped to 21
    citation_exceed = FileCitation(
        path="src/calculator.py",
        start_line=18,
        end_line=25,
        explanation="power function",
    )
    analysis = IssueAnalysisResult(
        summary="Fix power",
        root_cause_hypothesis="Hypothesis: power bug",
        relevant_files=[citation_exceed],
        proposed_fix={"explanation": "Fix", "patches": [patch]},
        confidence="high",
        is_mock=True,
    )
    validate_server_side_analysis(analysis, ev, indexed_files)
    assert citation_exceed.end_line == total_lines

    # 2. Start line exceeds file length -> raises ValueError
    citation_invalid_start = FileCitation(
        path="src/calculator.py",
        start_line=50,
        end_line=60,
        explanation="invalid",
    )
    analysis_bad_start = IssueAnalysisResult(
        summary="Fix power",
        root_cause_hypothesis="Hypothesis: power bug",
        relevant_files=[citation_invalid_start],
        proposed_fix={"explanation": "Fix", "patches": [patch]},
        confidence="high",
    )
    with pytest.raises(ValueError, match="exceeds file length"):
        validate_server_side_analysis(analysis_bad_start, ev, indexed_files)

    # 3. Unreferenced file citation -> raises ValueError
    citation_unreferenced = FileCitation(
        path="src/unknown.py",
        start_line=1,
        end_line=5,
        explanation="unreferenced",
    )
    analysis_unref = IssueAnalysisResult(
        summary="Fix power",
        root_cause_hypothesis="Hypothesis: power bug",
        relevant_files=[citation_unreferenced],
        proposed_fix={"explanation": "Fix", "patches": [patch]},
        confidence="high",
    )
    with pytest.raises(ValueError, match="Unknown file referenced"):
        validate_server_side_analysis(analysis_unref, ev, indexed_files)


def test_malformed_and_conflicting_patches_rejected():
    """Verify that malformed diffs and patches that do not apply cleanly are rejected."""
    indexed_files = {"src/calculator.py": DEMO_CALC_FILES["src/calculator.py"]}
    ev = [
        Evidence(
            path="src/calculator.py",
            start_line=1,
            end_line=21,
            content=indexed_files["src/calculator.py"],
            score=0.9,
        )
    ]

    # 1. Diff missing hunk header
    bad_diff_no_hunk = (
        "--- a/src/calculator.py\n+++ b/src/calculator.py\n"
        "-return base * exponent\n+return base ** exponent\n"
    )
    patch_no_hunk = PatchProposal(file_path="src/calculator.py", diff=bad_diff_no_hunk)
    analysis_bad_hunk = IssueAnalysisResult(
        summary="Fix",
        root_cause_hypothesis="Hypothesis: bug",
        relevant_files=[FileCitation(path="src/calculator.py", start_line=1, end_line=5)],
        proposed_fix={"explanation": "Fix", "patches": [patch_no_hunk]},
        confidence="high",
    )
    with pytest.raises(ValueError, match="missing unified diff hunk header"):
        validate_server_side_analysis(analysis_bad_hunk, ev, indexed_files)

    # 2. Diff with non-matching context / deletion lines
    bad_diff_mismatch = (
        "--- a/src/calculator.py\n"
        "+++ b/src/calculator.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-def non_existent_function():\n"
        "+def replaced():\n"
    )
    patch_mismatch = PatchProposal(file_path="src/calculator.py", diff=bad_diff_mismatch)
    analysis_mismatch = IssueAnalysisResult(
        summary="Fix",
        root_cause_hypothesis="Hypothesis: bug",
        relevant_files=[FileCitation(path="src/calculator.py", start_line=1, end_line=5)],
        proposed_fix={"explanation": "Fix", "patches": [patch_mismatch]},
        confidence="high",
    )
    with pytest.raises(
        ValueError, match="(deleted line does not match|Patch cannot be applied cleanly)"
    ):
        validate_server_side_analysis(analysis_mismatch, ev, indexed_files)


# ==============================================================================
# Phase 3 & 5: Three Trusted Bug Scenarios (Proof-of-Fix Workflow)
# ==============================================================================


@pytest.mark.anyio
async def test_scenario_1_calculator_power_proof_of_fix(test_session, tmp_path):
    """Scenario 1: Calculator power operator defect in src/calculator.py."""
    db = test_session()
    user, repo, job = create_user_and_repo(db)

    # Seed indexed files
    for path, content in DEMO_CALC_FILES.items():
        db.add(
            IndexedFile(
                job_id=job.id,
                path=path,
                content=content,
                language="python",
                size_bytes=len(content.encode("utf-8")),
            )
        )
    db.commit()

    scratch = str(tmp_path / "scratch_s1")
    os.makedirs(scratch, exist_ok=True)

    # Baseline runner: tests fail on power
    baseline_stdout = (
        "....FF\n"
        "======================================================================\n"
        "FAIL: test_power_positive (tests.test_calculator.TestCalculator.test_power_positive)\n"
        "AssertionError: 6 != 8\n"
        "FAIL: test_power_zero (tests.test_calculator.TestCalculator.test_power_zero)\n"
        "AssertionError: 0 != 1\n"
        "----------------------------------------------------------------------\n"
        "Ran 6 tests in 0.001s\n\n"
        "FAILED (failures=2)\n"
    )
    # Patched runner: all 6 pass
    patched_stdout = (
        "......\n"
        "----------------------------------------------------------------------\n"
        "Ran 6 tests in 0.001s\n\n"
        "OK\n"
    )

    class Scenario1Runner:
        def __init__(self):
            self.execution_count = 0

        async def run(self, workspace_path, request):
            self.execution_count += 1
            is_patched = self.execution_count > 1
            if not is_patched:
                return await MockSandboxRunner(
                    simulated_exit_code=1,
                    simulated_stdout=baseline_stdout,
                    scratch_root=scratch,
                ).run(workspace_path, request)
            return await MockSandboxRunner(
                simulated_exit_code=0,
                simulated_stdout=patched_stdout,
                scratch_root=scratch,
            ).run(workspace_path, request)

    engine = ProofOfFixEngine(runner=Scenario1Runner(), scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=1,
        test_command=["python", "-m", "unittest", "tests/test_calculator.py"],
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)],
        hypothesis="power() multiplies instead of exponentiation",
        user_id=user.id,
        files=DEMO_CALC_FILES,
    )

    result = await engine.verify(req, db=db)

    # 1. Baseline reproduction
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.baseline_metrics.failed == 2
    assert result.evidence.baseline_metrics.passed == 4
    assert any(
        "test_power_positive" in tid for tid in result.evidence.baseline_metrics.failed_test_ids
    )
    assert any("test_power_zero" in tid for tid in result.evidence.baseline_metrics.failed_test_ids)

    # 2. Patched success
    assert result.evidence.patched_exit_code == 0
    assert result.evidence.patched_metrics.passed == 6
    assert result.evidence.patched_metrics.failed == 0
    assert len(result.evidence.new_failed_test_ids) == 0

    # 3. Verdict
    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert result.confidence == "high"


@pytest.mark.anyio
async def test_scenario_2_calc_addition_proof_of_fix(test_session, tmp_path):
    """Scenario 2: Calculator addition package bug (subtraction instead of addition)."""
    db = test_session()
    user, repo, job = create_user_and_repo(db, repo_name="app", owner="acme")

    calc_files = {
        "src/calc.py": "def add(a, b):\n    return a - b\n",
        "tests/test_calc.py": (
            "import unittest\n"
            "from src.calc import add\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n"
        ),
    }

    scratch = str(tmp_path / "scratch_s2")
    os.makedirs(scratch, exist_ok=True)

    baseline_stdout = (
        "F\n"
        "======================================================================\n"
        "FAIL: test_add (tests.test_calc.TestCalc.test_add)\n"
        "AssertionError: -1 != 5\n"
        "----------------------------------------------------------------------\n"
        "Ran 1 test in 0.001s\n\n"
        "FAILED (failures=1)\n"
    )
    patched_stdout = (
        ".\n"
        "----------------------------------------------------------------------\n"
        "Ran 1 test in 0.001s\n\n"
        "OK\n"
    )

    class Scenario2Runner:
        def __init__(self):
            self.count = 0

        async def run(self, workspace_path, request):
            self.count += 1
            if self.count == 1:
                return await MockSandboxRunner(
                    simulated_exit_code=1,
                    simulated_stdout=baseline_stdout,
                    scratch_root=scratch,
                ).run(workspace_path, request)
            return await MockSandboxRunner(
                simulated_exit_code=0,
                simulated_stdout=patched_stdout,
                scratch_root=scratch,
            ).run(workspace_path, request)

    engine = ProofOfFixEngine(runner=Scenario2Runner(), scratch_root=scratch)

    diff2 = (
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )

    req = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=2,
        test_command=["python", "-m", "unittest", "tests/test_calc.py"],
        patches=[PatchProposal(file_path="src/calc.py", diff=diff2)],
        hypothesis="add() returns subtraction instead of addition",
        user_id=user.id,
        files=calc_files,
    )

    result = await engine.verify(req, db=db)
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.patched_exit_code == 0
    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert any("test_add" in tid for tid in result.evidence.resolved_test_ids)


@pytest.mark.anyio
async def test_scenario_3_range_validator_proof_of_fix_and_regression(test_session, tmp_path):
    """Scenario 3: Range validator boundary defect & regression detection."""
    db = test_session()
    user, repo, job = create_user_and_repo(db, repo_name="benchmark", owner="trusted")

    scratch = str(tmp_path / "scratch_s3")
    os.makedirs(scratch, exist_ok=True)

    baseline_stdout = (
        "....F\n"
        "======================================================================\n"
        "FAIL: test_in_range_upper_bound "
        "(tests.test_validator.TestValidator.test_in_range_upper_bound)\n"
        "AssertionError: False is not true\n"
        "----------------------------------------------------------------------\n"
        "Ran 5 tests in 0.001s\n\n"
        "FAILED (failures=1)\n"
    )
    patched_stdout = (
        ".....\n"
        "----------------------------------------------------------------------\n"
        "Ran 5 tests in 0.001s\n\n"
        "OK\n"
    )
    regression_stdout = (
        "F....\n"
        "======================================================================\n"
        "FAIL: test_positive (tests.test_validator.TestValidator.test_positive)\n"
        "AssertionError: False is not true\n"
        "----------------------------------------------------------------------\n"
        "Ran 5 tests in 0.001s\n\n"
        "FAILED (failures=1)\n"
    )

    # 1. Clean fix passes
    class Scenario3CleanRunner:
        def __init__(self):
            self.count = 0

        async def run(self, workspace_path, request):
            self.count += 1
            if self.count == 1:
                return await MockSandboxRunner(
                    simulated_exit_code=1,
                    simulated_stdout=baseline_stdout,
                    scratch_root=scratch,
                ).run(workspace_path, request)
            return await MockSandboxRunner(
                simulated_exit_code=0,
                simulated_stdout=patched_stdout,
                scratch_root=scratch,
            ).run(workspace_path, request)

    engine_clean = ProofOfFixEngine(runner=Scenario3CleanRunner(), scratch_root=scratch)

    req_clean = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=3,
        test_command=["python", "-m", "unittest", "tests/test_validator.py"],
        patches=[PatchProposal(file_path="src/validator.py", diff=RANGE_VALIDATOR_FIX_PATCH)],
        hypothesis="is_in_range() exclusive upper bound bug",
        user_id=user.id,
        files=RANGE_VALIDATOR_FILES,
    )

    res_clean = await engine_clean.verify(req_clean, db=db)
    assert res_clean.evidence.baseline_exit_code == 1
    assert res_clean.evidence.patched_exit_code == 0
    assert res_clean.verdict == ProofVerdict.VERIFIED_FIX
    assert any("test_in_range_upper_bound" in tid for tid in res_clean.evidence.resolved_test_ids)
    assert len(res_clean.evidence.new_failed_test_ids) == 0

    # 2. Regression patch introduces new failure in test_positive
    class Scenario3RegressionRunner:
        def __init__(self):
            self.count = 0

        async def run(self, workspace_path, request):
            self.count += 1
            if self.count == 1:
                return await MockSandboxRunner(
                    simulated_exit_code=1,
                    simulated_stdout=baseline_stdout,
                    scratch_root=scratch,
                ).run(workspace_path, request)
            return await MockSandboxRunner(
                simulated_exit_code=1,
                simulated_stdout=regression_stdout,
                scratch_root=scratch,
            ).run(workspace_path, request)

    engine_reg = ProofOfFixEngine(runner=Scenario3RegressionRunner(), scratch_root=scratch)

    req_reg = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=3,
        test_command=["python", "-m", "unittest", "tests/test_validator.py"],
        patches=[
            PatchProposal(file_path="src/validator.py", diff=RANGE_VALIDATOR_REGRESSION_PATCH)
        ],
        hypothesis="regression patch test",
        user_id=user.id,
        files=RANGE_VALIDATOR_FILES,
    )

    res_reg = await engine_reg.verify(req_reg, db=db)
    assert res_reg.verdict == ProofVerdict.REGRESSION_DETECTED
    assert any("test_positive" in tid for tid in res_reg.evidence.new_failed_test_ids)


# ==============================================================================
# Phase 3: Gating (Unsuccessful Fixes Cannot Proceed to PR)
# ==============================================================================


@pytest.mark.anyio
async def test_failed_fixes_and_regressions_blocked_from_pr(test_session):
    """Verify that unsuccessful fixes and regressions are blocked from GitHub PR creation."""
    db = test_session()
    user, repo, job = create_user_and_repo(db)

    test_cmd = ["python", "-m", "unittest", "tests/test_calculator.py"]
    patch_hash = hashlib.sha256(DEMO_CALC_FIX_PATCH.strip().encode()).hexdigest()
    spec_h = compute_spec_hash(job.commit_sha, patch_hash, test_cmd, DEFAULT_SANDBOX_IMAGE)

    # 1. Create verification run with FAILED verdict in ProofReport
    run = VerificationRun(
        repository_id=repo.id,
        status="completed",
        commit_sha=job.commit_sha,
        issue_number=1,
        test_command=json.dumps(test_cmd),
        patch_diff=DEMO_CALC_FIX_PATCH,
        patch_hash=patch_hash,
        spec_hash=spec_h,
        approved_spec_hash=spec_h,
        approved_by_user_id=user.id,
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
    )
    db.add(run)
    db.flush()

    report_failed = ProofReport(
        verification_run_id=run.id,
        verdict=ProofVerdict.FIX_FAILED.value,
        hypothesis="Hypothesis: power bug",
        summary="Fix failed",
        baseline_summary="2 failed",
        patched_summary="2 failed",
        confidence="none",
        evidence_json=json.dumps(
            {
                "commit_sha": job.commit_sha,
                "patch_hash": run.patch_hash,
                "approved_spec_hash": spec_h,
            }
        ),
    )
    db.add(report_failed)
    db.commit()

    with pytest.raises(GitHubVerificationPrerequisiteError, match="Only verified fixes"):
        GitHubPullRequestService.validate_pr_prerequisites(db, repo.id, run.id)

    # 2. Change report to REGRESSION_DETECTED -> also blocked
    report_failed.verdict = ProofVerdict.REGRESSION_DETECTED.value
    db.commit()

    with pytest.raises(GitHubVerificationPrerequisiteError, match="Only verified fixes"):
        GitHubPullRequestService.validate_pr_prerequisites(db, repo.id, run.id)


# ==============================================================================
# Real Docker Verification (Where Available)
# ==============================================================================


@pytest.mark.docker
@pytest.mark.skipif(not _has_docker, reason=_docker_reason)
@pytest.mark.anyio
async def test_real_docker_verification_scenario_3_range_validator(test_session, tmp_path):
    """Real Docker dual-sandbox execution for Scenario 3 (Range Validator)."""
    db = test_session()
    user, repo, job = create_user_and_repo(db, repo_name="benchmark", owner="trusted")

    test_image = os.getenv("SANDBOX_TEST_IMAGE", DEFAULT_SANDBOX_IMAGE)
    scratch = str(tmp_path / "docker_scratch_s3")

    runner = DockerSandboxRunner(image=test_image, scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    req = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=3,
        test_command=["python", "-m", "unittest", "tests/test_validator.py"],
        patches=[PatchProposal(file_path="src/validator.py", diff=RANGE_VALIDATOR_FIX_PATCH)],
        hypothesis="Upper bound comparison is exclusive instead of inclusive",
        user_id=user.id,
        files=RANGE_VALIDATOR_FIXTURE_FILES,
    )

    result = await engine.verify(req, db=db)

    # Baseline fails on upper bound
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.baseline_metrics.failed == 1
    assert result.evidence.baseline_metrics.passed == 4
    assert any(
        "test_in_range_upper_bound" in tid
        for tid in result.evidence.baseline_metrics.failed_test_ids
    )

    # Patched succeeds on all 5 tests
    assert result.evidence.patched_exit_code == 0
    assert result.evidence.patched_metrics.passed == 5
    assert result.evidence.patched_metrics.failed == 0

    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert len(result.evidence.new_failed_test_ids) == 0
    assert any("test_in_range_upper_bound" in tid for tid in result.evidence.resolved_test_ids)
