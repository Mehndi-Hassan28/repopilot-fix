import json

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
from repopilot_api.schemas import ChatQuestion, ChatSessionCreate, RepositoryImport, RepositoryRead
from repopilot_api.security import AuthContext, configured_origins, get_current_auth, require_csrf

app = FastAPI(title="RepoPilot Fix API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(configured_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
app.include_router(auth_router)


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
    owner, name = payload.url.removeprefix("https://github.com/").split("/")
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
            description=data.get("description"),
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
def list_repositories(
    db: Session = Depends(get_db), auth: AuthContext = Depends(get_current_auth)
):
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


@app.get("/api/repositories/{repository_id}/search")
def search_index(
    repository_id: int,
    q: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_current_auth),
):
    if not q.strip():
        raise HTTPException(422, "Search query cannot be empty.")
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
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": json.loads(message.citations),
        "created_at": message.created_at.isoformat(),
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
    session = _owned_chat_session(db, session_id, auth.user.id)
    try:
        from repopilot_api.chat import get_provider

        message = ask(db, session, payload.question, get_provider())
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
