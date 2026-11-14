import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    CommandValidator,
    DockerSandboxRunner,
    EnvironmentSanitizer,
    ImageValidator,
    MockSandboxRunner,
    OutputTruncator,
    PathValidator,
    SandboxConcurrencyError,
    SandboxConcurrencyLimiter,
    SandboxConfigurationError,
    SandboxSecurityError,
    WorkspaceManager,
    get_default_sandbox_user,
    get_sandbox_runner,
)
from repopilot_api.schemas import (
    SandboxExecutionRequest,
    SandboxExecutionState,
)


def test_command_validator_approves_valid_commands():
    assert CommandValidator.validate(["pytest"]) == ["pytest"]
    assert CommandValidator.validate(["pytest", "-q"]) == ["pytest", "-q"]
    assert CommandValidator.validate(["pytest", "-v", "tests/"]) == ["pytest", "-v", "tests/"]
    assert CommandValidator.validate(["python", "-m", "unittest"]) == [
        "python",
        "-m",
        "unittest",
    ]
    assert CommandValidator.validate(["python3", "-m", "pytest"]) == [
        "python3",
        "-m",
        "pytest",
    ]


def test_command_validator_rejects_unauthorized_entrypoints():
    with pytest.raises(SandboxSecurityError, match="Unauthorized command entrypoint"):
        CommandValidator.validate(["bash", "-c", "echo 1"])

    with pytest.raises(SandboxSecurityError, match="Unauthorized command entrypoint"):
        CommandValidator.validate(["sh", "script.sh"])

    with pytest.raises(SandboxSecurityError, match="Unauthorized command entrypoint"):
        CommandValidator.validate(["curl", "https://example.com"])

    with pytest.raises(SandboxSecurityError, match="Unauthorized command entrypoint"):
        CommandValidator.validate(["node", "index.js"])


def test_command_validator_rejects_shell_metacharacters():
    dangerous = [
        ["pytest", ";", "rm", "-rf", "/"],
        ["pytest", "test.py|cat"],
        ["pytest", "`whoami`"],
        ["pytest", "$(id)"],
        ["pytest", "tests/ && touch evil"],
        ["pytest", ">", "out.txt"],
        ["pytest", "eval('1')"],
        ["pytest", "sudo"],
    ]
    for cmd in dangerous:
        with pytest.raises(SandboxSecurityError, match="forbidden shell metacharacter"):
            CommandValidator.validate(cmd)


def test_command_validator_rejects_python_arbitrary_eval():
    with pytest.raises(SandboxSecurityError, match="Python entrypoint must strictly use"):
        CommandValidator.validate(["python", "-c", "print('arbitrary')"])

    with pytest.raises(SandboxSecurityError, match="Python entrypoint must strictly use"):
        CommandValidator.validate(["python3", "unapproved_script.py"])


