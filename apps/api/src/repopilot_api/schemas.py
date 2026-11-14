import re
from enum import StrEnum
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


class SandboxExecutionState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ERROR = "error"


class SandboxExecutionRequest(BaseModel):
    command: list[str]
    timeout_seconds: int = 30
    environment: dict[str, str] = {}

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if not value or not isinstance(value, list):
            raise ValueError("Command must be a non-empty list of string arguments.")
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("Command tokens must be non-empty strings.")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if type(value) is not int or value < 1 or value > 60:
            raise ValueError("timeout_seconds must be an integer between 1 and 60 seconds.")
        return value


class SandboxExecutionResult(BaseModel):
    run_id: str
    state: SandboxExecutionState
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    command: list[str]
    image: str
    error_message: str | None = None
    created_at: str


class PatchFileResult(BaseModel):
    file_path: str
    original_sha256: str
    patched_sha256: str
    hunks_applied: int
    lines_added: int
    lines_deleted: int


class PatchApplicationResult(BaseModel):
    success: bool
    changed_files: list[str]
    patch_hash: str
    failure_reason: str | None = None
    applied_at: str
    file_results: list[PatchFileResult] = []


class ProofVerdict(StrEnum):
    VERIFIED_FIX = "verified_fix"
    UNREPRODUCED = "unreproduced"
    FIX_FAILED = "fix_failed"
    REGRESSION_DETECTED = "regression_detected"
    PATCH_APPLICATION_ERROR = "patch_error"
    TIMEOUT = "timed_out"
    ERROR = "error"
    INCONCLUSIVE = "inconclusive"


class VerificationStage(StrEnum):
    INITIALIZED = "initialized"
    PREPARING_BASELINE = "preparing_baseline"
    RUNNING_BASELINE = "running_baseline"
    ANALYZING_BASELINE = "analyzing_baseline"
    PREPARING_PATCHED = "preparing_patched"
    RUNNING_PATCHED = "running_patched"
    ANALYZING_PATCHED = "analyzing_patched"
    EVALUATING_VERDICT = "evaluating_verdict"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class TestMetricsSchema(BaseModel):
    total: int | None = None
    passed: int | None = None
    failed: int | None = None
    errors: int | None = None
    failed_test_ids: list[str] = []
    failure_type: str | None = None
    summary: str = ""


class ProofEvidence(BaseModel):
    repository_id: int
    commit_sha: str
    issue_number: int
    patch_hash: str
    sandbox_image: str
    test_command: list[str]
    baseline_exit_code: int | None = None
    baseline_stdout: str = ""
    baseline_stderr: str = ""
    baseline_metrics: TestMetricsSchema | None = None
    baseline_failure_type: str | None = None
    patched_exit_code: int | None = None
    patched_stdout: str = ""
    patched_stderr: str = ""
    patched_metrics: TestMetricsSchema | None = None
    patched_failure_type: str | None = None
    resolved_test_ids: list[str] = []
    new_failed_test_ids: list[str] = []
    unresolved_test_ids: list[str] = []
    changed_files: list[str] = []
    duration_seconds: float = 0.0
    uncertainty_reasons: list[str] = []


class ProofOfFixRequest(BaseModel):
    repository_id: int
    commit_sha: str
    issue_number: int
    test_command: list[str]
    patches: list[PatchProposal] | ProposedFix
    hypothesis: str | None = None
    user_id: int | None = None
    timeout_seconds: int = 30
    environment: dict[str, str] = {}
    files: dict[str, str] | None = None

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: str) -> str:
        clean = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", clean):
            raise ValueError("commit_sha must be a valid 40-character hex string.")
        return clean

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if type(value) is not int or value < 1 or value > 60:
            raise ValueError("timeout_seconds must be an integer between 1 and 60 seconds.")
        return value


class ProofOfFixResult(BaseModel):
    run_id: str
    verdict: ProofVerdict
    confidence: str
    summary: str
    stage: VerificationStage
    evidence: ProofEvidence
    error_message: str | None = None
    verification_run_id: int | None = None
    proof_report_id: int | None = None
    is_persisted: bool = False
    persistence_error: str | None = None
    created_at: str


class VerificationJobStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class VerificationAuditEventType(StrEnum):
    CREATED = "created"
    SPEC_UPDATED = "spec_updated"
    SUBMITTED_FOR_APPROVAL = "submitted_for_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    QUEUED = "queued"
    STARTED = "started"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRIED = "retried"
    RECOVERED = "recovered"


class VerificationCreateRequest(BaseModel):
    commit_sha: str
    issue_number: int
    test_command: list[str]
    patches: list[PatchProposal] | ProposedFix
    hypothesis: str | None = None
    sandbox_image: str | None = None
    timeout_seconds: int = 30
    auto_submit_approval: bool = True
    idempotency_key: str | None = None

    @field_validator("commit_sha")
    @classmethod
    def validate_commit_sha(cls, value: str) -> str:
        clean = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}", clean):
            raise ValueError("commit_sha must be a valid 40-character hex string.")
        return clean

    @field_validator("issue_number")
    @classmethod
    def validate_issue_number(cls, value: int) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("issue_number must be a positive integer.")
        return value

    @field_validator("test_command")
    @classmethod
    def validate_test_command(cls, value: list[str]) -> list[str]:
        if not value or not isinstance(value, list):
            raise ValueError("test_command must be a non-empty list of tokens.")
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("test_command tokens must be non-empty strings.")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: int) -> int:
        if type(value) is not int or value < 1 or value > 60:
            raise ValueError("timeout_seconds must be an integer between 1 and 60 seconds.")
        return value


class VerificationApprovalRequest(BaseModel):
    expected_spec_hash: str | None = None
    auto_queue: bool = True
    comment: str | None = None


class VerificationRejectionRequest(BaseModel):
    reason: str = ""


class VerificationCancelRequest(BaseModel):
    reason: str = ""


class VerificationAuditEventRead(BaseModel):
    id: int
    verification_run_id: int
    event_type: str
    actor_user_id: int | None
    from_status: str | None
    to_status: str | None
    details: dict = {}
    created_at: str


class ProofReportRead(BaseModel):
    id: int
    verification_run_id: int
    verdict: str
    hypothesis: str
    summary: str
    baseline_summary: str
    patched_summary: str
    confidence: str
    evidence: dict
    created_at: str


class VerificationRunRead(BaseModel):
    id: int
    repository_id: int
    commit_sha: str
    issue_number: int
    user_id: int | None
    status: str
    spec_hash: str | None
    approved_spec_hash: str | None
    approved_by_user_id: int | None
    approved_at: str | None
    rejection_reason: str | None
    test_command: list[str]
    sandbox_image: str
    duration_seconds: float
    baseline_exit_code: int | None
    patched_exit_code: int | None
    patch_hash: str | None
    retry_count: int
    max_retries: int
    error_message: str | None
    cancelled_at: str | None
    cancelled_by_user_id: int | None
    cancellation_reason: str | None
    report: ProofReportRead | None = None
    created_at: str
    updated_at: str
