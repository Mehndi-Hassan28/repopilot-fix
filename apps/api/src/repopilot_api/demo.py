"""One-Click Interactive Demo API Router and Safe Fixture Initialization.

Provides an isolated, idempotent, deterministic demo environment:
1. Reuses the canonical calculator fixture (reproducible power bug in src/calculator.py).
2. Seeds AST-indexed source files and regression tests without external GitHub network calls.
3. Isolates demo repositories strictly per-user using database foreign keys and tenancy.
4. Detects host Docker daemon availability without fabricating execution evidence.
5. Prevents duplicate initialization and provides safe resets.
"""

import hashlib
import logging
import threading
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
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
    GitHubPullRequest,
    IndexedFile,
    IndexingJob,
    ProofReport,
    Repository,
    RepositoryAccess,
    VerificationAuditEvent,
    VerificationRun,
)
from repopilot_api.sandbox import DEFAULT_SANDBOX_IMAGE, check_docker_daemon_status
from repopilot_api.schemas import RepositoryRead
from repopilot_api.security import AuthContext, get_optional_auth, require_csrf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo", tags=["demo"])

DEMO_GITHUB_BASE_ID = 990000000
DEMO_PROVENANCE_TAG_PREFIX = "[provenance:internal_fixture:"
DEMO_FIXTURE_DESCRIPTION_PREFIX = "Deterministic Calculator Demo Fixture for RepoPilot Fix"
_DEMO_INIT_LOCK = threading.Lock()


def is_internal_demo_repository(
    db: Session,
    repository: Repository | None,
    user_id: int | None = None,
    verify_content: bool = False,
) -> bool:
    """Verify explicit server-controlled fixture provenance, exact identity, ownership, and content.

    Ensures external repository imports cannot acquire internal demo provenance.
    """
    if repository is None:
        return False

    # 1. Exact repository owner and name pattern
    if repository.owner != "repopilot":
        return False
    if not repository.name.startswith("demo-calc-"):
        return False

    try:
        repo_user_id = int(repository.name.removeprefix("demo-calc-"))
    except ValueError:
        return False

    # If user_id is provided, verify matching authenticated user
    if user_id is not None and user_id != repo_user_id:
        return False

    # 2. Exact server-calculated github_id (exact arithmetic identity, not open range)
    if repository.github_id != DEMO_GITHUB_BASE_ID + repo_user_id:
        return False

    # 3. Server-controlled fixture provenance in description and URL
    expected_tag = f"{DEMO_PROVENANCE_TAG_PREFIX}{repo_user_id}:demo-calculator]"
    if not repository.description or expected_tag not in repository.description:
        return False
    if not repository.description.startswith(DEMO_FIXTURE_DESCRIPTION_PREFIX):
        return False
    if repository.url != "https://github.com/repopilot/demo-calculator":
        return False
    if repository.default_branch != "main":
        return False

    # 4. Authenticated ownership in repository_accesses
    access = db.scalar(
        select(RepositoryAccess).where(
            RepositoryAccess.repository_id == repository.id,
            RepositoryAccess.user_id == repo_user_id,
            RepositoryAccess.role == "owner",
        )
    )
    if access is None:
        return False

    # 5. Canonical content validation if requested
    if verify_content:
        job = db.scalar(
            select(IndexingJob)
            .where(
                IndexingJob.repository_id == repository.id,
                IndexingJob.status == "completed",
            )
            .order_by(IndexingJob.id.desc())
        )
        if job is None:
            return False
        files = db.scalars(select(IndexedFile).where(IndexedFile.job_id == job.id)).all()
        file_map = {f.path: f.content for f in files}
        if set(file_map.keys()) != set(DEMO_CALC_FILES.keys()):
            return False
        for path, canonical_content in DEMO_CALC_FILES.items():
            actual = file_map.get(path)
            if actual is None:
                return False
            actual_hash = hashlib.sha256(actual.encode("utf-8")).hexdigest()
            canonical_hash = hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()
            if actual_hash != canonical_hash:
                return False

    return True





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
            RepositoryAccess.role == "owner",
            Repository.github_id == expected_github_id,
            Repository.owner == "repopilot",
            Repository.name == f"demo-calc-{user_id}",
        )
    )
    if access is None:
        return None
    repo = db.get(Repository, access.repository_id)
    if repo is None:
        return None
    if not is_internal_demo_repository(db, repo, user_id=user_id, verify_content=False):
        return None
    return repo