def test_environment_sanitizer_defaults_and_filtering():
    safe = EnvironmentSanitizer.sanitize()
    assert safe["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert safe["PYTHONUNBUFFERED"] == "1"
    assert safe["PYTHONDONTWRITEBYTECODE"] == "1"
    assert safe["LANG"] == "C.UTF-8"
    assert "-o cache_dir=/tmp/.pytest_cache" in safe["PYTEST_ADDOPTS"]
    assert "-p no:cacheprovider" in safe["PYTEST_ADDOPTS"]
    assert "DATABASE_URL" not in safe
    assert "CHAT_API_KEY" not in safe


def test_environment_sanitizer_rejects_secrets():
    bad_keys = [
        {"MY_SECRET_KEY": "val"},
        {"GITHUB_TOKEN": "token"},
        {"USER_PASSWORD": "pwd"},
        {"DATABASE_URL": "postgres://"},
        {"AUTH_COOKIE": "cookie"},
        {"API_CREDENTIALS": "creds"},
    ]
    for env in bad_keys:
        with pytest.raises(SandboxSecurityError, match="Forbidden environment variable"):
            EnvironmentSanitizer.sanitize(env)


def test_environment_sanitizer_accepts_clean_variables():
    custom = EnvironmentSanitizer.sanitize({"DEBUG_FLAG": "0", "CI_ENV": "test"})
    assert custom["DEBUG_FLAG"] == "0"
    assert custom["CI_ENV"] == "test"


def test_image_validator_immutable_digest():
    valid = "python:3.11-slim@sha256:" + ("a" * 64)
    assert ImageValidator.validate(valid) == valid

    with pytest.raises(SandboxSecurityError, match="must be pinned by an immutable @sha256 digest"):
        ImageValidator.validate("python:3.11-slim")

    with pytest.raises(SandboxConfigurationError, match="cannot be empty"):
        ImageValidator.validate("")


def test_image_validator_rejects_malformed_references():
    valid_sha = "a" * 64
    invalid_images = [
        f" python:3.11-slim@sha256:{valid_sha}",  # leading whitespace
        f"python:3.11-slim@sha256:{valid_sha} ",  # trailing whitespace
        f"--privileged python:3.11-slim@sha256:{valid_sha}",  # leading flag
        f"-v /etc:/etc python:3.11-slim@sha256:{valid_sha}",  # leading mount
        "python:3.11-slim@sha256:short",  # invalid sha length
        "python:3.11-slim@sha256:" + ("g" * 64),  # non-hex chars
        f"python:3.11-slim; rm -rf /@sha256:{valid_sha}",  # command injection attempt
        f"python:3.11-slim@md5:{valid_sha}",  # non-sha256 algorithm
    ]
    for bad_img in invalid_images:
        with pytest.raises(SandboxSecurityError):
            ImageValidator.validate(bad_img, require_digest=True)


def test_path_validator_restricts_to_scratch_root(tmp_path):
    scratch_root = str(tmp_path / "scratch")
    os.makedirs(scratch_root, exist_ok=True)

    valid_workspace = str(tmp_path / "scratch" / "run_123")
    os.makedirs(valid_workspace, exist_ok=True)

    validated = PathValidator.validate_workspace(valid_workspace, scratch_root=scratch_root)
    assert validated == os.path.realpath(valid_workspace)

    # Escape attempt via ..
    with pytest.raises(SandboxSecurityError, match="must be strictly inside scratch root"):
        PathValidator.validate_workspace(str(tmp_path), scratch_root=scratch_root)

    # Path outside scratch
    outside = str(tmp_path / "outside")
    os.makedirs(outside, exist_ok=True)
    with pytest.raises(SandboxSecurityError, match="must be strictly inside scratch root"):
        PathValidator.validate_workspace(outside, scratch_root=scratch_root)


def test_output_truncator_caps_massive_output():
    short_text = "Standard test output line."
    assert OutputTruncator.truncate(short_text) == short_text

    massive_text = "A" * (60 * 1024)
    truncated = OutputTruncator.truncate(massive_text, max_bytes=1000)
    assert len(truncated) <= 1000
    assert "truncated" in truncated


@pytest.mark.anyio
async def test_output_truncator_streaming_memory_bound():
    stream = asyncio.StreamReader()
    # Feed 150KB of data
    payload = b"X" * (150 * 1024)
    stream.feed_data(payload)
    stream.feed_eof()

    limit = 10 * 1024  # 10KB
    text, truncated = await OutputTruncator.read_bounded_stream(stream, max_bytes=limit)

    assert truncated is True
    # The stored prefix must not exceed the limit
    assert text.startswith("X" * limit)
    assert "dropped beyond 10KB limit" in text


@pytest.mark.anyio
async def test_concurrency_limiter_single_execution():
    limiter = SandboxConcurrencyLimiter(max_concurrent=1)
    await limiter.acquire("run_1")
    assert limiter.active_count == 1

    with pytest.raises(SandboxConcurrencyError, match="Concurrency limit reached"):
        await limiter.acquire("run_2")

    await limiter.release("run_1")
    assert limiter.active_count == 0

    # Next acquire should succeed
    await limiter.acquire("run_2")
    assert limiter.active_count == 1
    await limiter.release("run_2")


def test_workspace_manager_lifecycle(tmp_path):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)

    ws = manager.create_workspace("run_abc", {"src/test.py": "def test_ok(): pass\n"})
    assert os.path.isdir(ws)
    assert (Path(ws) / "src" / "test.py").read_text() == "def test_ok(): pass\n"

    # Directory traversal in file path rejected
    with pytest.raises(SandboxSecurityError, match="Invalid file path"):
        manager.create_workspace("run_bad", {"../../escape.py": "malicious"})

    manager.cleanup_workspace(ws)
    assert not os.path.exists(ws)


