import json
import os
import sys
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from httpx import Client
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repopilot_api.chat import ProviderError, ask
from repopilot_api.code_intelligence import SUPPORTED, GitHubSource, limits, parse_symbols
from repopilot_api.database import SessionLocal
from repopilot_api.demo import router as demo_router
from repopilot_api.dependencies import get_db
from repopilot_api.models import (
    ChatMessage,
    ChatSession,
    CodeSymbol,
    IndexedFile,
    IndexingJob,
    Repository,
    RepositoryAccess,
)
from repopilot_api.oauth import router as auth_router
from repopilot_api.schemas import (
    ChatQuestion,
    ChatSessionCreate,
    DependencyGraphResponse,
    ImpactAnalysisRequest,
    ImpactAnalysisResponse,
    IssueAnalysisRead,
    IssueAnalyzeRequest,
    RepositoryImport,
    RepositoryRead,
    SymbolsListResponse,
)
from repopilot_api.security import AuthContext, configured_origins, get_current_auth, require_csrf
from repopilot_api.verification_router import router as verification_router
from repopilot_api.verification_service import VerificationJobManager, check_rate_limit


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with SessionLocal() as db:
            VerificationJobManager.recover_stale_jobs(db)
    except Exception:
        pass
    yield


app = FastAPI(title="RepoPilot Fix API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(configured_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
app.include_router(auth_router)
app.include_router(verification_router)
app.include_router(demo_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "repopilot-api"}


def _require_repository_access(db: Session, repository_id: int, user_id: int):
    access = db.get(RepositoryAccess, (repository_id, user_id))
    if access is None:
        raise HTTPException(404, "Repository not found.")
    return access


def _owned_chat_session(db: Session, session_id: int, user_id: int):
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.owner_user_id == user_id,
        )
    )
    if session is None or db.get(RepositoryAccess, (session.repository_id, user_id)) is None:
        raise HTTPException(404, "Conversation not found.")
    return session


