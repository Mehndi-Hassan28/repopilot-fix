"""One-Click Interactive Demo API Router and Safe Fixture Initialization.

Provides an isolated, idempotent, deterministic demo environment:
1. Reuses the canonical calculator fixture (reproducible power bug in src/calculator.py).
2. Seeds AST-indexed source files and regression tests without external GitHub network calls.
3. Isolates demo repositories strictly per-user using database foreign keys and tenancy.
4. Detects host Docker daemon availability without fabricating execution evidence.
5. Prevents duplicate initialization and provides safe resets.
"""

import logging
import shutil
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from repopilot_api.code_intelligence import parse_symbols
from repopilot_api.dependencies import get_db
from repopilot_api.fixture_registry import (
    DEMO_CALC_COMMIT_SHA,
    DEMO_CALC_FILES,
    DEMO_CALC_TEST_CMD,
)
from repopilot_api.models import (
    CodeSymbol,
    IndexedFile,
    IndexingJob,
    ProofReport,
    Repository,
    RepositoryAccess,
    VerificationAuditEvent,
    VerificationRun,
)
from repopilot_api.sandbox import DEFAULT_SANDBOX_IMAGE
from repopilot_api.schemas import RepositoryRead
from repopilot_api.security import AuthContext, get_current_auth, require_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])

DEMO_GITHUB_BASE_ID = 990000000


def check_docker_daemon_status() -> tuple[bool, str]:
    """Check whether host Docker daemon is available and accessible.

    Fails closed and never fabricates container capability.
    """
    if shutil.which("docker") is None:
        return False, "Docker CLI executable not found on host PATH."
    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=3,
            check=False,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
            return False, f"Docker daemon is not accessible: {err_msg[:120]}"
    except Exception as exc:
        return False, f"Docker daemon check failed: {exc}"
    return True, "Docker daemon is running and accessible."


# ==============================================================================
# Demo API Schemas
# ==============================================================================


class DemoIssue(BaseModel):
    number: int = 1
    title: str = "Bug in power calculation: multiplication returned instead of exponentiation"
    body: str = (
        "When calling power(2, 3), the calculator returns 6 instead of 8. "
        "Suspect operator defect in src/calculator.py power() implementation where "
        "base * exponent is returned instead of base ** exponent."
    )
    state: str = "open"
    html_url: str = "https://github.com/repopilot/demo-calculator/issues/1"
    author: str = "repopilot-demo"
    labels: list[str] = ["bug", "math", "deterministic-demo"]


class DemoStatusResponse(BaseModel):
    initialized: bool
    repository_id: int | None = None
    repository_name: str | None = None
    repository_full_name: str | None = None
    default_branch: str = "main"
    commit_sha: str | None = None
    docker_available: bool
    docker_status_message: str
    sandbox_image: str = DEFAULT_SANDBOX_IMAGE
    preconfigured_issue_number: int = 1
    has_active_run: bool = False
    latest_run_id: int | None = None
    latest_run_status: str | None = None
    latest_report_verdict: str | None = None
    approved_spec_hash: str | None = None


class DemoInitializeResponse(BaseModel):
    repository: RepositoryRead
    indexing_job: dict[str, Any]
    issue: DemoIssue
    test_command: list[str]
    sandbox_image: str
    docker_available: bool
    docker_status_message: str
    is_mock_ai: bool = True


# ==============================================================================
# Demo Endpoints
# ==============================================================================


def _get_user_demo_repository(db: Session, user_id: int) -> Repository | None:
    expected_github_id = DEMO_GITHUB_BASE_ID + user_id
    access = db.scalar(
        select(RepositoryAccess)
        .join(Repository, Repository.id == RepositoryAccess.repository_id)
        .where(
            RepositoryAccess.user_id == user_id,
            Repository.github_id == expected_github_id,
        )
    )
    if access is None:
        return None
    return db.get(Repository, access.repository_id)


