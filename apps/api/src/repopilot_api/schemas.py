import re
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator


class RepositoryImport(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_github_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.hostname not in {"github.com", "www.github.com"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Enter a public GitHub repository URL (github.com/owner/repo).")
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", p) for p in parts):
            raise ValueError("Enter a repository URL in the form github.com/owner/repo.")
        if any(p in {".", ".."} for p in parts):
            raise ValueError("Invalid repository URL.")
        return f"https://github.com/{parts[0]}/{parts[1]}"


class RepositoryRead(BaseModel):
    id: int
    github_id: int
    owner: str
    name: str
    full_name: str
    url: str
    description: str | None
    default_branch: str
    stars: int
    created_at: str
    updated_at: str

    @classmethod
    def from_model(cls, repo):
        values = {
            key: getattr(repo, key)
            for key in cls.model_fields
            if key not in {"created_at", "updated_at"}
        }
        values["created_at"] = repo.created_at.isoformat()
        values["updated_at"] = repo.updated_at.isoformat()
        return cls(**values)


class ChatSessionCreate(BaseModel):
    repository_id: int


class ChatQuestion(BaseModel):
    question: str


class CitationRead(BaseModel):
    path: str
    start_line: int
    end_line: int


class ChatMessageRead(BaseModel):
    id: int
    role: str
    content: str
    citations: list[CitationRead]
    created_at: str


class ChatSessionRead(BaseModel):
    id: int
    repository_id: int
    commit_sha: str
    title: str
    created_at: str
    updated_at: str
