"""Secure, isolated Docker sandbox execution engine for untrusted code verification."""

import asyncio
import logging
import os
import re
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from repopilot_api.schemas import (
    SandboxExecutionRequest,
    SandboxExecutionResult,
    SandboxExecutionState,
)

logger = logging.getLogger(__name__)

# Constants & Defaults
DEFAULT_SANDBOX_IMAGE = (
    "python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535"
)
DEFAULT_MEMORY_LIMIT = "512m"
DEFAULT_CPU_LIMIT = "1.0"
DEFAULT_PIDS_LIMIT = 100
DEFAULT_TIMEOUT_SECONDS = 30
MAX_LOG_BYTES = 50 * 1024  # 50 KB output limit
DEFAULT_SCRATCH_ROOT = "/tmp/repopilot_sandbox"

# Dangerous tokens forbidden in test commands
FORBIDDEN_COMMAND_TOKENS = {
    ";",
    "&",
    "|",
    ">",
    "<",
    "$",
    "`",
    "\n",
    "\r",
    "\\",
    "(",
    ")",
    "{",
    "}",
    "eval",
    "exec",
    "sudo",
    "su",
    "chown",
    "chmod",
    "curl",
    "wget",
    "nc",
    "netcat",
    "sh",
    "bash",
    "zsh",
}

# Whitelist of approved test runners
APPROVED_ENTRYPOINTS = {"pytest", "python", "python3"}

FORBIDDEN_ENV_SUBSTRINGS = {
    "SECRET",
    "KEY",
    "TOKEN",
    "PASSWORD",
    "PASS",
    "DATABASE",
    "URL",
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
    "API",
}


class SandboxError(Exception):
    """Base exception for all sandbox errors."""


class SandboxSecurityError(SandboxError):
    """Raised when security boundaries or input validations are violated."""


class SandboxTimeoutError(SandboxError):
    """Raised when sandbox execution exceeds the allotted time limit."""


class SandboxConcurrencyError(SandboxError):
    """Raised when a sandbox execution request exceeds the host concurrency limit."""


class SandboxConfigurationError(SandboxError):
    """Raised when sandbox configuration or runner image is invalid."""


class SandboxExecutionError(SandboxError):
    """Raised when container execution encounters an infrastructure failure."""


class CommandValidator:
    """Validates commands to ensure no shell injection, arbitrary execution or unsafe tools."""

    @classmethod
    def validate(cls, command: list[str]) -> list[str]:
        if not command or not isinstance(command, list):
            raise SandboxSecurityError("Command must be a non-empty list of argument strings.")

        entrypoint = command[0].strip()
        if entrypoint not in APPROVED_ENTRYPOINTS:
            allowed = sorted(APPROVED_ENTRYPOINTS)
            raise SandboxSecurityError(
                f"Unauthorized command entrypoint '{entrypoint}'. Allowed: {allowed}"
            )

        # Specific checks for python entrypoints: must run module unittest or pytest
        if entrypoint in {"python", "python3"}:
            if len(command) < 3 or command[1] != "-m" or command[2] not in {"unittest", "pytest"}:
                raise SandboxSecurityError(
                    "Python entrypoint must strictly use '-m unittest' or '-m pytest'."
                )

        # Inspect all tokens for forbidden metacharacters or subshell triggers
        for token in command:
            if not isinstance(token, str) or not token.strip():
                raise SandboxSecurityError("Command tokens must be non-empty strings.")
            for forbidden in FORBIDDEN_COMMAND_TOKENS:
                if forbidden in token:
                    raise SandboxSecurityError(
                        f"Command token contains forbidden shell metacharacter: '{forbidden}'."
                    )

        return command


class EnvironmentSanitizer:
    """Provides a minimal, sanitized environment for containers without host secret leakage."""

    @classmethod
    def sanitize(cls, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        # Minimal safe default environment
        safe_env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
            "PYTEST_ADDOPTS": "-o cache_dir=/tmp/.pytest_cache -p no:cacheprovider",
        }

        if not extra_env:
            return safe_env

        for key, value in extra_env.items():
            if not re.fullmatch(r"[A-Z0-9_]{1,64}", key):
                raise SandboxSecurityError(f"Invalid environment variable name '{key}'.")
            upper_key = key.upper()
            if any(sub in upper_key for sub in FORBIDDEN_ENV_SUBSTRINGS):
                raise SandboxSecurityError(
                    f"Forbidden environment variable '{key}': secrets cannot be passed to sandbox."
                )
            if any(c in value for c in ("\x00", "\n", "\r")):
                raise SandboxSecurityError(
                    f"Environment variable '{key}' contains invalid control characters."
                )
            safe_env[key] = value

        return safe_env