@app.post("/api/repositories/import", response_model=RepositoryRead, status_code=200)
def import_repository(
    payload: RepositoryImport,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    if not check_rate_limit(f"repo_import:{auth.user.id}", max_requests=10, window_seconds=60):
        raise HTTPException(
            429, "Too many repository import requests. Please wait a moment and try again."
        )

    if payload.url != payload.url.strip() or any(
        c in payload.url for c in ("\n", "\r", "\t", "\0")
    ):
        raise HTTPException(
            status_code=422,
            detail="Repository URL contains invalid whitespace or control characters.",
        )

    raw_url = payload.url.strip()
    if not raw_url.startswith("https://github.com/"):
        raise HTTPException(
            status_code=422,
            detail="Invalid repository URL. Must start with 'https://github.com/'.",
        )

    clean_path = raw_url.removeprefix("https://github.com/").strip("/")
    parts = clean_path.split("/")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise HTTPException(
            status_code=422,
            detail="Invalid repository URL format. Expected 'https://github.com/owner/repo'.",
        )

    owner, name = parts[0].strip(), parts[1].strip()
    if any(c in owner or c in name for c in ("\0", "\n", "\r", " ", "?", "#")):
        raise HTTPException(
            status_code=422,
            detail="Repository URL contains invalid control or query characters.",
        )

    owner_clean = owner.strip().lower()
    name_clean = name.strip().lower()

    # Disallow importing internal demo fixtures or impersonating the demo namespace
    if owner_clean == "repopilot" or "demo-calc" in name_clean or "demo-calculator" in name_clean:
        raise HTTPException(
            status_code=400,
            detail=(
                "External repository import cannot acquire internal demo fixture provenance. "
                "Demo repositories must be initialized via the demo workspace."
            ),
        )

    try:
        with Client(timeout=httpx.Timeout(8.0), follow_redirects=False) as client:
            response = client.get(
                f"https://api.github.com/repos/{owner}/{name}",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "RepoPilot-Fix"},
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "GitHub did not respond in time. Please try again.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Could not reach GitHub. Please try again.") from exc

    if response.status_code == 404:
        raise HTTPException(404, "Repository not found or not public.")
    if response.status_code == 429 or (
        response.status_code == 403
        and (
            response.headers.get("X-RateLimit-Remaining") == "0"
            or "rate limit" in response.text.lower()
        )
    ):
        raise HTTPException(
            429,
            "GitHub rate limit reached. Please try again later.",
            headers={"Retry-After": response.headers.get("Retry-After", "60")},
        )
    if response.status_code == 403:
        raise HTTPException(403, "GitHub refused the request.")
    if response.status_code >= 400:
        raise HTTPException(502, "GitHub returned an unexpected error.")
    try:
        data = response.json()
        github_id = data["id"]
        repo_owner = data["owner"]["login"]
        repo_name = data["name"]
        full_name = data["full_name"]
        html_url = data["html_url"]
        default_branch = data["default_branch"]
        stars = data["stargazers_count"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "GitHub returned invalid repository metadata.") from exc
    if not isinstance(data.get("private"), bool):
        raise HTTPException(502, "GitHub returned invalid repository visibility metadata.")
    if data["private"]:
        raise HTTPException(403, "Only public GitHub repositories can be imported.")

    if (
        repo_owner.lower() == "repopilot"
        or "demo-calc" in repo_name.lower()
        or "demo-calculator" in repo_name.lower()
    ):
        raise HTTPException(
            status_code=400,
            detail="External repository import cannot acquire internal demo fixture provenance.",
        )
    from repopilot_api.demo import DEMO_GITHUB_BASE_ID

    if github_id >= DEMO_GITHUB_BASE_ID:
        raise HTTPException(
            status_code=400,
            detail="Repository ID collides with reserved internal demo fixture IDs.",
        )

    raw_desc = data.get("description")
    clean_desc = (
        raw_desc.replace("[provenance:internal_fixture:", "[external_ref:") if raw_desc else None
    )

    existing = db.scalar(select(Repository).where(Repository.github_id == github_id))
    if existing:
        repo = existing
    else:
        repo = Repository(
            github_id=github_id,
            owner=repo_owner,
            name=repo_name,
            full_name=full_name,
            url=html_url,
            description=clean_desc,
            default_branch=default_branch,
            stars=stars,
        )
        db.add(repo)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            repo = db.scalar(select(Repository).where(Repository.github_id == github_id))
            if repo is None:
                raise HTTPException(409, "Repository conflicts with an existing record.") from exc
    if db.get(RepositoryAccess, (repo.id, auth.user.id)) is None:
        db.add(RepositoryAccess(repository_id=repo.id, user_id=auth.user.id, role="owner"))
    db.commit()
    db.refresh(repo)
    return RepositoryRead.from_model(repo)


@app.get("/api/repositories", response_model=list[RepositoryRead])
def list_repositories(db: Session = Depends(get_db), auth: AuthContext = Depends(get_current_auth)):
    repos = db.scalars(
        select(Repository)
        .join(RepositoryAccess, RepositoryAccess.repository_id == Repository.id)
        .where(RepositoryAccess.user_id == auth.user.id)
        .order_by(Repository.created_at.desc())
    ).all()
    return [RepositoryRead.from_model(repo) for repo in repos]


