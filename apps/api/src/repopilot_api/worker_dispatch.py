"""On-demand GitHub Actions verification worker dispatch and status monitoring.

Enables secure, bounded verification runs for trusted deterministic demo fixtures
without exposing Docker sockets, worker credentials, or database secrets to untrusted visitors.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from repopilot_api.models import Repository, VerificationRun
from repopilot_api.schemas import VerificationJobStatus
from repopilot_api.verification_service import check_rate_limit

logger = logging.getLogger(__name__)

DEFAULT_WORKER_HEARTBEAT_WINDOW_SECONDS = 120
GITHUB_API_TIMEOUT_SECONDS = 10.0


def is_cloud_worker_dispatch_configured() -> bool:
    """Check if GitHub Actions workflow dispatch credentials are configured."""
    token = (
        os.getenv("GITHUB_WORKER_DISPATCH_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )
    return bool(token)


def get_worker_repository() -> str:
    """Get the target GitHub repository for worker dispatch (owner/repo)."""
    return os.getenv("GITHUB_REPOSITORY", "Mehndi-Hassan28/repopilot-fix").strip()


def get_worker_workflow() -> str:
    """Get the target GitHub Actions workflow filename."""
    return os.getenv("GITHUB_WORKER_WORKFLOW", "verification-worker.yml").strip()


def validate_worker_dispatch_eligibility(
    db: Session,
    run_id: int,
    repository_id: int | None = None,
) -> tuple[bool, str]:
    """Validate server-side eligibility for on-demand cloud worker dispatch.

    Enforces:
    1. Run exists in database.
    2. Run is in 'queued' or 'approved' state.
    3. Run has an immutable approved specification hash.
    4. Target repository is an internal demo fixture OR matches a known trusted fixture.
    5. Run does not already have an active unexpired worker lease (replay prevention).
    """
    run = db.get(VerificationRun, run_id)
    if run is None:
        return False, f"Verification run #{run_id} not found."

    if repository_id is not None and run.repository_id != repository_id:
        return False, f"Run #{run_id} does not belong to repository #{repository_id}."

    if run.status not in (VerificationJobStatus.QUEUED.value, VerificationJobStatus.APPROVED.value):
        return False, f"Run #{run_id} is in status '{run.status}', not queued/approved."

    if not run.approved_spec_hash:
        return False, f"Run #{run_id} has not been approved with an authentic specification hash."

    # Prevent duplicate dispatch if worker is already executing with valid lease
    now = datetime.now(UTC)
    is_running = run.status == VerificationJobStatus.RUNNING.value
    has_active_lease = run.lease_expires_at is not None and run.lease_expires_at > now
    if is_running and has_active_lease:
        return (
            False,
            f"Run #{run_id} is already actively executing (leased to worker '{run.worker_id}').",
        )

    # Enforce trusted fixture-only execution: repository must be verified as trusted demo fixture
    from repopilot_api.fixture_registry import get_default_fixture_registry

    repo = db.get(Repository, run.repository_id)
    user_id = run.approved_by_user_id or run.user_id
    if repo is None or user_id is None:
        return False, "Repository or user associated with run not found."

    registry = get_default_fixture_registry()
    is_trusted = registry.is_repository_trusted(
        repo.owner,
        repo.name,
        repo.github_id,
        repository=repo,
        db=db,
        user_id=user_id,
    )

    if not is_trusted:
        return (
            False,
            "Cloud verification worker execution is strictly restricted to trusted demo fixtures.",
        )

    return True, "Eligible"


async def dispatch_cloud_worker(
    run_id: int | None = None,
    repository_id: int | None = None,
    db: Session | None = None,
) -> bool:
    """Dispatch an on-demand GitHub Actions verification worker workflow.

    Uses GitHub workflow_dispatch (requiring only Actions:write scope) to trigger
    the bounded verification worker workflow in a runner with native Docker daemon access.
    """
    token = (
        os.getenv("GITHUB_WORKER_DISPATCH_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )
    if not token:
        logger.info("GITHUB_WORKER_DISPATCH_TOKEN not configured; skipping cloud worker dispatch.")
        return False

    # Global rate limit on dispatch requests
    if not check_rate_limit("global_cloud_worker_dispatch", max_requests=6, window_seconds=60):
        logger.warning("Global cloud worker dispatch rate limit exceeded.")
        return False

    # Server-side validation if database session is provided
    if db is not None and run_id is not None:
        eligible, reason = validate_worker_dispatch_eligibility(db, run_id, repository_id)
        if not eligible:
            logger.warning("Worker dispatch rejected for run %s: %s", run_id, reason)
            return False

    repo = get_worker_repository()
    workflow = get_worker_workflow()
    ref = os.getenv("GITHUB_WORKER_REF", "main").strip() or "main"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"

    inputs: dict[str, str] = {}
    if run_id is not None:
        inputs["run_id"] = str(run_id)

    payload = {
        "ref": ref,
        "inputs": inputs,
    }

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "RepoPilot-Fix-Cloud-Dispatcher",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        async with httpx.AsyncClient(timeout=GITHUB_API_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 204:
                logger.info(
                    "Successfully dispatched GitHub Actions verification worker for run %s "
                    "via workflow_dispatch on %s.",
                    run_id,
                    repo,
                )
                return True
            logger.warning(
                "GitHub Actions worker dispatch returned HTTP %d: %s",
                resp.status_code,
                resp.text[:500],
            )
            return False
    except Exception as exc:
        logger.warning("Failed to dispatch GitHub Actions verification worker: %s", exc)
        return False


def get_cloud_worker_status(db: Session) -> dict[str, Any]:
    """Inspect active worker heartbeats and dispatch readiness."""
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=DEFAULT_WORKER_HEARTBEAT_WINDOW_SECONDS)

    # Check if any worker sent a heartbeat or holds a valid lease recently
    active_worker = db.scalar(
        select(VerificationRun)
        .where(
            VerificationRun.status == VerificationJobStatus.RUNNING.value,
            or_(
                VerificationRun.last_heartbeat_at > cutoff,
                VerificationRun.lease_expires_at > now,
            ),
        )
        .order_by(VerificationRun.updated_at.desc())
        .limit(1)
    )

    dispatch_ready = is_cloud_worker_dispatch_configured()

    if active_worker is not None and active_worker.worker_id:
        return {
            "cloud_worker_available": True,
            "cloud_worker_dispatch_configured": dispatch_ready,
            "worker_status_message": (
                f"Active verification worker '{active_worker.worker_id}' online "
                f"(processing run #{active_worker.id})."
            ),
        }

    if dispatch_ready:
        return {
            "cloud_worker_available": True,
            "cloud_worker_dispatch_configured": True,
            "worker_status_message": (
                "On-demand GitHub Actions verification worker ready "
                "(Docker dual-sandbox execution)."
            ),
        }

    return {
        "cloud_worker_available": False,
        "cloud_worker_dispatch_configured": False,
        "worker_status_message": (
            "No active cloud verification worker daemon. Set GITHUB_WORKER_DISPATCH_TOKEN "
            "or run a local Docker daemon for authentic container verification."
        ),
    }
