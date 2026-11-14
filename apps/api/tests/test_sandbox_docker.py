"""Opt-in Docker integration tests verifying real container runtime behavior.

These tests are strictly opt-in and will NEVER run during standard unit test execution.
To run explicitly:
    RUN_DOCKER_TESTS=1 pytest -m docker apps/api/tests/test_sandbox_docker.py
"""

import os
import shutil
import subprocess

import pytest

from repopilot_api.patcher import apply_patch_proposal
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    DockerSandboxRunner,
    WorkspaceManager,
)
from repopilot_api.schemas import (
    PatchProposal,
    ProposedFix,
    SandboxExecutionRequest,
    SandboxExecutionState,
)
from tests.fixtures.sandbox_fixtures import (
    CALC_FIX_PATCH,
    FAILING_FIXTURE_FILES,
    NETWORK_PROBE_FILES,
    NON_ROOT_PROBE_FILES,
    OUTPUT_FLOOD_PROBE_FILES,
    PASSING_FIXTURE_FILES,
    READ_ONLY_PROBE_FILES,
    TIMEOUT_PROBE_FILES,
    WRITABLE_TMP_PROBE_FILES,
)


def _docker_is_ready() -> tuple[bool, str]:
    if os.getenv("RUN_DOCKER_TESTS") != "1":
        return False, "Docker integration tests are opt-in. Set RUN_DOCKER_TESTS=1 to execute."
    if shutil.which("docker") is None:
        return False, "Docker CLI executable not found on PATH."
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        if res.returncode != 0:
            return False, "Docker daemon is not running or not accessible."
    except Exception as exc:
        return False, f"Docker daemon check failed: {exc}"
    return True, "Docker is ready."


_ready, _reason = _docker_is_ready()

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not _ready, reason=_reason),
]


@pytest.fixture
def docker_test_image() -> str:
    """Return the pinned trusted sandbox image, or an override if configured."""
    return os.getenv("SANDBOX_TEST_IMAGE", DEFAULT_SANDBOX_IMAGE)


@pytest.mark.anyio
async def test_docker_runtime_passing_test(tmp_path, docker_test_image):
    """Verify clean container startup, passing exit code 0, and log capture."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_pass", PASSING_FIXTURE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_calc.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)

        assert result.state == SandboxExecutionState.PASSED
        assert result.exit_code == 0
        assert "OK" in (result.stderr + result.stdout)
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_failing_test(tmp_path, docker_test_image):
    """Verify non-zero exit code 1 capture and failure logs."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_fail", FAILING_FIXTURE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_calc.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)

        assert result.state == SandboxExecutionState.FAILED
        assert result.exit_code == 1
        assert "FAILED" in (result.stderr + result.stdout)
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_end_to_end_patch_verification(tmp_path, docker_test_image):
    """Verify full Proof-of-Fix flow: baseline fail -> atomic patch -> patched pass."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_p2f", FAILING_FIXTURE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_calc.py"],
            timeout_seconds=10,
        )

        # 1. Baseline Run (must fail)
        baseline_res = await runner.run(ws, req)
        assert baseline_res.state == SandboxExecutionState.FAILED
        assert baseline_res.exit_code == 1

        # 2. Host-Side Deterministic Patch Application
        proposal = ProposedFix(
            explanation="Fix addition bug in calc.py",
            patches=[
                PatchProposal(
                    file_path="calc.py",
                    diff=CALC_FIX_PATCH,
                    explanation="Change subtraction to addition",
                )
            ],
        )
        patch_res = apply_patch_proposal(ws, proposal, scratch_root=scratch)
        assert patch_res.success is True
        assert patch_res.changed_files == ["calc.py"]

        # 3. Patched Run (must pass)
        patched_res = await runner.run(ws, req)
        assert patched_res.state == SandboxExecutionState.PASSED
        assert patched_res.exit_code == 0
        assert "OK" in (patched_res.stderr + patched_res.stdout)
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_network_isolation(tmp_path, docker_test_image):
    """Verify --network none blocks outbound socket connections."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_net", NETWORK_PROBE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_network.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)

        assert result.state == SandboxExecutionState.PASSED
        assert result.exit_code == 0
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_non_root_execution(tmp_path, docker_test_image):
    """Verify container executes as unprivileged non-root user (uid >= 1000)."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_user", NON_ROOT_PROBE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_user.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)

        assert result.state == SandboxExecutionState.PASSED
        assert result.exit_code == 0
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_read_only_restrictions(tmp_path, docker_test_image):
    """Verify filesystem writes to /workspace and rootfs fail with Read-only error."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_ro", READ_ONLY_PROBE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_readonly.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)

        assert result.state == SandboxExecutionState.PASSED
        assert result.exit_code == 0
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_writable_tmpfs(tmp_path, docker_test_image):
    """Verify /tmp tmpfs allows writing and reading temporary files."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_tmp", WRITABLE_TMP_PROBE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_tmpfs.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)

        assert result.state == SandboxExecutionState.PASSED
        assert result.exit_code == 0
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_execution_timeout(tmp_path, docker_test_image):
    """Verify timeout triggers force-removal of container and TIMED_OUT state."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_timeout", TIMEOUT_PROBE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_timeout.py"],
            timeout_seconds=2,
        )
        result = await runner.run(ws, req)

        assert result.state == SandboxExecutionState.TIMED_OUT
        assert result.exit_code is None
        assert "timed out" in (result.error_message or "").lower()
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_bounded_output_capture(tmp_path, docker_test_image):
    """Verify high-volume output is safely capped at 50KB limit."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_flood", OUTPUT_FLOOD_PROBE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_flood.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)

        assert len(result.stdout) <= 60 * 1024  # bounded around 50KB limit
        assert "truncated" in result.stdout
    finally:
        manager.cleanup_workspace(ws)


@pytest.mark.anyio
async def test_docker_runtime_container_cleanup(tmp_path, docker_test_image):
    """Verify no lingering containers remain on host after execution."""
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_cleanup", PASSING_FIXTURE_FILES)

    try:
        runner = DockerSandboxRunner(image=docker_test_image, scratch_root=scratch)
        req = SandboxExecutionRequest(
            command=["python", "-m", "unittest", "test_calc.py"],
            timeout_seconds=10,
        )
        result = await runner.run(ws, req)
        assert result.state == SandboxExecutionState.PASSED

        # Check docker ps -a for any container with our prefix
        ps_res = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=repopilot_sbx", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert ps_res.returncode == 0
        assert "repopilot_sbx" not in ps_res.stdout
    finally:
        manager.cleanup_workspace(ws)