@router.get("/status", response_model=DemoStatusResponse)
def get_demo_status(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    """Retrieve current user's demo environment status and Docker capability."""
    docker_ok, docker_msg = check_docker_daemon_status()
    repo = _get_user_demo_repository(db, auth.user.id)

    if repo is None:
        return DemoStatusResponse(
            initialized=False,
            docker_available=docker_ok,
            docker_status_message=docker_msg,
        )

    # Check for latest verification run and proof report
    latest_run = db.scalar(
        select(VerificationRun)
        .where(VerificationRun.repository_id == repo.id)
        .order_by(VerificationRun.id.desc())
    )

    latest_report = None
    if latest_run is not None:
        latest_report = db.scalar(
            select(ProofReport).where(ProofReport.verification_run_id == latest_run.id)
        )

    return DemoStatusResponse(
        initialized=True,
        repository_id=repo.id,
        repository_name=repo.name,
        repository_full_name=repo.full_name,
        default_branch=repo.default_branch,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        docker_available=docker_ok,
        docker_status_message=docker_msg,
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
        preconfigured_issue_number=1,
        has_active_run=latest_run is not None,
        latest_run_id=latest_run.id if latest_run else None,
        latest_run_status=latest_run.status if latest_run else None,
        latest_report_verdict=latest_report.verdict if latest_report else None,
        approved_spec_hash=latest_run.approved_spec_hash if latest_run else None,
    )


@router.post("/initialize", response_model=DemoInitializeResponse, status_code=status.HTTP_200_OK)
def initialize_demo(
    reset: bool = Query(False, description="Whether to reset and re-seed demo data if it exists"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Safely initialize or reset the deterministic calculator demo repository for current user."""
    docker_ok, docker_msg = check_docker_daemon_status()
    expected_github_id = DEMO_GITHUB_BASE_ID + auth.user.id
    existing_repo = _get_user_demo_repository(db, auth.user.id)

    if existing_repo and reset:
        # Safe isolation: delete only demo verification runs, audit events, and index data
        vrun_ids = db.scalars(
            select(VerificationRun.id).where(VerificationRun.repository_id == existing_repo.id)
        ).all()
        if vrun_ids:
            db.execute(
                delete(VerificationAuditEvent).where(
                    VerificationAuditEvent.verification_run_id.in_(vrun_ids)
                )
            )
            db.execute(
                delete(ProofReport).where(ProofReport.verification_run_id.in_(vrun_ids))
            )
            db.execute(
                delete(VerificationRun).where(VerificationRun.repository_id == existing_repo.id)
            )

        job_ids = db.scalars(
            select(IndexingJob.id).where(IndexingJob.repository_id == existing_repo.id)
        ).all()
        if job_ids:
            file_ids = db.scalars(
                select(IndexedFile.id).where(IndexedFile.job_id.in_(job_ids))
            ).all()
            if file_ids:
                db.execute(delete(CodeSymbol).where(CodeSymbol.file_id.in_(file_ids)))
                db.execute(delete(IndexedFile).where(IndexedFile.id.in_(file_ids)))
            db.execute(delete(IndexingJob).where(IndexingJob.id.in_(job_ids)))

        db.execute(
            delete(RepositoryAccess).where(RepositoryAccess.repository_id == existing_repo.id)
        )
        db.delete(existing_repo)
        db.commit()
        existing_repo = None

    if existing_repo is not None:
        job = db.scalar(
            select(IndexingJob)
            .where(
                IndexingJob.repository_id == existing_repo.id,
                IndexingJob.status == "completed",
            )
            .order_by(IndexingJob.id.desc())
        )
        job_data = {
            "id": job.id if job else 0,
            "status": job.status if job else "completed",
            "file_count": job.file_count if job else len(DEMO_CALC_FILES),
            "commit_sha": DEMO_CALC_COMMIT_SHA,
        }
        return DemoInitializeResponse(
            repository=RepositoryRead.from_model(existing_repo),
            indexing_job=job_data,
            issue=DemoIssue(),
            test_command=DEMO_CALC_TEST_CMD,
            sandbox_image=DEFAULT_SANDBOX_IMAGE,
            docker_available=docker_ok,
            docker_status_message=docker_msg,
            is_mock_ai=True,
        )

    # 1. Create Repository with guaranteed per-user isolation
    repo_name = f"demo-calc-{auth.user.id}"
    repo_full_name = f"repopilot/{repo_name}"

    # Clean up any orphan repo with the same github_id or full_name
    orphan = db.scalar(
        select(Repository).where(
            (Repository.github_id == expected_github_id)
            | (Repository.full_name == repo_full_name)
        )
    )
    if orphan is not None:
        db.execute(delete(RepositoryAccess).where(RepositoryAccess.repository_id == orphan.id))
        db.delete(orphan)
        db.commit()

    repo = Repository(
        github_id=expected_github_id,
        owner="repopilot",
        name=repo_name,
        full_name=repo_full_name,
        url="https://github.com/repopilot/demo-calculator",
        description="Deterministic Calculator Demo Fixture for RepoPilot Fix",
        default_branch="main",
        stars=42,
    )
    db.add(repo)
    db.flush()

    # 2. Grant ownership access
    access = RepositoryAccess(repository_id=repo.id, user_id=auth.user.id, role="owner")
    db.add(access)

    # 3. Create completed indexing job
    job = IndexingJob(
        repository_id=repo.id,
        commit_sha=DEMO_CALC_COMMIT_SHA,
        status="completed",
        file_count=len(DEMO_CALC_FILES),
    )
    db.add(job)
    db.flush()

    # 4. Seed indexed files and AST code symbols
    for path, content in DEMO_CALC_FILES.items():
        indexed = IndexedFile(
            job_id=job.id,
            path=path,
            language="python",
            size_bytes=len(content.encode("utf-8")),
            content=content,
        )
        db.add(indexed)
        db.flush()

        symbols = parse_symbols(content, "python")
        for sym in symbols:
            db.add(
                CodeSymbol(
                    file_id=indexed.id,
                    name=sym["name"],
                    kind=sym["kind"],
                    line=sym["line"],
                )
            )

    db.commit()
    db.refresh(repo)
    db.refresh(job)

    job_data = {
        "id": job.id,
        "status": job.status,
        "file_count": job.file_count,
        "commit_sha": job.commit_sha,
    }

    return DemoInitializeResponse(
        repository=RepositoryRead.from_model(repo),
        indexing_job=job_data,
        issue=DemoIssue(),
        test_command=DEMO_CALC_TEST_CMD,
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
        docker_available=docker_ok,
        docker_status_message=docker_msg,
        is_mock_ai=True,
    )


@router.post("/reset", response_model=DemoStatusResponse)
def reset_demo(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Safely reset the demo environment for the current user."""
    initialize_demo(reset=True, db=db, auth=auth)
    return get_demo_status(db=db, auth=auth)
