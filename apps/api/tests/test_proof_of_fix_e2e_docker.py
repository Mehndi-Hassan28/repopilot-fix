"""End-to-End Proof-of-Fix Integration and Docker Sandbox Verification Tests.

Validates the full 8-stage production lifecycle:
1. Repository Import & Indexing
2. Issue Investigation & Root-Cause Analysis
3. Patch Generation & Distinguishing Mocked vs. Real AI
4. Human Approval Workflow & Immutable Spec Hashing
5. Isolated Dual-Sandbox Execution (Baseline Failure & Patched Success)
6. Cryptographic Evidence Report Generation & Revision Binding
7. Gating: Unsuccessful or Tampered Fixes Strictly Blocked from PR Creation
8. Secure GitHub PR Creation with Mocked External Writes
"""

import hashlib
import json
import os
import shutil
import subprocess

import httpx
import pytest
from sqlalchemy import select

from repopilot_api.chat import MockProvider, parse_and_validate_analysis
from repopilot_api.github_pr_service import (
    GitHubPullRequestService,
    GitHubVerificationPrerequisiteError,
)
from repopilot_api.models import (
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
    DockerSandboxRunner,
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
from repopilot_api.verification_service import (
    VerificationJobManager,
    compute_spec_hash,
)
from tests.fixtures.sandbox_fixtures import (
    DEMO_CALC_FAILED_PATCH,
    DEMO_CALC_FIX_PATCH,
    DEMO_CALC_REGRESSION_PATCH,
    DEMO_FIXTURE_FILES,
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


def create_user_and_repo(
    db, owner="repopilot", name="demo-repo", commit_sha=SAMPLE_COMMIT_SHA
):
    """Seed authenticated user, repository, and completed indexing job with demo fixture files."""
    user = User()
    db.add(user)
    db.flush()

    repo = Repository(
        github_id=987654,
        owner=owner,
        name=name,
        full_name=f"{owner}/{name}",
        url=f"https://github.com/{owner}/{name}",
        default_branch="main",
        stars=15,
    )
    db.add(repo)
    db.flush()

    access = RepositoryAccess(repository_id=repo.id, user_id=user.id, role="owner")
    db.add(access)

    job = IndexingJob(
        repository_id=repo.id,
        commit_sha=commit_sha,
        status="completed",
        file_count=len(DEMO_FIXTURE_FILES),
    )
    db.add(job)
    db.flush()

    for path, content in DEMO_FIXTURE_FILES.items():
        indexed = IndexedFile(
            job_id=job.id,
            path=path,
            language="python",
            size_bytes=len(content.encode("utf-8")),
            content=content,
        )
        db.add(indexed)

    db.commit()
    db.refresh(user)
    db.refresh(repo)
    db.refresh(job)
    return user, repo, job


class MockGitHubTransport(httpx.BaseTransport):
    """Strictly mocks all GitHub API read and write operations."""

    def __init__(self):
        self.recorded_requests: list[httpx.Request] = []
        self.created_prs: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.recorded_requests.append(request)
        url_str = str(request.url)
        method = request.method.upper()

        # 1. Base branch resolution
        if method == "GET" and "/git/ref/heads/main" in url_str:
            return httpx.Response(200, json={"object": {"sha": SAMPLE_COMMIT_SHA}})

        # 2. Query branch ref
        if method == "GET" and "/git/ref/heads/repopilot/fix-" in url_str:
            return httpx.Response(404, json={"message": "Not Found"})

        # 3. Create branch ref
        if method == "POST" and "/git/refs" in url_str:
            return httpx.Response(201, json={"object": {"sha": SAMPLE_COMMIT_SHA}})

        # 4. Read file content
        if method == "GET" and "/contents/" in url_str:
            import base64

            raw = DEMO_FIXTURE_FILES.get("src/calculator.py", "")
            b64_content = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
            return httpx.Response(200, json={"content": b64_content, "encoding": "base64"})

        # 5. Create git blob
        if method == "POST" and "/git/blobs" in url_str:
            return httpx.Response(201, json={"sha": "mock_blob_sha_" + "1" * 30})

        # 6. Create git tree
        if method == "POST" and "/git/trees" in url_str:
            return httpx.Response(201, json={"sha": "mock_tree_sha_" + "2" * 30})

        # 7. Create git commit
        if method == "POST" and "/git/commits" in url_str:
            return httpx.Response(201, json={"sha": "mock_commit_sha_" + "3" * 30})

        # 8. Update branch ref
        if method == "PATCH" and ("/git/refs/heads/" in url_str or "/git/ref/heads/" in url_str):
            return httpx.Response(200, json={"object": {"sha": "mock_commit_sha_" + "3" * 30}})

        # 9. Query existing PRs
        if method == "GET" and "/pulls" in url_str:
            return httpx.Response(200, json=self.created_prs)

        # 10. Create pull request
        if method == "POST" and "/pulls" in url_str:
            pr_data = {
                "number": 101,
                "html_url": "https://github.com/repopilot/demo-repo/pull/101",
                "title": "Fix power multiplication bug in src/calculator.py",
                "state": "open",
            }
            self.created_prs.append(pr_data)
            return httpx.Response(201, json=pr_data)

        return httpx.Response(404, json={"message": f"Unhandled mock route: {method} {url_str}"})


# ==============================================================================
# Unit & Integration Tests
# ==============================================================================


class SequenceRunner(MockSandboxRunner):
    """Sequential mock runner that returns predetermined results for call 1 and call 2."""

    def __init__(
        self,
        steps: list[tuple[int, str, str]],
        scratch_root: str,
        image: str = DEFAULT_SANDBOX_IMAGE,
    ):
        super().__init__(scratch_root=scratch_root, image=image)
        self.steps = steps
        self.call_count = 0

    async def run(self, workspace_path, request):
        idx = min(self.call_count, len(self.steps) - 1)
        self.call_count += 1
        exit_code, stdout, stderr = self.steps[idx]
        state = SandboxExecutionState.PASSED if exit_code == 0 else SandboxExecutionState.FAILED
        return await MockSandboxRunner(
            simulated_state=state,
            simulated_exit_code=exit_code,
            simulated_stdout=stdout,
            simulated_stderr=stderr,
            scratch_root=self.scratch_root,
            image=self.image,
        ).run(workspace_path, request)


@pytest.mark.docker
@pytest.mark.skipif(not _has_docker, reason=_docker_reason)
@pytest.mark.anyio
async def test_demo_fixture_baseline_failure_and_patched_success_docker(test_session, tmp_path):
    """Objective 4 & 5: Isolated Docker baseline failure and patched success."""
    db = test_session()
    user, repo, job = create_user_and_repo(db)

    # Pinned image and scratch
    test_image = os.getenv("SANDBOX_TEST_IMAGE", DEFAULT_SANDBOX_IMAGE)
    scratch = str(tmp_path / "docker_scratch")

    runner = DockerSandboxRunner(image=test_image, scratch_root=scratch)
    engine = ProofOfFixEngine(runner=runner, scratch_root=scratch)

    test_cmd = ["python", "-m", "unittest", "tests/test_calculator.py"]
    patch_prop = PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)

    req = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=1,
        test_command=test_cmd,
        patches=[patch_prop],
        hypothesis="power() incorrectly calculates base * exponent instead of base ** exponent",
        user_id=user.id,
        files=DEMO_FIXTURE_FILES,
    )

    result = await engine.verify(req, db=db)

    # 1. Verify baseline failed with exit code 1
    assert result.evidence.baseline_exit_code == 1
    assert result.evidence.baseline_failure_type == "assertion_failure"
    assert result.evidence.baseline_metrics is not None
    assert result.evidence.baseline_metrics.failed == 2
    failed_tids = result.evidence.baseline_metrics.failed_test_ids
    assert any("test_power_positive" in tid for tid in failed_tids)
    assert any("test_power_zero" in tid for tid in failed_tids)
    # Regression tests passed in baseline
    assert result.evidence.baseline_metrics.passed == 4

    # 2. Verify patched succeeded with exit code 0
    assert result.evidence.patched_exit_code == 0
    assert result.evidence.patched_failure_type == "none"
    assert result.evidence.patched_metrics is not None
    assert result.evidence.patched_metrics.passed == 6
    assert result.evidence.patched_metrics.failed == 0

    # 3. Verify verdict
    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert result.confidence == "high"
    assert any("test_power_positive" in tid for tid in result.evidence.resolved_test_ids)
    assert len(result.evidence.new_failed_test_ids) == 0

    # 4. Verify cryptographic evidence binding
    assert result.evidence.commit_sha == job.commit_sha
    expected_patch_hash = hashlib.sha256(DEMO_CALC_FIX_PATCH.strip().encode("utf-8")).hexdigest()
    assert result.evidence.patch_hash == expected_patch_hash


