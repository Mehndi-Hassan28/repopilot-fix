"use client";

import { useState } from "react";
import { ProofEvidence, ProofReportRead, VerificationRunRead } from "../lib/verification-types";
import { ProofVerdictBadge } from "./StatusBadges";

interface EvidenceReportViewProps {
  run: VerificationRunRead;
  report: ProofReportRead;
}

export function EvidenceReportView({ run, report }: EvidenceReportViewProps) {
  const [showLogs, setShowLogs] = useState(false);
  const evidence = report.evidence as ProofEvidence;

  const baselinePassed = evidence.baseline_exit_code === 0;
  const patchedPassed = evidence.patched_exit_code === 0;

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
    </div>
  );
}
