"""Comprehensive tests for deterministic patch application engine and verification models."""

import os
from pathlib import Path

import pytest
from sqlalchemy import select, text

from repopilot_api.models import ProofReport, Repository, User, VerificationRun
from repopilot_api.patcher import (
    PatchConflictError,
    PatchError,
    PatchSecurityError,
    PatchSyntaxError,
    PatchTargetNotFoundError,
    apply_patch_proposal,
    apply_unified_diff,
    validate_patch_target_path,
)
from repopilot_api.sandbox import WorkspaceManager
from repopilot_api.schemas import PatchProposal, ProposedFix


def test_apply_unified_diff_single_hunk():
    original = "def add(a, b):\n    return a - b\n"
    diff = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,3 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    # fixed addition\n"
        "+    return a + b\n"
    )
    patched, hunks, added, deleted = apply_unified_diff(original, diff)
    assert hunks == 1
    assert added == 2
    assert deleted == 1
    assert patched == "def add(a, b):\n    # fixed addition\n    return a + b\n"


def test_apply_unified_diff_multiple_hunks():
    original = "line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\nline 8\nline 9\nline 10\n"
    diff = (
        "@@ -2,3 +2,3 @@\n"
        " line 2\n"
        "-line 3\n"
        "+line 3 MODIFIED\n"
        " line 4\n"
        "@@ -7,3 +7,4 @@\n"
        " line 7\n"
        "-line 8\n"
        "+line 8 MODIFIED\n"
        "+line 8.5 ADDED\n"
        " line 9\n"
    )
    patched, hunks, added, deleted = apply_unified_diff(original, diff)
    assert hunks == 2
    assert added == 3
    assert deleted == 2
    assert "line 3 MODIFIED\n" in patched
    assert "line 8 MODIFIED\nline 8.5 ADDED\n" in patched
    assert "line 1\n" in patched
    assert "line 10\n" in patched


def test_apply_unified_diff_new_file_creation():
    diff = "--- /dev/null\n+++ b/new_file.py\n@@ -0,0 +1,2 @@\n+print('hello')\n+print('world')\n"
    patched, hunks, added, deleted = apply_unified_diff("", diff)
    assert hunks == 1
    assert added == 2
    assert deleted == 0
    assert patched == "print('hello')\nprint('world')\n"


def test_apply_unified_diff_rejects_context_mismatch():
    original = "def solve():\n    return 42\n"
    # Content mismatch in context line
    diff = (
        "@@ -1,2 +1,2 @@\n"
        " def solve_mismatched():\n"  # mismatched context line
        "-    return 42\n"
        "+    return 100\n"
    )
    with pytest.raises(PatchConflictError, match="Context mismatch at line 1"):
        apply_unified_diff(original, diff)


def test_apply_unified_diff_rejects_deletion_mismatch():
    original = "a = 1\nb = 2\n"
    diff = (
        "@@ -1,2 +1,2 @@\n"
        " a = 1\n"
        "-b = 999\n"  # original has b = 2
        "+b = 3\n"
    )
    with pytest.raises(PatchConflictError, match="Deletion mismatch at line 2"):
        apply_unified_diff(original, diff)


def test_apply_unified_diff_rejects_overlapping_hunks():
    original = "1\n2\n3\n4\n5\n"
    diff = (
        "@@ -2,3 +2,3 @@\n"
        " 2\n"
        "-3\n"
        "+3.1\n"
        " 4\n"
        "@@ -3,2 +3,2 @@\n"  # overlaps with lines 2-4
        "-3\n"
        "+3.2\n"
        " 4\n"
    )
    with pytest.raises(PatchSyntaxError, match="Overlapping or out-of-order"):
        apply_unified_diff(original, diff)


def test_apply_unified_diff_rejects_binary_patches():
    with pytest.raises(PatchError, match="Binary patches are not supported"):
        apply_unified_diff("code", "GIT binary patch\nliteral 1234")


def test_apply_unified_diff_rejects_malformed_diff():
    with pytest.raises(PatchSyntaxError, match="Diff text cannot be empty"):
        apply_unified_diff("code", "")

    with pytest.raises(PatchSyntaxError, match="missing unified diff hunk header"):
        apply_unified_diff("code", "Just replace line 1 with line 2")


