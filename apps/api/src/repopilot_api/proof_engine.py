"""Evidence-First Proof-of-Fix Verification Engine for RepoPilot Fix.

Orchestrates deterministic dual-sandbox verification:
1. Recreates repository state from exact commit SHA into isolated baseline workspace.
2. Runs allowlisted test command in baseline sandbox to verify bug reproduction.
3. Applies AI patch proposal to an isolated patched workspace using deterministic patcher.
4. Runs identical test command in patched sandbox.
5. Strictly evaluates results (VERIFIED_FIX, UNREPRODUCED, FIX_FAILED, REGRESSION_DETECTED, etc.).
6. Generates reproducible cryptographic evidence report and persists to database models.
"""

import hashlib
import logging
import re
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from repopilot_api.models import IndexedFile, IndexingJob, ProofReport, VerificationRun
from repopilot_api.patcher import PatchError, apply_patch_proposal
from repopilot_api.sandbox import (
    DEFAULT_SCRATCH_ROOT,
    CommandValidator,
    EnvironmentSanitizer,
    SandboxExecutionState,
    SandboxRunner,
    WorkspaceManager,
    get_sandbox_runner,
)
from repopilot_api.schemas import (
    ProofEvidence,
    ProofOfFixRequest,
    ProofOfFixResult,
    ProofVerdict,
    ProposedFix,
    SandboxExecutionRequest,
    SandboxExecutionResult,
    TestMetricsSchema,
    VerificationStage,
)

logger = logging.getLogger(__name__)


class ProofOfFixError(Exception):
    """Base exception for Proof-of-Fix engine errors."""


class TestOutputParser:
    """Deterministic parser extracting test counts, failure types, and test identities.

    Never invents test counts or confidence scores: if output does not contain
    recognizable patterns, metrics remain None and verdicts reflect uncertainty.
    """

    UNITTEST_RUN_REGEX = re.compile(r"Ran (\d+) tests? in")
    UNITTEST_FAILED_REGEX = re.compile(r"FAILED \((.*?)\)")
    UNITTEST_FAILURES_REGEX = re.compile(r"failures=(\d+)")
    UNITTEST_ERRORS_REGEX = re.compile(r"errors=(\d+)")
    UNITTEST_TEST_ID_REGEX = re.compile(
        r"^(?:FAIL|ERROR):\s+([a-zA-Z0-9_]+(?:\s+\(.*?\))?)", re.MULTILINE
    )

    PYTEST_PASSED_REGEX = re.compile(r"(\d+) passed")
    PYTEST_FAILED_REGEX = re.compile(r"(\d+) failed")
    PYTEST_ERRORS_REGEX = re.compile(r"(\d+) error(?:s)?")
    PYTEST_SKIPPED_REGEX = re.compile(r"(\d+) skipped")
    PYTEST_FAILED_ID_REGEX = re.compile(r"^(?:FAILED|ERROR)\s+([^\s]+)", re.MULTILINE)

    COLLECTION_ERROR_INDICATORS = (
        "ERROR collecting ",
        "ModuleNotFoundError:",
        "ImportError:",
        "SyntaxError:",
        "IndentationError:",
        "NameError:",
    )

    @classmethod
    def detect_failure_type(cls, exit_code: int | None, combined: str) -> str:
        """Classify the failure into assertion failure, collection error, syntax, or clean exit."""
        if exit_code == 0 or ("OK" in combined and exit_code is None):
            return "none"
        if any(ind in combined for ind in cls.COLLECTION_ERROR_INDICATORS):
            if "SyntaxError:" in combined or "IndentationError:" in combined:
                return "syntax_error"
            if "ModuleNotFoundError:" in combined or "ImportError:" in combined:
                return "import_error"
            return "collection_error"
        if (
            "AssertionError" in combined
            or "failures=" in combined
            or "FAILED " in combined
            or "FAIL:" in combined
        ):
            return "assertion_failure"
        return "unclassified_failure"

    @classmethod
    def parse(cls, stdout: str, stderr: str, exit_code: int | None = None) -> TestMetricsSchema:
        combined = f"{stdout}\n{stderr}"
        failure_type = cls.detect_failure_type(exit_code, combined)

        # 1. Try parsing unittest standard output
        unit_match = cls.UNITTEST_RUN_REGEX.search(combined)
        failed_match = cls.UNITTEST_FAILED_REGEX.search(combined)
        if unit_match or failed_match:
            total = int(unit_match.group(1)) if unit_match else None
            if "OK" in combined and (exit_code is None or exit_code == 0):
                return TestMetricsSchema(
                    total=total,
                    passed=total,
                    failed=0,
                    errors=0,
                    failed_test_ids=[],
                    failure_type="none",
                    summary=(
                        f"Ran {total} tests: OK (all passed)"
                        if total is not None
                        else "OK (all passed)"
                    ),
                )

            if failed_match:
                details = failed_match.group(1)
                f_m = cls.UNITTEST_FAILURES_REGEX.search(details)
                e_m = cls.UNITTEST_ERRORS_REGEX.search(details)
                failed = int(f_m.group(1)) if f_m else 0
                errors = int(e_m.group(1)) if e_m else 0
                passed = max(0, total - (failed + errors)) if total is not None else 0
                total = total if total is not None else (failed + errors)
                test_ids = cls.UNITTEST_TEST_ID_REGEX.findall(combined)
                return TestMetricsSchema(
                    total=total,
                    passed=passed,
                    failed=failed,
                    errors=errors,
                    failed_test_ids=sorted(list(dict.fromkeys(test_ids))),
                    failure_type=failure_type,
                    summary=f"Ran {total} tests: {failed} failed, {errors} errors",
                )

        # 2. Try parsing pytest summary output
        has_pytest = "pytest" in combined or "passed" in combined or "failed" in combined
        if has_pytest:
            p_m = cls.PYTEST_PASSED_REGEX.search(combined)
            f_m = cls.PYTEST_FAILED_REGEX.search(combined)
            e_m = cls.PYTEST_ERRORS_REGEX.search(combined)

            if p_m or f_m or e_m:
                passed = int(p_m.group(1)) if p_m else 0
                failed = int(f_m.group(1)) if f_m else 0
                errors = int(e_m.group(1)) if e_m else 0
                total = passed + failed + errors
                test_ids = cls.PYTEST_FAILED_ID_REGEX.findall(combined)
                return TestMetricsSchema(
                    total=total,
                    passed=passed,
                    failed=failed,
                    errors=errors,
                    failed_test_ids=sorted(list(dict.fromkeys(test_ids))),
                    failure_type=failure_type,
                    summary=f"{passed} passed, {failed} failed, {errors} errors",
                )

        # 3. Fallback: non-standard output
        first_line = (stdout.strip() or stderr.strip() or "No output").splitlines()[0][:100]
        return TestMetricsSchema(
            total=None,
            passed=None,
            failed=None,
            errors=None,
            failed_test_ids=[],
            failure_type=failure_type,
            summary=f"Output: {first_line}",
        )