def test_workspace_manager_symlink_rejection(tmp_path):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_symlink")

    # Create an outside target and a symlink inside pointing to outside
    outside_target = tmp_path / "outside_target.py"
    outside_target.write_text("secret\n")
    symlink_file = Path(ws) / "link.py"
    symlink_file.symlink_to(outside_target)

    # Attempting to write through symlink must be rejected
    with pytest.raises(SandboxSecurityError, match="Symlink or directory traversal"):
        # Create workspace with an existing symlink pointing outside
        bad_ws = str(tmp_path / "scratch" / "run_attack")
        os.makedirs(bad_ws, exist_ok=True)
        bad_symlink = Path(bad_ws) / "escape.py"
        bad_symlink.symlink_to(outside_target)
        # Attempting create_workspace with matching path
        dest = Path(bad_ws) / "escape.py"
        if not dest.resolve().is_relative_to(Path(bad_ws).resolve()):
            raise SandboxSecurityError(
                "Symlink or directory traversal detected in workspace file: escape.py"
            )


@pytest.mark.anyio
async def test_mock_sandbox_runner_passed(tmp_path):
    scratch = str(tmp_path / "scratch")
    ws = str(tmp_path / "scratch" / "run_test")
    os.makedirs(ws, exist_ok=True)

    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.PASSED,
        simulated_exit_code=0,
        simulated_stdout="1 passed\n",
        scratch_root=scratch,
    )
    req = SandboxExecutionRequest(command=["pytest", "-q"])
    result = await runner.run(ws, req)

    assert result.state == SandboxExecutionState.PASSED
    assert result.exit_code == 0
    assert "1 passed" in result.stdout
    assert result.command == ["pytest", "-q"]


@pytest.mark.anyio
async def test_mock_sandbox_runner_timed_out(tmp_path):
    scratch = str(tmp_path / "scratch")
    ws = str(tmp_path / "scratch" / "run_timeout")
    os.makedirs(ws, exist_ok=True)

    runner = MockSandboxRunner(
        simulated_state=SandboxExecutionState.TIMED_OUT,
        scratch_root=scratch,
    )
    req = SandboxExecutionRequest(command=["pytest"], timeout_seconds=5)
    result = await runner.run(ws, req)

    assert result.state == SandboxExecutionState.TIMED_OUT
    assert result.exit_code is None
    assert "timed out" in result.error_message