class ImageValidator:
    """Validates that sandbox container images are pinned by immutable cryptographic digests."""

    IMAGE_DIGEST_REGEX: ClassVar[re.Pattern] = re.compile(
        r"^[a-zA-Z0-9]+(?:[._:-][a-zA-Z0-9]+)*(?:/[a-zA-Z0-9]+(?:[._:-][a-zA-Z0-9]+)*)*@sha256:[a-f0-9]{64}$"
    )
    IMAGE_GENERAL_REGEX: ClassVar[re.Pattern] = re.compile(
        r"^[a-zA-Z0-9]+(?:[._:-][a-zA-Z0-9]+)*(?:/[a-zA-Z0-9]+(?:[._:-][a-zA-Z0-9]+)*)*(?:@sha256:[a-f0-9]{64})?$"
    )

    @classmethod
    def validate(cls, image: str, require_digest: bool = True) -> str:
        if not image or not isinstance(image, str):
            raise SandboxConfigurationError("Sandbox image cannot be empty.")
        if image != image.strip() or any(c.isspace() for c in image):
            raise SandboxSecurityError(f"Sandbox image '{image}' cannot contain whitespace.")
        if image.startswith("-"):
            raise SandboxSecurityError(f"Sandbox image '{image}' cannot start with a flag.")

        pattern = cls.IMAGE_DIGEST_REGEX if require_digest else cls.IMAGE_GENERAL_REGEX
        if not pattern.fullmatch(image):
            if require_digest:
                raise SandboxSecurityError(
                    f"Sandbox image '{image}' must be pinned by an immutable @sha256 digest."
                )
            raise SandboxSecurityError(f"Invalid sandbox image name format: '{image}'.")

        return image


class PathValidator:
    """Ensures workspace directories are strictly inside designated scratch root."""

    @classmethod
    def validate_workspace(cls, workspace_path: str, scratch_root: str | None = None) -> str:
        if scratch_root is None:
            scratch_root = os.getenv("SANDBOX_SCRATCH_ROOT", DEFAULT_SCRATCH_ROOT)
        if not workspace_path or not isinstance(workspace_path, str):
            raise SandboxSecurityError("Workspace path must be a valid non-empty string.")

        real_scratch = os.path.realpath(scratch_root)
        real_workspace = os.path.realpath(workspace_path)

        try:
            rel = os.path.relpath(real_workspace, real_scratch)
        except ValueError as exc:
            raise SandboxSecurityError(
                "Workspace path is on an invalid drive or filesystem."
            ) from exc

        if rel == "." or rel.startswith("..") or os.path.isabs(rel):
            raise SandboxSecurityError(
                f"Workspace must be strictly inside scratch root: {real_scratch}"
            )

        if not os.path.isdir(real_workspace):
            raise SandboxSecurityError(f"Workspace directory does not exist: {real_workspace}")

        return real_workspace


class OutputTruncator:
    """Caps output streams to prevent memory exhaustion and DoS from excessive logging."""

    @classmethod
    def truncate(cls, output: str, max_bytes: int = MAX_LOG_BYTES) -> str:
        if len(output) <= max_bytes:
            return output
        marker = "\n... [output truncated to 50KB limit] ...\n"
        keep = max(0, max_bytes - len(marker))
        half = keep // 2
        return output[:half] + marker + output[-half:]

    @classmethod
    async def read_bounded_stream(
        cls,
        stream: asyncio.StreamReader | None,
        max_bytes: int = MAX_LOG_BYTES,
    ) -> tuple[str, bool]:
        """Read an asynchronous stream without buffering unbounded data into host memory.

        Captures up to max_bytes in memory. Any subsequent bytes from the stream
        are drained and discarded to prevent memory exhaustion while avoiding
        subprocess pipe buffer deadlocks.
        """
        if stream is None:
            return "", False

        chunks: list[bytes] = []
        total_captured = 0
        total_seen = 0
        chunk_size = 4096

        try:
            while True:
                chunk = await stream.read(chunk_size)
                if not chunk:
                    break
                total_seen += len(chunk)
                if total_captured < max_bytes:
                    allowed = min(len(chunk), max_bytes - total_captured)
                    chunks.append(chunk[:allowed])
                    total_captured += allowed
        except asyncio.CancelledError:
            pass

        truncated = total_seen > max_bytes
        text = b"".join(chunks).decode("utf-8", errors="replace")
        if truncated:
            dropped = total_seen - total_captured
            text += (
                f"\n... [output truncated: {dropped} bytes dropped beyond "
                f"{max_bytes // 1024}KB limit] ...\n"
            )
        return text, truncated


