import { describe, it } from "node:test";
import assert from "node:assert/strict";
import type { ProofReportRead, VerificationRunRead } from "./verification-types.ts";
import { formatApiDetail, formatErrorMessage } from "./error-utils.ts";

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
      assert.equal(isPullRequestEligible(run, { verdict: "timed_out" as const }), false);
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

  describe("formatApiDetail", () => {
    it("returns string detail unchanged", () => {
      assert.equal(formatApiDetail("Authentication failed"), "Authentication failed");
    });

    it("formats FastAPI 422 validation error arrays cleanly without [object Object]", () => {
      const fastApiDetail = [
        {
          type: "value_error",
          loc: ["body", "custom_branch_name"],
          msg: "Value error, Invalid Git branch name: 'fix/issue-1-power-calc'",
          input: "fix/issue-1-power-calc",
        },
      ];
      const result = formatApiDetail(fastApiDetail);
      assert.equal(
        result,
        "custom_branch_name: Value error, Invalid Git branch name: 'fix/issue-1-power-calc'"
      );
      assert.ok(!result.includes("[object Object]"));
    });

    it("formats multiple validation errors delimited by semicolon", () => {
      const fastApiDetail = [
        { loc: ["body", "title"], msg: "Field required" },
        { loc: ["body", "branch"], msg: "Invalid branch" },
      ];
      const result = formatApiDetail(fastApiDetail);
      assert.equal(result, "title: Field required; branch: Invalid branch");
    });

    it("formats object detail with msg or message safely", () => {
      assert.equal(formatApiDetail({ msg: "Custom error message" }), "Custom error message");
      assert.equal(formatApiDetail({ message: "Second message format" }), "Second message format");
    });
  });

  describe("formatErrorMessage", () => {
    it("extracts clean message from Error instance", () => {
      const err = new Error("Something went wrong");
      assert.equal(formatErrorMessage(err, "Fallback"), "Something went wrong");
    });

    it("replaces [object Object] message with detail array from error object", () => {
      const err = new Error("[object Object]") as Error & { detail?: unknown };
      err.detail = [
        { loc: ["body", "custom_branch_name"], msg: "Invalid Git branch name" },
      ];
      const result = formatErrorMessage(err, "Fallback");
      assert.equal(result, "custom_branch_name: Invalid Git branch name");
      assert.ok(!result.includes("[object Object]"));
    });

    it("formats plain object error with detail", () => {
      const objErr = {
        detail: [{ loc: ["body", "token"], msg: "Token expired" }],
      };
      assert.equal(formatErrorMessage(objErr, "Fallback"), "token: Token expired");
    });

    it("translates queued state error to friendly worker explanation", () => {
      const err = new Error("No Proof-of-Fix report found for verification run 1 (current state: 'queued').");
      const result = formatErrorMessage(err, "Fallback");
      assert.ok(result.includes("Verification job is queued awaiting an available Docker worker"));
    });

    it("uses fallback string when error is empty or undefined", () => {
      assert.equal(formatErrorMessage(null, "Safe fallback"), "Safe fallback");
      assert.equal(formatErrorMessage(undefined, "Safe fallback"), "Safe fallback");
    });
  });
});
