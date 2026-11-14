"""Trusted Fixture Registry and Execution Policy Guard for isolated sandbox verifications.

Enforces:
1. Explicit allowlist of local test fixtures with immutable identifiers.
2. Cryptographic snapshot content verification (SHA-256) preventing modified fixtures.
3. Repository trust allowlist rejecting untrusted or arbitrary repositories.
4. User authorization and approved specification re-verification.
5. Strict fail-closed policy enforcement on any missing or inconsistent metadata.
"""

from dataclasses import dataclass, field
import hashlib
import json
import logging
from typing import ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from repopilot_api.models import (
    IndexedFile,
    IndexingJob,
    Repository,
    RepositoryAccess,
    VerificationRun,
)
from repopilot_api.sandbox import (
    DEFAULT_SANDBOX_IMAGE,
    CommandValidator,
    SandboxSecurityError,
)
from repopilot_api.schemas import (
    VerificationJobStatus,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# Security Exceptions
# ==============================================================================


class FixtureRegistryError(SandboxSecurityError):
    """Base exception for fixture registry and execution policy violations."""


class UntrustedRepositoryError(FixtureRegistryError):
    """Raised when an untrusted repository attempts verification execution."""


class UntrustedFixtureError(FixtureRegistryError):
    """Raised when code under verification does not match any approved trusted fixture."""


class ModifiedFixtureError(FixtureRegistryError):
    """Raised when trusted fixture contents or snapshot hash have been modified."""


class UnauthorizedUserError(FixtureRegistryError):
    """Raised when an unauthorized user attempts verification execution."""


class PolicyMetadataError(FixtureRegistryError):
    """Raised when required execution metadata is missing or inconsistent."""


# ==============================================================================
# Snapshot Hash Computation
# ==============================================================================


def compute_snapshot_hash(files: dict[str, str]) -> str:
    """Compute deterministic cryptographic SHA-256 hash of a file snapshot.

    Sorts file paths lexicographically and computes SHA-256 over:
    f"{clean_path}:{file_sha256}\n"
    """
    if not files:
        return hashlib.sha256(b"").hexdigest()

    hasher = hashlib.sha256()
    for path in sorted(files.keys()):
        clean_path = path.replace("\\", "/").strip()
        content = files[path]
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        hasher.update(f"{clean_path}:{file_hash}\n".encode("utf-8"))
    return hasher.hexdigest()


# ==============================================================================
# Trusted Fixture Model
# ==============================================================================


@dataclass(frozen=True)
class TrustedFixture:
    """Immutable metadata and snapshot identity for an approved test fixture."""

    fixture_id: str
    name: str
    description: str
    repository_owner: str
    repository_name: str
    commit_sha: str
    files: dict[str, str]
    content_hash: str
    approved_test_commands: list[list[str]]
    sandbox_image: str = DEFAULT_SANDBOX_IMAGE
    allowed_commit_shas: set[str] = field(default_factory=set)

    @classmethod
    def create(
        cls,
        fixture_id: str,
        name: str,
        description: str,
        repository_owner: str,
        repository_name: str,
        commit_sha: str,
        files: dict[str, str],
        approved_test_commands: list[list[str]],
        sandbox_image: str = DEFAULT_SANDBOX_IMAGE,
        allowed_commit_shas: set[str] | None = None,
    ) -> "TrustedFixture":
        if not fixture_id or not isinstance(fixture_id, str):
            raise ValueError("fixture_id must be a non-empty string.")
        content_hash = compute_snapshot_hash(files)

        # Validate all approved commands upfront with existing CommandValidator
        for cmd in approved_test_commands:
            CommandValidator.validate(cmd)

        all_shas = {commit_sha.lower().strip()}
        if allowed_commit_shas:
            all_shas.update(s.lower().strip() for s in allowed_commit_shas)

        return cls(
            fixture_id=fixture_id,
            name=name,
            description=description,
            repository_owner=repository_owner,
            repository_name=repository_name,
            commit_sha=commit_sha.lower().strip(),
            files=dict(files),
            content_hash=content_hash,
            approved_test_commands=[list(c) for c in approved_test_commands],
            sandbox_image=sandbox_image,
            allowed_commit_shas=all_shas,
        )

    def matches_repo(self, owner: str, name: str) -> bool:
        if self.repository_owner != "*" and self.repository_owner.lower() != owner.lower():
            return False
        if self.repository_name != "*" and self.repository_name.lower() != name.lower():
            return False
        return True

    def matches_commit(self, commit_sha: str) -> bool:
        sha = commit_sha.lower().strip()
        if self.commit_sha == sha:
            return True
        return sha in self.allowed_commit_shas


# ==============================================================================
# Trusted Fixture Registry
# ==============================================================================


class TrustedFixtureRegistry:
    """In-memory immutable registry of approved test fixtures and trusted repositories."""

    def __init__(self):
        self._fixtures: dict[str, TrustedFixture] = {}
        self._trusted_repositories: set[tuple[str, str]] = set()
        self._init_defaults()

    def register_trusted_repository(self, owner: str, name: str) -> None:
        self._trusted_repositories.add((owner.lower().strip(), name.lower().strip()))

    def is_repository_trusted(self, owner: str, name: str) -> bool:
        return (owner.lower().strip(), name.lower().strip()) in self._trusted_repositories

    def register_fixture(self, fixture: TrustedFixture) -> None:
        if fixture.fixture_id in self._fixtures:
            logger.warning("Overwriting fixture '%s' in registry.", fixture.fixture_id)
        self._fixtures[fixture.fixture_id] = fixture

    def get_fixture(self, fixture_id: str) -> TrustedFixture | None:
        return self._fixtures.get(fixture_id)

    def list_fixtures(self) -> list[TrustedFixture]:
        return list(self._fixtures.values())

    def find_fixture(
        self,
        repository_owner: str,
        repository_name: str,
        commit_sha: str,
        files: dict[str, str],
        fixture_id: str | None = None,
    ) -> TrustedFixture:
        """Locate and validate a fixture by identity or snapshot content."""
        actual_hash = compute_snapshot_hash(files)

        # 1. Explicit fixture_id requested
        if fixture_id:
            fixture = self._fixtures.get(fixture_id)
            if not fixture:
                raise UntrustedFixtureError(
                    f"Fixture ID '{fixture_id}' is not in the trusted fixture registry."
                )
            if not fixture.matches_repo(repository_owner, repository_name):
                raise UntrustedFixtureError(
                    f"Fixture '{fixture_id}' is not approved for repository "
                    f"'{repository_owner}/{repository_name}'."
                )
            if not fixture.matches_commit(commit_sha):
                raise UntrustedFixtureError(
                    f"Fixture '{fixture_id}' is not approved for commit SHA '{commit_sha}'."
                )
            if actual_hash != fixture.content_hash:
                raise ModifiedFixtureError(
                    f"Trusted fixture '{fixture_id}' snapshot integrity check failed. "
                    f"Expected hash {fixture.content_hash}, got {actual_hash}. "
                    "Repository files have been modified."
                )
            return fixture

        # 2. Search for candidates matching repo and commit
        matched_candidate: TrustedFixture | None = None
        for candidate in self._fixtures.values():
            if candidate.matches_repo(repository_owner, repository_name) and candidate.matches_commit(commit_sha):
                if actual_hash == candidate.content_hash:
                    return candidate
                # Only consider it a tampered candidate if file paths match the fixture
                if set(files.keys()) == set(candidate.files.keys()):
                    matched_candidate = candidate

        # If file paths matched but content hash differed, it is tampered!
        if matched_candidate is not None:
            raise ModifiedFixtureError(
                f"Trusted fixture '{matched_candidate.fixture_id}' matched repository and commit, "
                f"but snapshot content hash mismatch (expected {matched_candidate.content_hash}, got {actual_hash}). "
                "Fixture snapshot has been altered."
            )

        # 3. Check if actual_hash matches any other fixture in registry
        for other in self._fixtures.values():
            if actual_hash == other.content_hash:
                raise UntrustedFixtureError(
                    f"Snapshot content matches fixture '{other.fixture_id}', but commit SHA '{commit_sha}' "
                    f"is not approved for this fixture."
                )

        raise UntrustedFixtureError(
            f"No approved trusted fixture found matching repository '{repository_owner}/{repository_name}' "
            f"at commit '{commit_sha}'. Arbitrary repositories cannot be executed in sandbox."
        )

    def _init_defaults(self):
        # Default Trusted Repositories
        self.register_trusted_repository("acme", "app")
        self.register_trusted_repository("local", "sandbox-fixture")
        self.register_trusted_repository("trusted", "benchmark")
        self.register_trusted_repository("repopilot", "sandbox-fixtures")

        sample_sha = "a" * 40
        common_shas = {sample_sha, "0" * 40, "1" * 40, "f" * 40}

        # 1. Package-style Calc Fixture (used in test_verification_api.py)
        calc_pkg_files = {
            "src/calc.py": "def add(a, b):\n    return a - b\n",
            "tests/test_calc.py": (
                "import unittest\n"
                "from src.calc import add\n"
                "class TestCalc(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n"
            ),
        }
        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_calc_pkg",
                name="Calculator Addition Package Fixture",
                description="Package-structured 2-file calculator with subtraction bug in src/calc.py",
                repository_owner="acme",
                repository_name="app",
                commit_sha=sample_sha,
                files=calc_pkg_files,
                approved_test_commands=[
                    ["python", "-m", "unittest", "tests/test_calc.py"],
                    ["python", "-m", "unittest", "discover", "tests"],
                    ["pytest", "tests/test_calc.py"],
                    ["pytest"],
                ],
                sandbox_image=DEFAULT_SANDBOX_IMAGE,
                allowed_commit_shas=common_shas,
            )
        )

        # 2. Root-style Calc Failing Fixture (from sandbox_fixtures.py)
        calc_root_failing_files = {
            "calc.py": (
                "def add(a: int, b: int) -> int:\n"
                '    """Add two numbers."""\n'
                "    return a - b  # Bug: returns subtraction instead of addition\n"
            ),
            "test_calc.py": (
                "import unittest\n"
                "from calc import add\n\n\n"
                "class TestCalc(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(1, 2), 3)\n"
                "        self.assertEqual(add(-1, 1), 0)\n\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        }
        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_calc_root_failing",
                name="Calculator Root Failing Fixture",
                description="Root-level calculator fixture with subtraction bug in calc.py",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files=calc_root_failing_files,
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_calc.py"],
                    ["pytest", "test_calc.py"],
                ],
                sandbox_image=DEFAULT_SANDBOX_IMAGE,
                allowed_commit_shas=common_shas,
            )
        )

        # 3. Root-style Calc Passing Fixture (from sandbox_fixtures.py)
        calc_root_passing_files = {
            "calc.py": ('def add(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b\n'),
            "test_calc.py": (
                "import unittest\n"
                "from calc import add\n\n\n"
                "class TestCalc(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(1, 2), 3)\n"
                "        self.assertEqual(add(-1, 1), 0)\n\n\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n"
            ),
        }
        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_calc_root_passing",
                name="Calculator Root Passing Fixture",
                description="Root-level calculator fixture with working add function",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files=calc_root_passing_files,
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_calc.py"],
                    ["pytest", "test_calc.py"],
                ],
                sandbox_image=DEFAULT_SANDBOX_IMAGE,
                allowed_commit_shas=common_shas,
            )
        )

        # 4. Probe Fixtures
        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_network_probe",
                name="Network Isolation Probe",
                description="Verifies outbound network access is blocked",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files={
                    "test_network.py": (
                        "import socket\n"
                        "import unittest\n\n\n"
                        "class TestNetworkIsolation(unittest.TestCase):\n"
                        "    def test_outbound_network_blocked(self):\n"
                        "        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                        "        sock.settimeout(1.0)\n"
                        "        with self.assertRaises(OSError):\n"
                        "            sock.connect(('8.8.8.8', 53))\n"
                        "        sock.close()\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    )
                },
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_network.py"],
                    ["pytest", "test_network.py"],
                ],
                allowed_commit_shas=common_shas,
            )
        )

        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_non_root_probe",
                name="Non-Root User Probe",
                description="Verifies container executes as non-root uid >= 1000",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files={
                    "test_user.py": (
                        "import os\n"
                        "import unittest\n\n\n"
                        "class TestNonRootUser(unittest.TestCase):\n"
                        "    def test_user_is_not_root(self):\n"
                        "        uid = os.getuid()\n"
                        "        self.assertNotEqual(uid, 0, 'Security violation: running as root!')\n"
                        "        self.assertGreaterEqual(uid, 1000, f'Expected non-root uid, got {uid}')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    )
                },
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_user.py"],
                    ["pytest", "test_user.py"],
                ],
                allowed_commit_shas=common_shas,
            )
        )

        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_readonly_probe",
                name="Read-Only Filesystem Probe",
                description="Verifies container filesystem and workspace are read-only",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files={
                    "test_readonly.py": (
                        "import unittest\n\n\n"
                        "class TestReadOnlyFilesystem(unittest.TestCase):\n"
                        "    def test_cannot_write_to_workspace(self):\n"
                        "        with self.assertRaises(OSError):\n"
                        "            with open('/workspace/unauthorized.txt', 'w') as f:\n"
                        "                f.write('malicious write')\n\n"
                        "    def test_cannot_write_to_rootfs(self):\n"
                        "        with self.assertRaises(OSError):\n"
                        "            with open('/etc/unauthorized.txt', 'w') as f:\n"
                        "                f.write('malicious write')\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    )
                },
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_readonly.py"],
                    ["pytest", "test_readonly.py"],
                ],
                allowed_commit_shas=common_shas,
            )
        )

        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_tmpfs_probe",
                name="Tmpfs Scratch Probe",
                description="Verifies /tmp tmpfs allows writing and reading temporary files",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files={
                    "test_tmpfs.py": (
                        "import os\n"
                        "import unittest\n\n\n"
                        "class TestTmpfsBehavior(unittest.TestCase):\n"
                        "    def test_can_write_to_tmp(self):\n"
                        "        path = '/tmp/sandbox_scratch.txt'\n"
                        "        with open(path, 'w') as f:\n"
                        "            f.write('ephemeral data')\n"
                        "        self.assertTrue(os.path.exists(path))\n"
                        "        with open(path, 'r') as f:\n"
                        "            self.assertEqual(f.read(), 'ephemeral data')\n"
                        "        os.remove(path)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    )
                },
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_tmpfs.py"],
                    ["pytest", "test_tmpfs.py"],
                ],
                allowed_commit_shas=common_shas,
            )
        )

        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_timeout_probe",
                name="Timeout Enforcement Probe",
                description="Verifies sleep hangs are terminated at timeout limit",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files={
                    "test_timeout.py": (
                        "import time\n"
                        "import unittest\n\n\n"
                        "class TestExecutionTimeout(unittest.TestCase):\n"
                        "    def test_sleep_hang(self):\n"
                        "        time.sleep(30)\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    )
                },
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_timeout.py"],
                    ["pytest", "test_timeout.py"],
                ],
                allowed_commit_shas=common_shas,
            )
        )

        self.register_fixture(
            TrustedFixture.create(
                fixture_id="fixture_output_flood_probe",
                name="Output Flood Bounded Stream Probe",
                description="Verifies output flood is safely truncated at log limit",
                repository_owner="*",
                repository_name="*",
                commit_sha=sample_sha,
                files={
                    "test_flood.py": (
                        "import sys\n"
                        "import unittest\n\n\n"
                        "class TestOutputFlood(unittest.TestCase):\n"
                        "    def test_flood(self):\n"
                        "        for _ in range(2000):\n"
                        "            sys.stdout.write('Flood output line testing bounded stream capture.\\n')\n"
                        "        sys.stdout.flush()\n\n\n"
                        "if __name__ == '__main__':\n"
                        "    unittest.main()\n"
                    )
                },
                approved_test_commands=[
                    ["python", "-m", "unittest", "test_flood.py"],
                    ["pytest", "test_flood.py"],
                ],
                allowed_commit_shas=common_shas,
            )
        )


