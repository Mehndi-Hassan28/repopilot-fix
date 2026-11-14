"""FastAPI router for secure verification requests, human approvals, and reports."""

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from repopilot_api.dependencies import get_db
from repopilot_api.models import (
    ProofReport,
    RepositoryAccess,
    VerificationAuditEvent,
    VerificationRun,
)
from repopilot_api.sandbox import SandboxError
from repopilot_api.schemas import (
    ProofOfFixResult,
    ProofReportRead,
    VerificationApprovalRequest,
    VerificationAuditEventRead,
    VerificationCancelRequest,
    VerificationCreateRequest,
    VerificationRejectionRequest,
    VerificationRunRead,
)
from repopilot_api.security import AuthContext, get_current_auth, require_csrf
from repopilot_api.verification_service import (
    VerificationConcurrencyError,
    VerificationJobManager,
    VerificationSecurityError,
    VerificationSpecificationMismatchError,
    VerificationStateError,
    check_rate_limit,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/repositories/{repository_id}/verifications", tags=["verifications"])


def _require_repository_access(db: Session, repository_id: int, user_id: int) -> RepositoryAccess:
    """Enforce server-side repository authorization."""
    access = db.get(RepositoryAccess, (repository_id, user_id))
    if access is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found.")
    return access


def _get_run_or_404(db: Session, repository_id: int, verification_id: int) -> VerificationRun:
    """Retrieve verification run verifying repository tenancy."""
    run = db.get(VerificationRun, verification_id)
    if run is None or run.repository_id != repository_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Verification run not found.")
    return run


def _run_to_read(run: VerificationRun, report: ProofReport | None = None) -> VerificationRunRead:
    """Serialize VerificationRun model to public Pydantic schema."""
    report_read = None
    if report is not None:
        try:
            ev_dict = json.loads(report.evidence_json)
        except Exception:
            ev_dict = {}
        report_read = ProofReportRead(
            id=report.id,
            verification_run_id=report.verification_run_id,
            verdict=report.verdict,
            hypothesis=report.hypothesis,
            summary=report.summary,
            baseline_summary=report.baseline_summary,
            patched_summary=report.patched_summary,
            confidence=report.confidence,
            evidence=ev_dict,
            created_at=report.created_at.isoformat(),
        )

    try:
        cmd_list = json.loads(run.test_command)
    except Exception:
        cmd_list = []

    return VerificationRunRead(
        id=run.id,
        repository_id=run.repository_id,
        commit_sha=run.commit_sha,
        issue_number=run.issue_number,
        user_id=run.user_id,
        status=run.status,
        spec_hash=run.spec_hash,
        approved_spec_hash=run.approved_spec_hash,
        approved_by_user_id=run.approved_by_user_id,
        approved_at=run.approved_at.isoformat() if run.approved_at else None,
        rejection_reason=run.rejection_reason,
        test_command=cmd_list,
        sandbox_image=run.sandbox_image,
        duration_seconds=run.duration_seconds,
        baseline_exit_code=run.baseline_exit_code,
        patched_exit_code=run.patched_exit_code,
        patch_hash=run.patch_hash,
        retry_count=run.retry_count,
        max_retries=run.max_retries,
        error_message=run.error_message,
        cancelled_at=run.cancelled_at.isoformat() if run.cancelled_at else None,
        cancelled_by_user_id=run.cancelled_by_user_id,
        cancellation_reason=run.cancellation_reason,
        report=report_read,
        created_at=run.created_at.isoformat(),
        updated_at=run.updated_at.isoformat(),
    )


@router.post("", response_model=VerificationRunRead, status_code=status.HTTP_201_CREATED)
def create_verification(
    repository_id: int,
    payload: VerificationCreateRequest,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Create a new verification request with specification hash and approval requirement."""
    # 1. Enforce repository authorization
    _require_repository_access(db, repository_id, auth.user.id)

    # 2. Rate limiting check per user and repository
    rate_key = f"verif_create:{auth.user.id}:{repository_id}"
    if not check_rate_limit(rate_key, max_requests=15, window_seconds=60):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded for verification requests. Please try again later.",
        )

    # 3. Resolve idempotency key from header or body
    effective_idempotency_key = idempotency_key or payload.idempotency_key

    # 4. Create or fetch via job manager
    try:
        run, is_new = VerificationJobManager.create_verification_request(
            db=db,
            repository_id=repository_id,
            user_id=auth.user.id,
            payload=payload,
            idempotency_key=effective_idempotency_key,
        )
    except VerificationSecurityError as sec_err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(sec_err)) from sec_err
    except SandboxError as s_err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(s_err)) from s_err
    except ValueError as v_err:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(v_err)) from v_err

    response.status_code = status.HTTP_201_CREATED if is_new else status.HTTP_200_OK

    return _run_to_read(run)


@router.get("", response_model=list[VerificationRunRead])
def list_verifications(
    repository_id: int,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """List verification runs for an authorized repository with pagination."""
    _require_repository_access(db, repository_id, auth.user.id)

    query = select(VerificationRun).where(VerificationRun.repository_id == repository_id)
    if status_filter:
        query = query.where(VerificationRun.status == status_filter.strip().lower())
    query = query.order_by(VerificationRun.id.desc()).offset(offset).limit(limit)

    runs = db.scalars(query).all()
    return [_run_to_read(r) for r in runs]


@router.get("/{verification_id}", response_model=VerificationRunRead)
def get_verification(
    repository_id: int,
    verification_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Get status and details of a specific verification run."""
    _require_repository_access(db, repository_id, auth.user.id)
    run = _get_run_or_404(db, repository_id, verification_id)
    report = db.scalar(
        select(ProofReport).where(ProofReport.verification_run_id == run.id)
    )
    return _run_to_read(run, report)


@router.post("/{verification_id}/submit", response_model=VerificationRunRead)
def submit_verification(
    repository_id: int,
    verification_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Submit a draft verification specification for human approval."""
    _require_repository_access(db, repository_id, auth.user.id)
    _get_run_or_404(db, repository_id, verification_id)

    try:
        run = VerificationJobManager.submit_for_approval(db, verification_id, auth.user.id)
        return _run_to_read(run)
    except VerificationStateError as state_err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(state_err)) from state_err


@router.post("/{verification_id}/approve", response_model=VerificationRunRead)
def approve_verification(
    repository_id: int,
    verification_id: int,
    payload: VerificationApprovalRequest = VerificationApprovalRequest(),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Explicitly approve an immutable verification specification."""
    access = _require_repository_access(db, repository_id, auth.user.id)
    if access.role not in ("owner", "collaborator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Unauthorized to approve verifications.")

    _get_run_or_404(db, repository_id, verification_id)

    try:
        run = VerificationJobManager.approve_verification(
            db=db,
            run_id=verification_id,
            user_id=auth.user.id,
            expected_spec_hash=payload.expected_spec_hash,
            auto_queue=payload.auto_queue,
            comment=payload.comment,
        )
        return _run_to_read(run)
    except VerificationSpecificationMismatchError as spec_err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(spec_err)) from spec_err
    except VerificationStateError as state_err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(state_err)) from state_err


@router.post("/{verification_id}/reject", response_model=VerificationRunRead)
def reject_verification(
    repository_id: int,
    verification_id: int,
    payload: VerificationRejectionRequest = VerificationRejectionRequest(),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Reject a verification request."""
    _require_repository_access(db, repository_id, auth.user.id)
    _get_run_or_404(db, repository_id, verification_id)

    try:
        run = VerificationJobManager.reject_verification(
            db=db,
            run_id=verification_id,
            user_id=auth.user.id,
            reason=payload.reason,
        )
        return _run_to_read(run)
    except VerificationStateError as state_err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(state_err)) from state_err


@router.post("/{verification_id}/cancel", response_model=VerificationRunRead)
async def cancel_verification(
    repository_id: int,
    verification_id: int,
    payload: VerificationCancelRequest = VerificationCancelRequest(),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Cancel an approved, queued, or running verification job."""
    _require_repository_access(db, repository_id, auth.user.id)
    _get_run_or_404(db, repository_id, verification_id)

    try:
        run = await VerificationJobManager.cancel_verification(
            db=db,
            run_id=verification_id,
            user_id=auth.user.id,
            reason=payload.reason,
        )
        return _run_to_read(run)
    except VerificationStateError as state_err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(state_err)) from state_err


@router.post("/{verification_id}/queue", response_model=VerificationRunRead)
def queue_verification(
    repository_id: int,
    verification_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Enqueue an approved verification run for execution."""
    _require_repository_access(db, repository_id, auth.user.id)
    _get_run_or_404(db, repository_id, verification_id)

    try:
        run = VerificationJobManager.queue_verification(db, verification_id, auth.user.id)
        return _run_to_read(run)
    except (VerificationStateError, VerificationSpecificationMismatchError) as err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(err)) from err


@router.post("/{verification_id}/execute", response_model=ProofOfFixResult)
async def execute_verification(
    repository_id: int,
    verification_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Execute an approved/queued verification job through the durable manager."""
    _require_repository_access(db, repository_id, auth.user.id)
    _get_run_or_404(db, repository_id, verification_id)

    try:
        result = await VerificationJobManager.execute_job(
            db, verification_id, user_id=auth.user.id
        )
        return result
    except VerificationSpecificationMismatchError as spec_err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(spec_err)) from spec_err
    except VerificationConcurrencyError as conc_err:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(conc_err)) from conc_err
    except VerificationSecurityError as sec_err:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(sec_err)) from sec_err
    except VerificationStateError as state_err:
        raise HTTPException(status.HTTP_409_CONFLICT, str(state_err)) from state_err
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Verification failed: {exc}"
        ) from exc


