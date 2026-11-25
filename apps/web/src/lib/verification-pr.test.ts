import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { ProofReportRead, VerificationRunRead } from "./verification-types.ts";

function isPullRequestEligible(
  run: Pick<VerificationRunRead, "status" | "approved_spec_hash">,
  report: Pick<ProofReportRead, "verdict">
): boolean {
  const isVerified = report.verdict === "verified_fix";
  const isApproved = Boolean(run.approved_spec_hash);
  const isCompleted = run.status === "completed";
  return isVerified && isApproved && isCompleted;
}

function formatPatchFilename(issueNumber: number): string {
  return `repopilot-fix-issue-${issueNumber}.patch`;
}

function formatBranchName(issueNumber: number, runId: number, customBranch?: string): string {
  if (customBranch && customBranch.trim()) {
    return customBranch.trim();
  }
  return `repopilot/fix-issue-${issueNumber}-${runId}`;
}

describe("Verification PR & Export Utilities", () => {
  describe("isPullRequestEligible", () => {
    it("returns true only when completed, approved, and verdict is verified_fix", () => {
      const run = { status: "completed" as const, approved_spec_hash: "spec123" };
      const report = { verdict: "verified_fix" as const };
      assert.equal(isPullRequestEligible(run, report), true);
    });

    it("returns false if human approval is missing", () => {
      const run = { status: "completed" as const, approved_spec_hash: null };
      const report = { verdict: "verified_fix" as const };
      assert.equal(isPullRequestEligible(run, report), false);
    });

    it("returns false if verdict is not verified_fix", () => {
      const run = { status: "completed" as const, approved_spec_hash: "spec123" };
      assert.equal(isPullRequestEligible(run, { verdict: "fix_failed" as const }), false);
      assert.equal(isPullRequestEligible(run, { verdict: "unreproduced" as const }), false);
      assert.equal(isPullRequestEligible(run, { verdict: "inconclusive" as const }), false);
      assert.equal(isPullRequestEligible(run, { verdict: "timeout" as const }), false);
      assert.equal(isPullRequestEligible(run, { verdict: "error" as const }), false);
    });

    it("returns false if run status is running or pending_approval", () => {
      const runRunning = { status: "running" as const, approved_spec_hash: "spec123" };
      const runPending = { status: "pending_approval" as const, approved_spec_hash: null };
      const report = { verdict: "verified_fix" as const };
      assert.equal(isPullRequestEligible(runRunning, report), false);
      assert.equal(isPullRequestEligible(runPending, report), false);
    });
  });

  describe("formatPatchFilename", () => {
    it("generates safe patch filenames with issue numbers", () => {
      assert.equal(formatPatchFilename(42), "repopilot-fix-issue-42.patch");
      assert.equal(formatPatchFilename(101), "repopilot-fix-issue-101.patch");
    });
  });

  describe("formatBranchName", () => {
    it("generates default dedicated branch name", () => {
      assert.equal(formatBranchName(42, 7), "repopilot/fix-issue-42-7");
    });

    it("respects custom branch names when provided", () => {
      assert.equal(
        formatBranchName(42, 7, "custom/hotfix-branch"),
        "custom/hotfix-branch"
      );
    });
  });
});
