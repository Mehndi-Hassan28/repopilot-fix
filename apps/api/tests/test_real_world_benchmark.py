"""Automated tests for Real-World AI Repair Benchmark dataset, evaluator, and integrity."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from repopilot_api.benchmarks.dataset import (
    BENCHMARK_BY_ID,
    BENCHMARK_ISSUES,
    REQUIRED_CATEGORIES,
)
from repopilot_api.benchmarks.evaluator import (
    BenchmarkCaseResult,
    BenchmarkEvaluator,
    BenchmarkSecurityError,
)
from repopilot_api.benchmarks.run_benchmark import aggregate_metrics
from repopilot_api.chat import (
    Evidence,
    MockProvider,
    normalize_patch_diff,
    parse_and_validate_analysis,
    recount_and_align_hunks,
)
from repopilot_api.patcher import apply_unified_diff
from repopilot_api.schemas import PatchProposal
from repopilot_api.verification_service import compute_patch_hash, compute_spec_hash


def test_benchmark_dataset_integrity():
    """Verify all 20 curated benchmark issues meet strict specification requirements."""
    assert len(BENCHMARK_ISSUES) == 20, f"Expected exactly 20 issues, got {len(BENCHMARK_ISSUES)}"

    ids = [b.id for b in BENCHMARK_ISSUES]
    assert len(ids) == len(set(ids)), "Benchmark issue IDs must be unique"
    for i in range(1, 21):
        expected_id = f"BM-{i:02d}"
        assert expected_id in ids, f"Missing required issue ID {expected_id}"

    # Verify all categories covered
    present_categories = {b.category for b in BENCHMARK_ISSUES}
    for req_cat in REQUIRED_CATEGORIES:
        assert req_cat in present_categories, f"Category '{req_cat}' missing from dataset"

    # Verify metadata fields and provenance classification
    for issue in BENCHMARK_ISSUES:
        assert issue.repo_name and "/" in issue.repo_name
        assert issue.repo_url.startswith("https://github.com/")
        assert issue.pattern_ref and len(issue.pattern_ref) > 3
        # Fake upstream commit SHAs and misleading issue IDs have been removed
        assert issue.commit_sha is None
        assert issue.issue_id is None
        assert issue.license in ("MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "PSF-2.0")
        assert issue.target_file in issue.files
        assert issue.test_command
        assert issue.expected_baseline_error
        assert issue.reference_patch
        assert issue.provenance in (
            "defect_pattern_fixture",
            "minimized_reproduction",
            "synthetic_fixture",
            "original_historical",
        )
        # All 20 cases in this benchmark are accurately classified as defect_pattern_fixture
        assert issue.provenance == "defect_pattern_fixture"


def test_benchmark_baseline_and_reference_patches():
    """Verify that baseline fails and reference patch succeeds on a sample of issues."""
    sample_ids = ["BM-01", "BM-02", "BM-07", "BM-09", "BM-15"]
    for iid in sample_ids:
        issue = BENCHMARK_BY_ID[iid]
        with tempfile.TemporaryDirectory() as tmp_base, tempfile.TemporaryDirectory() as tmp_patch:
            # Setup baseline
            for path, content in issue.files.items():
                p = os.path.join(tmp_base, path)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(content)

            # Baseline must fail
            r_base = subprocess.run(
                issue.test_command,
                shell=True,
                cwd=tmp_base,
                capture_output=True,
                text=True,
            )
            assert r_base.returncode != 0, f"Baseline for {iid} should fail: {r_base.stdout}"
            assert issue.expected_baseline_error in (r_base.stderr + r_base.stdout)

            # Setup patched
            for path, content in issue.files.items():
                p = os.path.join(tmp_patch, path)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(content)

            # Apply reference patch
            patched_content, _, _, _ = apply_unified_diff(
                issue.files[issue.target_file], issue.reference_patch
            )
            with open(os.path.join(tmp_patch, issue.target_file), "w") as f:
                f.write(patched_content)

            # Patched must succeed
            r_patch = subprocess.run(
                issue.test_command,
                shell=True,
                cwd=tmp_patch,
                capture_output=True,
                text=True,
            )
            assert r_patch.returncode == 0, f"Patched {iid} should pass: {r_patch.stderr}"


def test_hunk_recount_and_alignment():
    """Verify that LLM hunk header discrepancies are safely corrected."""
    target_content = (
        "def compute(x):\n"
        "    # Line 2\n"
        "    # Line 3\n"
        "    if x > 10:\n"
        "        return x * 2\n"
        "    return x\n"
    )

    # LLM declared line 2 instead of 4, and declared 4 lines instead of 2
    miscounted_diff = (
        "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -2,4 +2,4 @@\n-    if x > 10:\n+    if x >= 10:\n"
    )

    corrected = recount_and_align_hunks(miscounted_diff, file_content=target_content)
    assert "@@ -4,1 +4,1 @@" in corrected

    # Should now apply cleanly
    patched, hunks, added, deleted = apply_unified_diff(target_content, corrected)
    assert hunks == 1
    assert added == 1
    assert deleted == 1
    assert "if x >= 10:" in patched


def test_evaluator_pipeline_with_mock_provider():
    """Verify full evaluation harness end-to-end execution with MockProvider."""
    issue = BENCHMARK_BY_ID["BM-01"]
    mock_provider = MockProvider()
    evaluator = BenchmarkEvaluator(
        provider=mock_provider,
        rate_limit_delay_seconds=0.0,
        use_docker=False,
        human_approved=True,
        allow_insecure_host_execution=True,  # Explicit opt-in for unit test execution
    )

    result = evaluator.evaluate_issue(issue)
    assert isinstance(result, BenchmarkCaseResult)
    assert result.issue_id == "BM-01"
    assert result.provider_mode == "mock_ai"
    assert result.retrieval_recall_at_5 == 1.0
    assert result.retrieval_mrr == 1.0
    assert result.baseline_reproduced is True
    assert result.verdict in (
        "VERIFIED_FIX",
        "FIX_FAILED",
        "PATCH_SYNTHESIS_FAILED",
        "PATCH_APPLICATION_ERROR",
        "REGRESSION_DETECTED",
    )


def test_evaluator_rejects_host_execution_by_default():
    """Verify that arbitrary host subprocess execution of AI patches is strictly blocked."""
    issue = BENCHMARK_BY_ID["BM-01"]
    mock_provider = MockProvider()
    evaluator = BenchmarkEvaluator(
        provider=mock_provider,
        rate_limit_delay_seconds=0.0,
        use_docker=False,
        allow_insecure_host_execution=False,
    )
    with pytest.raises(BenchmarkSecurityError) as exc_info:
        evaluator.evaluate_issue(issue)
    assert "Host subprocess execution of AI-generated patches is disabled" in str(exc_info.value)


def test_evaluator_rejects_unapproved_execution():
    """Verify that benchmark verification rejects unapproved patch execution."""
    issue = BENCHMARK_BY_ID["BM-01"]
    mock_provider = MockProvider()
    evaluator = BenchmarkEvaluator(
        provider=mock_provider,
        rate_limit_delay_seconds=0.0,
        use_docker=True,
        human_approved=False,
    )
    with pytest.raises(BenchmarkSecurityError) as exc_info:
        evaluator.evaluate_issue(issue)
    assert "requires explicit human approval" in str(exc_info.value)


def test_aggregate_metrics_calculation():
    """Verify that metric denominators and statistical calculations are accurate."""
    cases = [
        BenchmarkCaseResult(
            issue_id="BM-01",
            repo_name="pallets/click",
            category="boundary_condition",
            provider_mode="live_ai",
            model="test-model",
            root_cause_identified=True,
            citation_correctness=True,
            retrieval_recall_at_5=1.0,
            retrieval_mrr=1.0,
            patch_applicable=True,
            baseline_reproduced=True,
            verdict="VERIFIED_FIX",
            latency_ms=1200,
            prompt_tokens=500,
            completion_tokens=150,
            total_tokens=650,
            execution_duration_s=2.5,
            is_multi_file=False,
            generated_diff="--- a/file\n+++ b/file",
            citation_boundary_valid=True,
            semantic_citation_correct=True,
            execution_backend="docker",
            provenance="defect_pattern_fixture",
        ),
        BenchmarkCaseResult(
            issue_id="BM-02",
            repo_name="bottlepy/bottle",
            category="logic_error",
            provider_mode="live_ai",
            model="test-model",
            root_cause_identified=True,
            citation_correctness=False,
            retrieval_recall_at_5=1.0,
            retrieval_mrr=0.5,
            patch_applicable=False,
            baseline_reproduced=True,
            verdict="PATCH_APPLICATION_ERROR",
            latency_ms=1800,
            prompt_tokens=600,
            completion_tokens=200,
            total_tokens=800,
            execution_duration_s=3.0,
            is_multi_file=True,
            generated_diff="",
            citation_boundary_valid=False,
            semantic_citation_correct=False,
            execution_backend="docker",
            provenance="defect_pattern_fixture",
        ),
    ]

    metrics = aggregate_metrics(cases)
    assert metrics["total_evaluated"] == 2
    assert metrics["counts"]["verified_fixes"] == 1
    assert metrics["counts"]["patches_applicable"] == 1
    assert metrics["rates"]["verified_repair_rate"] == 0.5
    assert metrics["rates"]["patch_applicability_rate"] == 0.5
    assert metrics["rates"]["baseline_reproduction_rate"] == 1.0
    assert metrics["rates"]["root_cause_accuracy"] == 1.0
    assert metrics["rates"]["citation_boundary_validity_rate"] == 0.5
    assert metrics["rates"]["semantic_citation_accuracy"] == 0.5
    assert metrics["rates"]["observed_regression_incidence"] == 0.0
    assert metrics["rates"]["observed_regression_denominator"] == "applicable_patches"
    assert metrics["provenance"]["defect_pattern_fixtures"] == 2
    assert metrics["performance"]["total_tokens"] == 1450
    assert metrics["performance"]["avg_tokens_per_issue"] == 725.0

    # Test regression denominator calculation:
    # regression incidence must be regressions / applicable_patches
    cases_with_reg = [
        BenchmarkCaseResult(
            issue_id="BM-01",
            repo_name="pallets/click",
            category="boundary_condition",
            provider_mode="live_ai",
            model="test-model",
            root_cause_identified=True,
            citation_correctness=True,
            retrieval_recall_at_5=1.0,
            retrieval_mrr=1.0,
            patch_applicable=True,
            baseline_reproduced=True,
            verdict="REGRESSION_DETECTED",
            latency_ms=1200,
            prompt_tokens=500,
            completion_tokens=150,
            total_tokens=650,
            execution_duration_s=2.5,
            is_multi_file=False,
            generated_diff="--- a/file\n+++ b/file",
            citation_boundary_valid=True,
            semantic_citation_correct=True,
            execution_backend="docker",
            provenance="defect_pattern_fixture",
        ),
        BenchmarkCaseResult(
            issue_id="BM-02",
            repo_name="bottlepy/bottle",
            category="logic_error",
            provider_mode="live_ai",
            model="test-model",
            root_cause_identified=True,
            citation_correctness=False,
            retrieval_recall_at_5=1.0,
            retrieval_mrr=0.5,
            patch_applicable=False,
            baseline_reproduced=True,
            verdict="PATCH_APPLICATION_ERROR",
            latency_ms=1800,
            prompt_tokens=600,
            completion_tokens=200,
            total_tokens=800,
            execution_duration_s=3.0,
            is_multi_file=True,
            generated_diff="",
            citation_boundary_valid=False,
            semantic_citation_correct=False,
            execution_backend="docker",
            provenance="defect_pattern_fixture",
        ),
    ]
    metrics_reg = aggregate_metrics(cases_with_reg)
    # 1 regression out of 1 applicable patch = 1.0 (100%), NOT 0.5 (1/2)
    assert metrics_reg["rates"]["observed_regression_incidence"] == 1.0


def test_manifest_sequestration_and_sanitization():
    """Verify that manifest does not expose secret keys or unprompted solutions."""
    manifest_path = Path("docs/evaluations/real_world_benchmark_manifest.json")
    if not manifest_path.exists():
        pytest.skip("Manifest not generated yet")

    with open(manifest_path) as f:
        data = json.load(f)

    assert "benchmark_version" in data or "version" in data
    assert "metrics" in data or "categories" in data
    assert "cases" in data or "issues" in data

    raw_manifest = json.dumps(data)
    assert "AI_GATEWAY_API_KEY" not in raw_manifest
    assert "GEMINI_API_KEY" not in raw_manifest
    assert not re.search(r"\bsk-[a-zA-Z0-9]{10,}", raw_manifest)

    # Verify metrics transparency and terminology
    rates = data["metrics"]["rates"]
    assert "observed_regression_incidence" in rates
    assert rates["observed_regression_denominator"] == "applicable_executed_patches"
    assert "citation_boundary_validity_rate" in rates
    assert "semantic_citation_accuracy" in rates
    assert "regression_detection_effectiveness" in rates
    assert data["metrics"]["dataset_provenance"]["defect_pattern_fixtures"] == 20
    assert data["metrics"]["dataset_provenance"]["original_historical_checkouts"] == 0
    assert "historical_baseline_disclosure" in data


def test_regression_detection_effectiveness_with_known_regressions():
    """Verify that intentionally regressive patches are reliably detected by the test runner."""
    # Test case 1: In BM-01 (pallets/click), apply a patch that fixes max_val but regresses min_val
    issue = BENCHMARK_BY_ID["BM-01"]
    regressive_patch = normalize_patch_diff(
        (
            "--- a/src/click_types.py\n"
            "+++ b/src/click_types.py\n"
            "@@ -11,5 +11,5 @@\n"
            "-        if self.min_val is not None and value < self.min_val:\n"
            "-            raise BadParameter(f'{value} is smaller than minimum {self.min_val}')\n"
            "-        # Bug: strictly less-than rejects the upper bound itself\n"
            "-        if self.max_val is not None and value >= self.max_val:\n"
            "-            raise BadParameter("
            "f'{value} is not smaller than maximum {self.max_val}')\n"
            "+        if self.min_val is not None and value <= self.min_val:\n"
            "+            raise BadParameter(f'{value} is smaller than minimum {self.min_val}')\n"
            "+        if self.max_val is not None and value > self.max_val:\n"
            "+            raise BadParameter(f'{value} is larger than maximum {self.max_val}')\n"
        ),
        "src/click_types.py",
        file_content=issue.files[issue.target_file],
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        for path, content in issue.files.items():
            p = os.path.join(tmp_dir, path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(content)

        patched_content, _, _, _ = apply_unified_diff(
            issue.files[issue.target_file], regressive_patch
        )
        with open(os.path.join(tmp_dir, issue.target_file), "w") as f:
            f.write(patched_content)

        r = subprocess.run(
            issue.test_command, shell=True, cwd=tmp_dir, capture_output=True, text=True
        )
        # Test must fail
        assert r.returncode != 0
        # The failure is NOT the baseline error, so it constitutes a REGRESSION_DETECTED
        combined_err = r.stderr + r.stdout
        assert issue.expected_baseline_error not in combined_err, "Baseline error should be fixed"
        assert "is smaller than minimum" in combined_err

    # Test case 2: In BM-08 (pallets/jinja), apply a patch that drops matching items
    issue_jinja = BENCHMARK_BY_ID["BM-08"]
    regressive_jinja_patch = normalize_patch_diff(
        (
            "--- a/src/jinja_filters.py\n"
            "+++ b/src/jinja_filters.py\n"
            "@@ -2,8 +2,3 @@\n"
            "-    result = []\n"
            "-    for item in items:\n"
            "-        # Bug: getattr without default raises AttributeError if item "
            "lacks attr_name\n"
            "-        val = getattr(item, attr_name)\n"
            "-        if val:\n"
            "-            result.append(item)\n"
            "-    return result\n"
            "+    return []\n"
        ),
        "src/jinja_filters.py",
        file_content=issue_jinja.files[issue_jinja.target_file],
    )
    with tempfile.TemporaryDirectory() as tmp_dir:
        for path, content in issue_jinja.files.items():
            p = os.path.join(tmp_dir, path)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(content)

        patched_content, _, _, _ = apply_unified_diff(
            issue_jinja.files[issue_jinja.target_file], regressive_jinja_patch
        )
        with open(os.path.join(tmp_dir, issue_jinja.target_file), "w") as f:
            f.write(patched_content)

        r = subprocess.run(
            issue_jinja.test_command, shell=True, cwd=tmp_dir, capture_output=True, text=True
        )
        assert r.returncode != 0
        combined_err = r.stderr + r.stdout
        assert issue_jinja.expected_baseline_error not in combined_err, "Expected regression error"


def test_citation_boundary_validity_vs_semantic_correctness():
    """Verify distinction between structural boundary validity and semantic citation correctness."""
    issue = BENCHMARK_BY_ID["BM-01"]
    files = issue.files
    evidence = [
        Evidence(
            path="src/click_types.py",
            start_line=1,
            end_line=15,
            content=files["src/click_types.py"],
            score=1.0,
        ),
        Evidence(
            path="tests/test_click_types.py",
            start_line=1,
            end_line=20,
            content=files["tests/test_click_types.py"],
            score=0.8,
        ),
    ]

    base_payload = {
        "summary": "Boundary evaluation",
        "root_cause_hypothesis": "Upper bound error",
        "relevant_files": [{"path": "src/click_types.py", "start_line": 10, "end_line": 15}],
        "proposed_fix": {
            "explanation": "Fix bound",
            "patches": [
                {
                    "file_path": "src/click_types.py",
                    "diff": (
                        "--- a/src/click_types.py\n"
                        "+++ b/src/click_types.py\n"
                        "@@ -13,2 +13,2 @@\n"
                        "-        if self.max_val is not None and value >= self.max_val:\n"
                        "+        if self.max_val is not None and value > self.max_val:\n"
                    ),
                }
            ],
        },
        "confidence": "high",
    }

    # 1. Valid boundary AND semantic correctness: cites target file within bounds
    raw_json = json.dumps(base_payload)
    parsed = parse_and_validate_analysis(raw_json, evidence, files, is_mock=True)

    rf = parsed.relevant_files
    file_len = len(files[rf[0].path].splitlines())
    boundary_valid = rf[0].path in files and 1 <= rf[0].start_line <= rf[0].end_line <= file_len
    semantic_correct = boundary_valid and (rf[0].path == issue.target_file)
    assert boundary_valid is True
    assert semantic_correct is True

    # 2. Valid boundary BUT semantically incorrect: cites tests file instead of target file
    unrelated_payload = dict(base_payload)
    unrelated_payload["relevant_files"] = [
        {"path": "tests/test_click_types.py", "start_line": 1, "end_line": 5}
    ]
    parsed2 = parse_and_validate_analysis(
        json.dumps(unrelated_payload), evidence, files, is_mock=True
    )
    rf2 = parsed2.relevant_files
    file_len2 = len(files[rf2[0].path].splitlines())
    boundary_valid2 = (
        rf2[0].path in files and 1 <= rf2[0].start_line <= rf2[0].end_line <= file_len2
    )
    semantic_correct2 = boundary_valid2 and (rf2[0].path == issue.target_file)
    assert boundary_valid2 is True
    assert semantic_correct2 is False

    # 3. Invalid boundary: hallucinated out-of-bounds line number
    out_of_bounds_payload = dict(base_payload)
    out_of_bounds_payload["relevant_files"] = [
        {"path": "src/click_types.py", "start_line": 500, "end_line": 600}
    ]
    # parse_and_validate_analysis drops or rejects out-of-bounds citations
    with pytest.raises(Exception):
        parse_and_validate_analysis(
            json.dumps(out_of_bounds_payload), evidence, files, is_mock=False
        )


def test_patch_normalization_preserves_integrity_and_binds_to_approval():
    """Verify hunk normalization preserves patch diff semantics and binds to human approval."""
    target_content = (
        "def convert(val):\n"
        "    if val >= 10:\n"
        "        raise ValueError('too large')\n"
        "    return val\n"
    )

    # Patch with misdeclared hunk line counts (declares @@ -1,4 +1,4 @@ instead of 3 lines)
    raw_diff = (
        "--- src/click_types.py\n"
        "+++ src/click_types.py\n"
        "@@ -1,4 +1,4 @@\n"
        " def convert(val):\n"
        "-    if val >= 10:\n"
        "+    if val > 10:\n"
        "         raise ValueError('too large')\n"
    )

    # 1. Normalization preserves patch integrity (additions/deletions intact)
    normalized = normalize_patch_diff(raw_diff, "src/click_types.py", file_content=target_content)
    assert "-    if val >= 10:" in normalized
    assert "+    if val > 10:" in normalized

    # Apply normalized patch
    patched, hunks, added, deleted = apply_unified_diff(target_content, normalized)
    assert "if val > 10:" in patched
    assert added == 1
    assert deleted == 1

    # 2. Verify binding to human approval via specification hash
    proposal = PatchProposal(file_path="src/click_types.py", diff=normalized)
    patch_hash, _ = compute_patch_hash([proposal])
    test_cmd = ["python", "-m", "unittest"]
    commit_sha = "abc1234"
    image = "python:3.11-slim"

    approved_spec_hash = compute_spec_hash(
        commit_sha=commit_sha,
        patch_hash=patch_hash,
        test_command=test_cmd,
        sandbox_image=image,
    )
    assert approved_spec_hash and len(approved_spec_hash) == 64

    # 3. Tampering test: If any change is made to the normalized patch after approval, hash changes
    tampered_diff = normalized.replace("val > 10", "val > 20")
    tampered_proposal = PatchProposal(file_path="src/click_types.py", diff=tampered_diff)
    tampered_patch_hash, _ = compute_patch_hash([tampered_proposal])
    tampered_spec_hash = compute_spec_hash(
        commit_sha=commit_sha,
        patch_hash=tampered_patch_hash,
        test_command=test_cmd,
        sandbox_image=image,
    )
    assert tampered_spec_hash != approved_spec_hash, (
        "Tampered patch must produce different spec hash"
    )


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available on host")
def test_real_docker_benchmark_verification():
    """Verify isolated execution of BM-02 in actual Docker container if daemon is active."""
    # Check if docker daemon is accessible
    check = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode != 0:
        pytest.skip("Docker daemon not running or inaccessible")

    issue = BENCHMARK_BY_ID["BM-02"]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        for p, c in issue.files.items():
            fp = Path(tmpdir) / p
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(c)

        # Baseline execution in docker with non-root user and no pyc writing
        cmd = [
            "docker",
            "run",
            "--rm",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-u",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{tmpdir}:/workspace",
            "-w",
            "/workspace",
            "python:3.11-slim",
            "sh",
            "-c",
            issue.test_command,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        assert res.returncode != 0, "Docker baseline execution should fail"
