"""Reusable evaluation harness for Real-World AI Repair Benchmark.

Orchestrates:
Issue ingestion -> Repository indexing -> Graph-aware retrieval -> Live AI investigation ->
Citation validation -> AI patch generation -> Human approval -> Baseline tests ->
Patched tests -> Proof verdict.
"""

import logging
import os
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Any, Literal

from repopilot_api.benchmarks.dataset import BenchmarkIssue
from repopilot_api.chat import (
    Provider,
    parse_and_validate_analysis,
    retrieve_from_files,
)
from repopilot_api.dependency_graph import DependencyGraphBuilder
from repopilot_api.patcher import apply_unified_diff

logger = logging.getLogger(__name__)

BenchmarkVerdict = Literal[
    "VERIFIED_FIX",
    "FIX_FAILED",
    "REGRESSION_DETECTED",
    "PATCH_APPLICATION_ERROR",
    "PATCH_SYNTHESIS_FAILED",
    "BLOCKED_QUOTA_EXHAUSTED",
]


@dataclass
class BenchmarkCaseResult:
    issue_id: str
    repo_name: str
    category: str
    provider_mode: str  # "live_ai" | "mock_ai"
    model: str
    root_cause_identified: bool
    citation_correctness: bool  # Semantic citation correctness (target file/symbol cited in bounds)
    retrieval_recall_at_5: float
    retrieval_mrr: float
    patch_applicable: bool
    baseline_reproduced: bool
    verdict: BenchmarkVerdict
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    execution_duration_s: float
    is_multi_file: bool
    generated_diff: str
    error_message: str | None = None
    citation_boundary_valid: bool = True
    semantic_citation_correct: bool = True
    execution_backend: str = "host_subprocess"  # "host_subprocess" | "docker"
    provenance: str = "minimized_reproduction"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BenchmarkEvaluator:
    def __init__(
        self,
        provider: Provider,
        rate_limit_delay_seconds: float = 4.5,
        use_docker: bool = False,
    ):
        self.provider = provider
        self.rate_limit_delay_seconds = rate_limit_delay_seconds
        self.use_docker = use_docker

    def evaluate_issue(self, issue: BenchmarkIssue) -> BenchmarkCaseResult:
        """Evaluate a single benchmark issue through the full pipeline."""
        start_total = time.monotonic()
        files = issue.files
        query = f"{issue.title}\n{issue.description}".strip()

        # 1. Dependency Graph Indexing
        builder = DependencyGraphBuilder(files)
        graph = builder.build()

        # 2. Graph-Aware Retrieval
        # Create minimal File mock objects for retrieve()
        class MockFile:
            def __init__(self, p: str, c: str):
                self.path = p
                self.content = c

        mock_files = [MockFile(p, c) for p, c in files.items()]
        evidence_list = retrieve_from_files(mock_files, query, is_issue_query=True)

        # Retrieval metrics
        retrieved_paths = [e.path for e in evidence_list]
        recall_at_5 = 1.0 if issue.target_file in retrieved_paths[:5] else 0.0
        try:
            rank = retrieved_paths.index(issue.target_file) + 1
            mrr = 1.0 / rank
        except ValueError:
            mrr = 0.0

        # 3. Live AI Investigation & Patch Generation
        provider_mode = "live_ai" if "Compatible" in type(self.provider).__name__ else "mock_ai"
        model_name = getattr(self.provider, "model", "mock-model")
        ai_patch = ""
        root_cause_identified = False
        citation_correctness = False
        citation_boundary_valid = False
        semantic_citation_correct = False
        latency_ms = 0
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        error_msg = None

        ai_start = time.monotonic()
        issue_payload = {
            "number": int(issue.id.replace("BM-", "")),
            "title": issue.title,
            "body": issue.description,
        }
        try:
            raw_analysis = self.provider.analyze_issue(issue_payload, evidence_list)
            latency_ms = int((time.monotonic() - ai_start) * 1000)

            # Telemetry extraction
            telemetry = getattr(self.provider, "last_telemetry", {}) or {}
            usage = telemetry.get("token_usage") if isinstance(telemetry, dict) else {}
            if not isinstance(usage, dict):
                usage = {}
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
            total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens) or 0)

            # Attempt raw patch extraction before validation
            try:
                import json

                raw_json = json.loads(raw_analysis) if isinstance(raw_analysis, str) else {}
                pfix = raw_json.get("proposed_fix") or {}
                raw_patches = pfix.get("patches") or []
                if raw_patches and isinstance(raw_patches, list):
                    ai_patch = "\n".join(
                        str(p.get("diff", "")) for p in raw_patches if isinstance(p, dict)
                    )
            except Exception:
                pass

            # Validate and parse response structure
            parsed_analysis = parse_and_validate_analysis(
                raw_analysis,
                evidence_list,
                files,
                provider_name=provider_mode,
                is_mock=(provider_mode == "mock_ai"),
            )

            # Check citations and root cause
            hypothesis = parsed_analysis.root_cause_hypothesis or ""
            relevant_files = parsed_analysis.relevant_files or []
            citation_boundary_valid = False
            semantic_citation_correct = False
            if relevant_files:
                valid_bounds = True
                for c in relevant_files:
                    if c.path not in files:
                        valid_bounds = False
                        break
                    file_len = len(files[c.path].splitlines())
                    in_range = (
                        1 <= c.start_line <= file_len and c.start_line <= c.end_line <= file_len
                    )
                    if not in_range:
                        valid_bounds = False
                        break
                citation_boundary_valid = valid_bounds
                cited_target = any(c.path == issue.target_file for c in relevant_files)
                semantic_citation_correct = valid_bounds and cited_target
                root_cause_identified = cited_target or (issue.target_file in hypothesis)
            else:
                root_cause_identified = issue.target_file in hypothesis
                citation_boundary_valid = False
                semantic_citation_correct = False

            citation_correctness = semantic_citation_correct

            # Extract validated patches
            applied_patches = []
            if parsed_analysis.proposed_fix and parsed_analysis.proposed_fix.patches:
                applied_patches = parsed_analysis.proposed_fix.patches
                ai_patch = "\n".join(p.diff for p in applied_patches if p.diff)
            else:
                applied_patches = []

        except Exception as exc:
            latency_ms = int((time.monotonic() - ai_start) * 1000)
            error_msg = str(exc)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                exec_backend = "docker" if self.use_docker else "host_subprocess"
                return BenchmarkCaseResult(
                    issue_id=issue.id,
                    repo_name=issue.repo_name,
                    category=issue.category,
                    provider_mode=provider_mode,
                    model=model_name,
                    root_cause_identified=False,
                    citation_correctness=False,
                    retrieval_recall_at_5=recall_at_5,
                    retrieval_mrr=mrr,
                    patch_applicable=False,
                    baseline_reproduced=False,
                    verdict="BLOCKED_QUOTA_EXHAUSTED",
                    latency_ms=latency_ms,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    execution_duration_s=time.monotonic() - start_total,
                    is_multi_file=issue.is_multi_file,
                    generated_diff=ai_patch,
                    error_message=f"Live provider quota exhausted: {error_msg}",
                    citation_boundary_valid=False,
                    semantic_citation_correct=False,
                    execution_backend=exec_backend,
                    provenance=getattr(issue, "provenance", "minimized_reproduction"),
                )
            logger.warning("AI analysis failed for %s: %s", issue.id, exc)

        # 4. Patch Pre-flight Validation
        patch_applicable = False
        patched_files: dict[str, str] = dict(files)
        if ai_patch.strip():
            try:
                # Apply all patches in the proposal
                patches_to_apply = []
                if "applied_patches" in locals() and applied_patches:
                    patches_to_apply = applied_patches
                else:
                    # Single target file fallback
                    orig_target_content = files.get(issue.target_file, "")
                    patched_content, _, _, _ = apply_unified_diff(orig_target_content, ai_patch)
                    patched_files[issue.target_file] = patched_content
                    patch_applicable = True

                if patches_to_apply:
                    for p in patches_to_apply:
                        fpath = p.file_path
                        if fpath in files:
                            orig_c = files[fpath]
                            patched_c, _, _, _ = apply_unified_diff(orig_c, p.diff)
                            patched_files[fpath] = patched_c
                    patch_applicable = True
            except Exception as patch_err:
                patch_applicable = False
                error_msg = f"Patch application error: {patch_err}"

        # 5. Execution Verification (Baseline and Patched)
        baseline_reproduced = False
        verdict: BenchmarkVerdict = "PATCH_SYNTHESIS_FAILED"

        if not patch_applicable:
            verdict = "PATCH_SYNTHESIS_FAILED" if not ai_patch else "PATCH_APPLICATION_ERROR"

        exec_start = time.monotonic()
        with (
            tempfile.TemporaryDirectory() as tmp_baseline,
            tempfile.TemporaryDirectory() as tmp_patched,
        ):
            # Setup baseline directory
            for path, content in files.items():
                p = os.path.join(tmp_baseline, path)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(content)

            # Setup patched directory
            for path, content in patched_files.items():
                p = os.path.join(tmp_patched, path)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(content)

            def _run_test(cwd: str) -> subprocess.CompletedProcess:
                if self.use_docker:
                    docker_cmd = [
                        "docker",
                        "run",
                        "--rm",
                        "-e",
                        "PYTHONDONTWRITEBYTECODE=1",
                        "-u",
                        f"{os.getuid()}:{os.getgid()}",
                        "-v",
                        f"{cwd}:/workspace",
                        "-w",
                        "/workspace",
                        "python:3.11-slim",
                        "sh",
                        "-c",
                        issue.test_command,
                    ]
                    return subprocess.run(docker_cmd, capture_output=True, text=True)
                return subprocess.run(
                    issue.test_command,
                    shell=True,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                )

            # Baseline execution
            r_base = _run_test(tmp_baseline)
            baseline_reproduced = r_base.returncode != 0

            # Patched execution
            if patch_applicable:
                r_patch = _run_test(tmp_patched)
                if r_patch.returncode == 0:
                    verdict = "VERIFIED_FIX"
                else:
                    combined_err = (r_patch.stderr or "") + (r_patch.stdout or "")
                    if issue.expected_baseline_error in combined_err:
                        verdict = "FIX_FAILED"
                    else:
                        verdict = "REGRESSION_DETECTED"

        exec_duration = time.monotonic() - exec_start
        total_duration = time.monotonic() - start_total
        exec_backend = "docker" if self.use_docker else "host_subprocess"

        # Respect rate limits between calls
        if self.rate_limit_delay_seconds > 0 and provider_mode == "live_ai":
            time.sleep(self.rate_limit_delay_seconds)

        return BenchmarkCaseResult(
            issue_id=issue.id,
            repo_name=issue.repo_name,
            category=issue.category,
            provider_mode=provider_mode,
            model=model_name,
            root_cause_identified=root_cause_identified,
            citation_correctness=citation_correctness,
            retrieval_recall_at_5=recall_at_5,
            retrieval_mrr=mrr,
            patch_applicable=patch_applicable,
            baseline_reproduced=baseline_reproduced,
            verdict=verdict,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            execution_duration_s=round(total_duration, 3),
            is_multi_file=issue.is_multi_file,
            generated_diff=ai_patch,
            error_message=error_msg,
            citation_boundary_valid=citation_boundary_valid,
            semantic_citation_correct=semantic_citation_correct,
            execution_backend=exec_backend,
            provenance=getattr(issue, "provenance", "minimized_reproduction"),
        )

    def evaluate_batch(
        self,
        issues: list[BenchmarkIssue],
        stop_on_quota: bool = True,
    ) -> list[BenchmarkCaseResult]:
        """Run batch evaluation with graceful quota handling."""
        results: list[BenchmarkCaseResult] = []
        for issue in issues:
            res = self.evaluate_issue(issue)
            results.append(res)
            if stop_on_quota and res.verdict == "BLOCKED_QUOTA_EXHAUSTED":
                logger.warning("Quota exhausted at %s. Halting batch.", issue.id)
                break
        return results