def test_validate_patch_target_path_traversal(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    ws_str = str(ws)

    # Valid relative path
    target = validate_patch_target_path(ws_str, "src/main.py")
    assert target == (ws / "src" / "main.py").resolve()

    # Reject traversal
    with pytest.raises(PatchSecurityError, match="Directory traversal"):
        validate_patch_target_path(ws_str, "../../outside.py")

    with pytest.raises(PatchSecurityError, match="Absolute or control-character"):
        validate_patch_target_path(ws_str, "/etc/passwd")

    with pytest.raises(PatchSecurityError, match="Patch file_path cannot be empty"):
        validate_patch_target_path(ws_str, "")


def test_validate_patch_target_path_symlink_rejection(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    ws_str = str(ws)

    outside = tmp_path / "sensitive.txt"
    outside.write_text("secret\n")

    symlink_file = ws / "symlink_target.py"
    symlink_file.symlink_to(outside)

    with pytest.raises(PatchSecurityError, match="Target path is a symlink"):
        validate_patch_target_path(ws_str, "symlink_target.py")


def test_apply_patch_proposal_multi_file_success(tmp_path):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace(
        "run_multi",
        {
            "src/a.py": "def foo():\n    return False\n",
            "src/b.py": "def bar():\n    return 0\n",
        },
    )

    diff_a = "@@ -1,2 +1,2 @@\n def foo():\n-    return False\n+    return True\n"
    diff_b = "@@ -1,2 +1,2 @@\n def bar():\n-    return 0\n+    return 1\n"

    proposal = ProposedFix(
        explanation="Fix both files",
        patches=[
            PatchProposal(file_path="src/a.py", diff=diff_a, explanation="fix a"),
            PatchProposal(file_path="src/b.py", diff=diff_b, explanation="fix b"),
        ],
    )

    result = apply_patch_proposal(ws, proposal, scratch_root=scratch)
    assert result.success is True
    assert result.changed_files == ["src/a.py", "src/b.py"]
    assert len(result.patch_hash) == 64
    assert len(result.file_results) == 2

    # Verify disk contents modified correctly
    assert (Path(ws) / "src" / "a.py").read_text() == "def foo():\n    return True\n"
    assert (Path(ws) / "src" / "b.py").read_text() == "def bar():\n    return 1\n"


def test_apply_patch_proposal_atomic_rollback_on_conflict(tmp_path):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    initial_a = "def foo():\n    return False\n"
    initial_b = "def bar():\n    return 0\n"
    ws = manager.create_workspace(
        "run_atomic_fail",
        {"src/a.py": initial_a, "src/b.py": initial_b},
    )

    # Valid diff for a
    diff_a = "@@ -1,2 +1,2 @@\n def foo():\n-    return False\n+    return True\n"
    # Conflicting diff for b (expected line does not match)
    diff_b = (
        "@@ -1,2 +1,2 @@\n"
        " def bar():\n"
        "-    return 9999\n"  # Mismatch!
        "+    return 1\n"
    )

    proposal = ProposedFix(
        explanation="Fix both files",
        patches=[
            PatchProposal(file_path="src/a.py", diff=diff_a, explanation="fix a"),
            PatchProposal(file_path="src/b.py", diff=diff_b, explanation="conflict b"),
        ],
    )

    with pytest.raises(PatchConflictError):
        apply_patch_proposal(ws, proposal, scratch_root=scratch)

    # Atomicity assertion: File A must NOT have been changed on disk!
    assert (Path(ws) / "src" / "a.py").read_text() == initial_a
    assert (Path(ws) / "src" / "b.py").read_text() == initial_b


def test_apply_patch_proposal_atomic_rollback_on_missing_file(tmp_path):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    initial_a = "def a(): pass\n"
    ws = manager.create_workspace("run_missing", {"src/a.py": initial_a})

    diff_a = "@@ -1,1 +1,1 @@\n-def a(): pass\n+def a(): return 1\n"
    diff_missing = "@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"

    proposal = ProposedFix(
        explanation="Try missing",
        patches=[
            PatchProposal(file_path="src/a.py", diff=diff_a, explanation="fix a"),
            PatchProposal(file_path="src/nonexistent.py", diff=diff_missing, explanation="missing"),
        ],
    )

    with pytest.raises(PatchTargetNotFoundError):
        apply_patch_proposal(ws, proposal, scratch_root=scratch)

    # File A was untouched
    assert (Path(ws) / "src" / "a.py").read_text() == initial_a


def test_apply_patch_proposal_rejects_host_workspace(tmp_path):
    scratch = str(tmp_path / "scratch")
    os.makedirs(scratch, exist_ok=True)

    # Workspace outside scratch root
    outside_ws = tmp_path / "outside_workspace"
    outside_ws.mkdir()

    proposal = ProposedFix(
        explanation="Test",
        patches=[
            PatchProposal(
                file_path="test.py",
                diff="@@ -1,1 +1,1 @@\n-a\n+b\n",
                explanation="test",
            )
        ],
    )

    with pytest.raises(Exception):
        apply_patch_proposal(str(outside_ws), proposal, scratch_root=scratch)


def test_verification_models_persistence_in_test_db(test_session):
    with test_session() as session:
        # Create user and repository
        user = User(disabled=False)
        session.add(user)
        session.flush()

        repo = Repository(
            github_id=12345,
            owner="testowner",
            name="testrepo",
            full_name="testowner/testrepo",
            url="https://github.com/testowner/testrepo",
            default_branch="main",
            stars=10,
        )
        session.add(repo)
        session.flush()

        # Create VerificationRun
        vrun = VerificationRun(
            repository_id=repo.id,
            commit_sha="a" * 40,
            issue_number=42,
            user_id=user.id,
            status="passed",
            baseline_exit_code=1,
            baseline_stdout="FAILED tests/test_calc.py::test_add",
            baseline_stderr="",
            patched_exit_code=0,
            patched_stdout="PASSED 1 test in 0.05s",
            patched_stderr="",
            patch_hash="b" * 64,
            patch_diff="--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-1\n+2\n",
            duration_seconds=1.25,
            sandbox_image="python:3.11-slim@sha256:" + ("c" * 64),
        )
        session.add(vrun)
        session.flush()

        # Create ProofReport linked to VerificationRun
        report = ProofReport(
            verification_run_id=vrun.id,
            verdict="verified_fix",
            hypothesis="Hypothesis: subtraction instead of addition",
            summary="Fixed addition operator in calc.py",
            baseline_summary="1 failed test (AssertionError: 0 != 2)",
            patched_summary="1 passed test in 0.05s",
            confidence="high",
            evidence_json='{"tests_run": 1, "tests_passed": 1}',
        )
        session.add(report)
        session.commit()

        # Query and verify relationships
        saved_run = session.scalar(select(VerificationRun).where(VerificationRun.id == vrun.id))
        assert saved_run is not None
        assert saved_run.status == "passed"
        assert saved_run.issue_number == 42
        assert saved_run.baseline_exit_code == 1
        assert saved_run.patched_exit_code == 0

        saved_report = session.scalar(
            select(ProofReport).where(ProofReport.verification_run_id == vrun.id)
        )
        assert saved_report is not None
        assert saved_report.verdict == "verified_fix"
        assert "subtraction" in saved_report.hypothesis


def test_apply_patch_proposal_rollback_on_disk_write_ioerror(tmp_path, monkeypatch):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    initial_a = "def foo(): return 1\n"
    initial_b = "def bar(): return 2\n"
    ws = manager.create_workspace("run_write_err", {"src/a.py": initial_a, "src/b.py": initial_b})

    diff_a = "@@ -1,1 +1,1 @@\n-def foo(): return 1\n+def foo(): return 10\n"
    diff_b = "@@ -1,1 +1,1 @@\n-def bar(): return 2\n+def bar(): return 20\n"

    proposal = ProposedFix(
        explanation="Fix both files",
        patches=[
            PatchProposal(file_path="src/a.py", diff=diff_a, explanation="fix a"),
            PatchProposal(file_path="src/b.py", diff=diff_b, explanation="fix b"),
        ],
    )

    real_write_text = Path.write_text

    def fake_write_text(self, data, *args, **kwargs):
        if self.name == "b.py":
            raise OSError("Simulated disk write failure on b.py")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fake_write_text)

    with pytest.raises(PatchError, match="Disk write error during patch application"):
        apply_patch_proposal(ws, proposal, scratch_root=scratch)

    # Rollback assertion: File A was written first, but must be restored to its original state!
    assert (Path(ws) / "src" / "a.py").read_text() == initial_a
    assert (Path(ws) / "src" / "b.py").read_text() == initial_b


def test_apply_patch_proposal_rollback_removes_newly_created_file(tmp_path, monkeypatch):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    initial_existing = "existing = True\n"
    ws = manager.create_workspace("run_new_fail", {"src/existing.py": initial_existing})

    diff_new = "@@ -0,0 +1,2 @@\n+line 1\n+line 2\n"
    diff_existing = "@@ -1,1 +1,1 @@\n-existing = True\n+existing = False\n"

    proposal = ProposedFix(
        explanation="Create new and modify existing",
        patches=[
            PatchProposal(file_path="src/new_file.py", diff=diff_new, explanation="create new"),
            PatchProposal(
                file_path="src/existing.py", diff=diff_existing, explanation="modify existing"
            ),
        ],
    )

    real_write_text = Path.write_text

    def fake_write_text(self, data, *args, **kwargs):
        if self.name == "existing.py":
            raise OSError("Simulated disk write failure on existing.py")
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fake_write_text)

    with pytest.raises(PatchError, match="Disk write error during patch application"):
        apply_patch_proposal(ws, proposal, scratch_root=scratch)

    # Newly created file must have been unlinked during rollback!
    assert not (Path(ws) / "src" / "new_file.py").exists()
    assert (Path(ws) / "src" / "existing.py").read_text() == initial_existing