_GLOBAL_FIXTURE_REGISTRY: TrustedFixtureRegistry | None = None


def get_default_fixture_registry() -> TrustedFixtureRegistry:
    global _GLOBAL_FIXTURE_REGISTRY
    if _GLOBAL_FIXTURE_REGISTRY is None:
        _GLOBAL_FIXTURE_REGISTRY = TrustedFixtureRegistry()
    return _GLOBAL_FIXTURE_REGISTRY


def reset_default_fixture_registry() -> TrustedFixtureRegistry:
    global _GLOBAL_FIXTURE_REGISTRY
    _GLOBAL_FIXTURE_REGISTRY = TrustedFixtureRegistry()
    return _GLOBAL_FIXTURE_REGISTRY


# ==============================================================================
# Execution Policy Guard
# ==============================================================================


class ExecutionPolicyGuard:
    """Enforces fail-closed execution policies for sandbox verification jobs."""

    @classmethod
    def validate_user_authorization(
        cls,
        db: Session,
        repository_id: int,
        user_id: int | None,
        required_roles: tuple[str, ...] = ("owner", "collaborator"),
    ) -> RepositoryAccess:
        """Verify user is authenticated and possesses authorized role for repository."""
        if user_id is None:
            raise UnauthorizedUserError("Verification execution requires an authenticated user.")
        access = db.get(RepositoryAccess, (repository_id, user_id))
        if access is None:
            raise UnauthorizedUserError(
                f"User {user_id} has no authorization record for repository {repository_id}."
            )
        if access.role not in required_roles:
            raise UnauthorizedUserError(
                f"User {user_id} with role '{access.role}' cannot execute verification. "
                f"Required role: {required_roles}."
            )
        return access

    @classmethod
    def validate_repository_trust(
        cls,
        repository: Repository | None,
        registry: TrustedFixtureRegistry,
    ) -> None:
        """Verify repository belongs to server-controlled trusted repository allowlist."""
        if repository is None:
            raise PolicyMetadataError("Repository metadata is missing.")
        if not registry.is_repository_trusted(repository.owner, repository.name):
            raise UntrustedRepositoryError(
                f"Repository '{repository.owner}/{repository.name}' is not in the trusted repository allowlist. "
                "Execution of arbitrary untrusted repositories in Docker sandbox is strictly rejected."
            )

    @classmethod
    def validate_approved_specification(
        cls,
        run: VerificationRun,
    ) -> None:
        """Revalidate that the verification specification is approved and untampered."""
        from repopilot_api.verification_service import (
            VerificationSecurityError,
            VerificationSpecificationMismatchError,
            VerificationStateError,
            compute_spec_hash,
        )

        if run.status not in (
            VerificationJobStatus.APPROVED.value,
            VerificationJobStatus.QUEUED.value,
        ):
            raise VerificationStateError(
                f"Cannot execute verification in state '{run.status}'. "
                f"Must be '{VerificationJobStatus.APPROVED.value}' or '{VerificationJobStatus.QUEUED.value}'."
            )
        if not run.approved_spec_hash:
            raise VerificationSecurityError("No execution without human approval.")
        if not run.approved_by_user_id:
            raise VerificationSecurityError("Missing approved_by_user_id on approved verification.")

        try:
            test_command = json.loads(run.test_command)
        except Exception as exc:
            raise PolicyMetadataError(f"Corrupted test_command JSON: {exc}") from exc

        current_spec = compute_spec_hash(
            run.commit_sha, run.patch_hash or "", test_command, run.sandbox_image
        )
        if current_spec != run.approved_spec_hash:
            raise VerificationSpecificationMismatchError(
                "Verification specification altered after approval. Execution rejected."
            )

    @classmethod
    def validate_patch_integrity(
        cls,
        run: VerificationRun,
    ) -> None:
        """Verify patch hash matches SHA-256 of patch diff."""
        if not run.patch_hash or len(run.patch_hash) != 64:
            raise PolicyMetadataError("Verification run has missing or invalid patch_hash.")
        if not run.patch_diff or not run.patch_diff.strip():
            raise PolicyMetadataError("Verification run has empty patch diff.")

        expected_patch_hash = hashlib.sha256(run.patch_diff.strip().encode("utf-8")).hexdigest()
        if run.patch_hash != expected_patch_hash:
            raise FixtureRegistryError(
                "Patch hash mismatch: recorded patch_hash does not match patch_diff digest."
            )

    @classmethod
    def validate_fixture_and_snapshot(
        cls,
        repository: Repository,
        run: VerificationRun,
        files: dict[str, str],
        registry: TrustedFixtureRegistry,
    ) -> TrustedFixture:
        """Verify repository snapshot files match an approved trusted fixture exactly."""
        if not files:
            raise PolicyMetadataError("Repository snapshot has no files to verify.")

        # Find matching fixture; raises ModifiedFixtureError or UntrustedFixtureError if mismatch
        fixture = registry.find_fixture(
            repository_owner=repository.owner,
            repository_name=repository.name,
            commit_sha=run.commit_sha,
            files=files,
        )

        # Validate test command against fixture approved commands
        try:
            test_cmd = json.loads(run.test_command)
        except Exception as exc:
            raise PolicyMetadataError(f"Corrupted test_command JSON: {exc}") from exc

        if test_cmd not in fixture.approved_test_commands:
            raise FixtureRegistryError(
                f"Test command {test_cmd} is not approved for fixture '{fixture.fixture_id}'. "
                f"Approved commands: {fixture.approved_test_commands}"
            )

        # Validate sandbox image against fixture pinned image
        if run.sandbox_image != fixture.sandbox_image:
            raise FixtureRegistryError(
                f"Sandbox image '{run.sandbox_image}' does not match fixture pinned image "
                f"'{fixture.sandbox_image}'."
            )

        return fixture

    @classmethod
    def validate_all(
        cls,
        db: Session,
        run: VerificationRun,
        user_id: int | None = None,
        files: dict[str, str] | None = None,
        registry: TrustedFixtureRegistry | None = None,
    ) -> tuple[Repository, TrustedFixture]:
        """Execute all policy checks in strict sequence. Fails closed on any inconsistency."""
        active_registry = registry or get_default_fixture_registry()

        # 1. User authorization check
        effective_user_id = user_id or run.approved_by_user_id or run.user_id
        cls.validate_user_authorization(db, run.repository_id, effective_user_id)

        # 2. Approved specification check
        cls.validate_approved_specification(run)

        # 3. Patch integrity check
        cls.validate_patch_integrity(run)

        # 4. Repository trust check
        repo = db.get(Repository, run.repository_id)
        cls.validate_repository_trust(repo, active_registry)

        # 5. Resolve snapshot files if not provided
        resolved_files = files
        if resolved_files is None:
            job = db.scalar(
                select(IndexingJob).where(
                    IndexingJob.repository_id == run.repository_id,
                    IndexingJob.commit_sha == run.commit_sha,
                )
            )
            if not job:
                raise PolicyMetadataError(
                    f"No completed indexing job found for repo {run.repository_id} at {run.commit_sha}."
                )
            indexed = db.scalars(select(IndexedFile).where(IndexedFile.job_id == job.id)).all()
            if not indexed:
                raise PolicyMetadataError(f"No indexed files found for job {job.id}.")
            resolved_files = {f.path: f.content for f in indexed}

        # 6. Fixture and snapshot integrity check
        fixture = cls.validate_fixture_and_snapshot(repo, run, resolved_files, active_registry)

        return repo, fixture
