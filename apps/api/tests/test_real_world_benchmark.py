"""Automated tests for Real-World AI Repair Benchmark dataset, evaluator, and integrity."""

import json
import os
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
from repopilot_api.benchmarks.evaluator import BenchmarkCaseResult, BenchmarkEvaluator
from repopilot_api.benchmarks.run_benchmark import aggregate_metrics
from repopilot_api.chat import MockProvider, recount_and_align_hunks
from repopilot_api.patcher import apply_unified_diff


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

    # Verify metadata fields
    for issue in BENCHMARK_ISSUES:
        assert issue.repo_name and "/" in issue.repo_name
        assert issue.repo_url.startswith("https://github.com/")
        assert len(issue.commit_sha) in (7, 40)
        assert issue.license in ("MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "PSF-2.0")
        assert issue.target_file in issue.files
        assert issue.test_command
        assert issue.expected_baseline_error
        assert issue.reference_patch


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
        "--- a/src/calc.py\n"
        "+++ b/src/calc.py\n"
        "@@ -2,4 +2,4 @@\n"
        "-    if x > 10:\n"
        "+    if x >= 10:\n"
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
    evaluator = BenchmarkEvaluator(provider=mock_provider, rate_limit_delay_seconds=0.0)

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
        ),
    ]

    metrics = aggregate_metrics(cases)
    assert metrics["total_evaluated"] == 2
    assert metrics["counts"]["verified_fixes"] == 1
    assert metrics["rates"]["verified_repair_rate"] == 0.5
    assert metrics["rates"]["patch_applicability_rate"] == 0.5
    assert metrics["rates"]["baseline_reproduction_rate"] == 1.0
    assert metrics["rates"]["root_cause_accuracy"] == 1.0
    assert metrics["rates"]["semantic_citation_accuracy"] == 0.5
    assert metrics["performance"]["total_tokens"] == 1450
    assert metrics["performance"]["avg_tokens_per_issue"] == 725.0


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
    assert "sk-" not in raw_manifest


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
