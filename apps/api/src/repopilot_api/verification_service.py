"""Secure verification service, human approval workflow, and durable job management."""

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from repopilot_api.models import (
    VerificationAuditEvent,
    VerificationRun,
)
from repopilot_api.proof_engine import (
    ProofOfFixCancelledError,
    ProofOfFixEngine,
)
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    DEFAULT_SCRATCH_ROOT,
    CommandValidator,
    SandboxConfigurationError,
    SandboxRunner,
    SandboxSecurityError,
    get_sandbox_runner,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProofOfFixRequest,
    ProofOfFixResult,
    ProposedFix,
    VerificationAuditEventType,
    VerificationCreateRequest,
    VerificationJobStatus,
)

logger = logging.getLogger(__name__)

# Server-controlled sandbox image allowlist
APPROVED_SANDBOX_IMAGES = {
    DEFAULT_SANDBOX_IMAGE,
    "python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535",
    "python:3.11-slim",
    "python:3.12-slim",
    "repopilot-sandbox:test",
}

# Concurrency limits
MAX_REPO_CONCURRENT_JOBS = int(os.getenv("MAX_REPO_CONCURRENT_JOBS", "1"))
MAX_GLOBAL_CONCURRENT_JOBS = int(os.getenv("MAX_GLOBAL_CONCURRENT_JOBS", "2"))


class VerificationServiceError(Exception):
    """Base exception for verification service operations."""


class VerificationSecurityError(VerificationServiceError):
    """Raised when security boundaries or allowlists are violated."""


class VerificationStateError(VerificationServiceError):
    """Raised on invalid lifecycle state transitions."""


class VerificationSpecificationMismatchError(VerificationServiceError):
    """Raised when an approved specification differs from runtime state."""


class VerificationConcurrencyError(VerificationServiceError):
    """Raised when concurrency limits are reached."""


class VerificationInfrastructureError(VerificationServiceError):
    """Raised when sandbox or runner infrastructure fails."""


def compute_patch_hash(patches: list[PatchProposal] | ProposedFix) -> tuple[str, str]:
    """Compute combined diff and deterministic SHA-256 patch hash."""
    patch_list = patches.patches if isinstance(patches, ProposedFix) else patches
    if not patch_list:
        raise VerificationServiceError("At least one patch proposal is required.")
    combined_diff = "\n".join(p.diff.strip() for p in patch_list)
    patch_hash = hashlib.sha256(combined_diff.encode("utf-8")).hexdigest()
    return patch_hash, combined_diff