class SandboxConcurrencyLimiter:
    """Single-execution concurrency guard for low-memory environments."""

    def __init__(self, max_concurrent: int = 1):
        self._max_concurrent = max_concurrent
        self._lock = asyncio.Lock()
        self._active_runs: set[str] = set()

    async def acquire(self, run_id: str) -> None:
        async with self._lock:
            if len(self._active_runs) >= self._max_concurrent:
                raise SandboxConcurrencyError(
                    "Concurrency limit reached: another sandbox execution is currently in progress."
                )
            self._active_runs.add(run_id)

    async def release(self, run_id: str) -> None:
        async with self._lock:
            self._active_runs.discard(run_id)

    @property
    def active_count(self) -> int:
        return len(self._active_runs)


# Global limiter singleton enforcing 1 execution at a time on low-memory host
GLOBAL_CONCURRENCY_LIMITER = SandboxConcurrencyLimiter(max_concurrent=1)


class WorkspaceManager:
    """Manages ephemeral workspace directories with restricted permissions and assured cleanup."""

    def __init__(self, scratch_root: str = DEFAULT_SCRATCH_ROOT):
        self.scratch_root = scratch_root

    def create_workspace(self, run_id: str, files: dict[str, str] | None = None) -> str:
        os.makedirs(self.scratch_root, mode=0o700, exist_ok=True)
        workspace_dir = os.path.join(self.scratch_root, run_id)
        os.makedirs(workspace_dir, mode=0o700, exist_ok=False)
        canonical_workspace = Path(workspace_dir).resolve()

        if files:
            for rel_path, content in files.items():
                clean_path = rel_path.strip().replace("\\", "/")
                if clean_path.startswith("/") or ".." in clean_path.split("/"):
                    raise SandboxSecurityError(f"Invalid file path in workspace: {rel_path}")
                dest_file = Path(workspace_dir) / clean_path
                dest_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                dest_file.write_text(content, encoding="utf-8")
                if not dest_file.resolve().is_relative_to(canonical_workspace):
                    raise SandboxSecurityError(
                        f"Symlink or directory traversal detected in workspace file: {rel_path}"
                    )

        return workspace_dir

    def cleanup_workspace(self, workspace_dir: str) -> None:
        if os.path.exists(workspace_dir):
            shutil.rmtree(workspace_dir, ignore_errors=True)


class SandboxRunner:
    """Abstract interface for executing code verifications inside isolated environments."""

    async def run(
        self, workspace_path: str, request: SandboxExecutionRequest
    ) -> SandboxExecutionResult:
        raise NotImplementedError


