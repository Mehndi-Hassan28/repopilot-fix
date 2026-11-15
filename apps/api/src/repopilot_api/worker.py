"""Lightweight, durable asynchronous verification worker for RepoPilot Fix.

Milestone 3.6 Phase 2:
- Polls queued verification jobs.
- Atomically claims jobs using database transactions (FOR UPDATE SKIP LOCKED on PostgreSQL).
- Strictly prevents duplicate execution between concurrent workers.
- Uses short-lived database sessions.
- Enforces Phase 1 ExecutionPolicyGuard and human approval before execution.
- Manages complete job lifecycle: QUEUED -> RUNNING -> COMPLETED / FAILED / CANCELLED.
- Supports graceful shutdown without leaving jobs stuck in RUNNING.
- Runs standalone CLI without spawning duplicate workers from FastAPI reload.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from repopilot_api.database import SessionLocal
from repopilot_api.fixture_registry import (
    ExecutionPolicyGuard,
    TrustedFixtureRegistry,
    get_default_fixture_registry,
)
from repopilot_api.models import (
    IndexedFile,
    IndexingJob,
    ProofReport,
    VerificationRun,
)
from repopilot_api.proof_engine import (
    ProofOfFixCancelledError,
    ProofOfFixEngine,
)
from repopilot_api.sandbox import (
    DEFAULT_SCRATCH_ROOT,
    SandboxRunner,
    get_sandbox_runner,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofOfFixRequest,
    ProofOfFixResult,
    ProofVerdict,
    VerificationAuditEventType,
    VerificationJobStatus,
)
from repopilot_api.verification_service import (
    VerificationJobManager,
    VerificationSecurityError,
    clear_cancellation_event,
    log_audit_event,
    register_cancellation_event,
)

logger = logging.getLogger(__name__)


class WorkerLoop:
    """Lightweight, durable asynchronous verification worker loop."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        runner: SandboxRunner | None = None,
        fixture_registry: TrustedFixtureRegistry | None = None,
        scratch_root: str | None = None,
        poll_interval: float = 1.0,
        worker_id: str | None = None,
    ):
        self.session_factory = session_factory or SessionLocal
        self.runner = runner
        self.fixture_registry = fixture_registry or get_default_fixture_registry()
        self.scratch_root = scratch_root or DEFAULT_SCRATCH_ROOT
        self.poll_interval = max(0.01, poll_interval)
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._stop_event = asyncio.Event()
        self._is_running = False
        self._current_job_id: int | None = None
        self._processing_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Whether the worker polling loop is currently active."""
        return self._is_running

    @property
    def current_job_id(self) -> int | None:
        """The verification run ID currently being processed, or None."""
        return self._current_job_id

    def stop(self) -> None:
        """Request graceful shutdown of the worker loop."""
        logger.info("Signaling stop to WorkerLoop '%s'...", self.worker_id)
        self._stop_event.set()

    def _fail_security(self, db: Session, run: VerificationRun, reason: str) -> None:
        """Mark a job as FAILED due to a security violation, with audit log."""
        run.status = VerificationJobStatus.FAILED.value
        run.error_message = reason[:1000]
        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.FAILED.value,
            actor_user_id=run.approved_by_user_id,
            from_status=VerificationJobStatus.RUNNING.value,
            to_status=VerificationJobStatus.FAILED.value,
            details={"error": reason, "policy_violation": True},
        )
        db.commit()

    async def process_job(self, run_id: int) -> ProofOfFixResult | None:
        """Process a single claimed verification job.

        Enforces concurrency limit (at most 1 concurrent job per worker instance),
        executes Phase 1 ExecutionPolicyGuard checks fail-closed, runs sandbox verification,
        and records lifecycle transitions with short-lived database sessions.
        """
        async with self._processing_lock:
            self._current_job_id = run_id
            try:
                return await self._execute_claimed_job(run_id)
            finally:
                self._current_job_id = None

    async def _execute_claimed_job(self, run_id: int) -> ProofOfFixResult | None:
        # ----------------------------------------------------------------------
        # Session 1: Security Policy Guard & Snapshot Loading (Short-Lived)
        # ----------------------------------------------------------------------
        with self.session_factory() as db:
            run = db.get(VerificationRun, run_id)
            if run is None:
                logger.error("Job %d not found in database.", run_id)
                return None

            # Check if job was cancelled before execution started
            if run.status == VerificationJobStatus.CANCELLED.value or run.cancelled_at is not None:
                logger.info("Job %d was cancelled before execution.", run_id)
                return None

            # Guard: Verify human approval
            if not run.approved_spec_hash:
                self._fail_security(db, run, "No execution without human approval.")
                return None
            if not run.approved_by_user_id:
                self._fail_security(
                    db, run, "Missing approved_by_user_id on approved verification."
                )
                return None

            # Guard: Fail closed on missing user identity
            effective_user_id = run.approved_by_user_id or run.user_id
            if effective_user_id is None:
                self._fail_security(
                    db, run, "Verification execution requires an authenticated user."
                )
                return None

            # Re-verify approved specification hash & ExecutionPolicyGuard
            try:
                ExecutionPolicyGuard.validate_all(
                    db=db,
                    run=run,
                    user_id=effective_user_id,
                    registry=self.fixture_registry,
                )
            except Exception as sec_err:
                logger.warning("Job %d failed policy guard: %s", run_id, sec_err)
                self._fail_security(
                    db, run, f"Execution policy validation failed: {sec_err}"
                )
                return None

            # Parse test command
            try:
                test_command = json.loads(run.test_command)
            except Exception as exc:
                self._fail_security(db, run, f"Corrupted test_command JSON: {exc}")
                return None

            # Parse patches
            try:
                patch_data = json.loads(run.patches_json or "[]")
                if patch_data:
                    patches = [PatchProposal(**p) for p in patch_data]
                else:
                    patches = [PatchProposal(file_path="source_code", diff=run.patch_diff)]
            except Exception as exc:
                self._fail_security(db, run, f"Corrupted patches JSON: {exc}")
                return None

            # Load snapshot files so ProofOfFixEngine does not require db during execution
            job = db.scalar(
                select(IndexingJob).where(
                    IndexingJob.repository_id == run.repository_id,
                    IndexingJob.commit_sha == run.commit_sha,
                )
            )
            if not job:
                self._fail_security(
                    db,
                    run,
                    f"No completed indexing job found for repo {run.repository_id} "
                    f"at {run.commit_sha}.",
                )
                return None

            indexed = db.scalars(
                select(IndexedFile).where(IndexedFile.job_id == job.id)
            ).all()
            if not indexed:
                self._fail_security(db, run, f"No indexed files found for job {job.id}.")
                return None

            file_map = {f.path: f.content for f in indexed}
            approved_by_user_id = run.approved_by_user_id

            proof_request = ProofOfFixRequest(
                repository_id=run.repository_id,
                commit_sha=run.commit_sha,
                issue_number=run.issue_number,
                test_command=test_command,
                patches=patches,
                hypothesis=run.hypothesis,
                user_id=run.user_id,
                files=file_map,
            )

        # ----------------------------------------------------------------------
        # Runner Check & Docker Restriction Enforcement (STEP 5)
        # ----------------------------------------------------------------------
        active_runner = self.runner or get_sandbox_runner()
        allow_docker = os.getenv(
            "ALLOW_DOCKER_VERIFICATION_EXECUTION", "false"
        ).lower() in ("true", "1")
        is_docker_runner = (
            hasattr(active_runner, "run")
            and getattr(active_runner, "__class__", None).__name__ == "DockerSandboxRunner"
        )
        if is_docker_runner and not allow_docker:
            with self.session_factory() as db:
                run = db.get(VerificationRun, run_id)
                if run:
                    self._fail_security(
                        db,
                        run,
                        "Real Docker execution is disabled in normal worker flows. "
                        "Set ALLOW_DOCKER_VERIFICATION_EXECUTION=true after security review.",
                    )
            return None

        # ----------------------------------------------------------------------
        # Execution (No DB Session Held During Sandbox Run)
        # ----------------------------------------------------------------------
        cancel_event = await register_cancellation_event(run_id)
        engine = ProofOfFixEngine(
            runner=active_runner,
            scratch_root=self.scratch_root,
        )

        try:
            result = await engine.verify(
                proof_request,
                db=None,
                cancellation_token=cancel_event,
            )

            # ------------------------------------------------------------------
            # Session 2: Persist Outcome (COMPLETED, FAILED, or CANCELLED)
            # ------------------------------------------------------------------
            with self.session_factory() as db:
                run = db.get(VerificationRun, run_id)
                if run is None:
                    return result

                run.baseline_exit_code = result.evidence.baseline_exit_code
                run.baseline_stdout = result.evidence.baseline_stdout
                run.baseline_stderr = result.evidence.baseline_stderr
                run.patched_exit_code = result.evidence.patched_exit_code
                run.patched_stdout = result.evidence.patched_stdout
                run.patched_stderr = result.evidence.patched_stderr
                run.duration_seconds = result.evidence.duration_seconds

                baseline_sum = (
                    result.evidence.baseline_metrics.summary
                    if result.evidence.baseline_metrics
                    else "Baseline execution"
                )
                patched_sum = (
                    result.evidence.patched_metrics.summary
                    if result.evidence.patched_metrics
                    else "Patched execution"
                )

                report = ProofReport(
                    verification_run_id=run.id,
                    verdict=result.verdict.value,
                    hypothesis=run.hypothesis or "AI proposed fix hypothesis under evaluation.",
                    summary=result.summary,
                    baseline_summary=baseline_sum,
                    patched_summary=patched_sum,
                    confidence=result.confidence,
                    evidence_json=result.evidence.model_dump_json(),
                )
                db.add(report)

                if cancel_event.is_set():
                    run.status = VerificationJobStatus.CANCELLED.value
                    run.cancelled_at = run.cancelled_at or datetime.now(UTC)
                    log_audit_event(
                        db,
                        run_id=run.id,
                        event_type=VerificationAuditEventType.CANCELLED.value,
                        actor_user_id=run.cancelled_by_user_id or approved_by_user_id,
                        from_status=VerificationJobStatus.RUNNING.value,
                        to_status=VerificationJobStatus.CANCELLED.value,
                        details={"reason": "cancelled_during_execution"},
                    )
                elif result.verdict == ProofVerdict.ERROR:
                    run.status = VerificationJobStatus.FAILED.value
                    run.error_message = result.summary[:1000]
                    log_audit_event(
                        db,
                        run_id=run.id,
                        event_type=VerificationAuditEventType.FAILED.value,
                        actor_user_id=approved_by_user_id,
                        from_status=VerificationJobStatus.RUNNING.value,
                        to_status=VerificationJobStatus.FAILED.value,
                        details={"error": result.summary, "verdict": result.verdict.value},
                    )
                else:
                    run.status = VerificationJobStatus.COMPLETED.value
                    log_audit_event(
                        db,
                        run_id=run.id,
                        event_type=VerificationAuditEventType.COMPLETED.value,
                        actor_user_id=approved_by_user_id,
                        from_status=VerificationJobStatus.RUNNING.value,
                        to_status=VerificationJobStatus.COMPLETED.value,
                        details={
                            "verdict": result.verdict.value,
                            "confidence": result.confidence,
                            "duration_seconds": result.evidence.duration_seconds,
                        },
                    )
                db.commit()
                return result

        except ProofOfFixCancelledError as c_err:
            with self.session_factory() as db:
                run = db.get(VerificationRun, run_id)
                if run:
                    run.status = VerificationJobStatus.CANCELLED.value
                    run.cancelled_at = run.cancelled_at or datetime.now(UTC)
                    run.error_message = str(c_err)
                    log_audit_event(
                        db,
                        run_id=run.id,
                        event_type=VerificationAuditEventType.CANCELLED.value,
                        actor_user_id=run.cancelled_by_user_id or approved_by_user_id,
                        from_status=VerificationJobStatus.RUNNING.value,
                        to_status=VerificationJobStatus.CANCELLED.value,
                        details={"reason": str(c_err)},
                    )
                    db.commit()
            return None

        except (VerificationSecurityError, Exception) as exc:
            logger.exception("Worker execution error on job %d: %s", run_id, exc)
            with self.session_factory() as db:
                run = db.get(VerificationRun, run_id)
                if run:
                    run.status = VerificationJobStatus.FAILED.value
                    run.error_message = f"Execution failed: {exc}"[:1000]
                    log_audit_event(
                        db,
                        run_id=run.id,
                        event_type=VerificationAuditEventType.FAILED.value,
                        actor_user_id=approved_by_user_id,
                        from_status=VerificationJobStatus.RUNNING.value,
                        to_status=VerificationJobStatus.FAILED.value,
                        details={"error": str(exc)},
                    )
                    db.commit()
            return None

        finally:
            await clear_cancellation_event(run_id)

    async def run_once(self) -> bool:
        """Atomically claim and process at most one job.

        Returns True if a job was claimed and processed, False if queue was empty.
        """
        run_id = None
        with self.session_factory() as db:
            run = VerificationJobManager.claim_next_job(db, worker_id=self.worker_id)
            if run is not None:
                run_id = run.id

        if run_id is None:
            return False

        await self.process_job(run_id)
        return True

    async def run(self, max_jobs: int | None = None) -> int:
        """Run the worker polling loop until stopped or max_jobs processed.

        Returns total count of processed jobs.
        """
        self._is_running = True
        jobs_processed = 0
        logger.info(
            "WorkerLoop '%s' started. poll_interval=%.2fs, max_jobs=%s",
            self.worker_id,
            self.poll_interval,
            max_jobs,
        )

        try:
            while not self._stop_event.is_set():
                if max_jobs is not None and jobs_processed >= max_jobs:
                    break

                claimed = await self.run_once()
                if claimed:
                    jobs_processed += 1
                else:
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(), timeout=self.poll_interval
                        )
                    except TimeoutError:
                        pass
        finally:
            self._is_running = False
            logger.info(
                "WorkerLoop '%s' stopped gracefully. Total jobs processed: %d",
                self.worker_id,
                jobs_processed,
            )

        return jobs_processed


def main():
    """Standalone CLI entrypoint for RepoPilot verification worker."""
    parser = argparse.ArgumentParser(
        description="RepoPilot Fix — Asynchronous Verification Worker"
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("WORKER_POLL_INTERVAL", "1.0")),
        help="Queue polling interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one job from the queue and exit",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Maximum number of jobs to process before exiting",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Custom worker instance identifier",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=os.getenv("LOG_LEVEL", "INFO").upper(),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    worker = WorkerLoop(
        poll_interval=args.poll_interval,
        worker_id=args.worker_id,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal(*_):
        logger.info("Received termination signal. Shutting down gracefully...")
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _handle_signal)

    try:
        if args.once:
            loop.run_until_complete(worker.run_once())
        else:
            loop.run_until_complete(worker.run(max_jobs=args.max_jobs))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
