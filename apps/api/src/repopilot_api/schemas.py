import re
from typing import Literal
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


class IssueAnalyzeRequest(BaseModel):
    issue_number: int

    @field_validator("issue_number")
    @classmethod
    def validate_issue_number(cls, value: int) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("Issue number must be a positive integer.")
        return value


class IssueEvidenceRead(BaseModel):
    path: str
    start_line: int
    end_line: int
    content: str
    score: float


class FileCitation(BaseModel):
    path: str
    start_line: int
    end_line: int
    explanation: str = ""

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        clean = value.strip().replace("\\", "/")
        if ".." in clean.split("/") or clean.startswith("/"):
            raise ValueError("Path must be a clean relative path.")
        return clean

    @field_validator("start_line", "end_line")
    @classmethod
    def validate_lines(cls, value: int) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("Line numbers must be positive integers.")
        return value


class PatchProposal(BaseModel):
    file_path: str
    explanation: str = ""
    diff: str

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, value: str) -> str:
        clean = value.strip().replace("\\", "/")
        if ".." in clean.split("/") or clean.startswith("/"):
            raise ValueError("file_path must be a clean relative path.")
        return clean

    @field_validator("diff")
    @classmethod
    def validate_diff(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Patch diff cannot be empty.")
        return value


class ProposedFix(BaseModel):
    explanation: str
    patches: list[PatchProposal] = []


class IssueAnalysisResult(BaseModel):
    summary: str
    root_cause_hypothesis: str
    relevant_files: list[FileCitation] = []
    proposed_fix: ProposedFix
    confidence: Literal["low", "medium", "high"]
    confidence_reasoning: str = ""
    limitations: list[str] = []

    @field_validator("root_cause_hypothesis")
    @classmethod
    def validate_hypothesis(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Root cause hypothesis cannot be empty.")
        if not re.search(
            r"\b(hypothesis|hypothesize|possible|potential|suspected|likely)\b",
            clean,
            re.IGNORECASE,
        ):
            return f"Hypothesis: {clean}"
        return clean


class IssueAnalysisRead(BaseModel):
    repository_id: int
    commit_sha: str
    issue_number: int
    issue_title: str
    issue_body: str
    issue_url: str
    query: str
    evidence: list[IssueEvidenceRead]
    analysis: IssueAnalysisResult