@pytest.mark.anyio
async def test_unsuccessful_fixes_blocked_from_pr_creation(test_session, tmp_path):
    """Objective 6 & 7: Verify unsuccessful fixes and tampered evidence are blocked."""
    db = test_session()
    user, repo, job = create_user_and_repo(db)

    scratch = str(tmp_path / "mock_scratch")
    test_cmd = ["python", "-m", "unittest", "tests/test_calculator.py"]

    # Case 1: Baseline passes -> UNREPRODUCED -> PR creation blocked
    unrep_runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.PASSED,
        simulated_exit_code=0,
        simulated_stdout="Ran 6 tests in 0.01s\n\nOK\n",
        scratch_root=scratch,
        image=DEFAULT_SANDBOX_IMAGE,
    )
    engine_unrep = ProofOfFixEngine(runner=unrep_runner, scratch_root=scratch)

    req_unreproduced = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=2,
        test_command=test_cmd,
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)],
        user_id=user.id,
        files=DEMO_FIXTURE_FILES,
    )
    res_unreproduced = await engine_unrep.verify(req_unreproduced, db=db)
    assert res_unreproduced.verdict == ProofVerdict.UNREPRODUCED

    # Fetch created verification run and attempt PR creation
    run_unrep = db.scalar(
        select(VerificationRun).where(VerificationRun.id == res_unreproduced.verification_run_id)
    )
    run_unrep.status = VerificationJobStatus.COMPLETED.value
    run_unrep.approved_by_user_id = user.id
    run_unrep.approved_spec_hash = compute_spec_hash(
        run_unrep.commit_sha, run_unrep.patch_hash, test_cmd, run_unrep.sandbox_image
    )
    db.commit()

    with pytest.raises(GitHubVerificationPrerequisiteError) as exc_info:
        GitHubPullRequestService.create_pull_request(
            db=db,
            repository_id=repo.id,
            verification_id=run_unrep.id,
            user_id=user.id,
            payload=PullRequestCreateRequest(github_token="fake_token"),
        )
    assert "Only verified fixes ('verified_fix') qualify for PR creation" in str(exc_info.value)

    # Case 2: Fix Failed (patched exit code != 0) -> FIX_FAILED -> PR creation blocked
    failed_runner = SequenceRunner(
        steps=[
            (1, "FAIL: test_power_positive\nAssertionError: 6 != 8\nFAILED (failures=1)\n", ""),
            (1, "FAIL: test_power_positive\nAssertionError: 5 != 8\nFAILED (failures=1)\n", ""),
        ],
        scratch_root=scratch,
    )
    engine_failed = ProofOfFixEngine(runner=failed_runner, scratch_root=scratch)
    req_failed = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=3,
        test_command=test_cmd,
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FAILED_PATCH)],
        user_id=user.id,
        files=DEMO_FIXTURE_FILES,
    )
    res_failed = await engine_failed.verify(req_failed, db=db)
    assert res_failed.verdict == ProofVerdict.FIX_FAILED

    run_failed = db.scalar(
        select(VerificationRun).where(VerificationRun.id == res_failed.verification_run_id)
    )
    run_failed.status = VerificationJobStatus.COMPLETED.value
    run_failed.approved_by_user_id = user.id
    run_failed.approved_spec_hash = compute_spec_hash(
        run_failed.commit_sha, run_failed.patch_hash, test_cmd, run_failed.sandbox_image
    )
    db.commit()

    with pytest.raises(GitHubVerificationPrerequisiteError) as exc_info:
        GitHubPullRequestService.create_pull_request(
            db=db,
            repository_id=repo.id,
            verification_id=run_failed.id,
            user_id=user.id,
            payload=PullRequestCreateRequest(github_token="fake_token"),
        )
    assert "ProofReport verdict is 'fix_failed'" in str(exc_info.value)

    # Case 3: Regression Detected (failures increase) -> REGRESSION_DETECTED -> PR creation blocked
    reg_runner = SequenceRunner(
        steps=[
            (1, "FAIL: test_power_positive\nAssertionError: 6 != 8\nFAILED (failures=1)\n", ""),
            (
                1,
                "FAIL: test_multiply\nAssertionError: 7 != 12\n"
                "FAIL: test_power_positive\nFAILED (failures=2)\n",
                "",
            ),
        ],
        scratch_root=scratch,
    )
    engine_reg = ProofOfFixEngine(runner=reg_runner, scratch_root=scratch)
    req_reg = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=4,
        test_command=test_cmd,
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_REGRESSION_PATCH)],
        user_id=user.id,
        files=DEMO_FIXTURE_FILES,
    )
    res_reg = await engine_reg.verify(req_reg, db=db)
    assert res_reg.verdict == ProofVerdict.REGRESSION_DETECTED

    run_reg = db.scalar(
        select(VerificationRun).where(VerificationRun.id == res_reg.verification_run_id)
    )
    run_reg.status = VerificationJobStatus.COMPLETED.value
    run_reg.approved_by_user_id = user.id
    run_reg.approved_spec_hash = compute_spec_hash(
        run_reg.commit_sha, run_reg.patch_hash, test_cmd, run_reg.sandbox_image
    )
    db.commit()

    with pytest.raises(GitHubVerificationPrerequisiteError) as exc_info:
        GitHubPullRequestService.create_pull_request(
            db=db,
            repository_id=repo.id,
            verification_id=run_reg.id,
            user_id=user.id,
            payload=PullRequestCreateRequest(github_token="fake_token"),
        )
    assert "regression_detected" in str(exc_info.value)

    # Case 4: Evidence Tampering Check (evidence_json commit_sha does not match run)
    tamper_runner = SequenceRunner(
        steps=[
            (1, "FAIL: test_power_positive\nFAILED (failures=1)\n", ""),
            (0, "Ran 6 tests in 0.01s\n\nOK\n", ""),
        ],
        scratch_root=scratch,
    )
    engine_tamper = ProofOfFixEngine(runner=tamper_runner, scratch_root=scratch)
    req_tamper = ProofOfFixRequest(
        repository_id=repo.id,
        commit_sha=job.commit_sha,
        issue_number=5,
        test_command=test_cmd,
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)],
        user_id=user.id,
        files=DEMO_FIXTURE_FILES,
    )
    res_tamper = await engine_tamper.verify(req_tamper, db=db)
    assert res_tamper.verdict == ProofVerdict.VERIFIED_FIX

    run_tamper = db.scalar(
        select(VerificationRun).where(VerificationRun.id == res_tamper.verification_run_id)
    )
    run_tamper.status = VerificationJobStatus.COMPLETED.value
    run_tamper.approved_by_user_id = user.id
    run_tamper.approved_spec_hash = compute_spec_hash(
        run_tamper.commit_sha, run_tamper.patch_hash, test_cmd, run_tamper.sandbox_image
    )

    report_tamper = db.scalar(
        select(ProofReport).where(ProofReport.verification_run_id == run_tamper.id)
    )
    # Tamper evidence commit_sha
    tampered_ev = json.loads(report_tamper.evidence_json)
    tampered_ev["commit_sha"] = "b" * 40
    report_tamper.evidence_json = json.dumps(tampered_ev)
    db.commit()

    with pytest.raises(GitHubVerificationPrerequisiteError) as exc_info:
        GitHubPullRequestService.create_pull_request(
            db=db,
            repository_id=repo.id,
            verification_id=run_tamper.id,
            user_id=user.id,
            payload=PullRequestCreateRequest(github_token="fake_token"),
        )
    assert "Evidence mismatch: report commit" in str(exc_info.value)