@router.get("/{verification_id}/report", response_model=ProofReportRead)
def get_verification_report(
    repository_id: int,
    verification_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Retrieve structured cryptographic Proof-of-Fix evidence report."""
    _require_repository_access(db, repository_id, auth.user.id)
    run = _get_run_or_404(db, repository_id, verification_id)

    report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run.id))
    if report is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No Proof-of-Fix report found for verification run {verification_id} "
            f"(current state: '{run.status}').",
        )

    try:
        ev_dict = json.loads(report.evidence_json)
    except Exception:
        ev_dict = {}

    return ProofReportRead(
        id=report.id,
        verification_run_id=report.verification_run_id,
        verdict=report.verdict,
        hypothesis=report.hypothesis,
        summary=report.summary,
        baseline_summary=report.baseline_summary,
        patched_summary=report.patched_summary,
        confidence=report.confidence,
        evidence=ev_dict,
        created_at=report.created_at.isoformat(),
    )


@router.get("/{verification_id}/events", response_model=list[VerificationAuditEventRead])
def get_verification_audit_events(
    repository_id: int,
    verification_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Retrieve audit trail of events and state transitions for a verification run."""
    _require_repository_access(db, repository_id, auth.user.id)
    _get_run_or_404(db, repository_id, verification_id)

    events = db.scalars(
        select(VerificationAuditEvent)
        .where(VerificationAuditEvent.verification_run_id == verification_id)
        .order_by(VerificationAuditEvent.id.asc())
    ).all()

    result = []
    for ev in events:
        try:
            details_dict = json.loads(ev.details_json)
        except Exception:
            details_dict = {}
        result.append(
            VerificationAuditEventRead(
                id=ev.id,
                verification_run_id=ev.verification_run_id,
                event_type=ev.event_type,
                actor_user_id=ev.actor_user_id,
                from_status=ev.from_status,
                to_status=ev.to_status,
                details=details_dict,
                created_at=ev.created_at.isoformat(),
            )
        )
    return result
