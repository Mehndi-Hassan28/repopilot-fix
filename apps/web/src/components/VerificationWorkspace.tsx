"use client";

import { useEffect, useState } from "react";
import { Repository, PatchProposal } from "../lib/types";
import { VerificationPanel } from "./VerificationPanel";
import { listVerifications } from "../lib/verification-api";
import { VerificationRunRead } from "../lib/verification-types";
import { VerificationStatusBadge } from "./StatusBadges";

interface VerificationWorkspaceProps {
  repositories: Repository[];
  activeRepo: Repository | null;
  onSelectRepo: (repo: Repository) => void;
  commitSha?: string;
  issueNumber?: number;
  patches?: PatchProposal[];
  hypothesis?: string;
  csrfToken: string;
  onNavigateToIssues: () => void;
}

export function VerificationWorkspace({
  repositories,
  activeRepo,
  onSelectRepo,
  commitSha,
  issueNumber,
  patches = [],
  hypothesis,
  csrfToken,
  onNavigateToIssues,
}: VerificationWorkspaceProps) {
  const [pastRuns, setPastRuns] = useState<VerificationRunRead[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyError, setHistoryError] = useState("");

  useEffect(() => {
    if (!activeRepo) return;
    let isMounted = true;
    setLoadingHistory(true);
    setHistoryError("");

    listVerifications(activeRepo.id)
      .then((runs) => {
        if (isMounted) setPastRuns(runs);
      })
      .catch((err) => {
        if (isMounted) {
          setHistoryError(err instanceof Error ? err.message : "Could not fetch verification runs.");
        }
      })
      .finally(() => {
        if (isMounted) setLoadingHistory(false);
      });

    return () => {
      isMounted = false;
    };
  }, [activeRepo]);

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6 bg-[#050505]">
      {/* Header */}
      <div className="border-b border-white/10 pb-5">
        <div className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-emerald-400" />
          <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
            Dual Sandbox Verification Center
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-semibold text-[#F5F5F5]">
          Verification Center
        </h1>
        <p className="mt-1 text-xs text-zinc-400">
          Run differential tests across baseline vs. patched Docker sandboxes with human approval gating.
        </p>
      </div>

      {/* Repository Selector */}
      <div className="flex items-center gap-3">
        <label className="text-xs font-mono text-zinc-400">Target Repository:</label>
        <select
          value={activeRepo?.id || ""}
          onChange={(e) => {
            const found = repositories.find((r) => r.id === Number(e.target.value));
            if (found) onSelectRepo(found);
          }}
          className="rounded-lg border border-white/10 bg-[#12151c] px-3 py-1.5 text-xs text-zinc-200 outline-none focus:border-[#BFFF00]/50 font-sans"
        >
          {repositories.map((repo) => (
            <option key={repo.id} value={repo.id}>
              {repo.full_name}
            </option>
          ))}
        </select>
      </div>

      {/* Main Verification Panel if an active patch / issue is ready */}
      {activeRepo && issueNumber && commitSha ? (
        <div className="space-y-6">
          <VerificationPanel
            repositoryId={activeRepo.id}
            commitSha={commitSha}
            issueNumber={issueNumber}
            patches={patches}
            hypothesis={hypothesis}
            csrfToken={csrfToken}
          />
        </div>
      ) : (
        <div className="rounded-2xl border border-white/10 bg-[#0d1016] p-8 space-y-6">
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 border-b border-white/10 pb-6">
            <div>
              <h2 className="text-base font-semibold text-zinc-200">
                Verification Sandbox Status for {activeRepo?.full_name || "Repository"}
              </h2>
              <p className="mt-1 text-xs text-zinc-400">
                To launch a new dual-sandbox run, an analyzed issue with a verified patch proposal is required.
              </p>
            </div>
            <button
              onClick={onNavigateToIssues}
              className="rounded-lg bg-[#BFFF00] px-4 py-2 text-xs font-semibold text-black hover:brightness-110 shadow-lg shadow-[#BFFF00]/10 transition-all font-sans"
            >
              Go to Issue Investigation →
            </button>
          </div>

          {/* Verification History for this repo */}
          <div>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                Recorded Verification Runs ({pastRuns.length})
              </h3>
              <span className="text-[10px] font-mono text-zinc-500">Dual Container Logs</span>
            </div>

            {loadingHistory ? (
              <div className="p-8 text-center text-xs text-zinc-500 font-mono">
                Loading verification history…
              </div>
            ) : historyError ? (
              <div className="p-4 rounded-lg border border-rose-900/40 bg-rose-950/20 text-xs text-rose-300 font-mono">
                {historyError}
              </div>
            ) : pastRuns.length === 0 ? (
              <div className="p-8 text-center text-xs text-zinc-500 font-mono rounded-xl border border-dashed border-white/10">
                No verification jobs recorded yet for this repository.
              </div>
            ) : (
              <div className="divide-y divide-white/5 rounded-xl border border-white/10 bg-[#090b0e] overflow-hidden">
                {pastRuns.map((run) => (
                  <div
                    key={run.id}
                    className="p-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 text-xs"
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-zinc-200 font-semibold">
                          Run #{run.id} · Issue #{run.issue_number}
                        </span>
                        <VerificationStatusBadge status={run.status} />
                      </div>
                      <p className="text-[11px] font-mono text-zinc-500 truncate max-w-lg">
                        Commit: {run.commit_sha.slice(0, 7)} · Command: {run.test_command?.join(" ") || "default"}
                      </p>
                    </div>

                    <div className="text-right text-[11px] font-mono text-zinc-400">
                      <div>Created: {new Date(run.created_at).toLocaleDateString()}</div>
                      {run.duration_seconds > 0 && (
                        <div className="font-semibold text-cyan-300">
                          Duration: {run.duration_seconds.toFixed(1)}s
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