@pytest.mark.anyio
async def test_complete_end_to_end_workflow_integration(test_session, tmp_path):
    """Objective 1, 3, 7, 8: Complete 8-stage workflow from repo import to mock PR creation."""
    from repopilot_api.chat import Evidence

    db = test_session()
    scratch = str(tmp_path / "e2e_scratch")

    # 1. Repository Import & Indexing
    user, repo, job = create_user_and_repo(db)
    assert repo.id is not None
    assert job.status == "completed"

    # 2. Issue Investigation & Root-Cause Analysis (Deterministic MockProvider)
    mock_provider = MockProvider()
    issue = {
        "number": 10,
        "title": "Bug in power calculation: multiplication returned instead of power",
        "body": "When calling power(2, 3), result is 6 instead of 8. Suspect operator defect.",
    }
    demo_ev = [
        Evidence(
            path="src/calculator.py",
            start_line=18,
            end_line=21,
            content="def power(base: int, exponent: int) -> int:\n    return base * exponent\n",
            score=0.98,
        )
    ]
    raw_analysis = mock_provider.analyze_issue(issue, demo_ev)

    # 3. Distinguishing Mocked vs. Real AI responses
    analysis_data = json.loads(raw_analysis)
    assert analysis_data.get("is_mock") is True
    assert analysis_data.get("provider") == "mock"
    assert "Mock AI execution" in analysis_data["limitations"][0]

    indexed_map = {path: content for path, content in DEMO_FIXTURE_FILES.items()}
    # Pass mock provider metadata
    parsed_analysis = parse_and_validate_analysis(
        raw_analysis,
        demo_ev,
        indexed_map,
        provider_name="mock",
        is_mock=True,
    )
    assert parsed_analysis.is_mock is True
    assert parsed_analysis.provider == "mock"
    assert len(parsed_analysis.proposed_fix.patches) > 0

    # 4. Human Approval Workflow
    test_cmd = ["python", "-m", "unittest", "tests/test_calculator.py"]
    create_req = VerificationCreateRequest(
        commit_sha=job.commit_sha,
        issue_number=10,
        test_command=test_cmd,
        patches=[PatchProposal(file_path="src/calculator.py", diff=DEMO_CALC_FIX_PATCH)],
        hypothesis=parsed_analysis.root_cause_hypothesis,
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

    # Human explicitly approves specification
    approved_run = VerificationJobManager.approve_verification(
        db=db,
        run_id=run.id,
        user_id=user.id,
        expected_spec_hash=run.spec_hash,
        auto_queue=True,
    )
    assert approved_run.status == VerificationJobStatus.QUEUED.value
    assert approved_run.approved_spec_hash == run.spec_hash
    assert approved_run.approved_by_user_id == user.id

    # 5. Dual-Sandbox Verification Execution (Simulated with SequenceRunner)
    mock_runner = SequenceRunner(
        steps=[
            # Baseline fails
            (
                1,
                "FAIL: test_power_positive\n"
                "AssertionError: 6 != 8\nFAILED (failures=1)\n",
                "",
            ),
            # Patched succeeds
            (0, "Ran 6 tests in 0.012s\n\nOK\n", ""),
        ],
        scratch_root=scratch,
    )

    # Allow mock runner in execute_job
    os.environ["ALLOW_DOCKER_VERIFICATION_EXECUTION"] = "true"
    result = await VerificationJobManager.execute_job(
        db=db,
        run_id=approved_run.id,
        runner=mock_runner,
        scratch_root=scratch,
        user_id=user.id,
    )

    assert result.verdict == ProofVerdict.VERIFIED_FIX
    assert approved_run.status == VerificationJobStatus.COMPLETED.value

    # 6. Cryptographic Evidence Report Generation
    report = db.scalar(
        select(ProofReport).where(ProofReport.verification_run_id == approved_run.id)
    )
    assert report is not None
    assert report.verdict == ProofVerdict.VERIFIED_FIX.value

    ev_dict = json.loads(report.evidence_json)
    assert ev_dict["commit_sha"] == job.commit_sha
    assert ev_dict["patch_hash"] == approved_run.patch_hash
    assert ev_dict["approved_spec_hash"] == approved_run.approved_spec_hash

    # 7. Secure GitHub PR Creation with Mocked External Writes
    mock_transport = MockGitHubTransport()
    mock_http_client = httpx.Client(transport=mock_transport)

    pr_resp = GitHubPullRequestService.create_pull_request(
        db=db,
        repository_id=repo.id,
        verification_id=approved_run.id,
        user_id=user.id,
        payload=PullRequestCreateRequest(
            github_token="ghp_test_mock_token_12345",
            custom_title="fix(calculator): resolve power operator multiplication bug",
        ),
        client=mock_http_client,
    )

    assert pr_resp.success is True
    assert pr_resp.pr_number == 101
    assert "https://github.com/repopilot/demo-repo/pull/101" in pr_resp.pr_url
    assert pr_resp.status == "created"

    # Verify PR record saved in database
    db_pr = db.scalar(
        select(GitHubPullRequest).where(
            GitHubPullRequest.verification_run_id == approved_run.id
        )
    )
    assert db_pr is not None
    assert db_pr.pr_number == 101
    assert db_pr.patch_hash == approved_run.patch_hash

    # Idempotency: Second call returns existing PR record without re-calling GitHub
    pr_idempotent = GitHubPullRequestService.create_pull_request(
        db=db,
        repository_id=repo.id,
        verification_id=approved_run.id,
        user_id=user.id,
        payload=PullRequestCreateRequest(github_token="ghp_test_mock_token_12345"),
        client=mock_http_client,
    )
    assert pr_idempotent.success is True
    assert pr_idempotent.pr_number == 101
    assert pr_idempotent.status == "already_exists"