def _build_demo_init_response(
    db: Session, repo: Repository, docker_ok: bool, docker_msg: str
) -> DemoInitializeResponse:
    job = db.scalar(
        select(IndexingJob)
        .where(
            IndexingJob.repository_id == repo.id,
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
    import os

    chat_prov = os.getenv("CHAT_PROVIDER", "mock").lower()
    is_mock = chat_prov == "mock"
    return DemoInitializeResponse(
        repository=RepositoryRead.from_model(repo),
        indexing_job=job_data,
        issue=DemoIssue(),
        test_command=DEMO_CALC_TEST_CMD,
        sandbox_image=DEFAULT_SANDBOX_IMAGE,
        docker_available=docker_ok,
        docker_status_message=docker_msg,
        is_mock_ai=is_mock,
    )


@router.get("/status", response_model=DemoStatusResponse)
def get_demo_status(
    db: Session = Depends(get_db),
    auth: AuthContext | None = Depends(get_optional_auth),
):
    """Retrieve current user's demo environment status and Docker capability."""
    docker_ok, docker_msg = check_docker_daemon_status()
    if auth is None:
        return DemoStatusResponse(
            initialized=False,
            docker_available=docker_ok,
            docker_status_message=docker_msg,
        )

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

    with _DEMO_INIT_LOCK:
        # In PostgreSQL, acquire transaction advisory lock unique to user demo init
        if db.bind and db.bind.dialect.name == "postgresql":
            user_lock_key = (DEMO_GITHUB_BASE_ID + auth.user.id) & 0x7FFFFFFF
            db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": user_lock_key})

        existing_repo = _get_user_demo_repository(db, auth.user.id)

        if existing_repo and reset:
            # 1. Safely handle active Docker workers: reject reset while execution is active
            active_running = db.scalars(
                select(VerificationRun).where(
                    VerificationRun.repository_id == existing_repo.id,
                    VerificationRun.status == "running",
                )
            ).all()
            if active_running:
                run_ids = ", ".join(f"#{r.id}" for r in active_running)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot reset demo repository while verification run ({run_ids}) is "
                        "actively executing in Docker. Please cancel the run or wait for "
                        "container execution to complete."
                    ),
                )

            # Cancel queued/approved/pending_approval runs safely before deleting records
            queued_runs = db.scalars(
                select(VerificationRun).where(
                    VerificationRun.repository_id == existing_repo.id,
                    VerificationRun.status.in_(["queued", "approved", "pending_approval"]),
                )
            ).all()
            for qrun in queued_runs:
                qrun.status = "cancelled"
                qrun.cancelled_at = datetime.now(UTC)
                qrun.cancelled_by_user_id = auth.user.id
                qrun.cancellation_reason = "Demo environment reset by user"
                qrun.fencing_token = (qrun.fencing_token or 0) + 1
            db.flush()

            # Clean up pull requests
            db.execute(
                delete(GitHubPullRequest).where(GitHubPullRequest.repository_id == existing_repo.id)
            )

            # Clean up verification runs and related records
            vrun_ids = db.scalars(
                select(VerificationRun.id).where(VerificationRun.repository_id == existing_repo.id)
            ).all()
            if vrun_ids:
                db.execute(
                    delete(VerificationAuditEvent).where(
                        VerificationAuditEvent.verification_run_id.in_(vrun_ids)
                    )
                )
                db.execute(delete(ProofReport).where(ProofReport.verification_run_id.in_(vrun_ids)))
                db.execute(
                    delete(VerificationRun).where(VerificationRun.repository_id == existing_repo.id)
                )

            # Clean up indexing data
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
            return _build_demo_init_response(db, existing_repo, docker_ok, docker_msg)

        # 1. Create Repository with guaranteed per-user isolation
        repo_name = f"demo-calc-{auth.user.id}"
        repo_full_name = f"repopilot/{repo_name}"

        # Clean up any orphan repo scoped strictly to this user's demo identity
        orphan = db.scalar(
            select(Repository).where(
                (Repository.github_id == expected_github_id)
                | (Repository.full_name == repo_full_name)
            )
        )
        if orphan is not None:
            db.execute(
                delete(GitHubPullRequest).where(GitHubPullRequest.repository_id == orphan.id)
            )
            orphan_vrun_ids = db.scalars(
                select(VerificationRun.id).where(VerificationRun.repository_id == orphan.id)
            ).all()
            if orphan_vrun_ids:
                db.execute(
                    delete(VerificationAuditEvent).where(
                        VerificationAuditEvent.verification_run_id.in_(orphan_vrun_ids)
                    )
                )
                db.execute(
                    delete(ProofReport).where(ProofReport.verification_run_id.in_(orphan_vrun_ids))
                )
                db.execute(
                    delete(VerificationRun).where(VerificationRun.repository_id == orphan.id)
                )
            orphan_job_ids = db.scalars(
                select(IndexingJob.id).where(IndexingJob.repository_id == orphan.id)
            ).all()
            if orphan_job_ids:
                orphan_file_ids = db.scalars(
                    select(IndexedFile.id).where(IndexedFile.job_id.in_(orphan_job_ids))
                ).all()
                if orphan_file_ids:
                    db.execute(delete(CodeSymbol).where(CodeSymbol.file_id.in_(orphan_file_ids)))
                    db.execute(delete(IndexedFile).where(IndexedFile.id.in_(orphan_file_ids)))
                db.execute(delete(IndexingJob).where(IndexingJob.id.in_(orphan_job_ids)))
            db.execute(delete(RepositoryAccess).where(RepositoryAccess.repository_id == orphan.id))
            db.delete(orphan)
            db.commit()

        provenance_tag = f"{DEMO_PROVENANCE_TAG_PREFIX}{auth.user.id}:demo-calculator]"
        repo = Repository(
            github_id=expected_github_id,
            owner="repopilot",
            name=repo_name,
            full_name=repo_full_name,
            url="https://github.com/repopilot/demo-calculator",
            description=f"{DEMO_FIXTURE_DESCRIPTION_PREFIX} {provenance_tag}",
            default_branch="main",
            stars=42,
        )
        db.add(repo)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing_repo = _get_user_demo_repository(db, auth.user.id)
            if existing_repo is not None:
                return _build_demo_init_response(db, existing_repo, docker_ok, docker_msg)
            raise

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

        return _build_demo_init_response(db, repo, docker_ok, docker_msg)


@router.post("/reset", response_model=DemoStatusResponse)
def reset_demo(
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    """Safely reset the demo environment for the current user."""
    initialize_demo(reset=True, db=db, auth=auth)
    return get_demo_status(db=db, auth=auth)