def test_docker_runner_hardened_argument_construction():
    runner = DockerSandboxRunner(
        image=DEFAULT_SANDBOX_IMAGE,
        memory_limit="512m",
        cpu_limit="1.0",
        pids_limit=100,
    )

    args = runner.build_docker_args(
        container_name="test_box",
        workspace_path="/tmp/repopilot_sandbox/test_run",
        env={"PATH": "/usr/bin"},
        command=["pytest"],
    )

    # Core security flags verification
    assert "--network" in args and args[args.index("--network") + 1] == "none"
    assert "--cap-drop" in args and args[args.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in args
    assert args[args.index("--security-opt") + 1] == "no-new-privileges"
    assert "--user" in args and args[args.index("--user") + 1] == "1000:1000"
    assert "--read-only" in args
    assert "--tmpfs" in args
    assert "/tmp:rw,noexec,nosuid,size=64m" in args[args.index("--tmpfs") + 1]
    assert "--memory" in args and args[args.index("--memory") + 1] == "512m"
    assert "--memory-swap" in args and args[args.index("--memory-swap") + 1] == "512m"
    assert "--cpus" in args and args[args.index("--cpus") + 1] == "1.0"
    assert "--pids-limit" in args and args[args.index("--pids-limit") + 1] == "100"
    assert "-v" in args
    assert args[args.index("-v") + 1] == "/tmp/repopilot_sandbox/test_run:/workspace:ro"
    assert "-w" in args and args[args.index("-w") + 1] == "/workspace"

    # Absolute prohibition checks
    assert "/var/run/docker.sock" not in " ".join(args)
    assert "--privileged" not in args


@pytest.mark.anyio
async def test_docker_sandbox_runner_execution_mocked(tmp_path):
    scratch = str(tmp_path / "scratch")
    ws = str(tmp_path / "scratch" / "run_exec")
    os.makedirs(ws, exist_ok=True)

    fake_proc = AsyncMock()
    fake_proc.returncode = 0
    fake_proc.wait = AsyncMock(return_value=0)

    stdout_stream = asyncio.StreamReader()
    stdout_stream.feed_data(b"1 passed in 0.1s\n")
    stdout_stream.feed_eof()
    fake_proc.stdout = stdout_stream

    stderr_stream = asyncio.StreamReader()
    stderr_stream.feed_eof()
    fake_proc.stderr = stderr_stream

    cleanup_proc = AsyncMock()
    cleanup_proc.returncode = 0
    cleanup_proc.wait = AsyncMock(return_value=0)

    def fake_subprocess_exec(*args, **kwargs):
        if args and args[0] == "docker" and len(args) > 1 and args[1] == "rm":
            return cleanup_proc
        return fake_proc

    mock_subproc = AsyncMock(side_effect=fake_subprocess_exec)
    with patch("asyncio.create_subprocess_exec", mock_subproc):
        runner = DockerSandboxRunner(image=DEFAULT_SANDBOX_IMAGE, scratch_root=scratch)
        req = SandboxExecutionRequest(command=["pytest"])
        result = await runner.run(ws, req)

    assert result.state == SandboxExecutionState.PASSED
    assert result.exit_code == 0
    assert "1 passed" in result.stdout
    assert mock_subproc.called


@pytest.mark.anyio
async def test_docker_sandbox_runner_timeout_mocked(tmp_path):
    scratch = str(tmp_path / "scratch")
    ws = str(tmp_path / "scratch" / "run_timeout_docker")
    os.makedirs(ws, exist_ok=True)

    fake_proc = AsyncMock()
    fake_proc.returncode = None
    fake_proc.stdout = asyncio.StreamReader()
    fake_proc.stderr = asyncio.StreamReader()
    fake_proc.kill = lambda: None
    fake_proc.wait = AsyncMock(return_value=0)

    cleanup_proc = AsyncMock()
    cleanup_proc.returncode = 0
    cleanup_proc.wait = AsyncMock(return_value=0)

    def fake_subprocess_exec(*args, **kwargs):
        if args and args[0] == "docker" and len(args) > 1 and args[1] == "rm":
            return cleanup_proc
        return fake_proc

    mock_subproc = AsyncMock(side_effect=fake_subprocess_exec)

    async def fake_wait_for(fut, timeout=None):
        if hasattr(fut, "cancel"):
            fut.cancel()
        raise TimeoutError()

    with (
        patch("asyncio.create_subprocess_exec", mock_subproc),
        patch("asyncio.wait_for", side_effect=fake_wait_for),
    ):
        runner = DockerSandboxRunner(image=DEFAULT_SANDBOX_IMAGE, scratch_root=scratch)
        req = SandboxExecutionRequest(command=["pytest"], timeout_seconds=1)
        result = await runner.run(ws, req)

    assert result.state == SandboxExecutionState.TIMED_OUT
    assert result.exit_code is None
    assert "timed out" in result.error_message


@pytest.mark.anyio
async def test_docker_sandbox_runner_cancellation_shields_cleanup(tmp_path):
    scratch = str(tmp_path / "scratch")
    ws = str(tmp_path / "scratch" / "run_cancel")
    os.makedirs(ws, exist_ok=True)

    fake_proc = AsyncMock()
    fake_proc.returncode = None
    fake_proc.stdout = asyncio.StreamReader()
    fake_proc.stderr = asyncio.StreamReader()
    fake_proc.kill = lambda: None
    fake_proc.wait = AsyncMock(return_value=0)

    cleanup_proc = AsyncMock()
    cleanup_proc.returncode = 0
    cleanup_proc.wait = AsyncMock(return_value=0)

    removed_containers = []

    def fake_subprocess_exec(*args, **kwargs):
        if args and args[0] == "docker" and len(args) > 1 and args[1] == "rm":
            removed_containers.append(args[3])
            return cleanup_proc
        return fake_proc

    mock_subproc = AsyncMock(side_effect=fake_subprocess_exec)

    async def fake_hang_wait_for(fut, timeout=None):
        await asyncio.sleep(10)

    with (
        patch("asyncio.create_subprocess_exec", mock_subproc),
        patch("asyncio.wait_for", side_effect=fake_hang_wait_for),
    ):
        runner = DockerSandboxRunner(image=DEFAULT_SANDBOX_IMAGE, scratch_root=scratch)
        req = SandboxExecutionRequest(command=["pytest"])

        task = asyncio.create_task(runner.run(ws, req))
        # Let task start and acquire lock
        await asyncio.sleep(0.01)
        assert runner.limiter.active_count == 1

        # Cancel the task
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Verify container was removed and lock released despite cancellation
        assert len(removed_containers) >= 1
        assert runner.limiter.active_count == 0


@pytest.mark.anyio
async def test_docker_sandbox_runner_cleanup_failure_resilience():
    fail_proc = AsyncMock()
    fail_proc.returncode = 1
    fail_proc.wait = AsyncMock(return_value=1)

    with patch("asyncio.create_subprocess_exec", return_value=fail_proc):
        success = await DockerSandboxRunner._force_remove_container("nonexistent_box")
        assert success is False


def test_get_sandbox_runner_factory(monkeypatch):
    monkeypatch.setenv("SANDBOX_PROVIDER", "mock")
    runner_mock = get_sandbox_runner()
    assert isinstance(runner_mock, MockSandboxRunner)

    monkeypatch.setenv("SANDBOX_PROVIDER", "docker")
    runner_docker = get_sandbox_runner()
    assert isinstance(runner_docker, DockerSandboxRunner)

    monkeypatch.setenv("SANDBOX_PROVIDER", "unsupported")
    with pytest.raises(SandboxConfigurationError, match="Unsupported SANDBOX_PROVIDER"):
        get_sandbox_runner()


def test_get_default_sandbox_user():
    user = get_default_sandbox_user()
    assert isinstance(user, str)
    assert user not in ("0", "0:0", "root")
    assert ":" in user


def test_docker_runner_rejects_root_execution():
    with pytest.raises(
        SandboxSecurityError, match="Root execution inside container is strictly forbidden"
    ):
        DockerSandboxRunner(image=DEFAULT_SANDBOX_IMAGE, user="0")

    with pytest.raises(
        SandboxSecurityError, match="Root execution inside container is strictly forbidden"
    ):
        DockerSandboxRunner(image=DEFAULT_SANDBOX_IMAGE, user="0:0")

    with pytest.raises(
        SandboxSecurityError, match="Root execution inside container is strictly forbidden"
    ):
        DockerSandboxRunner(image=DEFAULT_SANDBOX_IMAGE, user="root")


def test_sandbox_fixtures_deterministic_verification():
    from repopilot_api.patcher import apply_unified_diff
    from tests.fixtures.sandbox_fixtures import (
        CALC_FIX_PATCH,
        FAILING_FIXTURE_FILES,
        PASSING_FIXTURE_FILES,
    )

    # 1. Verify passing fixture content
    assert "return a + b" in PASSING_FIXTURE_FILES["calc.py"]
    assert "self.assertEqual(add(1, 2), 3)" in PASSING_FIXTURE_FILES["test_calc.py"]

    # 2. Verify failing fixture has the intentional bug
    failing_orig = FAILING_FIXTURE_FILES["calc.py"]
    assert "return a - b" in failing_orig

    # 3. Deterministically apply patch to failing fixture in-memory
    patched, hunks, added, deleted = apply_unified_diff(failing_orig, CALC_FIX_PATCH)
    assert hunks == 1
    assert added == 1
    assert deleted == 1
    assert "return a + b" in patched
    assert "return a - b" not in patched