@app.get("/api/repositories/{repository_id}", response_model=RepositoryRead)
def get_repository(
    repository_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    _require_repository_access(db, repository_id, auth.user.id)
    repo = db.get(Repository, repository_id)
    return RepositoryRead.from_model(repo)


def process_index(job_id: int):
    """One job at a time; each source file is bounded and persisted incrementally."""
    with SessionLocal() as db:
        job = db.get(IndexingJob, job_id)
        if job is None:
            return
        repo = db.get(Repository, job.repository_id)
        job.status = "running"
        db.commit()
        source = GitHubSource()
        try:
            files = source.discover(repo.owner, repo.name, job.commit_sha)
            max_bytes = limits()[1]
            for item in files:
                try:
                    content = source.fetch_file(repo.owner, repo.name, job.commit_sha, item["path"])
                    raw_size = len(content.encode("utf-8"))
                    if raw_size > max_bytes:
                        continue
                    lang = SUPPORTED["." + item["path"].rsplit(".", 1)[-1].lower()]
                    record = IndexedFile(
                        job_id=job.id,
                        path=item["path"],
                        language=lang,
                        size_bytes=raw_size,
                        content=content,
                    )
                    db.add(record)
                    db.flush()
                    for symbol in parse_symbols(content, lang):
                        db.add(CodeSymbol(file_id=record.id, **symbol))
                    job.file_count += 1
                    db.commit()
                except Exception:
                    db.rollback()
                    job = db.get(IndexingJob, job_id)
            job.status = "completed"
            db.commit()
        except Exception as exc:
            job = db.get(IndexingJob, job_id)
            if job:
                job.status = "failed"
                job.error = str(exc)[:500]
                db.commit()
        finally:
            source.close()


@app.post("/api/repositories/{repository_id}/index", status_code=202)
def start_index(
    repository_id: int,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    _require_repository_access(db, repository_id, auth.user.id)
    repo = db.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(404, "Repository not found.")

    from repopilot_api.demo import is_internal_demo_repository

    is_demo = is_internal_demo_repository(db, repo, auth.user.id)
    if is_demo:
        from repopilot_api.fixture_registry import DEMO_CALC_COMMIT_SHA

        sha = DEMO_CALC_COMMIT_SHA
    else:
        source = GitHubSource()
        try:
            sha = source.resolve_commit(repo.owner, repo.name, repo.default_branch)
        except RuntimeError as exc:
            raise HTTPException(429 if "rate limit" in str(exc).lower() else 502, str(exc)) from exc
        finally:
            source.close()
    job = db.scalar(
        select(IndexingJob).where(
            IndexingJob.repository_id == repository_id, IndexingJob.commit_sha == sha
        )
    )
    created = job is None
    if job is None:
        job = IndexingJob(repository_id=repository_id, commit_sha=sha, status="queued")
        db.add(job)
        db.commit()
        db.refresh(job)
    if created:
        background.add_task(process_index, job.id)
    return {
        "id": job.id,
        "commit_sha": job.commit_sha,
        "status": job.status,
        "file_count": job.file_count,
    }


@app.get("/api/repositories/{repository_id}/index")
def indexing_status(
    repository_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    _require_repository_access(db, repository_id, auth.user.id)
    job = db.scalar(
        select(IndexingJob)
        .where(IndexingJob.repository_id == repository_id)
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        raise HTTPException(404, "No indexing job found.")
    langs = db.query(IndexedFile.language).filter(IndexedFile.job_id == job.id).all()
    symbols = db.query(CodeSymbol).join(IndexedFile).filter(IndexedFile.job_id == job.id).count()
    return {
        "id": job.id,
        "commit_sha": job.commit_sha,
        "status": job.status,
        "file_count": job.file_count,
        "languages": sorted({v[0] for v in langs}),
        "symbol_count": symbols,
        "error": job.error,
    }


@app.get("/api/repositories/{repository_id}/files")
def indexed_files(
    repository_id: int,
    q: str = "",
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    if len(q) > 256 or "\0" in q:
        raise HTTPException(422, "Search filter is invalid or exceeds maximum length.")
    _require_repository_access(db, repository_id, auth.user.id)
    job = db.scalar(
        select(IndexingJob)
        .where(IndexingJob.repository_id == repository_id)
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        return []
    return [
        {
            "path": f.path,
            "language": f.language,
            "size_bytes": f.size_bytes,
            "symbols": [
                {"kind": s.kind, "name": s.name, "line": s.line}
                for s in db.scalars(select(CodeSymbol).where(CodeSymbol.file_id == f.id))
            ],
        }
        for f in db.scalars(
            select(IndexedFile)
            .where(IndexedFile.job_id == job.id, IndexedFile.path.ilike(f"%{q}%"))
            .limit(500)
        )
    ]


@app.get("/api/repositories/{repository_id}/file")
def get_file_content(
    repository_id: int,
    path: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    path_clean = path.strip()
    if not path_clean:
        raise HTTPException(422, "File path cannot be empty.")
    if (
        len(path_clean) > 1024
        or "\0" in path_clean
        or path_clean.startswith("/")
        or ".." in path_clean.split("/")
    ):
        raise HTTPException(422, "Invalid or unsafe file path.")
    _require_repository_access(db, repository_id, auth.user.id)
    job = db.scalar(
        select(IndexingJob)
        .where(IndexingJob.repository_id == repository_id)
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        raise HTTPException(404, "Repository is not indexed.")
    file_record = db.scalar(
        select(IndexedFile).where(
            IndexedFile.job_id == job.id,
            IndexedFile.path == path_clean,
        )
    )
    if file_record is None:
        raise HTTPException(404, f"File '{path_clean}' not found in repository index.")
    symbols = db.scalars(
        select(CodeSymbol).where(CodeSymbol.file_id == file_record.id).order_by(CodeSymbol.line)
    ).all()
    return {
        "path": file_record.path,
        "language": file_record.language,
        "size_bytes": file_record.size_bytes,
        "content": file_record.content,
        "symbols": [{"kind": s.kind, "name": s.name, "line": s.line} for s in symbols],
    }


@app.get("/api/repositories/{repository_id}/search")
def search_index(
    repository_id: int,
    q: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    clean_q = q.strip()
    if not clean_q:
        raise HTTPException(422, "Search query cannot be empty.")
    if len(clean_q) > 256 or "\0" in clean_q:
        raise HTTPException(422, "Search query is invalid or exceeds maximum length.")
    _require_repository_access(db, repository_id, auth.user.id)
    job = db.scalar(
        select(IndexingJob)
        .where(IndexingJob.repository_id == repository_id)
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        return []
    return [
        {"path": f.path, "kind": s.kind, "name": s.name, "line": s.line}
        for s, f in db.query(CodeSymbol, IndexedFile)
        .join(IndexedFile, CodeSymbol.file_id == IndexedFile.id)
        .filter(IndexedFile.job_id == job.id, CodeSymbol.name.ilike(f"%{q}%"))
        .limit(100)
    ]


@app.get(
    "/api/repositories/{repository_id}/dependency-graph",
    response_model=DependencyGraphResponse,
)
def get_repository_dependency_graph(
    repository_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    _require_repository_access(db, repository_id, auth.user.id)
    job = db.scalar(
        select(IndexingJob)
        .where(
            IndexingJob.repository_id == repository_id,
            IndexingJob.status == "completed",
        )
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        raise HTTPException(404, "No completed indexing job found for this repository.")

    files = db.scalars(select(IndexedFile).where(IndexedFile.job_id == job.id)).all()
    file_dict = {f.path: f.content for f in files}

    from repopilot_api.dependency_graph import DependencyGraphBuilder

    builder = DependencyGraphBuilder(file_dict)
    graph = builder.build()

    nodes = []
    for path, n in graph.nodes.items():
        nodes.append(
            {
                "path": n["path"],
                "language": n["language"],
                "functions": [s["name"] for s in n["symbols"] if s["kind"] == "function"],
                "classes": [s["name"] for s in n["symbols"] if s["kind"] == "class"],
                "imports": [i["imported_name"] for i in n["imports"]],
                "exports": [s["name"] for s in n["symbols"]],
                "calls": n["outgoing_files"],
                "outgoing_files": n.get("outgoing_files", []),
                "incoming_files": n.get("incoming_files", []),
                "has_circular_dependency": n.get("has_circular_dependency", False),
            }
        )

    edges = []
    for e in graph.edges:
        rel = e.get("relationship", "resolved")
        conf = 1.0 if rel == "resolved" else (0.8 if rel == "inferred" else 0.5)
        edges.append(
            {
                "source": e["source"],
                "target": e["target"],
                "kind": e["kind"],
                "symbol": e.get("target_symbol") or e.get("source_symbol") or "",
                "status": rel,
                "confidence": conf,
            }
        )

    resolved_count = sum(1 for ed in edges if ed["status"] == "resolved")
    inferred_count = sum(1 for ed in edges if ed["status"] == "inferred")
    unresolved_count = sum(1 for ed in edges if ed["status"] == "unresolved")
    total_symbols = sum(len(n["symbols"]) for n in graph.nodes.values())

    return {
        "repository_id": repository_id,
        "commit_sha": job.commit_sha,
        "summary": {
            "total_files": graph.file_count,
            "total_symbols": total_symbols,
            "total_edges": graph.edge_count,
            "resolved_edges": resolved_count,
            "inferred_edges": inferred_count,
            "unresolved_edges": unresolved_count,
            "has_circular_dependency": graph.cycle_count > 0,
            "circular_chains": graph.cycles,
        },
        "nodes": nodes,
        "edges": edges,
    }


@app.get(
    "/api/repositories/{repository_id}/symbols",
    response_model=SymbolsListResponse,
)
def get_repository_symbols(
    repository_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    _require_repository_access(db, repository_id, auth.user.id)
    job = db.scalar(
        select(IndexingJob)
        .where(
            IndexingJob.repository_id == repository_id,
            IndexingJob.status == "completed",
        )
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        raise HTTPException(404, "No completed indexing job found for this repository.")

    files = db.scalars(select(IndexedFile).where(IndexedFile.job_id == job.id)).all()
    file_dict = {f.path: f.content for f in files}

    from repopilot_api.dependency_graph import DependencyGraphBuilder

    builder = DependencyGraphBuilder(file_dict)
    graph = builder.build()

    symbols = []
    for name, sym_list in graph.symbol_index.items():
        for s in sym_list:
            incoming = [
                e["source"]
                for e in graph.edges
                if e.get("target_symbol") == name or e["target"] == s["file_path"]
            ]
            outgoing = [e["target"] for e in graph.edges if e["source"] == s["file_path"]]
            symbols.append(
                {
                    "name": s["name"],
                    "qualified_name": (
                        f"{s['parent_class']}.{s['name']}" if s.get("parent_class") else s["name"]
                    ),
                    "kind": s["kind"],
                    "file_path": s["file_path"],
                    "start_line": s["line"],
                    "end_line": s["end_line"],
                    "docstring": s.get("docstring"),
                    "incoming_calls": sorted(set(incoming)),
                    "outgoing_calls": sorted(set(outgoing)),
                }
            )

    symbols.sort(key=lambda item: (item["file_path"], item["start_line"]))
    return {
        "repository_id": repository_id,
        "commit_sha": job.commit_sha,
        "total_symbols": len(symbols),
        "symbols": symbols,
    }


@app.post(
    "/api/repositories/{repository_id}/impact-analysis",
    response_model=ImpactAnalysisResponse,
)
def analyze_repository_bug_impact(
    repository_id: int,
    payload: ImpactAnalysisRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    _require_repository_access(db, repository_id, auth.user.id)
    job = db.scalar(
        select(IndexingJob)
        .where(
            IndexingJob.repository_id == repository_id,
            IndexingJob.status == "completed",
        )
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        raise HTTPException(404, "No completed indexing job found for this repository.")

    files = db.scalars(select(IndexedFile).where(IndexedFile.job_id == job.id)).all()
    file_dict = {f.path: f.content for f in files}

    from repopilot_api.dependency_graph import DependencyGraphBuilder

    builder = DependencyGraphBuilder(file_dict)
    query_text = f"{payload.issue_title} {payload.issue_body}".strip()
    target_files = [payload.target_file] if payload.target_file else None
    target_symbols = [payload.target_symbol] if payload.target_symbol else None

    impact = builder.analyze_bug_impact(
        query=query_text,
        target_files=target_files,
        target_symbols=target_symbols,
    )

    relations = []
    for dep in impact.affected_dependencies:
        relations.append(
            {
                "target": dep["file_path"],
                "relation": "tested_by" if dep.get("is_test") else "imported_by",
                "confidence": 0.9 if dep.get("relationship") == "resolved" else 0.7,
                "status": dep.get("relationship", "inferred"),
                "evidence_lines": [],
                "explanation": dep.get("evidence")
                or f"Directly affected by {dep.get('affected_by')}",
            }
        )

    for test in impact.suggested_tests:
        relations.append(
            {
                "target": test["test_file"],
                "relation": "tested_by",
                "confidence": 0.95 if test.get("relevance") == "high" else 0.75,
                "status": "resolved",
                "evidence_lines": [],
                "explanation": test.get("reason", ""),
            }
        )

    return {
        "repository_id": repository_id,
        "commit_sha": job.commit_sha,
        "likely_root_cause_files": [c["file_path"] for c in impact.root_cause_candidates],
        "affected_downstream_files": [
            d["file_path"] for d in impact.affected_dependencies if not d.get("is_test")
        ],
        "suggested_regression_tests": [t["test_file"] for t in impact.suggested_tests],
        "relations": relations,
        "disclaimer": impact.disclaimer,
    }


def _session_read(session: ChatSession):
    return {
        "id": session.id,
        "repository_id": session.repository_id,
        "commit_sha": session.commit_sha,
        "title": session.title,
        "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(),
    }


@app.post("/api/chat/sessions", status_code=201)
def create_chat_session(
    payload: ChatSessionCreate,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    _require_repository_access(db, payload.repository_id, auth.user.id)
    if db.get(Repository, payload.repository_id) is None:
        raise HTTPException(404, "Repository not found.")
    job = db.scalar(
        select(IndexingJob)
        .where(
            IndexingJob.repository_id == payload.repository_id,
            IndexingJob.status == "completed",
        )
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        raise HTTPException(409, "Index this repository before starting a chat.")
    session = ChatSession(
        repository_id=payload.repository_id,
        owner_user_id=auth.user.id,
        commit_sha=job.commit_sha,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _session_read(session)


@app.get("/api/chat/sessions")
def list_chat_sessions(
    repository_id: int | None = None,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    query = (
        select(ChatSession)
        .join(RepositoryAccess, RepositoryAccess.repository_id == ChatSession.repository_id)
        .where(
            ChatSession.owner_user_id == auth.user.id,
            RepositoryAccess.user_id == auth.user.id,
        )
        .order_by(ChatSession.updated_at.desc())
        .limit(100)
    )
    if repository_id is not None:
        query = query.where(ChatSession.repository_id == repository_id)
    return [_session_read(item) for item in db.scalars(query)]


def _message_read(message: ChatMessage):
    from repopilot_api.chat import infer_message_mode

    mode = getattr(message, "mode", None) or infer_message_mode(message)
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": json.loads(message.citations) if message.citations else [],
        "created_at": message.created_at.isoformat(),
        "mode": mode,
    }


@app.get("/api/chat/sessions/{session_id}")
def chat_history(
    session_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    session = _owned_chat_session(db, session_id, auth.user.id)
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
    )
    return {**_session_read(session), "messages": [_message_read(message) for message in messages]}


@app.post("/api/chat/sessions/{session_id}/messages")
def ask_chat(
    session_id: int,
    payload: ChatQuestion,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    if not check_rate_limit(
        f"ask_chat:{auth.user.id}:{session_id}", max_requests=25, window_seconds=60
    ):
        raise HTTPException(
            429, "Too many chat messages sent in a short window. Please wait a moment."
        )
    session = _owned_chat_session(db, session_id, auth.user.id)
    try:
        from repopilot_api.chat import MockProvider, get_provider
        from repopilot_api.models import OAuthIdentity

        # Usage limit & public AI protection: guest users use mock provider unless ALLOW_PUBLIC_AI=1
        allow_public_ai = (
            os.getenv("ALLOW_PUBLIC_AI", "false").lower() in ("true", "1")
            or "pytest" in sys.modules
        )
        is_guest = (
            db.scalar(select(OAuthIdentity).where(OAuthIdentity.user_id == auth.user.id)) is None
        )

        if is_guest and not allow_public_ai:
            provider = MockProvider()
        else:
            provider = get_provider(mode=payload.mode) if payload.mode else None

        message = ask(db, session, payload.question, provider, mode=payload.mode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _message_read(message)


@app.delete("/api/chat/sessions/{session_id}", status_code=204)
def delete_chat_session(
    session_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    session = _owned_chat_session(db, session_id, auth.user.id)
    db.execute(delete(ChatMessage).where(ChatMessage.session_id == session.id))
    db.delete(session)
    db.commit()
    return Response(status_code=204)


@app.delete("/api/chat/sessions/{session_id}/messages")
def clear_chat_history(
    session_id: int,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    session = _owned_chat_session(db, session_id, auth.user.id)
    result = db.execute(delete(ChatMessage).where(ChatMessage.session_id == session.id))
    db.commit()
    return {"cleared": result.rowcount or 0}


@app.post("/api/repositories/{repository_id}/issues/analyze", response_model=IssueAnalysisRead)
def analyze_issue(
    repository_id: int,
    payload: IssueAnalyzeRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(require_csrf),
):
    _require_repository_access(db, repository_id, auth.user.id)
    if not check_rate_limit(
        f"analyze_issue:{auth.user.id}:{repository_id}", max_requests=10, window_seconds=60
    ):
        raise HTTPException(
            429, "Too many issue analysis requests. Please wait a moment before trying again."
        )
    repo = db.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(404, "Repository not found.")

    job = db.scalar(
        select(IndexingJob)
        .where(
            IndexingJob.repository_id == repository_id,
            IndexingJob.status == "completed",
        )
        .order_by(IndexingJob.id.desc())
    )
    if job is None:
        raise HTTPException(409, "Index this repository before analyzing issues.")

    from repopilot_api.demo import is_internal_demo_repository

    is_demo = is_internal_demo_repository(db, repo, auth.user.id)
    if is_demo:
        issue = {
            "number": payload.issue_number,
            "title": "Bug in power calculation: multiplication returned instead of exponentiation",
            "body": (
                "When calling power(2, 3), result is 6 instead of 8. "
                "Suspect operator defect in src/calculator.py where power() uses "
                "multiplication instead of exponentiation."
            ),
            "state": "open",
            "html_url": f"https://github.com/{repo.owner}/{repo.name}/issues/{payload.issue_number}",
            "author": "demo-reporter",
            "labels": ["bug", "math", "deterministic-demo"],
        }
    else:
        source = GitHubSource()
        try:
            issue = source.fetch_issue(repo.owner, repo.name, payload.issue_number)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            msg = str(exc)
            if "not found" in msg.lower() or "404" in msg:
                raise HTTPException(
                    404, f"Issue #{payload.issue_number} was not found on GitHub."
                ) from exc
            if "rate limit" in msg.lower():
                raise HTTPException(
                    429, "GitHub rate limit reached. Please try again later."
                ) from exc
            raise HTTPException(502, f"Could not fetch issue from GitHub: {msg}") from exc
        finally:
            source.close()

    from repopilot_api.chat import (
        build_issue_query,
        get_provider,
        parse_and_validate_analysis,
        retrieve,
    )

    query = build_issue_query(issue["title"], issue["body"])
    evidence = retrieve(db, repository_id, job.commit_sha, query)
    if not evidence:
        raise HTTPException(
            422, "No relevant code evidence found in indexed files to analyze this issue."
        )

    indexed_files = {
        f.path: f.content
        for f in db.scalars(select(IndexedFile).where(IndexedFile.job_id == job.id)).all()
    }

    req_mode = getattr(payload, "mode", "auto")
    from repopilot_api.models import OAuthIdentity

    allow_public_ai = (
        os.getenv("ALLOW_PUBLIC_AI", "false").lower() in ("true", "1")
        or "pytest" in sys.modules
    )
    is_guest = db.scalar(select(OAuthIdentity).where(OAuthIdentity.user_id == auth.user.id)) is None

    if req_mode in ("mock_ai", "deterministic_fixture") or (is_guest and not allow_public_ai):
        from repopilot_api.chat import MockProvider

        provider = MockProvider()
    else:
        provider = get_provider(task="issue_analysis")

    is_mock = getattr(provider, "__class__", None).__name__ == "MockProvider"
    provider_name = "mock" if is_mock else "cloud"

    try:
        raw_analysis = provider.analyze_issue(issue, evidence)
    except ProviderError as exc:
        raise HTTPException(502, f"The configured answer provider failed: {exc}") from exc

    try:
        analysis_result = parse_and_validate_analysis(
            raw_analysis,
            evidence,
            indexed_files,
            provider_name=provider_name,
            is_mock=is_mock,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not valid JSON" in msg or "JSON" in msg:
            raise HTTPException(502, f"The AI provider returned malformed JSON: {msg}") from exc
        raise HTTPException(502, f"Model analysis failed validation: {msg}") from exc

    telemetry = getattr(provider, "last_telemetry", {}) or {}
    model_name = (
        telemetry.get("model")
        or getattr(provider, "model", None)
        or ("mock" if is_mock else "cloud")
    )
    latency_ms = telemetry.get("latency_ms")
    token_usage = telemetry.get("token_usage")
    generation_mode = telemetry.get("generation_mode") or ("mock_ai" if is_mock else "live_ai")

    analysis_result.model = model_name
    analysis_result.latency_ms = latency_ms
    analysis_result.token_usage = token_usage
    analysis_result.generation_mode = generation_mode

    return {
        "repository_id": repository_id,
        "commit_sha": job.commit_sha,
        "issue_number": issue["number"],
        "issue_title": issue["title"],
        "issue_body": issue["body"],
        "issue_url": issue["html_url"],
        "query": query,
        "provider": provider_name,
        "is_mock": is_mock,
        "model": model_name,
        "latency_ms": latency_ms,
        "token_usage": token_usage,
        "generation_mode": generation_mode,
        "evidence": [
            {
                "path": item.path,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "content": item.content,
                "score": round(item.score, 4),
            }
            for item in evidence
        ],
        "analysis": analysis_result,
    }