def compute_spec_hash(
    commit_sha: str,
    patch_hash: str,
    test_command: list[str],
    sandbox_image: str,
) -> str:
    """Compute canonical SHA-256 specification hash for immutable human approval."""
    payload = {
        "commit_sha": commit_sha.strip().lower(),
        "patch_hash": patch_hash.strip().lower(),
        "sandbox_image": sandbox_image.strip(),
        "test_command": [str(c).strip() for c in test_command],
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def log_audit_event(
    db: Session,
    run_id: int,
    event_type: str,
    actor_user_id: int | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    details: dict | None = None,
) -> VerificationAuditEvent:
    """Record an immutable verification lifecycle audit event."""
    event = VerificationAuditEvent(
        verification_run_id=run_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        from_status=from_status,
        to_status=to_status,
        details_json=json.dumps(details or {}),
    )
    db.add(event)
    db.flush()
    return event


# Registry of active cancellation events for in-flight jobs
_ACTIVE_CANCELLATION_EVENTS: dict[int, asyncio.Event] = {}
_ACTIVE_LOCK = asyncio.Lock()


async def register_cancellation_event(run_id: int) -> asyncio.Event:
    async with _ACTIVE_LOCK:
        event = asyncio.Event()
        _ACTIVE_CANCELLATION_EVENTS[run_id] = event
        return event


async def signal_cancellation(run_id: int) -> bool:
    async with _ACTIVE_LOCK:
        event = _ACTIVE_CANCELLATION_EVENTS.get(run_id)
        if event is not None:
            event.set()
            return True
        return False


async def clear_cancellation_event(run_id: int) -> None:
    async with _ACTIVE_LOCK:
        _ACTIVE_CANCELLATION_EVENTS.pop(run_id, None)


# In-memory sliding window rate limiter
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(key: str, max_requests: int = 15, window_seconds: int = 60) -> bool:
    """Check if request count for key exceeds rate limit."""
    now = time.monotonic()
    timestamps = _RATE_LIMIT_BUCKETS[key]
    cutoff = now - window_seconds
    _RATE_LIMIT_BUCKETS[key] = [t for t in timestamps if t > cutoff]
    if len(_RATE_LIMIT_BUCKETS[key]) >= max_requests:
        return False
    _RATE_LIMIT_BUCKETS[key].append(now)
    return True


class VerificationJobManager:
    """Manages the verification job lifecycle, approval workflow, and durability."""

    @classmethod
    def validate_sandbox_image(cls, image: str | None) -> str:
        """Enforce server-controlled sandbox image allowlist."""
        chosen = (image or DEFAULT_SANDBOX_IMAGE).strip()
        if chosen not in APPROVED_SANDBOX_IMAGES:
            raise VerificationSecurityError(
                f"Sandbox image '{chosen}' is not in the server allowlist. "
                f"Approved images: {sorted(APPROVED_SANDBOX_IMAGES)}"
            )
        return chosen

    @classmethod
    def create_verification_request(
        cls,
        db: Session,
        repository_id: int,
        user_id: int,
        payload: VerificationCreateRequest,
        idempotency_key: str | None = None,
    ) -> tuple[VerificationRun, bool]:
        """Create a new verification job with specification hash and approval state.

        Returns (verification_run, is_new).
        """
        # Idempotency check: if an existing run with this key exists, return it
        idem_key = idempotency_key or payload.idempotency_key
        if idem_key:
            existing = db.scalar(
                select(VerificationRun).where(
                    VerificationRun.repository_id == repository_id,
                    VerificationRun.idempotency_key == idem_key,
                )
            )
            if existing is not None:
                return existing, False

        # Validate test command against approved entrypoints and forbidden characters
        CommandValidator.validate(payload.test_command)

        # Validate sandbox image against server allowlist
        sandbox_image = cls.validate_sandbox_image(payload.sandbox_image)

        # Extract patch proposals and compute cryptographic patch hash
        patches = (
            payload.patches.patches
            if isinstance(payload.patches, ProposedFix)
            else payload.patches
        )
        patch_hash, combined_diff = compute_patch_hash(patches)
        patches_json = json.dumps([p.model_dump() for p in patches])

        # Compute immutable verification specification hash
        spec_hash = compute_spec_hash(
            payload.commit_sha, patch_hash, payload.test_command, sandbox_image
        )

        initial_status = (
            VerificationJobStatus.PENDING_APPROVAL.value
            if payload.auto_submit_approval
            else VerificationJobStatus.DRAFT.value
        )

        run = VerificationRun(
            repository_id=repository_id,
            commit_sha=payload.commit_sha,
            issue_number=payload.issue_number,
            user_id=user_id,
            status=initial_status,
            spec_hash=spec_hash,
            test_command=json.dumps(payload.test_command),
            hypothesis=payload.hypothesis,
            patch_diff=combined_diff,
            patch_hash=patch_hash,
            patches_json=patches_json,
            sandbox_image=sandbox_image,
            idempotency_key=idem_key,
            retry_count=0,
            max_retries=2,
        )
        db.add(run)
        db.flush()

        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.CREATED.value,
            actor_user_id=user_id,
            to_status=initial_status,
            details={
                "spec_hash": spec_hash,
                "commit_sha": payload.commit_sha,
                "issue_number": payload.issue_number,
                "sandbox_image": sandbox_image,
                "idempotency_key": idem_key,
            },
        )

        if payload.auto_submit_approval:
            log_audit_event(
                db,
                run_id=run.id,
                event_type=VerificationAuditEventType.SUBMITTED_FOR_APPROVAL.value,
                actor_user_id=user_id,
                from_status=VerificationJobStatus.DRAFT.value,
                to_status=VerificationJobStatus.PENDING_APPROVAL.value,
            )

        db.commit()
        db.refresh(run)
        return run, True

    @classmethod
    def submit_for_approval(cls, db: Session, run_id: int, user_id: int) -> VerificationRun:
        """Submit a DRAFT verification specification for human approval."""
        run = db.get(VerificationRun, run_id)
        if run is None:
            raise VerificationStateError("Verification run not found.")
        if run.status != VerificationJobStatus.DRAFT.value:
            raise VerificationStateError(
                f"Cannot submit verification in state '{run.status}' for approval. "
                f"Must be '{VerificationJobStatus.DRAFT.value}'."
            )

        prev_status = run.status
        run.status = VerificationJobStatus.PENDING_APPROVAL.value
        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.SUBMITTED_FOR_APPROVAL.value,
            actor_user_id=user_id,
            from_status=prev_status,
            to_status=run.status,
            details={"spec_hash": run.spec_hash},
        )
        db.commit()
        db.refresh(run)
        return run

    @classmethod
    def approve_verification(
        cls,
        db: Session,
        run_id: int,
        user_id: int,
        expected_spec_hash: str | None = None,
        auto_queue: bool = True,
        comment: str | None = None,
    ) -> VerificationRun:
        """Explicitly approve an immutable verification specification."""
        run = db.get(VerificationRun, run_id)
        if run is None:
            raise VerificationStateError("Verification run not found.")

        # Approval allowed only from PENDING_APPROVAL or DRAFT
        if run.status not in (
            VerificationJobStatus.PENDING_APPROVAL.value,
            VerificationJobStatus.DRAFT.value,
        ):
            raise VerificationStateError(
                f"Cannot approve verification in state '{run.status}'. "
                f"Run must be in '{VerificationJobStatus.PENDING_APPROVAL.value}'."
            )

        # Specification hash integrity check
        if expected_spec_hash and expected_spec_hash.strip() != run.spec_hash:
            raise VerificationSpecificationMismatchError(
                "Specification hash mismatch: the verification specification has changed "
                "since the review was prepared."
            )

        prev_status = run.status
        run.status = (
            VerificationJobStatus.QUEUED.value
            if auto_queue
            else VerificationJobStatus.APPROVED.value
        )
        run.approved_spec_hash = run.spec_hash
        run.approved_by_user_id = user_id
        run.approved_at = datetime.now(UTC)

        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.APPROVED.value,
            actor_user_id=user_id,
            from_status=prev_status,
            to_status=VerificationJobStatus.APPROVED.value,
            details={
                "approved_spec_hash": run.approved_spec_hash,
                "auto_queue": auto_queue,
                "comment": comment or "",
            },
        )

        if auto_queue:
            log_audit_event(
                db,
                run_id=run.id,
                event_type=VerificationAuditEventType.QUEUED.value,
                actor_user_id=user_id,
                from_status=VerificationJobStatus.APPROVED.value,
                to_status=VerificationJobStatus.QUEUED.value,
            )

        db.commit()
        db.refresh(run)
        return run

    @classmethod
    def reject_verification(
        cls, db: Session, run_id: int, user_id: int, reason: str = ""
    ) -> VerificationRun:
        """Reject a verification request."""
        run = db.get(VerificationRun, run_id)
        if run is None:
            raise VerificationStateError("Verification run not found.")

        if run.status not in (
            VerificationJobStatus.PENDING_APPROVAL.value,
            VerificationJobStatus.DRAFT.value,
        ):
            raise VerificationStateError(
                f"Cannot reject verification in state '{run.status}'. "
                f"Must be in '{VerificationJobStatus.PENDING_APPROVAL.value}' or "
                f"'{VerificationJobStatus.DRAFT.value}'."
            )

        prev_status = run.status
        run.status = VerificationJobStatus.REJECTED.value
        run.rejection_reason = reason[:500] if reason else "Rejected by reviewer."

        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.REJECTED.value,
            actor_user_id=user_id,
            from_status=prev_status,
            to_status=run.status,
            details={"reason": run.rejection_reason},
        )
        db.commit()
        db.refresh(run)
        return run

    @classmethod
    async def cancel_verification(
        cls, db: Session, run_id: int, user_id: int, reason: str = ""
    ) -> VerificationRun:
        """Cancel an in-flight, queued, or pending verification run."""
        run = db.get(VerificationRun, run_id)
        if run is None:
            raise VerificationStateError("Verification run not found.")

        allowed_cancel_states = {
            VerificationJobStatus.DRAFT.value,
            VerificationJobStatus.PENDING_APPROVAL.value,
            VerificationJobStatus.APPROVED.value,
            VerificationJobStatus.QUEUED.value,
            VerificationJobStatus.RUNNING.value,
        }
        if run.status not in allowed_cancel_states:
            raise VerificationStateError(
                f"Cannot cancel verification in state '{run.status}'. "
                f"Terminal states cannot be cancelled."
            )

        prev_status = run.status
        run.status = VerificationJobStatus.CANCELLED.value
        run.cancelled_at = datetime.now(UTC)
        run.cancelled_by_user_id = user_id
        run.cancellation_reason = reason[:500] if reason else "Cancelled by user."

        # If currently running, signal the cancellation event
        if prev_status == VerificationJobStatus.RUNNING.value:
            await signal_cancellation(run_id)

        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.CANCELLED.value,
            actor_user_id=user_id,
            from_status=prev_status,
            to_status=run.status,
            details={"reason": run.cancellation_reason},
        )
        db.commit()
        db.refresh(run)
        return run

    @classmethod
    def queue_verification(cls, db: Session, run_id: int, user_id: int) -> VerificationRun:
        """Queue an APPROVED verification run for execution."""
        run = db.get(VerificationRun, run_id)
        if run is None:
            raise VerificationStateError("Verification run not found.")

        if run.status != VerificationJobStatus.APPROVED.value:
            raise VerificationStateError(
                f"Cannot queue verification in state '{run.status}'. Run must be 'approved'."
            )

        # Re-verify spec hash has not been tampered with
        if run.spec_hash != run.approved_spec_hash:
            raise VerificationSpecificationMismatchError(
                "Specification tampered: spec_hash does not match approved_spec_hash."
            )

        run.status = VerificationJobStatus.QUEUED.value
        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.QUEUED.value,
            actor_user_id=user_id,
            from_status=VerificationJobStatus.APPROVED.value,
            to_status=run.status,
        )
        db.commit()
        db.refresh(run)
        return run

    @classmethod
    async def execute_job(
        cls,
        db: Session,
        run_id: int,
        runner: SandboxRunner | None = None,
        scratch_root: str | None = None,
    ) -> ProofOfFixResult:
        """Execute an approved, queued verification job with strict security and state controls."""
        run = db.get(VerificationRun, run_id)
        if run is None:
            raise VerificationStateError("Verification run not found.")

        # Guard: Run must be APPROVED or QUEUED
        if run.status not in (
            VerificationJobStatus.APPROVED.value,
            VerificationJobStatus.QUEUED.value,
        ):
            raise VerificationStateError(
                f"Cannot execute verification in state '{run.status}'. "
                f"Must be '{VerificationJobStatus.APPROVED.value}' or "
                f"'{VerificationJobStatus.QUEUED.value}'."
            )

        # Guard: Human approval check
        if not run.approved_spec_hash:
            raise VerificationSecurityError("No execution without human approval.")

        # Guard: Specification Immutability Check
        test_command = json.loads(run.test_command)
        current_spec = compute_spec_hash(
            run.commit_sha, run.patch_hash or "", test_command, run.sandbox_image
        )
        if current_spec != run.approved_spec_hash:
            run.status = VerificationJobStatus.FAILED.value
            run.error_message = (
                "Specification mismatch: verification specification altered after approval."
            )
            log_audit_event(
                db,
                run_id=run.id,
                event_type=VerificationAuditEventType.FAILED.value,
                from_status=run.status,
                to_status=VerificationJobStatus.FAILED.value,
                details={"reason": "specification_tampered_after_approval"},
            )
            db.commit()
            raise VerificationSpecificationMismatchError(
                "Verification specification altered after approval. Execution rejected."
            )

        # Guard: Concurrency check
        repo_running = db.scalar(
            select(func.count(VerificationRun.id)).where(
                VerificationRun.repository_id == run.repository_id,
                VerificationRun.status == VerificationJobStatus.RUNNING.value,
                VerificationRun.id != run.id,
            )
        ) or 0
        global_running = db.scalar(
            select(func.count(VerificationRun.id)).where(
                VerificationRun.status == VerificationJobStatus.RUNNING.value,
                VerificationRun.id != run.id,
            )
        ) or 0
        if (
            repo_running >= MAX_REPO_CONCURRENT_JOBS
            or global_running >= MAX_GLOBAL_CONCURRENT_JOBS
        ):
            run.status = VerificationJobStatus.QUEUED.value
            db.commit()
            raise VerificationConcurrencyError(
                f"Concurrency limit reached (Repo: {repo_running}/{MAX_REPO_CONCURRENT_JOBS}, "
                f"Global: {global_running}/{MAX_GLOBAL_CONCURRENT_JOBS}). Job remains queued."
            )

        # Resolve Sandbox Runner
        active_runner = runner or get_sandbox_runner()

        # Guard: Restrict real Docker execution in normal API flows
        allow_docker = os.getenv("ALLOW_DOCKER_VERIFICATION_EXECUTION", "false").lower() in (
            "true",
            "1",
        )
        is_docker_runner = (
            hasattr(active_runner, "run")
            and getattr(active_runner, "__class__", None).__name__ == "DockerSandboxRunner"
        )
        if is_docker_runner and not allow_docker:
            raise VerificationSecurityError(
                "Real Docker execution is disabled in normal API flows. "
                "Set ALLOW_DOCKER_VERIFICATION_EXECUTION=true after security review."
            )

        # Transition to RUNNING
        prev_status = run.status
        run.status = VerificationJobStatus.RUNNING.value
        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.STARTED.value,
            actor_user_id=run.approved_by_user_id,
            from_status=prev_status,
            to_status=run.status,
        )
        db.commit()

        # Register active cancellation token
        cancel_event = await register_cancellation_event(run_id)

        # Reconstruct patch proposal objects
        patch_data = json.loads(run.patches_json or "[]")
        if patch_data:
            patches = [PatchProposal(**p) for p in patch_data]
        else:
            patches = [PatchProposal(file_path="source_code", diff=run.patch_diff)]

        proof_request = ProofOfFixRequest(
            repository_id=run.repository_id,
            commit_sha=run.commit_sha,
            issue_number=run.issue_number,
            test_command=test_command,
            patches=patches,
            hypothesis=run.hypothesis,
            user_id=run.user_id,
        )

        engine = ProofOfFixEngine(
            runner=active_runner,
            scratch_root=scratch_root or DEFAULT_SCRATCH_ROOT,
        )

        try:
            result = await engine.verify(
                proof_request,
                db=db,
                verification_run=run,
                cancellation_token=cancel_event,
            )

            # Check if job was cancelled during execution
            if cancel_event.is_set():
                run.status = VerificationJobStatus.CANCELLED.value
                log_audit_event(
                    db,
                    run_id=run.id,
                    event_type=VerificationAuditEventType.CANCELLED.value,
                    from_status=VerificationJobStatus.RUNNING.value,
                    to_status=VerificationJobStatus.CANCELLED.value,
                    details={"reason": "cancelled_during_execution"},
                )
            else:
                # Execution finished normally: job lifecycle state is COMPLETED!
                run.status = VerificationJobStatus.COMPLETED.value
                log_audit_event(
                    db,
                    run_id=run.id,
                    event_type=VerificationAuditEventType.COMPLETED.value,
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
            run.status = VerificationJobStatus.CANCELLED.value
            run.error_message = str(c_err)
            log_audit_event(
                db,
                run_id=run.id,
                event_type=VerificationAuditEventType.CANCELLED.value,
                from_status=VerificationJobStatus.RUNNING.value,
                to_status=VerificationJobStatus.CANCELLED.value,
                details={"reason": str(c_err)},
            )
            db.commit()
            raise

        except (SandboxConfigurationError, Exception) as exc:
            # Check if retry is permitted for infrastructure failures
            is_infra_error = isinstance(exc, (SandboxConfigurationError, SandboxSecurityError))
            if is_infra_error and run.retry_count < run.max_retries:
                run.retry_count += 1
                run.status = VerificationJobStatus.QUEUED.value
                log_audit_event(
                    db,
                    run_id=run.id,
                    event_type=VerificationAuditEventType.RETRIED.value,
                    from_status=VerificationJobStatus.RUNNING.value,
                    to_status=VerificationJobStatus.QUEUED.value,
                    details={
                        "retry_attempt": run.retry_count,
                        "max_retries": run.max_retries,
                        "error": str(exc),
                    },
                )
            else:
                run.status = VerificationJobStatus.FAILED.value
                run.error_message = f"Execution failed: {exc}"[:1000]
                log_audit_event(
                    db,
                    run_id=run.id,
                    event_type=VerificationAuditEventType.FAILED.value,
                    from_status=VerificationJobStatus.RUNNING.value,
                    to_status=VerificationJobStatus.FAILED.value,
                    details={"error": str(exc)},
                )
            db.commit()
            raise

        finally:
            await clear_cancellation_event(run_id)

    @classmethod
    def recover_stale_jobs(cls, db: Session) -> int:
        """Restart recovery strategy: mark interrupted running jobs as FAILED with audit log."""
        stale_runs = db.scalars(
            select(VerificationRun).where(
                VerificationRun.status == VerificationJobStatus.RUNNING.value
            )
        ).all()

        recovered_count = 0
        for run in stale_runs:
            run.status = VerificationJobStatus.FAILED.value
            run.error_message = (
                "Verification job was interrupted by server restart or process termination."
            )
            log_audit_event(
                db,
                run_id=run.id,
                event_type=VerificationAuditEventType.RECOVERED.value,
                from_status=VerificationJobStatus.RUNNING.value,
                to_status=VerificationJobStatus.FAILED.value,
                details={"reason": "server_restart_recovery"},
            )
            recovered_count += 1

        if recovered_count > 0:
            db.commit()
            logger.warning(
                "Durable Job Manager recovered %d stale RUNNING verification jobs.",
                recovered_count,
            )

        return recovered_count