class MockSandboxRunner(SandboxRunner):
    """Deterministic in-memory mock runner for unit testing and offline development."""

    def __init__(
        self,
        simulated_state: SandboxExecutionState = SandboxExecutionState.PASSED,
        simulated_exit_code: int = 0,
        simulated_stdout: str = "================ 1 passed in 0.05s ================\n",
        simulated_stderr: str = "",
        simulated_duration: float = 0.5,
        simulated_error: str | None = None,
        image: str = DEFAULT_SANDBOX_IMAGE,
        limiter: SandboxConcurrencyLimiter | None = None,
        scratch_root: str | None = None,
    ):
        self.simulated_state = simulated_state
        self.simulated_exit_code = simulated_exit_code
        self.simulated_stdout = simulated_stdout
        self.simulated_stderr = simulated_stderr
        self.simulated_duration = simulated_duration
        self.simulated_error = simulated_error
        self.image = image
        self.limiter = limiter or GLOBAL_CONCURRENCY_LIMITER
        self.scratch_root = scratch_root
        self.executed_commands: list[list[str]] = []

    async def run(
        self, workspace_path: str, request: SandboxExecutionRequest
    ) -> SandboxExecutionResult:
        run_id = str(uuid.uuid4())
        created_at = datetime.now(UTC).isoformat()

        # Enforce all security validations even in mock mode
        CommandValidator.validate(request.command)
        EnvironmentSanitizer.sanitize(request.environment)
        ImageValidator.validate(self.image, require_digest=False)
        PathValidator.validate_workspace(workspace_path, scratch_root=self.scratch_root)

        await self.limiter.acquire(run_id)
        try:
            self.executed_commands.append(request.command)

            if self.simulated_state == SandboxExecutionState.TIMED_OUT:
                return SandboxExecutionResult(
                    run_id=run_id,
                    state=SandboxExecutionState.TIMED_OUT,
                    exit_code=None,
                    stdout=OutputTruncator.truncate(self.simulated_stdout),
                    stderr=OutputTruncator.truncate(self.simulated_stderr),
                    duration_seconds=float(request.timeout_seconds),
                    command=request.command,
                    image=self.image,
                    error_message=f"Execution timed out after {request.timeout_seconds} seconds.",
                    created_at=created_at,
                )

            if self.simulated_error:
                return SandboxExecutionResult(
                    run_id=run_id,
                    state=SandboxExecutionState.ERROR,
                    exit_code=None,
                    stdout="",
                    stderr="",
                    duration_seconds=0.01,
                    command=request.command,
                    image=self.image,
                    error_message=self.simulated_error,
                    created_at=created_at,
                )

            return SandboxExecutionResult(
                run_id=run_id,
                state=self.simulated_state,
                exit_code=self.simulated_exit_code,
                stdout=OutputTruncator.truncate(self.simulated_stdout),
                stderr=OutputTruncator.truncate(self.simulated_stderr),
                duration_seconds=self.simulated_duration,
                command=request.command,
                image=self.image,
                error_message=None,
                created_at=created_at,
            )
        finally:
            await self.limiter.release(run_id)


def get_default_sandbox_user() -> str:
    """Return a non-root container user string matching host non-root uid (e.g. '1000:1000')."""
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        uid = os.getuid()
        gid = os.getgid()
        if uid != 0:
            return f"{uid}:{gid}"
    return "1000:1000"