def test_apply_patch_proposal_rejects_duplicate_targets(tmp_path):
    scratch = str(tmp_path / "scratch")
    manager = WorkspaceManager(scratch_root=scratch)
    ws = manager.create_workspace("run_dups", {"src/a.py": "x = 1\n"})

    diff = "@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"

    proposal = ProposedFix(
        explanation="Duplicate target",
        patches=[
            PatchProposal(file_path="src/a.py", diff=diff, explanation="first"),
            PatchProposal(file_path="src/a.py", diff=diff, explanation="second duplicate"),
        ],
    )

    with pytest.raises(PatchError, match="Duplicate patch target in proposal: 'src/a.py'"):
        apply_patch_proposal(ws, proposal, scratch_root=scratch)

    # Also test path normalization catching duplicate targets like './src/a.py'
    proposal_norm = ProposedFix(
        explanation="Duplicate with relative prefix",
        patches=[
            PatchProposal(file_path="src/a.py", diff=diff, explanation="first"),
            PatchProposal(
                file_path="./src/a.py", diff=diff, explanation="duplicate with dot slash"
            ),
        ],
    )

    with pytest.raises(PatchError, match="Duplicate patch target in proposal: 'src/a.py'"):
        apply_patch_proposal(ws, proposal_norm, scratch_root=scratch)


