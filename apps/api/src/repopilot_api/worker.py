"""Lightweight, durable asynchronous verification worker for RepoPilot Fix.

Milestone 3.6 Phase 3 & Phase 4:
- Phase 3:
  - Connects asynchronous worker to ProofOfFixEngine and sandbox abstractions.
  - Enforces Phase 1 ExecutionPolicyGuard fail-closed before any execution.
  - Enforces explicit execution mode (default mock, Docker disabled by default).
  - Enforces hardened container boundaries with non-root, read-only rootfs, no network, drop caps.
  - Implements bounded concurrency with database-backed global and per-repo execution slots.
  - Guarantees deterministic baseline vs. patched comparisons.
- Phase 4:
  - Durable worker ownership: worker_id, fencing_token, lease_expires_at, last_heartbeat_at.
  - Heartbeat background renewal loop with short-lived DB sessions.
  - Fencing token check prevents stale workers from writing final results.
  - Cross-process cancellation detection through durable DB state.
  - Idempotent crash recovery reconciles expired leases safely to FAILED/INTERRUPTED.
  - Scoped container cleanup strictly targeting proven RepoPilot containers with ownership labels.
  - Startup reconciliation clears stale jobs before claiming new work.
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
    Repository,
    VerificationRun,
)
from repopilot_api.proof_engine import (
    ProofOfFixCancelledError,
    ProofOfFixEngine,
)
from repopilot_api.sandbox import (
    DEFAULT_SCRATCH_ROOT,
    DockerSandboxRunner,
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
    DEFAULT_LEASE_DURATION_SECONDS,
    VerificationJobManager,
    VerificationSchemaError,
    VerificationSecurityError,
    clear_cancellation_event,
    has_lease_columns,
    log_audit_event,
    register_cancellation_event,
)

logger = logging.getLogger(__name__)


def _is_lease_valid(lease_expires_at: datetime | None, now: datetime) -> bool:
    """Check if worker lease is still active, handling naive and aware datetimes safely."""
    if lease_expires_at is None:
        return True
    if lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
    return lease_expires_at > now


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
        lease_duration: float = float(DEFAULT_LEASE_DURATION_SECONDS),
        heartbeat_interval: float = 5.0,
        max_global_concurrent: int | None = None,
        max_repo_concurrent: int | None = None,
    ):
        self.session_factory = session_factory or SessionLocal
        self.runner = runner
        self.fixture_registry = fixture_registry or get_default_fixture_registry()
        self.scratch_root = scratch_root or DEFAULT_SCRATCH_ROOT
        self.poll_interval = max(0.01, poll_interval)
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease_duration = max(5.0, lease_duration)
        self.heartbeat_interval = max(0.1, min(heartbeat_interval, self.lease_duration / 2))
        self.max_global_concurrent = max_global_concurrent
        self.max_repo_concurrent = max_repo_concurrent

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

    def stop(self, cancel_active: bool = False) -> None:
        """Request graceful shutdown (or immediate cancellation if cancel_active is True)."""
        logger.info(
            "Signaling stop to WorkerLoop '%s' (cancel_active=%s)...",
            self.worker_id,
            cancel_active,
        )
        self._stop_event.set()
        if cancel_active and self._current_job_id is not None:
            job_to_cancel = self._current_job_id
            try:
                loop = asyncio.get_running_loop()
                from repopilot_api.verification_service import signal_cancellation
                loop.create_task(signal_cancellation(job_to_cancel))
            except RuntimeError:
                pass

    def _fail_security(
        self,
        db: Session,
        run: VerificationRun,
        reason: str,
        fencing_token: int | None = None,
    ) -> None:
        """Mark a job as FAILED due to a security violation, with audit log."""
        if fencing_token is not None and run.fencing_token != fencing_token:
            logger.warning(
                "Worker %s lost ownership before security failure on job %d",
                self.worker_id,
                run.id,
            )
            return

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

    async def _heartbeat_loop(
        self,
        run_id: int,
        fencing_token: int,
        cancel_event: asyncio.Event,
    ) -> None:
        """Periodically renew lease in database and monitor for cross-process cancellation."""
        try:
            while not cancel_event.is_set():
                await asyncio.sleep(self.heartbeat_interval)
                if cancel_event.is_set():
                    break

                with self.session_factory() as db:
                    # Check cross-process cancellation through durable state
                    current_run = db.get(VerificationRun, run_id)
                    if (
                        current_run is None
                        or current_run.status == VerificationJobStatus.CANCELLED.value
                        or current_run.cancelled_at is not None
                    ):
                        logger.info(
                            "Heartbeat detected cross-process cancellation on job %d.",
                            run_id,
                        )
                        cancel_event.set()
                        break

                    # Refresh lease with fencing check
                    renewed = VerificationJobManager.heartbeat(
                        db,
                        run_id=run_id,
                        worker_id=self.worker_id,
                        fencing_token=fencing_token,
                        lease_duration_seconds=int(self.lease_duration),
                    )
                    if not renewed:
                        logger.warning(
                            "Worker '%s' lost lease / ownership for job %d (fenced out). "
                            "Aborting execution.",
                            self.worker_id,
                            run_id,
                        )
                        cancel_event.set()
                        break
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Heartbeat encountered error on job %d: %s", run_id, exc)

    async def process_job(
        self, run_id: int, fencing_token: int | None = None
    ) -> ProofOfFixResult | None:
        """Process a single claimed verification job.

        Enforces concurrency limit (at most 1 concurrent job per worker instance),
        executes Phase 1 ExecutionPolicyGuard checks fail-closed, runs sandbox verification,
        and records lifecycle transitions with short-lived database sessions.
        """
        async with self._processing_lock:
            self._current_job_id = run_id
            try:
                return await self._execute_claimed_job(run_id, fencing_token=fencing_token)
            finally:
                self._current_job_id = None

    async def _execute_claimed_job(
        self, run_id: int, fencing_token: int | None = None
    ) -> ProofOfFixResult | None:
        # ----------------------------------------------------------------------
        # Session 1: Security Policy Guard & Snapshot Loading (Short-Lived)
        # ----------------------------------------------------------------------
        with self.session_factory() as db:
            run = db.get(VerificationRun, run_id)
            if run is None:
                logger.error("Job %d not found in database.", run_id)
                return None

            effective_fencing_token = (
                fencing_token if fencing_token is not None else (run.fencing_token or 0)
            )

            # Check if job was cancelled before execution started
            if run.status == VerificationJobStatus.CANCELLED.value or run.cancelled_at is not None:
                logger.info("Job %d was cancelled before execution.", run_id)
                return None

            # Fencing check
            if run.worker_id != self.worker_id or run.fencing_token != effective_fencing_token:
                logger.warning(
                    "Worker '%s' cannot execute job %d: ownership lost "
                    "(worker=%s, token=%d vs %d).",
                    self.worker_id,
                    run_id,
                    run.worker_id,
                    run.fencing_token,
                    effective_fencing_token,
                )
                return None

            # Guard: Verify human approval
            if not run.approved_spec_hash:
                self._fail_security(
                    db, run, "No execution without human approval.", effective_fencing_token
                )
                return None
            if not run.approved_by_user_id:
                self._fail_security(
                    db,
                    run,
                    "Missing approved_by_user_id on approved verification.",
                    effective_fencing_token,
                )
                return None

            # Guard: Fail closed on missing user identity
            effective_user_id = run.approved_by_user_id or run.user_id
            if effective_user_id is None:
                self._fail_security(
                    db,
                    run,
                    "Verification execution requires an authenticated user.",
                    effective_fencing_token,
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
                    db,
                    run,
                    f"Execution policy validation failed: {sec_err}",
                    effective_fencing_token,
                )
                return None

            # Parse test command
            try:
                test_command = json.loads(run.test_command)
            except Exception as exc:
                self._fail_security(
                    db, run, f"Corrupted test_command JSON: {exc}", effective_fencing_token
                )
                return None

            # Parse patches
            try:
                patch_data = json.loads(run.patches_json or "[]")
                if patch_data:
                    patches = [PatchProposal(**p) for p in patch_data]
                else:
                    patches = [PatchProposal(file_path="source_code", diff=run.patch_diff)]
            except Exception as exc:
                self._fail_security(
                    db, run, f"Corrupted patches JSON: {exc}", effective_fencing_token
                )
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
                    effective_fencing_token,
                )
                return None

            indexed = db.scalars(
                select(IndexedFile).where(IndexedFile.job_id == job.id)
            ).all()
            if not indexed:
                self._fail_security(
                    db,
                    run,
                    f"No indexed files found for job {job.id}.",
                    effective_fencing_token,
                )
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
                verification_run_id=run.id,
                worker_id=self.worker_id,
                approved_spec_hash=run.approved_spec_hash,
            )

        # ----------------------------------------------------------------------
        # Runner Check & Docker Restriction Enforcement
        # ----------------------------------------------------------------------
        active_runner = self.runner or get_sandbox_runner(worker_id=self.worker_id)
        allow_docker_env = os.getenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "").strip().lower()

        # Enforce that real Docker execution strictly requires:
        # 1. Explicit activation flag (ALLOW_DOCKER_VERIFICATION_EXECUTION=true)
        # 2. Target repository must be an authentic internal demo fixture
        from repopilot_api.demo import is_internal_demo_repository

        with self.session_factory() as check_db:
            repo = check_db.get(Repository, proof_request.repository_id)
            is_demo = is_internal_demo_repository(check_db, repo, user_id=None)

        docker_enabled_flag = allow_docker_env in ("true", "1")
        allow_docker = docker_enabled_flag and is_demo

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
                        "Real Docker execution is disabled: strictly restricted to registered "
                        "demo fixtures with ALLOW_DOCKER_VERIFICATION_EXECUTION=true.",
                        effective_fencing_token,
                    )
            return None

        # ----------------------------------------------------------------------
        # Execution (No DB Session Held During Sandbox Run)
        # Heartbeat loop runs in background to renew lease & detect cancellation
        # ----------------------------------------------------------------------
        cancel_event = await register_cancellation_event(run_id)
        engine = ProofOfFixEngine(
            runner=active_runner,
            scratch_root=self.scratch_root,
        )

        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(run_id, effective_fencing_token, cancel_event)
        )

        try:
            result = await engine.verify(
                proof_request,
                db=None,
                cancellation_token=cancel_event,
            )

            # ------------------------------------------------------------------
            # Session 2: Persist Outcome with Strict Fencing & Row Lock (FIX 2 / SEC-02)
            # ------------------------------------------------------------------
            with self.session_factory() as db:
                bind = db.get_bind()
                dialect_name = bind.dialect.name if bind is not None else ""
                query = select(VerificationRun).where(VerificationRun.id == run_id)
                if dialect_name == "postgresql":
                    query = query.with_for_update()

                run = db.scalar(query)
                if run is None:
                    return result

                now = datetime.now(UTC)
                is_lease_valid = _is_lease_valid(run.lease_expires_at, now)
                if (
                    run.status != VerificationJobStatus.RUNNING.value
                    or run.worker_id != self.worker_id
                    or run.fencing_token != effective_fencing_token
                    or not is_lease_valid
                ):
                    logger.warning(
                        "Worker '%s' was fenced out or job %d lost ownership before persistence "
                        "(status=%s, worker=%s, token=%s vs %s, lease_valid=%s). "
                        "Discarding stale result.",
                        self.worker_id,
                        run_id,
                        run.status,
                        run.worker_id,
                        run.fencing_token,
                        effective_fencing_token,
                        is_lease_valid,
                    )
                    db.rollback()
                    return None

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

                # Ensure cryptographic evidence spec binding is never null when run was approved
                if not result.evidence.approved_spec_hash and run.approved_spec_hash:
                    result.evidence.approved_spec_hash = run.approved_spec_hash

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
                bind = db.get_bind()
                dialect_name = bind.dialect.name if bind is not None else ""
                query = select(VerificationRun).where(VerificationRun.id == run_id)
                if dialect_name == "postgresql":
                    query = query.with_for_update()
                run = db.scalar(query)
                if (
                    run
                    and run.status == VerificationJobStatus.RUNNING.value
                    and run.worker_id == self.worker_id
                    and run.fencing_token == effective_fencing_token
                ):
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
                else:
                    db.rollback()
            return None

        except (VerificationSecurityError, Exception) as exc:
            logger.exception("Worker execution error on job %d: %s", run_id, exc)
            with self.session_factory() as db:
                bind = db.get_bind()
                dialect_name = bind.dialect.name if bind is not None else ""
                query = select(VerificationRun).where(VerificationRun.id == run_id)
                if dialect_name == "postgresql":
                    query = query.with_for_update()
                run = db.scalar(query)
                if (
                    run
                    and run.status == VerificationJobStatus.RUNNING.value
                    and run.worker_id == self.worker_id
                    and run.fencing_token == effective_fencing_token
                ):
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
                else:
                    db.rollback()
            return None

        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass

            # Safe scoped container cleanup (only touching proven RepoPilot containers)
            if is_docker_runner:
                try:
                    await DockerSandboxRunner.safe_cleanup_containers(
                        run_id=run_id, worker_id=self.worker_id
                    )
                except Exception as cl_err:
                    logger.warning("Error during post-execution container cleanup: %s", cl_err)

            await clear_cancellation_event(run_id)

    async def run_once(self, target_run_id: int | None = None) -> bool:
        """Atomically claim and process at most one job.

        Returns True if a job was claimed and processed, False if queue was empty or slots full.
        """
        run_id = None
        fencing_token = None
        with self.session_factory() as db:
            run = VerificationJobManager.claim_next_job(
                db,
                worker_id=self.worker_id,
                lease_duration_seconds=int(self.lease_duration),
                max_global_concurrent=self.max_global_concurrent,
                max_repo_concurrent=self.max_repo_concurrent,
                target_run_id=target_run_id,
            )
            if run is not None:
                run_id = run.id
                fencing_token = run.fencing_token

        if run_id is None:
            return False

        await self.process_job(run_id, fencing_token=fencing_token)
        return True

    async def run(
        self,
        max_jobs: int | None = None,
        target_run_id: int | None = None,
    ) -> int:
        """Run the worker polling loop until stopped or max_jobs processed.

        Performs crash recovery reconciliation on startup before claiming new work.
        Returns total count of processed jobs.
        """
        self._is_running = True
        jobs_processed = 0
        logger.info(
            "WorkerLoop '%s' started. poll_interval=%.2fs, lease_duration=%.1fs, "
            "max_jobs=%s, target_run_id=%s",
            self.worker_id,
            self.poll_interval,
            self.lease_duration,
            max_jobs,
            target_run_id,
        )

        # ----------------------------------------------------------------------
        # Startup: Reconcile Stale Jobs & Lingering Owned Containers
        # ----------------------------------------------------------------------
        try:
            with self.session_factory() as db:
                if not has_lease_columns(db):
                    raise VerificationSchemaError(
                        "Database schema is missing required durable worker lease columns "
                        "(from migration 0007). Run 'alembic upgrade head' before "
                        "starting the verification worker."
                    )
                reconcile_legacy_flag = os.getenv("RECONCILE_LEGACY_JOBS", "false").lower() in (
                    "true",
                    "1",
                    "yes",
                )
                reconciled = VerificationJobManager.reconcile_stale_jobs(
                    db, reconcile_legacy=reconcile_legacy_flag
                )
                if reconciled:
                    logger.info(
                        "Worker '%s' startup recovery: reconciled %d stale jobs: %s",
                        self.worker_id,
                        len(reconciled),
                        reconciled,
                    )
            provider = os.getenv("SANDBOX_PROVIDER", "mock").lower()
            if provider == "docker" or (
                self.runner
                and getattr(self.runner, "__class__", None).__name__ == "DockerSandboxRunner"
            ):
                await DockerSandboxRunner.safe_cleanup_containers(worker_id=self.worker_id)
        except VerificationSchemaError:
            raise
        except Exception as startup_err:
            logger.warning("Worker startup reconciliation encountered: %s", startup_err)

        try:
            while not self._stop_event.is_set():
                if max_jobs is not None and jobs_processed >= max_jobs:
                    break

                try:
                    claimed = await self.run_once(target_run_id=target_run_id)
                except VerificationSchemaError:
                    raise
                if claimed:
                    jobs_processed += 1
                    if target_run_id is not None:
                        break
                else:
                    if target_run_id is not None:
                        # Targeted run was not available/claimable; terminate loop
                        break
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
        "--lease-seconds",
        type=float,
        default=float(os.getenv("WORKER_LEASE_SECONDS", str(DEFAULT_LEASE_DURATION_SECONDS))),
        help="Worker lease expiration duration in seconds (default: 30.0)",
    )
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=float(os.getenv("WORKER_HEARTBEAT_SECONDS", "5.0")),
        help="Heartbeat renewal interval in seconds (default: 5.0)",
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
        "--run-id",
        type=int,
        default=None,
        help="Target a specific verification run ID instead of claiming the oldest queued run",
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
        lease_duration=args.lease_seconds,
        heartbeat_interval=args.heartbeat_seconds,
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
            loop.run_until_complete(worker.run_once(target_run_id=args.run_id))
        else:
            loop.run_until_complete(
                worker.run(max_jobs=args.max_jobs, target_run_id=args.run_id)
            )
    finally:
        loop.close()


if __name__ == "__main__":
    main()
