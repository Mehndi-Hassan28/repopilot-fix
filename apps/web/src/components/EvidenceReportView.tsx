"use client";

import { useEffect, useState } from "react";
import {
  ProofEvidence,
  ProofReportRead,
  PullRequestStatusResponse,
  VerificationRunRead,
} from "../lib/verification-types";
import { getPullRequestStatus } from "../lib/verification-api";
import { ProofVerdictBadge } from "./StatusBadges";
import { PullRequestModal } from "./PullRequestModal";
import { PatchExportModal } from "./PatchExportModal";

interface EvidenceReportViewProps {
  run: VerificationRunRead;
  report: ProofReportRead;
  csrfToken?: string;
}

export function EvidenceReportView({ run, report, csrfToken = "" }: EvidenceReportViewProps) {
  const [showLogs, setShowLogs] = useState(false);
  const [isPrModalOpen, setIsPrModalOpen] = useState(false);
  const [isExportModalOpen, setIsExportModalOpen] = useState(false);
  const [prStatus, setPrStatus] = useState<PullRequestStatusResponse | null>(null);

  const evidence = report.evidence as ProofEvidence;
  const baselinePassed = evidence.baseline_exit_code === 0;
  const patchedPassed = evidence.patched_exit_code === 0;

  const isVerified = report.verdict === "verified_fix";
  const isApproved = Boolean(run.approved_spec_hash);
  const isPrEligible = isVerified && isApproved && run.status === "completed";

  useEffect(() => {
    getPullRequestStatus(run.repository_id, run.id)
      .then((data) => setPrStatus(data))
      .catch(() => setPrStatus(null));
  }, [run.repository_id, run.id]);

  return (
    <div className="space-y-6">
      {/* 1. Header Banner */}
      <div className="rounded-2xl border border-[var(--line)] bg-[#0d141b] p-6 shadow-xl">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--line)] pb-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
                Proof-of-Fix Verdict
              </span>
              <span className="text-xs text-[var(--muted)]">· Run #{run.id}</span>
            </div>
            <div className="mt-2 flex items-center gap-3">
              <ProofVerdictBadge verdict={report.verdict} />
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wider ${
                  report.confidence === "high"
                    ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800/40"
                    : report.confidence === "medium"
                    ? "bg-amber-950/60 text-amber-300 border border-amber-800/40"
                    : "bg-rose-950/60 text-rose-300 border border-rose-800/40"
                }`}
              >
                {report.confidence} confidence
              </span>
            </div>
          </div>

          <div className="text-right text-xs text-[var(--muted)]">
            <p>Execution Duration: <span className="font-mono text-white">{run.duration_seconds.toFixed(2)}s</span></p>
            <p className="mt-1">Generated: <span className="font-mono">{new Date(report.created_at).toLocaleTimeString()}</span></p>
          </div>
        </div>

        <div className="mt-4">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
            Deterministic Verdict Summary
          </h4>
          <p className="mt-1.5 text-sm leading-relaxed text-[#c0d0d8]">
            {report.summary}
          </p>
        </div>

        {report.hypothesis && (
          <div className="mt-4 rounded-xl border border-sky-900/30 bg-sky-950/15 p-3 text-xs leading-relaxed text-sky-200">
            <span className="font-semibold text-sky-400 uppercase tracking-wider text-[10px] block mb-0.5">
              Evaluated Hypothesis
            </span>
            {report.hypothesis}
          </div>
        )}
      </div>

      {/* 1b. Pull Request & Export Actions Banner */}
      <div className="rounded-2xl border border-white/10 bg-[#0e1218] p-5 shadow-lg flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span
              className={`size-2 rounded-full ${
                prStatus?.has_pr ? "bg-[#BFFF00]" : isPrEligible ? "bg-emerald-400" : "bg-zinc-500"
              }`}
            />
            <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-zinc-400">
              GitHub Pull Request & Deployment
            </span>
          </div>
          <h4 className="mt-1 text-sm font-semibold text-white">
            {prStatus?.has_pr
              ? `Pull Request #${prStatus.pr_number} Active on GitHub`
              : isPrEligible
              ? "Verified Fix Ready for Pull Request"
              : "Pull Request Creation Gated"}
          </h4>
          <p className="mt-0.5 text-xs text-zinc-400">
            {prStatus?.has_pr
              ? `Branch: ${prStatus.branch_name} targeting ${prStatus.base_branch || "main"}.`
              : isPrEligible
              ? "Human approval confirmed and dual-sandbox verification passed. Ready to open PR."
              : !isApproved
              ? "Requires human approval before a PR can be created."
              : !isVerified
              ? "Requires a verified fix verdict before a PR can be created."
              : "Run must be in completed status."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <button
            onClick={() => setIsExportModalOpen(true)}
            className="rounded-xl border border-white/10 bg-white/5 px-3.5 py-2 text-xs font-mono text-zinc-300 hover:text-white hover:bg-white/10 transition-colors"
          >
            Export Patch (Offline)
          </button>

          {prStatus?.has_pr && prStatus.pr_url ? (
            <a
              href={prStatus.pr_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-xl bg-[#BFFF00] px-4 py-2 text-xs font-semibold text-black hover:brightness-110 shadow-lg shadow-[#BFFF00]/10 transition-all font-mono"
            >
              <span>View PR #{prStatus.pr_number}</span>
              <span>↗</span>
            </a>
          ) : (
            <button
              onClick={() => setIsPrModalOpen(true)}
              disabled={!isPrEligible}
              title={
                !isPrEligible
                  ? "Requires human approval and a verified fix verdict"
                  : "Create GitHub Pull Request"
              }
              className="rounded-xl bg-[#BFFF00] px-4 py-2 text-xs font-semibold text-black hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-lg shadow-[#BFFF00]/10"
            >
              Create GitHub PR
            </button>
          )}
        </div>
      </div>

      {/* 2. Dual Sandbox Comparison Grid */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Baseline Sandbox Card */}
        <div className="rounded-2xl border border-[var(--line)] bg-[#090e13] p-5">
          <div className="flex items-center justify-between border-b border-[var(--line)] pb-3">
            <div>
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
                Phase 1 Sandbox
              </span>
              <h4 className="font-semibold text-white">Baseline (Original Commit)</h4>
            </div>
            <span
              className={`rounded-lg px-2.5 py-1 text-xs font-mono font-bold ${
                baselinePassed
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800/50"
                  : "bg-rose-950 text-rose-300 border border-rose-800/50"
              }`}
            >
              Exit {evidence.baseline_exit_code ?? "—"}
            </span>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-[var(--muted)]">
            {report.baseline_summary || "Baseline reproduction executed against exact commit state."}
          </p>

          <div className="mt-4 grid grid-cols-3 gap-2 rounded-xl border border-[var(--line)] bg-[#0c1218] p-3 text-center text-xs">
            <div>
              <p className="text-[10px] uppercase text-[var(--muted)]">Total</p>
              <p className="mt-0.5 font-mono text-sm font-semibold">{evidence.baseline_metrics?.total ?? "—"}</p>
            </div>
            <div>
              <p className="text-[10px] uppercase text-[var(--muted)]">Passed</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-emerald-400">{evidence.baseline_metrics?.passed ?? "—"}</p>
            </div>
            <div>
              <p className="text-[10px] uppercase text-[var(--muted)]">Failed</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-rose-400">{evidence.baseline_metrics?.failed ?? "—"}</p>
            </div>
          </div>
        </div>

        {/* Patched Sandbox Card */}
        <div className="rounded-2xl border border-[var(--line)] bg-[#090e13] p-5">
          <div className="flex items-center justify-between border-b border-[var(--line)] pb-3">
            <div>
              <span className="text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
                Phase 2 Sandbox
              </span>
              <h4 className="font-semibold text-white">Patched (With AI Proposed Fix)</h4>
            </div>
            <span
              className={`rounded-lg px-2.5 py-1 text-xs font-mono font-bold ${
                patchedPassed
                  ? "bg-emerald-950 text-emerald-400 border border-emerald-800/50"
                  : "bg-rose-950 text-rose-300 border border-rose-800/50"
              }`}
            >
              Exit {evidence.patched_exit_code ?? "—"}
            </span>
          </div>

          <p className="mt-3 text-xs leading-relaxed text-[var(--muted)]">
            {report.patched_summary || "Deterministic patch applied and test re-run in isolated container."}
          </p>

          <div className="mt-4 grid grid-cols-3 gap-2 rounded-xl border border-[var(--line)] bg-[#0c1218] p-3 text-center text-xs">
            <div>
              <p className="text-[10px] uppercase text-[var(--muted)]">Total</p>
              <p className="mt-0.5 font-mono text-sm font-semibold">{evidence.patched_metrics?.total ?? "—"}</p>
            </div>
            <div>
              <p className="text-[10px] uppercase text-[var(--muted)]">Passed</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-emerald-400">{evidence.patched_metrics?.passed ?? "—"}</p>
            </div>
            <div>
              <p className="text-[10px] uppercase text-[var(--muted)]">Failed</p>
              <p className="mt-0.5 font-mono text-sm font-semibold text-rose-400">{evidence.patched_metrics?.failed ?? "—"}</p>
            </div>
          </div>
        </div>
      </div>

      {/* 3. Test Identity Delta Breakdown */}
      <div className="rounded-2xl border border-[var(--line)] bg-[#0d141b] p-5">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
          Specific Test Resolution Delta
        </h4>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-3.5">
            <span className="text-xs font-semibold text-emerald-400">
              Resolved Tests ({evidence.resolved_test_ids?.length || 0})
            </span>
            <p className="mt-1 text-[11px] text-[var(--muted)]">
              Tests that failed in baseline and now pass in patched sandbox:
            </p>
            {evidence.resolved_test_ids && evidence.resolved_test_ids.length > 0 ? (
              <ul className="mt-2 space-y-1 font-mono text-xs text-emerald-300">
                {evidence.resolved_test_ids.map((id, idx) => (
                  <li key={idx} className="truncate">✓ {id}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-[var(--muted)] italic">No individual test IDs resolved.</p>
            )}
          </div>

          <div className="rounded-xl border border-rose-900/40 bg-rose-950/20 p-3.5">
            <span className="text-xs font-semibold text-rose-400">
              New Regressions ({evidence.new_failed_test_ids?.length || 0})
            </span>
            <p className="mt-1 text-[11px] text-[var(--muted)]">
              New test failures introduced by the patch proposal:
            </p>
            {evidence.new_failed_test_ids && evidence.new_failed_test_ids.length > 0 ? (
              <ul className="mt-2 space-y-1 font-mono text-xs text-rose-300">
                {evidence.new_failed_test_ids.map((id, idx) => (
                  <li key={idx} className="truncate">✗ {id}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-emerald-400/80 italic">No regressions detected.</p>
            )}
          </div>
        </div>

        {evidence.uncertainty_reasons && evidence.uncertainty_reasons.length > 0 && (
          <div className="mt-4 rounded-xl border border-amber-900/30 bg-amber-950/15 p-3 text-xs">
            <span className="font-semibold text-amber-300 uppercase tracking-wider text-[10px]">
              Evidence Limitations & Uncertainty Reasons
            </span>
            <ul className="mt-1.5 list-inside list-disc space-y-1 text-amber-200/90">
              {evidence.uncertainty_reasons.map((reason, idx) => (
                <li key={idx}>{reason}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* 4. Collapsible Raw Sandbox Logs */}
      <div className="rounded-2xl border border-[var(--line)] bg-[#0d141b] p-5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
            Sandbox Execution Logs
          </span>
          <button
            onClick={() => setShowLogs(!showLogs)}
            className="rounded-lg border border-[var(--line)] px-3 py-1 text-xs text-[var(--muted)] hover:text-white"
          >
            {showLogs ? "Hide Logs" : "Show Full Output"}
          </button>
        </div>

        {showLogs && (
          <div className="mt-4 space-y-4">
            <div>
              <p className="text-xs font-mono text-[var(--muted)] mb-1">Baseline Sandbox Output:</p>
              <pre className="max-h-60 overflow-y-auto rounded-xl border border-[var(--line)] bg-[#060a0e] p-3 text-xs font-mono text-slate-300">
                {evidence.baseline_stdout || evidence.baseline_stderr || "(No output recorded)"}
              </pre>
            </div>
            <div>
              <p className="text-xs font-mono text-[var(--muted)] mb-1">Patched Sandbox Output:</p>
              <pre className="max-h-60 overflow-y-auto rounded-xl border border-[var(--line)] bg-[#060a0e] p-3 text-xs font-mono text-slate-300">
                {evidence.patched_stdout || evidence.patched_stderr || "(No output recorded)"}
              </pre>
            </div>
          </div>
        )}
      </div>

      {/* PR Creation Modal */}
      <PullRequestModal
        isOpen={isPrModalOpen}
        onClose={() => setIsPrModalOpen(false)}
        run={run}
        report={report}
        csrfToken={csrfToken}
        onPrCreated={(url, num) => {
          setPrStatus({
            has_pr: true,
            pr_url: url,
            pr_number: num,
            branch_name: `repopilot/fix-issue-${run.issue_number}-${run.id}`,
            base_branch: "main",
          });
        }}
        onOpenExportFallback={() => setIsExportModalOpen(true)}
      />

      {/* Patch Export Modal */}
      <PatchExportModal
        isOpen={isExportModalOpen}
        onClose={() => setIsExportModalOpen(false)}
        run={run}
      />
    </div>
  );
}