def test_validate_patch_target_path_rejects_directory(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "src").mkdir()

    with pytest.raises(PatchSecurityError, match="Patch target is a directory"):
        validate_patch_target_path(str(ws), "src")

    with pytest.raises(PatchSecurityError, match="Patch file_path cannot be empty or root"):
        validate_patch_target_path(str(ws), ".")


def test_validate_patch_target_path_parent_symlink_rejection(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()

    symlink_dir = ws / "symdir"
    symlink_dir.symlink_to(outside_dir)

    with pytest.raises(PatchSecurityError, match="Parent directory contains a symlink"):
        validate_patch_target_path(str(ws), "symdir/target.py")


def test_cascade_delete_verification_run_and_proof_report(test_session):
    with test_session() as session:
        session.execute(text("PRAGMA foreign_keys = ON;"))

        user = User(disabled=False)
        session.add(user)
        session.flush()

        repo = Repository(
            github_id=99901,
            owner="testowner",
            name="testrepo",
            full_name="testowner/testrepo",
            url="https://github.com/testowner/testrepo",
            default_branch="main",
            stars=5,
        )
        session.add(repo)
        session.flush()

        vrun = VerificationRun(
            repository_id=repo.id,
            commit_sha="d" * 40,
            issue_number=10,
            user_id=user.id,
            status="passed",
            duration_seconds=0.5,
            sandbox_image="python:3.11-slim@sha256:" + ("e" * 64),
        )
        session.add(vrun)
        session.flush()

        report = ProofReport(
            verification_run_id=vrun.id,
            verdict="verified_fix",
            hypothesis="test hypothesis",
            summary="test summary",
            baseline_summary="fail",
            patched_summary="pass",
        )
        session.add(report)
        session.commit()

        # Delete VerificationRun
        vrun_to_delete = session.scalar(
            select(VerificationRun).where(VerificationRun.id == vrun.id)
        )
        session.delete(vrun_to_delete)
        session.commit()

        # Assert cascade deleted ProofReport
        saved_report = session.scalar(
            select(ProofReport).where(ProofReport.verification_run_id == vrun.id)
        )
        assert saved_report is None


def test_cascade_delete_repository_cascades_to_verification_run(test_session):
    with test_session() as session:
        session.execute(text("PRAGMA foreign_keys = ON;"))

        repo = Repository(
            github_id=99902,
            owner="testowner",
            name="testrepo2",
            full_name="testowner/testrepo2",
            url="https://github.com/testowner/testrepo2",
            default_branch="main",
            stars=3,
        )
        session.add(repo)
        session.flush()

        vrun = VerificationRun(
            repository_id=repo.id,
            commit_sha="f" * 40,
            issue_number=11,
            status="passed",
            duration_seconds=0.8,
            sandbox_image="python:3.11-slim@sha256:" + ("0" * 64),
        )
        session.add(vrun)
        session.flush()

        report = ProofReport(
            verification_run_id=vrun.id,
            verdict="verified_fix",
            hypothesis="test hypothesis",
            summary="test summary",
            baseline_summary="fail",
            patched_summary="pass",
        )
        session.add(report)
        session.commit()

        # Delete Repository
        repo_to_delete = session.scalar(select(Repository).where(Repository.id == repo.id))
        session.delete(repo_to_delete)
        session.commit()

        # VerificationRun and ProofReport should both be deleted by cascade
        saved_vrun = session.scalar(select(VerificationRun).where(VerificationRun.id == vrun.id))
        assert saved_vrun is None

        saved_report = session.scalar(
            select(ProofReport).where(ProofReport.verification_run_id == vrun.id)
        )
        assert saved_report is None
