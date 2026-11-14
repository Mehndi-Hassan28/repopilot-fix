import { ProofVerdict, VerificationJobStatus } from "../lib/verification-types";

export function VerificationStatusBadge({ status }: { status: VerificationJobStatus }) {
  switch (status) {
    case "draft":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900/60 px-2.5 py-0.5 text-xs font-medium text-slate-300">
          <span className="size-1.5 rounded-full bg-slate-400" />
          Draft
        </span>
      );
    case "pending_approval":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-800/60 bg-amber-950/40 px-2.5 py-0.5 text-xs font-semibold text-amber-300 animate-pulse">
          <span className="size-1.5 rounded-full bg-amber-400" />
          Pending Human Approval
        </span>
      );
    case "approved":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-blue-800/60 bg-blue-950/40 px-2.5 py-0.5 text-xs font-semibold text-blue-300">
          <span className="size-1.5 rounded-full bg-blue-400" />
          Approved
        </span>
      );
    case "queued":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-purple-800/60 bg-purple-950/40 px-2.5 py-0.5 text-xs font-semibold text-purple-300">
          <span className="size-1.5 rounded-full bg-purple-400" />
          Queued
        </span>
      );
    case "running":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-800/60 bg-cyan-950/40 px-2.5 py-0.5 text-xs font-semibold text-cyan-300">
          <span className="size-1.5 rounded-full bg-cyan-400 animate-ping" />
          Running Dual Sandboxes…
        </span>
      );
    case "completed":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-800/60 bg-emerald-950/40 px-2.5 py-0.5 text-xs font-semibold text-emerald-300">
          <span className="size-1.5 rounded-full bg-emerald-400" />
          Execution Completed
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-800/60 bg-rose-950/40 px-2.5 py-0.5 text-xs font-semibold text-rose-300">
          <span className="size-1.5 rounded-full bg-rose-400" />
          Failed
        </span>
      );
    case "timed_out":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-orange-800/60 bg-orange-950/40 px-2.5 py-0.5 text-xs font-semibold text-orange-300">
          <span className="size-1.5 rounded-full bg-orange-400" />
          Timed Out
        </span>
      );
    case "cancelled":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700 bg-slate-900/60 px-2.5 py-0.5 text-xs font-semibold text-slate-400">
          <span className="size-1.5 rounded-full bg-slate-500" />
          Cancelled
        </span>
      );
    case "rejected":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-900 bg-rose-950/60 px-2.5 py-0.5 text-xs font-semibold text-rose-400">
          <span className="size-1.5 rounded-full bg-rose-500" />
          Rejected by Reviewer
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center rounded-full bg-slate-800 px-2.5 py-0.5 text-xs text-slate-300">
          {status}
        </span>
      );
  }
}

export function ProofVerdictBadge({ verdict }: { verdict: ProofVerdict }) {
  switch (verdict) {
    case "verified_fix":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/50 bg-emerald-950/60 px-3 py-1 text-xs font-bold uppercase tracking-wider text-emerald-300 shadow-md shadow-emerald-900/20">
          <span className="size-2 rounded-full bg-emerald-400" />
          Verified Fix
        </span>
      );
    case "unreproduced":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-950/50 px-3 py-1 text-xs font-bold uppercase tracking-wider text-amber-300">
          <span className="size-2 rounded-full bg-amber-400" />
          Unreproduced in Baseline
        </span>
      );
    case "fix_failed":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-500/50 bg-rose-950/60 px-3 py-1 text-xs font-bold uppercase tracking-wider text-rose-300">
          <span className="size-2 rounded-full bg-rose-400" />
          Fix Failed (Tests Still Failing)
        </span>
      );
    case "regression_detected":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-600 bg-rose-950 px-3 py-1 text-xs font-bold uppercase tracking-wider text-rose-200">
          <span className="size-2 rounded-full bg-rose-500 animate-pulse" />
          Regression Detected
        </span>
      );
    case "patch_error":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-rose-700 bg-rose-950/50 px-3 py-1 text-xs font-bold uppercase tracking-wider text-rose-300">
          <span className="size-2 rounded-full bg-rose-400" />
          Patch Conflict / Syntax Error
        </span>
      );
    case "timed_out":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-orange-500/50 bg-orange-950/50 px-3 py-1 text-xs font-bold uppercase tracking-wider text-orange-300">
          <span className="size-2 rounded-full bg-orange-400" />
          Timed Out
        </span>
      );
    case "inconclusive":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full border border-yellow-500/50 bg-yellow-950/50 px-3 py-1 text-xs font-bold uppercase tracking-wider text-yellow-300">
          <span className="size-2 rounded-full bg-yellow-400" />
          Inconclusive Evidence
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center rounded-full border border-slate-700 bg-slate-900 px-3 py-1 text-xs font-bold uppercase tracking-wider text-slate-300">
          {verdict}
        </span>
      );
  }
}