class DockerSandboxRunner(SandboxRunner):
    """Secure Docker runner enforcing non-root, read-only rootfs, zero networking and limits."""

    DEFAULT_SECURITY_ARGS: ClassVar[list[str]] = [
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        "1000:1000",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
    ]

    def __init__(
        self,
        image: str = DEFAULT_SANDBOX_IMAGE,
        memory_limit: str = DEFAULT_MEMORY_LIMIT,
        cpu_limit: str = DEFAULT_CPU_LIMIT,
        pids_limit: int = DEFAULT_PIDS_LIMIT,
        require_digest: bool = True,
        user: str | None = None,
        limiter: SandboxConcurrencyLimiter | None = None,
        scratch_root: str | None = None,
    ):
        self.image = ImageValidator.validate(image, require_digest=require_digest)
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit
        self.user = user or get_default_sandbox_user()
        if self.user in ("0", "0:0", "root"):
            raise SandboxSecurityError("Root execution inside container is strictly forbidden.")
        self.limiter = limiter or GLOBAL_CONCURRENCY_LIMITER
        self.scratch_root = scratch_root

    def build_docker_args(
        self, container_name: str, workspace_path: str, env: dict[str, str], command: list[str]
    ) -> list[str]:
        """Construct the hardened Docker execution arguments array."""
        args = [
            "docker",
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            self.user,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--memory",
            self.memory_limit,
            "--memory-swap",
            self.memory_limit,
            "--cpus",
            self.cpu_limit,
            "--pids-limit",
            str(self.pids_limit),
            "-v",
            f"{workspace_path}:/workspace:ro",
            "-w",
            "/workspace",
        ]

        for k, v in env.items():
            args.extend(["-e", f"{k}={v}"])

        args.append(self.image)
        args.extend(command)
        return args

    @staticmethod
    async def _force_remove_container(container_name: str) -> bool:
        """Forcibly remove a Docker container, returning True on success."""
        try:
            cleanup_proc = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "-f",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await cleanup_proc.wait()
            if cleanup_proc.returncode != 0:
                logger.warning(
                    "Docker container %s force-removal exited with code %s",
                    container_name,
                    cleanup_proc.returncode,
                )
                return False
            return True
        except Exception as exc:
            logger.warning(
                "Failed to execute force-removal for container %s: %s",
                container_name,
                exc,
            )
            return False

    async def run(
        self, workspace_path: str, request: SandboxExecutionRequest
    ) -> SandboxExecutionResult:
        run_id = str(uuid.uuid4())
        container_name = f"repopilot_sbx_{run_id[:12]}"
        created_at = datetime.now(UTC).isoformat()

        # Input validations
        validated_command = CommandValidator.validate(request.command)
        sanitized_env = EnvironmentSanitizer.sanitize(request.environment)
        validated_workspace = PathValidator.validate_workspace(
            workspace_path, scratch_root=self.scratch_root
        )

        docker_args = self.build_docker_args(
            container_name=container_name,
            workspace_path=validated_workspace,
            env=sanitized_env,
            command=validated_command,
        )

        await self.limiter.acquire(run_id)
        start_time = time.monotonic()
        process = None

        try:
            process = await asyncio.create_subprocess_exec(
                *docker_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_task = asyncio.create_task(
                OutputTruncator.read_bounded_stream(process.stdout, MAX_LOG_BYTES)
            )
            stderr_task = asyncio.create_task(
                OutputTruncator.read_bounded_stream(process.stderr, MAX_LOG_BYTES)
            )

            execution_gather = asyncio.gather(process.wait(), stdout_task, stderr_task)
            try:
                await asyncio.wait_for(
                    execution_gather,
                    timeout=float(request.timeout_seconds),
                )
                duration = round(time.monotonic() - start_time, 3)
                exit_code = process.returncode

                stdout_text, _ = stdout_task.result()
                stderr_text, _ = stderr_task.result()

                state = (
                    SandboxExecutionState.PASSED if exit_code == 0 else SandboxExecutionState.FAILED
                )

                return SandboxExecutionResult(
                    run_id=run_id,
                    state=state,
                    exit_code=exit_code,
                    stdout=stdout_text,
                    stderr=stderr_text,
                    duration_seconds=duration,
                    command=validated_command,
                    image=self.image,
                    error_message=None,
                    created_at=created_at,
                )

            except TimeoutError:
                duration = round(time.monotonic() - start_time, 3)
                if not execution_gather.done():
                    execution_gather.cancel()
                try:
                    await execution_gather
                except (asyncio.CancelledError, Exception):
                    pass

                # Forcibly remove container on timeout
                await self._force_remove_container(container_name)

                # Terminate and reap host process if still alive
                if process.returncode is None:
                    try:
                        process.kill()
                    except Exception:
                        pass
                    try:
                        await process.wait()
                    except Exception:
                        pass

                return SandboxExecutionResult(
                    run_id=run_id,
                    state=SandboxExecutionState.TIMED_OUT,
                    exit_code=None,
                    stdout="",
                    stderr=f"Execution timed out after {request.timeout_seconds}s.",
                    duration_seconds=duration,
                    command=validated_command,
                    image=self.image,
                    error_message=f"Execution timed out after {request.timeout_seconds} seconds.",
                    created_at=created_at,
                )

        except Exception as exc:
            duration = round(time.monotonic() - start_time, 3)
            return SandboxExecutionResult(
                run_id=run_id,
                state=SandboxExecutionState.ERROR,
                exit_code=None,
                stdout="",
                stderr=str(exc),
                duration_seconds=duration,
                command=validated_command,
                image=self.image,
                error_message=f"Sandbox execution failed: {exc}",
                created_at=created_at,
            )

        finally:
            # Assured cleanup: remove container with cancellation shield
            try:
                await asyncio.shield(self._force_remove_container(container_name))
            except Exception:
                pass

            # Reap host subprocess if still alive
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                    await asyncio.shield(process.wait())
                except Exception:
                    pass

            await self.limiter.release(run_id)


def get_sandbox_runner() -> SandboxRunner:
    """Factory creating the appropriate runner according to server environment configuration."""
    provider = os.getenv("SANDBOX_PROVIDER", "mock").lower()
    scratch_root = os.getenv("SANDBOX_SCRATCH_ROOT", DEFAULT_SCRATCH_ROOT)
    if provider == "mock":
        return MockSandboxRunner(scratch_root=scratch_root)
    if provider == "docker":
        image = os.getenv("SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE)
        memory = os.getenv("SANDBOX_MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT)
        cpu = os.getenv("SANDBOX_CPU_LIMIT", DEFAULT_CPU_LIMIT)
        return DockerSandboxRunner(
            image=image,
            memory_limit=memory,
            cpu_limit=cpu,
            scratch_root=scratch_root,
        )
    raise SandboxConfigurationError(
        f"Unsupported SANDBOX_PROVIDER '{provider}'. Use 'mock' or 'docker'."
    )
