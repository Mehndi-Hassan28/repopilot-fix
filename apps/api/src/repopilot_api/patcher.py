"""Deterministic, atomic unified diff patch application engine for RepoPilot Fix."""

import hashlib
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from repopilot_api.sandbox import PathValidator
from repopilot_api.schemas import (
    PatchApplicationResult,
    PatchFileResult,
    PatchProposal,
    ProposedFix,
)

HUNK_HEADER_REGEX = re.compile(r"^@@\s*-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s*@@(?: (.*))?$")


class PatchError(Exception):
    """Base exception for all patch application errors."""


class PatchSyntaxError(PatchError):
    """Raised when a unified diff cannot be parsed or is malformed."""


class PatchConflictError(PatchError):
    """Raised when context or deleted lines do not match source content exactly."""


class PatchSecurityError(PatchError):
    """Raised when a patch attempts path traversal, symlink escapes, or unsafe target access."""


class PatchTargetNotFoundError(PatchError):
    """Raised when the target file to be modified does not exist."""


@dataclass(frozen=True)
class Hunk:
    """Represents a single parsed unified diff hunk."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


def parse_diff_hunks(diff_text: str) -> list[Hunk]:
    """Parse a unified diff into structured hunks with strict validation."""
    if not diff_text or not diff_text.strip():
        raise PatchSyntaxError("Diff text cannot be empty.")

    if "GIT binary patch" in diff_text or "\x00" in diff_text:
        raise PatchError("Binary patches are not supported.")

    lines = diff_text.splitlines()
    hunks: list[Hunk] = []
    current_hunk_header: tuple[int, int, int, int] | None = None
    current_hunk_lines: list[str] = []

    for line in lines:
        match = HUNK_HEADER_REGEX.match(line)
        if match:
            # If we were already collecting lines for a previous hunk, finish it
            if current_hunk_header is not None:
                hunks.append(
                    Hunk(
                        old_start=current_hunk_header[0],
                        old_count=current_hunk_header[1],
                        new_start=current_hunk_header[2],
                        new_count=current_hunk_header[3],
                        lines=current_hunk_lines,
                    )
                )
                current_hunk_lines = []

            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            current_hunk_header = (old_start, old_count, new_start, new_count)
        elif current_hunk_header is not None:
            # Inside a hunk
            if line.startswith(("+", "-", " ", "\\")):
                current_hunk_lines.append(line)
            else:
                # Malformed line inside hunk
                raise PatchSyntaxError(f"Malformed diff line in hunk: {line!r}")

    if current_hunk_header is not None:
        hunks.append(
            Hunk(
                old_start=current_hunk_header[0],
                old_count=current_hunk_header[1],
                new_start=current_hunk_header[2],
                new_count=current_hunk_header[3],
                lines=current_hunk_lines,
            )
        )

    if not hunks:
        raise PatchSyntaxError(
            "Malformed patch: missing unified diff hunk header (@@ -start,count +start,count @@)."
        )

    # Validate hunk ordering and overlap
    for i in range(len(hunks) - 1):
        prev_hunk = hunks[i]
        next_hunk = hunks[i + 1]
        prev_end = prev_hunk.old_start + max(prev_hunk.old_count, 1)
        if next_hunk.old_start < prev_end:
            raise PatchSyntaxError(
                f"Overlapping or out-of-order hunks detected at lines "
                f"{prev_hunk.old_start} and {next_hunk.old_start}."
            )

    return hunks


def apply_unified_diff(original_content: str, diff_text: str) -> tuple[str, int, int, int]:
    """Apply a unified diff to original_content with exact context matching.

    Returns:
        tuple[str, int, int, int]: (patched_content, hunks_applied, lines_added, lines_deleted)
    """
    hunks = parse_diff_hunks(diff_text)
    orig_lines = original_content.splitlines(keepends=False)
    has_trailing_newline = original_content.endswith("\n") if original_content else True

    orig_idx = 0
    new_lines: list[str] = []
    total_added = 0
    total_deleted = 0

    for hunk in hunks:
        target_idx = max(0, hunk.old_start - 1) if hunk.old_start > 0 else 0

        if target_idx < orig_idx:
            raise PatchSyntaxError(
                f"Hunk target index {target_idx} is before current line {orig_idx}."
            )

        # Copy unchanged lines leading up to this hunk
        while orig_idx < target_idx:
            if orig_idx < len(orig_lines):
                new_lines.append(orig_lines[orig_idx])
            orig_idx += 1

        hunk_old_consumed = 0
        hunk_new_produced = 0

        for line in hunk.lines:
            if line.startswith(" "):
                # Context line: must match exact original content
                if orig_idx >= len(orig_lines):
                    raise PatchConflictError(
                        f"Unexpected end of file while expecting context line: {line[1:]!r}"
                    )
                expected = orig_lines[orig_idx]
                actual = line[1:]
                if expected != actual:
                    raise PatchConflictError(
                        f"Context mismatch at line {orig_idx + 1}. "
                        f"Expected: {expected!r}, Got: {actual!r}"
                    )
                new_lines.append(expected)
                orig_idx += 1
                hunk_old_consumed += 1
                hunk_new_produced += 1

            elif line.startswith("-"):
                # Deletion line: must match exact original content
                if orig_idx >= len(orig_lines):
                    raise PatchConflictError(
                        f"Unexpected end of file while expecting deletion line: {line[1:]!r}"
                    )
                expected = orig_lines[orig_idx]
                actual = line[1:]
                if expected != actual:
                    raise PatchConflictError(
                        f"Deletion mismatch at line {orig_idx + 1}. "
                        f"Expected: {expected!r}, Got: {actual!r}"
                    )
                orig_idx += 1
                hunk_old_consumed += 1
                total_deleted += 1

            elif line.startswith("+"):
                # Addition line: emit into new lines
                new_lines.append(line[1:])
                hunk_new_produced += 1
                total_added += 1

            elif line.startswith("\\"):
                # Handle '\\ No newline at end of file'
                has_trailing_newline = False

        if hunk.old_count != hunk_old_consumed:
            raise PatchSyntaxError(
                f"Hunk declared {hunk.old_count} old lines but consumed {hunk_old_consumed}."
            )
        if hunk.new_count != hunk_new_produced:
            raise PatchSyntaxError(
                f"Hunk declared {hunk.new_count} new lines but produced {hunk_new_produced}."
            )

    # Copy any remaining original lines after the last hunk
    while orig_idx < len(orig_lines):
        new_lines.append(orig_lines[orig_idx])
        orig_idx += 1

    patched_content = "\n".join(new_lines)
    if has_trailing_newline and patched_content:
        patched_content += "\n"

    return patched_content, len(hunks), total_added, total_deleted


def validate_patch_target_path(workspace_path: str, rel_path: str) -> Path:
    """Validate that target path is strictly inside workspace and not a symlink escape."""
    canonical_ws = Path(workspace_path).resolve()
    clean_path = rel_path.strip().replace("\\", "/")

    if not clean_path or clean_path in (".", "./"):
        raise PatchSecurityError("Patch file_path cannot be empty or root directory.")
    if clean_path.startswith("/") or "\x00" in clean_path:
        raise PatchSecurityError(f"Absolute or control-character path rejected: '{rel_path}'.")
    if ".." in clean_path.split("/"):
        raise PatchSecurityError(f"Directory traversal detected in path: '{rel_path}'.")

    raw_path = canonical_ws / clean_path
    if raw_path.is_symlink() or os.path.islink(raw_path):
        raise PatchSecurityError(f"Target path is a symlink: '{rel_path}'.")

    curr = raw_path.parent
    while curr != canonical_ws and curr != curr.parent:
        if curr.is_symlink() or os.path.islink(curr):
            raise PatchSecurityError(f"Parent directory contains a symlink: '{curr}'.")
        curr = curr.parent

    target_path = raw_path.resolve()
    if not target_path.is_relative_to(canonical_ws):
        raise PatchSecurityError(f"Path escape outside workspace detected: '{rel_path}'.")
    if target_path.is_dir():
        raise PatchSecurityError(f"Patch target is a directory, not a file: '{rel_path}'.")

    return target_path


def apply_patch_proposal(
    workspace_path: str,
    proposal: ProposedFix | list[PatchProposal],
    scratch_root: str | None = None,
) -> PatchApplicationResult:
    """Atomically apply a patch proposal to an isolated disposable workspace.

    Safety:
        - NEVER applies changes to developer original repository or host files.
        - Requires workspace to reside strictly inside designated scratch root.
        - Multi-file patch application is atomic: if any file fails in-memory
          patching, no files on disk are touched.
        - If disk write fails, performs automatic rollback.
    """
    applied_at = datetime.now(UTC).isoformat()
    validated_ws = PathValidator.validate_workspace(workspace_path, scratch_root=scratch_root)

    patches = proposal.patches if isinstance(proposal, ProposedFix) else proposal
    if not patches:
        return PatchApplicationResult(
            success=True,
            changed_files=[],
            patch_hash="",
            failure_reason="No patches to apply.",
            applied_at=applied_at,
            file_results=[],
        )

    # In-memory staging dictionary: target_path -> (original_content, patched_content, file_result)
    staged: dict[Path, tuple[str, str, PatchFileResult]] = {}
    seen_files: set[str] = set()
    combined_diff_builder: list[str] = []

    # Phase 1: Pure in-memory validation and diff application
    for patch in patches:
        clean_name = os.path.normpath(patch.file_path.strip().replace("\\", "/"))
        if clean_name in seen_files:
            raise PatchError(f"Duplicate patch target in proposal: '{clean_name}'.")
        seen_files.add(clean_name)

        target_file = validate_patch_target_path(validated_ws, clean_name)

        original_content = ""
        if target_file.exists():
            try:
                raw_bytes = target_file.read_bytes()
                if b"\x00" in raw_bytes:
                    raise PatchError(f"Binary file cannot be patched: '{clean_name}'.")
                original_content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PatchError(f"Non-UTF8 file cannot be patched: '{clean_name}'.") from exc
        else:
            # Check if this patch is creating a new file
            hunks = parse_diff_hunks(patch.diff)
            if not hunks or hunks[0].old_start != 0:
                raise PatchTargetNotFoundError(
                    f"Target file does not exist in workspace: '{clean_name}'."
                )

        patched_content, hunks_count, added, deleted = apply_unified_diff(
            original_content, patch.diff
        )

        orig_hash = hashlib.sha256(original_content.encode("utf-8")).hexdigest()
        patched_hash = hashlib.sha256(patched_content.encode("utf-8")).hexdigest()

        file_result = PatchFileResult(
            file_path=clean_name,
            original_sha256=orig_hash,
            patched_sha256=patched_hash,
            hunks_applied=hunks_count,
            lines_added=added,
            lines_deleted=deleted,
        )
        staged[target_file] = (original_content, patched_content, file_result)
        combined_diff_builder.append(patch.diff.strip())

    # Phase 2: Atomic disk application with guaranteed rollback
    written_files: list[tuple[Path, str, bool]] = []  # (path, original_content, existed_before)
    try:
        for target_file, (orig_content, patched_content, file_res) in staged.items():
            # Re-validate target path to prevent TOCTOU symlink races
            validate_patch_target_path(validated_ws, file_res.file_path)
            if target_file.is_symlink() or os.path.islink(target_file):
                raise PatchSecurityError(f"Symlink detected before writing: '{target_file}'")
            existed_before = target_file.exists()
            written_files.append((target_file, orig_content, existed_before))
            target_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target_file.write_text(patched_content, encoding="utf-8")
    except Exception as exc:
        # Atomic Rollback: restore all files modified or created during this run
        for path, orig_content, existed in written_files:
            try:
                if not existed:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(orig_content, encoding="utf-8")
            except Exception:
                pass
        if isinstance(exc, PatchError):
            raise
        raise PatchError(f"Disk write error during patch application: {exc}") from exc

    combined_hash = hashlib.sha256("\n".join(combined_diff_builder).encode("utf-8")).hexdigest()
    changed_files = [res.file_path for _, _, res in staged.values()]
    file_results = [res for _, _, res in staged.values()]

    return PatchApplicationResult(
        success=True,
        changed_files=changed_files,
        patch_hash=combined_hash,
        failure_reason=None,
        applied_at=applied_at,
        file_results=file_results,
    )
