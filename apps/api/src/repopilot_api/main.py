import os

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from httpx import Client
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repopilot_api.database import SessionLocal
from repopilot_api.models import Repository
from repopilot_api.schemas import RepositoryImport, RepositoryRead

app = FastAPI(title="RepoPilot Fix API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def get_db():
    with SessionLocal() as session:
        yield session


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "repopilot-api"}


@app.post("/api/repositories/import", response_model=RepositoryRead, status_code=200)
def import_repository(payload: RepositoryImport, db: Session = Depends(get_db)):
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
    existing = db.scalar(select(Repository).where(Repository.github_id == github_id))
    if existing:
        return RepositoryRead.from_model(existing)
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
        db.commit()
    except IntegrityError:
        db.rollback()
        repo = db.scalar(select(Repository).where(Repository.github_id == github_id))
        if repo is None:
            raise HTTPException(409, "Repository conflicts with an existing record.")
    else:
        db.refresh(repo)
    return RepositoryRead.from_model(repo)


@app.get("/api/repositories", response_model=list[RepositoryRead])
def list_repositories(db: Session = Depends(get_db)):
    repos = db.scalars(select(Repository).order_by(Repository.created_at.desc())).all()
    return [RepositoryRead.from_model(repo) for repo in repos]


@app.get("/api/repositories/{repository_id}", response_model=RepositoryRead)
def get_repository(repository_id: int, db: Session = Depends(get_db)):
    repo = db.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(404, "Repository not found.")
    return RepositoryRead.from_model(repo)
