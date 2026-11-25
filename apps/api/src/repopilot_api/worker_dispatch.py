"""On-demand GitHub Actions verification worker dispatch and status monitoring.

Enables secure, bounded verification runs for trusted deterministic demo fixtures
without exposing Docker sockets, worker credentials, or database secrets to untrusted visitors.
"""

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from repopilot_api.models import VerificationRun
from repopilot_api.schemas import VerificationJobStatus

logger = logging.getLogger(__name__)

DEFAULT_WORKER_HEARTBEAT_WINDOW_SECONDS = 120
GITHUB_API_TIMEOUT_SECONDS = 10.0


def is_cloud_worker_dispatch_configured() -> bool:
    """Check if GitHub Actions workflow dispatch credentials are configured."""
    token = os.getenv("GITHUB_WORKER_DISPATCH_TOKEN", "").strip() or os.getenv("GITHUB_TOKEN", "").strip()
    return bool(token)


def get_worker_repository() -> str:
    """Get the target GitHub repository for worker dispatch (owner/repo)."""
    return os.getenv("GITHUB_REPOSITORY", "Mehndi-Hassan28/repopilot-fix").strip()


async def dispatch_cloud_worker(
    run_id: int | None = None,
    repository_id: int | None = None,
) -> bool:
    """Dispatch an on-demand GitHub Actions verification worker workflow.

    Uses GitHub repository_dispatch to trigger the verification-worker.yml workflow
    in an isolated runner with native Docker daemon access.
    """
    token = os.getenv("GITHUB_WORKER_DISPATCH_TOKEN", "").strip() or os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        logger.info("GITHUB_WORKER_DISPATCH_TOKEN not configured; skipping cloud worker dispatch.")
        return False

    repo = get_worker_repository()
    url = f"https://api.github.com/repos/{repo}/dispatches"

    payload = {
        "event_type": "repopilot_verify",
        "client_payload": {
            "run_id": run_id,
            "repository_id": repository_id,
            "timestamp": datetime.now(UTC).isoformat(),
        },
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
                    "Successfully dispatched GitHub Actions verification worker for run %s on %s.",
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
                "On-demand GitHub Actions verification worker ready (Docker dual-sandbox execution)."
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
