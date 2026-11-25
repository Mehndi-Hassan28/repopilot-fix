"""Secure GitHub PR creation, verification gating, and offline patch export engine."""

import base64
import json
import logging
import os
import re
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from repopilot_api.models import (
    GitHubPullRequest,
    ProofReport,
    Repository,
    VerificationRun,
)
from repopilot_api.patcher import PatchError, apply_unified_diff
from repopilot_api.schemas import (
    PatchExportResponse,
    ProofVerdict,
    PullRequestCreateRequest,
    PullRequestCreateResponse,
    PullRequestStatusResponse,
    VerificationAuditEventType,
    VerificationJobStatus,
)
from repopilot_api.verification_service import compute_spec_hash, log_audit_event

logger = logging.getLogger(__name__)

DEFAULT_HTTP_TIMEOUT = 10.0


class GitHubPRError(Exception):
    """Base exception for all GitHub pull request creation errors."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class GitHubAuthError(GitHubPRError):
    """Missing, invalid, or expired GitHub authentication credentials."""

    def __init__(self, message: str = "Missing or invalid GitHub authorization."):
        super().__init__(message, status_code=401)


class GitHubPermissionError(GitHubPRError):
    """Insufficient GitHub repository permissions."""

    def __init__(
        self, message: str = "Insufficient GitHub permissions to create branch or pull request."
    ):
        super().__init__(message, status_code=403)


class GitHubVerificationPrerequisiteError(GitHubPRError):
    """Verification run is not in an eligible state for pull request creation."""

    def __init__(self, message: str):
        super().__init__(message, status_code=400)


class GitHubRateLimitError(GitHubPRError):
    """GitHub API rate limit exceeded."""

    def __init__(self, message: str = "GitHub API rate limit exceeded. Please try again later."):
        super().__init__(message, status_code=429)


class GitHubUpstreamError(GitHubPRError):
    """GitHub API network failure or unexpected upstream response."""

    def __init__(self, message: str = "GitHub API request failed."):
        super().__init__(message, status_code=502)


class GitHubPRClient:
    """GitHub REST client for branch creation, tree commit, and pull request operations."""

    def __init__(self, token: str, client: httpx.Client | None = None):
        self.token = token.strip()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(DEFAULT_HTTP_TIMEOUT), follow_redirects=False
        )
        self.owned = client is None

    def __repr__(self) -> str:
        return "<GitHubPRClient token='***'>"

    def close(self) -> None:
        if self.owned:
            self.client.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "RepoPilot-Fix",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _handle_error(self, response: httpx.Response, action_description: str) -> None:
        if response.status_code in (401,):
            raise GitHubAuthError("GitHub authentication failed. Token may be expired or revoked.")
        if response.status_code in (403, 429) and (
            response.status_code == 429
            or response.headers.get("X-RateLimit-Remaining") == "0"
            or "rate limit" in response.text.lower()
        ):
            raise GitHubRateLimitError()
        if response.status_code in (403, 404):
            raise GitHubPermissionError(f"Insufficient GitHub permissions to {action_description}.")
        if response.status_code >= 500:
            raise GitHubUpstreamError(
                f"GitHub service error (HTTP {response.status_code}) while attempting to "
                f"{action_description}."
            )
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = body.get("message") or ""
        except Exception:
            detail = response.text[:200]
        # Sanitize any accidental credentials in detail
        if detail:
            pattern = r"(?:ghp_|gho_|github_pat_|AIza|sk-|Bearer\s+)[A-Za-z0-9_.-]+"
            detail = re.sub(pattern, "***", detail)
        raise GitHubPRError(
            f"GitHub API error ({response.status_code}) while attempting to "
            f"{action_description}: {detail}".strip()
        )

    def get_base_branch_sha(self, owner: str, repo: str, branch: str) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe='')}"
        try:
            resp = self.client.get(url, headers=self._headers())
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            msg = f"Failed to resolve base branch '{branch}' on GitHub."
            raise GitHubUpstreamError(msg) from exc

        if resp.status_code != 200:
            self._handle_error(resp, f"resolve branch '{branch}'")
        data = resp.json()
        obj = data.get("object")
        if not isinstance(obj, dict) or not obj.get("sha"):
            raise GitHubPRError(f"Invalid git reference structure for branch '{branch}'.")
        return str(obj["sha"])

    def get_branch_ref(self, owner: str, repo: str, branch: str) -> str | None:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe='')}"
        try:
            resp = self.client.get(url, headers=self._headers())
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError(f"Failed to query branch '{branch}' on GitHub.") from exc

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            self._handle_error(resp, f"query branch '{branch}'")
        data = resp.json()
        obj = data.get("object")
        if isinstance(obj, dict) and obj.get("sha"):
            return str(obj["sha"])
        return None

    def create_branch_ref(self, owner: str, repo: str, branch: str, base_sha: str) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs"
        payload = {"ref": f"refs/heads/{branch}", "sha": base_sha}
        try:
            resp = self.client.post(url, headers=self._headers(), json=payload)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError(f"Failed to create branch '{branch}' on GitHub.") from exc

        if resp.status_code == 422 and "Reference already exists" in resp.text:
            existing_sha = self.get_branch_ref(owner, repo, branch)
            if existing_sha:
                return existing_sha
        if resp.status_code not in (200, 201):
            self._handle_error(resp, f"create branch '{branch}'")
        data = resp.json()
        obj = data.get("object", {})
        return str(obj.get("sha", base_sha))

    def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> tuple[str, bool]:
        encoded_path = quote(path, safe="/")
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{encoded_path}?ref={ref}"
        try:
            resp = self.client.get(url, headers=self._headers())
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError(f"Failed to read file '{path}' from GitHub.") from exc

        if resp.status_code == 404:
            return "", False
        if resp.status_code != 200:
            self._handle_error(resp, f"read file '{path}'")
        data = resp.json()
        if not isinstance(data, dict):
            raise GitHubPRError(f"Invalid content response for file '{path}'.")
        raw_b64 = data.get("content", "")
        if data.get("encoding") == "base64" and raw_b64:
            try:
                decoded = base64.b64decode(raw_b64).decode("utf-8")
                return decoded, True
            except UnicodeDecodeError as exc:
                raise GitHubPRError(f"File '{path}' contains non-UTF8 content.") from exc
        return "", True

    def create_blob(self, owner: str, repo: str, content: str) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/blobs"
        payload = {"content": content, "encoding": "utf-8"}
        try:
            resp = self.client.post(url, headers=self._headers(), json=payload)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError("Failed to create git blob on GitHub.") from exc

        if resp.status_code not in (200, 201):
            self._handle_error(resp, "create git blob")
        data = resp.json()
        sha = data.get("sha")
        if not sha:
            raise GitHubPRError("GitHub did not return blob SHA.")
        return str(sha)

    def create_tree(
        self, owner: str, repo: str, base_tree: str, tree_items: list[dict[str, Any]]
    ) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees"
        payload = {"base_tree": base_tree, "tree": tree_items}
        try:
            resp = self.client.post(url, headers=self._headers(), json=payload)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError("Failed to create git tree on GitHub.") from exc

        if resp.status_code not in (200, 201):
            self._handle_error(resp, "create git tree")
        data = resp.json()
        sha = data.get("sha")
        if not sha:
            raise GitHubPRError("GitHub did not return tree SHA.")
        return str(sha)

    def create_commit(
        self, owner: str, repo: str, message: str, tree_sha: str, parent_sha: str
    ) -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/commits"
        payload = {"message": message, "tree": tree_sha, "parents": [parent_sha]}
        try:
            resp = self.client.post(url, headers=self._headers(), json=payload)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError("Failed to create git commit on GitHub.") from exc

        if resp.status_code not in (200, 201):
            self._handle_error(resp, "create git commit")
        data = resp.json()
        sha = data.get("sha")
        if not sha:
            raise GitHubPRError("GitHub did not return commit SHA.")
        return str(sha)

    def update_branch_ref(self, owner: str, repo: str, branch: str, commit_sha: str) -> None:
        url = f"https://api.github.com/repos/{owner}/{repo}/git/refs/heads/{quote(branch, safe='')}"
        payload = {"sha": commit_sha, "force": False}
        try:
            resp = self.client.patch(url, headers=self._headers(), json=payload)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError(f"Failed to update branch ref '{branch}' on GitHub.") from exc

        if resp.status_code not in (200, 201):
            self._handle_error(resp, f"update branch ref '{branch}'")

    def find_existing_pr(
        self, owner: str, repo: str, head_branch: str, base_branch: str
    ) -> dict[str, Any] | None:
        head_query = f"{owner}:{head_branch}"
        url = (
            f"https://api.github.com/repos/{owner}/{repo}/pulls"
            f"?head={quote(head_query, safe='')}&base={quote(base_branch, safe='')}&state=all"
        )
        try:
            resp = self.client.get(url, headers=self._headers())
        except Exception:
            return None

        if resp.status_code == 200:
            prs = resp.json()
            if isinstance(prs, list) and len(prs) > 0:
                first = prs[0]
                if isinstance(first, dict) and first.get("html_url") and first.get("number"):
                    return first
        return None

    def create_pull_request(
        self, owner: str, repo: str, title: str, body: str, head_branch: str, base_branch: str
    ) -> tuple[int, str]:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        payload = {
            "title": title,
            "body": body,
            "head": head_branch,
            "base": base_branch,
        }
        try:
            resp = self.client.post(url, headers=self._headers(), json=payload)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise GitHubUpstreamError("Failed to open pull request on GitHub.") from exc

        if resp.status_code == 422:
            existing = self.find_existing_pr(owner, repo, head_branch, base_branch)
            if existing:
                return int(existing["number"]), str(existing["html_url"])

        if resp.status_code not in (200, 201):
            self._handle_error(resp, "create pull request")

        data = resp.json()
        pr_number = data.get("number")
        html_url = data.get("html_url")
        if not pr_number or not html_url:
            raise GitHubPRError("GitHub did not return valid pull request metadata.")
        return int(pr_number), str(html_url)


class GitHubPullRequestService:
    """Orchestrates human-approved, verified fix PR creation with strict idempotency."""

    @classmethod
    def _validate_run_for_pr(
        cls, db: Session, repository_id: int, verification_id: int
    ) -> tuple[VerificationRun, ProofReport, Repository]:
        run = db.get(VerificationRun, verification_id)
        if run is None or run.repository_id != repository_id:
            raise GitHubPRError("Verification run not found.", status_code=404)

        repo = db.get(Repository, repository_id)
        if repo is None:
            raise GitHubPRError("Repository record not found.", status_code=404)

        if run.status != VerificationJobStatus.COMPLETED.value:
            raise GitHubVerificationPrerequisiteError(
                f"Cannot create PR: verification status is '{run.status}'. "
                f"Run must be '{VerificationJobStatus.COMPLETED.value}'."
            )

        if not run.approved_spec_hash or not run.approved_by_user_id:
            raise GitHubVerificationPrerequisiteError(
                "Cannot create PR without prior explicit human approval of the exact patch."
            )

        # Integrity Check: current spec must match approved spec hash
        try:
            test_cmd = json.loads(run.test_command)
        except Exception:
            test_cmd = []
        spec_hash = compute_spec_hash(
            run.commit_sha, run.patch_hash or "", test_cmd, run.sandbox_image
        )
        if spec_hash != run.approved_spec_hash:
            raise GitHubVerificationPrerequisiteError(
                "Specification tampered: verification specification altered after approval. "
                "PR creation blocked."
            )

        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run.id))
        if report is None:
            raise GitHubVerificationPrerequisiteError(
                "Cannot create PR: no ProofReport found for this verification run."
            )

        if report.verdict != ProofVerdict.VERIFIED_FIX.value:
            v_fix = ProofVerdict.VERIFIED_FIX.value
            raise GitHubVerificationPrerequisiteError(
                f"Cannot create PR: ProofReport verdict is '{report.verdict}'. "
                f"Only verified fixes ('{v_fix}') qualify for PR creation."
            )

        # Cryptographic Evidence Binding: verify evidence matches exact revision, patch, and spec
        try:
            ev_data = json.loads(report.evidence_json or "{}")
        except Exception:
            ev_data = {}

        ev_commit = ev_data.get("commit_sha")
        if ev_commit and ev_commit.lower().strip() != run.commit_sha.lower().strip():
            raise GitHubVerificationPrerequisiteError(
                f"Evidence mismatch: report commit '{ev_commit}' does not match "
                f"run commit '{run.commit_sha}'."
            )

        ev_patch = ev_data.get("patch_hash")
        if ev_patch and ev_patch.lower().strip() != (run.patch_hash or "").lower().strip():
            raise GitHubVerificationPrerequisiteError(
                "Evidence mismatch: report patch hash does not match run patch hash."
            )

        ev_approved_spec = ev_data.get("approved_spec_hash")
        if (
            ev_approved_spec
            and ev_approved_spec.lower().strip() != (run.approved_spec_hash or "").lower().strip()
        ):
            raise GitHubVerificationPrerequisiteError(
                "Evidence mismatch: report approved specification hash does not match run."
            )

        if not run.patch_diff or not run.patch_diff.strip():
            raise GitHubVerificationPrerequisiteError(
                "Cannot create PR: verification run contains an empty patch diff."
            )

        return run, report, repo

    validate_pr_prerequisites = _validate_run_for_pr

    @classmethod
    def _resolve_token(
        cls, payload: PullRequestCreateRequest, auth_token_header: str | None = None
    ) -> str:
        if payload.github_token and payload.github_token.strip():
            return payload.github_token.strip()
        if auth_token_header and auth_token_header.strip():
            return auth_token_header.strip()
        env_token = (
            os.getenv("GITHUB_PR_TOKEN", "").strip() or os.getenv("GITHUB_TOKEN", "").strip()
        )
        if env_token:
            return env_token
        raise GitHubAuthError(
            "Missing GitHub authorization token. Please provide a GitHub token with repository "
            "write access or use manual patch export."
        )

    @classmethod
    def _parse_patches(cls, run: VerificationRun) -> list[dict[str, str]]:
        patches: list[dict[str, str]] = []
        if run.patches_json:
            try:
                parsed = json.loads(run.patches_json)
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict) and item.get("file_path") and item.get("diff"):
                            patches.append(
                                {
                                    "file_path": item["file_path"].strip(),
                                    "diff": item["diff"],
                                }
                            )
            except Exception:
                pass

        if not patches and run.patch_diff:
            diff_text = run.patch_diff.strip()
            # Extract target file path from unified diff header
            match = re.search(r"^(?:---|\+\+\+)\s+[ab]/(.+)$", diff_text, re.MULTILINE)
            path = match.group(1).strip() if match else "patched_file"
            patches.append({"file_path": path, "diff": diff_text})

        if not patches:
            raise GitHubPRError("Could not extract any valid file patches from verification run.")
        return patches

    @classmethod
    def create_pull_request(
        cls,
        db: Session,
        repository_id: int,
        verification_id: int,
        user_id: int,
        payload: PullRequestCreateRequest,
        auth_token_header: str | None = None,
        client: httpx.Client | None = None,
    ) -> PullRequestCreateResponse:
        """Create a dedicated GitHub branch, apply patch, and open a PR with strict safeguards."""
        run, report, repo = cls._validate_run_for_pr(db, repository_id, verification_id)

        # Intercept deterministic demo repositories to prevent external GitHub write calls
        from repopilot_api.demo import is_internal_demo_repository
        from repopilot_api.models import User

        is_demo_repo = is_internal_demo_repository(db, repo, user_id=user_id)

        # Guest users are strictly restricted to local demo simulation; external writes forbidden
        user = db.get(User, user_id)
        if user and getattr(user, "is_guest", False) and not is_demo_repo:
            raise GitHubPermissionError(
                "Guest users cannot create external GitHub pull requests. "
                "Please log in with a GitHub account."
            )

        # Idempotency Check: Existing DB record
        existing_pr = db.scalar(
            select(GitHubPullRequest).where(GitHubPullRequest.verification_run_id == run.id)
        )
        if existing_pr is not None:
            return PullRequestCreateResponse(
                success=True,
                pr_number=existing_pr.pr_number,
                pr_url=existing_pr.pr_url,
                branch_name=existing_pr.branch_name,
                base_branch=existing_pr.base_branch,
                status="already_exists",
                message=f"Pull request #{existing_pr.pr_number} already exists for this run.",
                is_simulated=is_demo_repo,
            )

        if is_demo_repo:
            pr_number = 101
            pr_url = f"https://github.com/{repo.owner}/{repo.name}/pull/{pr_number}"
            branch_name = (
                payload.custom_branch_name or f"repopilot/fix-issue-{run.issue_number}-{run.id}"
            ).strip()
            title = (
                payload.custom_title or "fix(calculator): resolve power operator multiplication bug"
            )
            body = payload.custom_body or (
                f"## Automated Fix by RepoPilot Dual-Sandbox Engine\n\n"
                f"- **Verdict**: `{report.verdict}`\n"
                f"- **Verification Run**: `#{run.id}`\n"
                f"- **Approved Spec Hash**: `{run.approved_spec_hash}`\n\n"
                f"{report.summary}"
            )
            record = GitHubPullRequest(
                repository_id=repository_id,
                verification_run_id=run.id,
                created_by_user_id=user_id,
                issue_number=run.issue_number,
                pr_number=pr_number,
                pr_url=pr_url,
                branch_name=branch_name,
                base_branch=repo.default_branch or "main",
                head_sha="mock_commit_sha_" + "a" * 24,
                patch_hash=run.patch_hash or "",
                title=title,
                body=body,
            )
            try:
                db.add(record)
                log_audit_event(
                    db,
                    run_id=run.id,
                    event_type=VerificationAuditEventType.PR_CREATED.value,
                    actor_user_id=user_id,
                    from_status=run.status,
                    to_status=run.status,
                    details={
                        "pr_number": pr_number,
                        "pr_url": pr_url,
                        "branch_name": branch_name,
                        "base_branch": repo.default_branch or "main",
                        "is_simulated": True,
                    },
                )
                db.commit()
            except IntegrityError:
                db.rollback()
                existing_pr = db.scalar(
                    select(GitHubPullRequest).where(GitHubPullRequest.verification_run_id == run.id)
                )
                if existing_pr is not None:
                    return PullRequestCreateResponse(
                        success=True,
                        pr_number=existing_pr.pr_number,
                        pr_url=existing_pr.pr_url,
                        branch_name=existing_pr.branch_name,
                        base_branch=existing_pr.base_branch,
                        status="already_exists",
                        message=(
                            f"Pull request #{existing_pr.pr_number} already exists for this run."
                        ),
                        is_simulated=True,
                    )
                raise
            return PullRequestCreateResponse(
                success=True,
                pr_number=pr_number,
                pr_url=pr_url,
                branch_name=branch_name,
                base_branch=repo.default_branch or "main",
                status="created",
                message=f"Simulated GitHub pull request #{pr_number} created for demo repository.",
                is_simulated=True,
            )

        token = cls._resolve_token(payload, auth_token_header)
        gh_client = GitHubPRClient(token=token, client=client)

        try:
            owner = repo.owner
            repo_name = repo.name
            base_branch = (payload.target_base_branch or repo.default_branch or "main").strip()

            safe_issue = run.issue_number
            default_branch = f"repopilot/fix-issue-{safe_issue}-{run.id}"
            branch_name = (payload.custom_branch_name or default_branch).strip()

            # 1. Resolve base branch commit SHA
            base_sha = gh_client.get_base_branch_sha(owner, repo_name, base_branch)

            # 2. Check if branch already exists
            existing_head_sha = gh_client.get_branch_ref(owner, repo_name, branch_name)
            if existing_head_sha is not None:
                existing_pr_info = gh_client.find_existing_pr(
                    owner, repo_name, branch_name, base_branch
                )
                if existing_pr_info:
                    num = int(existing_pr_info["number"])
                    url = str(existing_pr_info["html_url"])
                    record = GitHubPullRequest(
                        repository_id=repository_id,
                        verification_run_id=run.id,
                        created_by_user_id=user_id,
                        issue_number=run.issue_number,
                        pr_number=num,
                        pr_url=url,
                        branch_name=branch_name,
                        base_branch=base_branch,
                        head_sha=existing_head_sha,
                        patch_hash=run.patch_hash or "",
                        title=payload.custom_title or f"fix: resolve issue #{run.issue_number}",
                        body=payload.custom_body or report.summary,
                    )
                    db.add(record)
                    db.commit()
                    return PullRequestCreateResponse(
                        success=True,
                        pr_number=num,
                        pr_url=url,
                        branch_name=branch_name,
                        base_branch=base_branch,
                        status="already_exists",
                        message=f"Reused existing GitHub pull request #{num}.",
                    )
                branch_head_sha = existing_head_sha
            else:
                branch_head_sha = gh_client.create_branch_ref(
                    owner, repo_name, branch_name, base_sha
                )

            # 3. Apply patches to create git tree
            patches = cls._parse_patches(run)
            tree_items: list[dict[str, Any]] = []

            for p in patches:
                orig_content, _ = gh_client.get_file_content(
                    owner, repo_name, p["file_path"], branch_head_sha
                )
                try:
                    patched_content, _, _, _ = apply_unified_diff(orig_content, p["diff"])
                except PatchError as patch_err:
                    raise GitHubPRError(
                        f"Failed to apply patch to '{p['file_path']}': {patch_err}"
                    ) from patch_err

                blob_sha = gh_client.create_blob(owner, repo_name, patched_content)
                tree_items.append(
                    {
                        "path": p["file_path"],
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha,
                    }
                )

            tree_sha = gh_client.create_tree(owner, repo_name, branch_head_sha, tree_items)

            # 4. Create git commit
            commit_msg = (
                f"fix: resolve issue #{run.issue_number}\n\n"
                f"Automated fix verified by RepoPilot Dual-Sandbox Engine.\n"
                f"Verification Run: #{run.id}\n"
                f"Verdict: {report.verdict}\n"
                f"Confidence: {report.confidence}\n"
                f"Approved Spec Hash: {run.approved_spec_hash}"
            )
            new_commit_sha = gh_client.create_commit(
                owner, repo_name, commit_msg, tree_sha, branch_head_sha
            )

            # 5. Update branch reference to new commit
            gh_client.update_branch_ref(owner, repo_name, branch_name, new_commit_sha)

            # 6. Open Pull Request
            pr_title = (
                payload.custom_title or f"fix: resolve issue #{run.issue_number} (Verified Fix)"
            ).strip()
            pr_body = (
                payload.custom_body
                or (
                    f"## RepoPilot Dual-Sandbox Verification Report\n\n"
                    f"**Issue**: #{run.issue_number}\n"
                    f"**Base Commit**: `{base_sha[:8]}`\n"
                    f"**Verdict**: `{report.verdict}` (Confidence: {report.confidence})\n\n"
                    f"### Summary\n{report.summary}\n\n"
                    f"### Baseline Reproduction\n{report.baseline_summary}\n\n"
                    f"### Patched Verification\n{report.patched_summary}\n\n"
                    f"---\n"
                    f"*Verified deterministically with RepoPilot isolated Docker sandboxes.*"
                )
            ).strip()

            pr_number, pr_url = gh_client.create_pull_request(
                owner, repo_name, pr_title, pr_body, branch_name, base_branch
            )

            # 7. Persist record in database
            pr_record = GitHubPullRequest(
                repository_id=repository_id,
                verification_run_id=run.id,
                created_by_user_id=user_id,
                issue_number=run.issue_number,
                pr_number=pr_number,
                pr_url=pr_url,
                branch_name=branch_name,
                base_branch=base_branch,
                head_sha=new_commit_sha,
                patch_hash=run.patch_hash or "",
                title=pr_title,
                body=pr_body,
            )
            try:
                db.add(pr_record)
                log_audit_event(
                    db,
                    run_id=run.id,
                    event_type=VerificationAuditEventType.PR_CREATED.value,
                    actor_user_id=user_id,
                    from_status=run.status,
                    to_status=run.status,
                    details={
                        "pr_number": pr_number,
                        "pr_url": pr_url,
                        "branch_name": branch_name,
                        "base_branch": base_branch,
                        "commit_sha": new_commit_sha,
                    },
                )
                db.commit()
                db.refresh(pr_record)
            except IntegrityError:
                db.rollback()
                existing_pr = db.scalar(
                    select(GitHubPullRequest).where(GitHubPullRequest.verification_run_id == run.id)
                )
                if existing_pr is not None:
                    return PullRequestCreateResponse(
                        success=True,
                        pr_number=existing_pr.pr_number,
                        pr_url=existing_pr.pr_url,
                        branch_name=existing_pr.branch_name,
                        base_branch=existing_pr.base_branch,
                        status="already_exists",
                        message=(
                            f"Pull request #{existing_pr.pr_number} already exists for this run."
                        ),
                    )
                raise

            return PullRequestCreateResponse(
                success=True,
                pr_number=pr_number,
                pr_url=pr_url,
                branch_name=branch_name,
                base_branch=base_branch,
                status="created",
                message=f"Pull request #{pr_number} created successfully on GitHub.",
            )

        finally:
            gh_client.close()

    @classmethod
    def export_patch(
        cls, db: Session, repository_id: int, verification_id: int, user_id: int
    ) -> PatchExportResponse:
        """Provide safe offline patch export with step-by-step CLI application commands."""
        run = db.get(VerificationRun, verification_id)
        if run is None or run.repository_id != repository_id:
            raise GitHubPRError("Verification run not found.", status_code=404)

        if not run.patch_diff or not run.patch_diff.strip():
            raise GitHubPRError("Verification run contains no patch content to export.")

        report = db.scalar(select(ProofReport).where(ProofReport.verification_run_id == run.id))
        if report is None or report.verdict != ProofVerdict.VERIFIED_FIX.value:
            verdict_str = report.verdict if report else "pending"
            raise GitHubVerificationPrerequisiteError(
                f"Cannot export verified patch: verification verdict is '{verdict_str}'. "
                f"Only verified fixes qualify for verified patch export."
            )
        verdict = report.verdict

        filename = f"repopilot-fix-issue-{run.issue_number}.patch"
        branch = f"repopilot/fix-issue-{run.issue_number}"

        instructions = [
            "# 1. Fetch latest repository changes and checkout a clean branch:",
            "git fetch origin",
            f"git checkout -b {branch} origin/main",
            "# 2. Apply the verified patch deterministically:",
            f"git apply {filename}",
            "# 3. Review staged code changes:",
            "git diff",
            "# 4. Commit and push:",
            f"git commit -am 'fix: resolve issue #{run.issue_number} (RepoPilot verified)'",
            f"git push -u origin {branch}",
        ]

        log_audit_event(
            db,
            run_id=run.id,
            event_type=VerificationAuditEventType.PATCH_EXPORTED.value,
            actor_user_id=user_id,
            from_status=run.status,
            to_status=run.status,
            details={"filename": filename, "patch_hash": run.patch_hash},
        )
        db.commit()

        return PatchExportResponse(
            patch_diff=run.patch_diff,
            patch_hash=run.patch_hash or "",
            filename=filename,
            instructions=instructions,
            issue_number=run.issue_number,
            commit_sha=run.commit_sha,
            verdict=verdict,
        )

    @classmethod
    def get_pr_status(
        cls, db: Session, repository_id: int, verification_id: int
    ) -> PullRequestStatusResponse:
        """Query pull request state for a verification run."""
        existing = db.scalar(
            select(GitHubPullRequest).where(
                GitHubPullRequest.verification_run_id == verification_id,
                GitHubPullRequest.repository_id == repository_id,
            )
        )
        if existing is None:
            return PullRequestStatusResponse(has_pr=False)
        return PullRequestStatusResponse(
            has_pr=True,
            pr_number=existing.pr_number,
            pr_url=existing.pr_url,
            branch_name=existing.branch_name,
            base_branch=existing.base_branch,
            created_at=existing.created_at.isoformat(),
        )