class ProofOfFixEngine:
    """Orchestrates deterministic Proof-of-Fix verification runs."""

    def __init__(
        self,
        runner: SandboxRunner | None = None,
        scratch_root: str | None = None,
    ):
        self.runner = runner or get_sandbox_runner()
        self.scratch_root = scratch_root or DEFAULT_SCRATCH_ROOT
        self.manager = WorkspaceManager(scratch_root=self.scratch_root)

    async def verify(
        self,
        request: ProofOfFixRequest,
        db: Session | None = None,
    ) -> ProofOfFixResult:
        """Run complete Proof-of-Fix verification lifecycle with deterministic state transitions."""
        run_id = f"proof_{uuid.uuid4().hex[:12]}"
        created_at = datetime.now(UTC).isoformat()
        start_time = time.monotonic()
        stage = VerificationStage.INITIALIZED

        # 1. Validate Command, Environment, and Patches
        CommandValidator.validate(request.test_command)
        EnvironmentSanitizer.sanitize(request.environment)

        patches = (
            request.patches.patches if isinstance(request.patches, ProposedFix) else request.patches
        )
        if not patches:
            raise ProofOfFixError("At least one patch proposal must be provided for verification.")

        combined_diff = "\n".join(p.diff.strip() for p in patches)
        patch_hash = hashlib.sha256(combined_diff.encode("utf-8")).hexdigest()

        # 2. Resolve Repository Files
        files: dict[str, str] = {}
        if request.files is not None:
            files = request.files
        elif db is not None:
            job = db.scalar(
                select(IndexingJob).where(
                    IndexingJob.repository_id == request.repository_id,
                    IndexingJob.commit_sha == request.commit_sha,
                )
            )
            if not job:
                raise ProofOfFixError(
                    f"No index job for repo {request.repository_id} at {request.commit_sha}."
                )
            indexed = db.scalars(select(IndexedFile).where(IndexedFile.job_id == job.id)).all()
            if not indexed:
                raise ProofOfFixError(f"No indexed files found for indexing job {job.id}.")
            files = {f.path: f.content for f in indexed}
        else:
            raise ProofOfFixError(
                "Cannot resolve repository files: request.files is None and db is None."
            )

        # 3. Create VerificationRun DB Record if db session is provided
        vrun: VerificationRun | None = None
        if db is not None:
            try:
                vrun = VerificationRun(
                    repository_id=request.repository_id,
                    commit_sha=request.commit_sha,
                    issue_number=request.issue_number,
                    user_id=request.user_id,
                    status="running",
                    sandbox_image=getattr(self.runner, "image", "default"),
                    patch_diff=combined_diff,
                    patch_hash=patch_hash,
                )
                db.add(vrun)
                db.commit()
                db.refresh(vrun)
            except Exception as db_init_exc:
                logger.warning("Failed to initialize VerificationRun in DB: %s", db_init_exc)

        # Workspaces tracking for guaranteed cleanup
        baseline_ws: str | None = None
        patched_ws: str | None = None

        baseline_res: SandboxExecutionResult | None = None
        patched_res: SandboxExecutionResult | None = None
        baseline_metrics: TestMetricsSchema = TestMetricsSchema()
        patched_metrics: TestMetricsSchema = TestMetricsSchema()
        changed_files: list[str] = []
        resolved_test_ids: list[str] = []
        new_failed_test_ids: list[str] = []
        unresolved_test_ids: list[str] = []
        uncertainty_reasons: list[str] = []

        verdict = ProofVerdict.ERROR
        summary = "Verification initialization failed."
        confidence = "low"

        try:
            # 4. Prepare Baseline Workspace
            stage = VerificationStage.PREPARING_BASELINE
            baseline_ws = self.manager.create_workspace(f"{run_id}_base", files)

            # 5. Run Baseline Sandbox
            stage = VerificationStage.RUNNING_BASELINE
            exec_req = SandboxExecutionRequest(
                command=request.test_command,
                timeout_seconds=request.timeout_seconds,
                environment=request.environment,
            )
            baseline_res = await self.runner.run(baseline_ws, exec_req)

            # 6. Analyze Baseline Outcome
            stage = VerificationStage.ANALYZING_BASELINE
            baseline_metrics = TestOutputParser.parse(
                baseline_res.stdout, baseline_res.stderr, exit_code=baseline_res.exit_code
            )

            if baseline_res.state == SandboxExecutionState.TIMED_OUT:
                verdict = ProofVerdict.TIMEOUT
                summary = (
                    f"Baseline test execution timed out after {request.timeout_seconds} seconds. "
                    "Original issue could not be reproduced within time limit."
                )
                confidence = "low"
                uncertainty_reasons.append("Baseline execution timed out.")
            elif baseline_res.state == SandboxExecutionState.ERROR:
                verdict = ProofVerdict.ERROR
                summary = f"Baseline sandbox infrastructure error: {baseline_res.error_message}"
                confidence = "low"
                uncertainty_reasons.append("Sandbox infrastructure error occurred.")
            elif baseline_res.exit_code == 0:
                # Core Evidence-First Rule: If baseline passes, fix CANNOT be proven!
                verdict = ProofVerdict.UNREPRODUCED
                summary = (
                    "Baseline test passed with exit code 0. Original bug could not be reproduced "
                    "in the baseline environment; proof cannot be established."
                )
                confidence = "low"
                uncertainty_reasons.append(
                    "Original bug was not observable in the baseline environment."
                )
            elif baseline_metrics.failure_type in (
                "collection_error",
                "syntax_error",
                "import_error",
            ):
                # Distinguish collection/syntax/import errors from actual assertion failures
                verdict = ProofVerdict.ERROR
                summary = (
                    f"Baseline execution failed due to {baseline_metrics.failure_type} "
                    f"rather than test assertion failure: {baseline_metrics.summary}"
                )
                confidence = "low"
                uncertainty_reasons.append(
                    f"Baseline failed with {baseline_metrics.failure_type}; "
                    "not a clean test failure."
                )
            else:
                # Bug reproduced in baseline! Now prepare identical patched workspace
                stage = VerificationStage.PREPARING_PATCHED
                patched_ws = self.manager.create_workspace(f"{run_id}_patch", files)

                try:
                    patch_app_res = apply_patch_proposal(
                        patched_ws, request.patches, scratch_root=self.scratch_root
                    )
                    changed_files = patch_app_res.changed_files
                except PatchError as p_err:
                    verdict = ProofVerdict.PATCH_APPLICATION_ERROR
                    summary = f"Patch application failed: {p_err}"
                    confidence = "low"
                    uncertainty_reasons.append(f"Patch conflict or error: {p_err}")
                    patch_app_res = None

                if patch_app_res is not None and patch_app_res.success:
                    # 7. Run Patched Sandbox with identical command and environment
                    stage = VerificationStage.RUNNING_PATCHED
                    patched_res = await self.runner.run(patched_ws, exec_req)

                    # 8. Analyze Patched Outcome and Evaluate Verdict
                    stage = VerificationStage.ANALYZING_PATCHED
                    patched_metrics = TestOutputParser.parse(
                        patched_res.stdout, patched_res.stderr, exit_code=patched_res.exit_code
                    )

                    stage = VerificationStage.EVALUATING_VERDICT
                    if patched_res.state == SandboxExecutionState.TIMED_OUT:
                        verdict = ProofVerdict.TIMEOUT
                        summary = (
                            f"Patched execution timed out after {request.timeout_seconds} seconds."
                        )
                        confidence = "low"
                        uncertainty_reasons.append("Patched execution timed out.")
                    elif patched_res.state == SandboxExecutionState.ERROR:
                        verdict = ProofVerdict.ERROR
                        summary = (
                            f"Patched sandbox infrastructure error: {patched_res.error_message}"
                        )
                        confidence = "low"
                        uncertainty_reasons.append("Patched sandbox infrastructure error.")
                    elif patched_res.exit_code == 0:
                        # Patched run passed after baseline failed!
                        # Verify that we actually have identifiable test metrics / test IDs
                        has_structured_evidence = (
                            (baseline_metrics.total is not None and baseline_metrics.total > 0)
                            or bool(baseline_metrics.failed_test_ids)
                        ) and (
                            (patched_metrics.total is not None and patched_metrics.total > 0)
                            or patched_metrics.passed is not None
                        )

                        if has_structured_evidence:
                            resolved_test_ids = list(baseline_metrics.failed_test_ids)
                            verdict = ProofVerdict.VERIFIED_FIX
                            confidence = "high"
                            summary = (
                                "Proof-of-Fix Verified: Baseline test reproduced failure (exit 1) "
                                "and patched test passed with exit code 0 without regressions."
                            )
                        else:
                            # Missing/unparseable test metrics: do not claim proven fix!
                            verdict = ProofVerdict.INCONCLUSIVE
                            confidence = "low"
                            summary = (
                                "Patched test exited with code 0, but specific test identities or "
                                "metrics could not be verified from runner output."
                            )
                            uncertainty_reasons.append(
                                "Missing or unparseable test metrics; cannot reliably "
                                "prove absence of regressions."
                            )
                    else:
                        # Patched run still failed: compare test identities
                        base_ids = set(baseline_metrics.failed_test_ids)
                        patch_ids = set(patched_metrics.failed_test_ids)

                        new_failed = sorted(list(patch_ids - base_ids))
                        unresolved = sorted(list(patch_ids & base_ids))
                        resolved = sorted(list(base_ids - patch_ids))

                        new_failed_test_ids = new_failed
                        unresolved_test_ids = unresolved
                        resolved_test_ids = resolved

                        if new_failed:
                            # Even if total failures didn't increase, a new test failed!
                            verdict = ProofVerdict.REGRESSION_DETECTED
                            confidence = "high"
                            summary = (
                                f"Regression detected: New test failure(s) introduced by patch: "
                                f"{new_failed}."
                            )
                        elif (
                            baseline_metrics.failed is not None
                            and patched_metrics.failed is not None
                            and patched_metrics.failed > baseline_metrics.failed
                        ):
                            verdict = ProofVerdict.REGRESSION_DETECTED
                            confidence = "high"
                            summary = (
                                f"Regression detected: test failures increased from "
                                f"{baseline_metrics.failed} in baseline to "
                                f"{patched_metrics.failed} in patched code."
                            )
                        else:
                            verdict = ProofVerdict.FIX_FAILED
                            has_metric_evidence = bool(unresolved) or (
                                baseline_metrics.failed is not None
                                and patched_metrics.failed is not None
                            )
                            confidence = "high" if has_metric_evidence else "medium"
                            summary = (
                                f"Fix failed: test failed (exit {patched_res.exit_code}), "
                                f"{len(unresolved) or patched_metrics.failed or 1} failures remain."
                            )

            stage = VerificationStage.COMPLETED

        except Exception as exc:
            logger.exception("Unexpected error during Proof-of-Fix verification: %s", exc)
            verdict = ProofVerdict.ERROR
            summary = f"Unexpected verification failure: {exc}"
            stage = VerificationStage.FAILED
            confidence = "low"
            uncertainty_reasons.append(f"Unexpected exception during verification: {exc}")

        finally:
            # Assured cleanup: both isolated workspaces are always deleted
            if baseline_ws:
                self.manager.cleanup_workspace(baseline_ws)
            if patched_ws:
                self.manager.cleanup_workspace(patched_ws)

        duration = round(time.monotonic() - start_time, 3)

        evidence = ProofEvidence(
            repository_id=request.repository_id,
            commit_sha=request.commit_sha,
            issue_number=request.issue_number,
            patch_hash=patch_hash,
            sandbox_image=getattr(self.runner, "image", "default"),
            test_command=request.test_command,
            baseline_exit_code=baseline_res.exit_code if baseline_res else None,
            baseline_stdout=baseline_res.stdout if baseline_res else "",
            baseline_stderr=baseline_res.stderr if baseline_res else "",
            baseline_metrics=baseline_metrics,
            baseline_failure_type=baseline_metrics.failure_type,
            patched_exit_code=patched_res.exit_code if patched_res else None,
            patched_stdout=patched_res.stdout if patched_res else "",
            patched_stderr=patched_res.stderr if patched_res else "",
            patched_metrics=patched_metrics,
            patched_failure_type=patched_metrics.failure_type,
            resolved_test_ids=resolved_test_ids,
            new_failed_test_ids=new_failed_test_ids,
            unresolved_test_ids=unresolved_test_ids,
            changed_files=changed_files,
            duration_seconds=duration,
            uncertainty_reasons=uncertainty_reasons,
        )

        proof_report_id: int | None = None
        verification_run_id: int | None = vrun.id if vrun else None
        is_persisted = False
        persistence_error: str | None = None

        # 9. Persist to Database if session is available
        if db is not None and vrun is not None:
            try:
                db_status = (
                    "passed"
                    if verdict == ProofVerdict.VERIFIED_FIX
                    else ("timed_out" if verdict == ProofVerdict.TIMEOUT else "failed")
                )
                vrun.status = db_status
                vrun.duration_seconds = duration
                vrun.baseline_exit_code = evidence.baseline_exit_code
                vrun.baseline_stdout = evidence.baseline_stdout
                vrun.baseline_stderr = evidence.baseline_stderr
                vrun.patched_exit_code = evidence.patched_exit_code
                vrun.patched_stdout = evidence.patched_stdout
                vrun.patched_stderr = evidence.patched_stderr
                vrun.patch_hash = patch_hash
                vrun.error_message = None if verdict == ProofVerdict.VERIFIED_FIX else summary

                report = ProofReport(
                    verification_run_id=vrun.id,
                    verdict=verdict.value,
                    hypothesis=request.hypothesis or "AI proposed fix hypothesis under evaluation.",
                    summary=summary,
                    baseline_summary=baseline_metrics.summary or "Baseline execution",
                    patched_summary=patched_metrics.summary or "Patched execution",
                    confidence=confidence,
                    evidence_json=evidence.model_dump_json(),
                )
                db.add(report)
                db.commit()
                db.refresh(report)
                proof_report_id = report.id
                is_persisted = True
            except Exception as db_persist_exc:
                if hasattr(db, "rollback"):
                    try:
                        db.rollback()
                    except Exception:
                        pass
                is_persisted = False
                persistence_error = f"Database persistence failed: {db_persist_exc}"
                evidence.uncertainty_reasons.append(
                    "Evidence was not durably persisted to database."
                )
                logger.error(persistence_error, exc_info=True)
        else:
            persistence_error = "No database session provided (in-memory execution only)."

        return ProofOfFixResult(
            run_id=run_id,
            verdict=verdict,
            confidence=confidence,
            summary=summary,
            stage=stage,
            evidence=evidence,
            error_message=None if verdict == ProofVerdict.VERIFIED_FIX else summary,
            verification_run_id=verification_run_id,
            proof_report_id=proof_report_id,
            is_persisted=is_persisted,
            persistence_error=persistence_error,
            created_at=created_at,
        )
