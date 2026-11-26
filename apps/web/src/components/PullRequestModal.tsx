"use client";

import { FormEvent, useState } from "react";
import { ProofEvidence, ProofReportRead, VerificationRunRead } from "../lib/verification-types";
import { createPullRequest } from "../lib/verification-api";
import { useFocusTrap } from "../lib/useFocusTrap";

interface PullRequestModalProps {
  isOpen: boolean;
  onClose: () => void;
  run: VerificationRunRead;
  report: ProofReportRead;
  csrfToken: string;
  onPrCreated?: (prUrl: string, prNumber: number) => void;
  onOpenExportFallback?: () => void;
}

export function PullRequestModal({
  isOpen,
  onClose,
  run,
  report,
  csrfToken,
  onPrCreated,
  onOpenExportFallback,
}: PullRequestModalProps) {
  const defaultBranch = `repopilot/fix-issue-${run.issue_number}-${run.id}`;
  const defaultTitle = `fix: resolve issue #${run.issue_number} (Verified Fix)`;

  const [branchName, setBranchName] = useState(defaultBranch);
  const [baseBranch, setBaseBranch] = useState("");
  const [title, setTitle] = useState(defaultTitle);
  const [githubToken, setGithubToken] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successPr, setSuccessPr] = useState<{ url: string; number: number } | null>(null);

  const modalRef = useFocusTrap({
    isOpen,
    onClose: () => {
      if (!isSubmitting) onClose();
    },
    closeOnEscape: !isSubmitting,
  });

  if (!isOpen) return null;

  const isVerified = report.verdict === "verified_fix";
  const evidenceSpec = (report.evidence as ProofEvidence)?.approved_spec_hash;
  const isApproved = Boolean(run.approved_spec_hash || evidenceSpec);
  const isPrEligible = isVerified && isApproved && run.status === "completed";

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!isPrEligible) return;

    setIsSubmitting(true);
    setError(null);

    try {
      const resp = await createPullRequest(
        run.repository_id,
        run.id,
        {
          custom_branch_name: branchName.trim() || undefined,
          target_base_branch: baseBranch.trim() || undefined,
          custom_title: title.trim() || undefined,
          github_token: githubToken.trim() || undefined,
        },
        csrfToken
      );

      if (resp.pr_url && resp.pr_number) {
        setSuccessPr({ url: resp.pr_url, number: resp.pr_number });
        if (onPrCreated) {
          onPrCreated(resp.pr_url, resp.pr_number);
        }
      } else {
        throw new Error(resp.message || "Failed to retrieve pull request URL.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create pull request on GitHub.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="pr-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
    >
      <div
        ref={modalRef}
        className="relative w-full max-w-xl rounded-2xl border border-white/10 bg-[#0d1218] p-6 shadow-2xl flex flex-col"
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/10 pb-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-[#BFFF00] animate-pulse" aria-hidden="true" />
              <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
                Verified Pull Request Creation
              </span>
            </div>
            <h3 id="pr-modal-title" className="mt-1 text-base font-semibold text-white">
              Create Pull Request for Issue #{run.issue_number}
            </h3>
            <p className="mt-1 text-xs text-zinc-400">
              Apply the human-approved fix and open an automated PR against the repository base branch.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-lg p-1 text-zinc-400 hover:text-white hover:bg-white/5 transition-colors font-mono text-sm"
          >
            ✕
          </button>
        </div>

        {/* Success View */}
        {successPr ? (
          <div className="py-6 space-y-4">
            <div className="rounded-xl border border-emerald-900/50 bg-emerald-950/20 p-4 text-center space-y-2">
              <span className="text-emerald-400 text-lg block">✓</span>
              <h4 className="text-sm font-semibold text-emerald-300">
                Pull Request #{successPr.number} Opened Successfully
              </h4>
              <p className="text-xs text-zinc-400">
                The verified patch was committed to <code className="text-white font-mono">{branchName}</code> and the pull request is active on GitHub.
              </p>
            </div>

            <div className="flex justify-center gap-3 pt-2">
              <button
                onClick={onClose}
                className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
              >
                Close
              </button>
              <a
                href={successPr.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-xl bg-[#BFFF00] px-5 py-2 text-xs font-semibold text-black hover:brightness-110 shadow-lg shadow-[#BFFF00]/10 transition-all"
              >
                <span>View Pull Request #{successPr.number} on GitHub</span>
                <span>↗</span>
              </a>
            </div>
          </div>
        ) : (
          /* Form View */
          <form onSubmit={handleSubmit} className="py-4 space-y-4">
            {/* Preconditions summary */}
            <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
              <div className="rounded-lg border border-white/5 bg-[#090b0e] p-2 flex items-center justify-between">
                <span className="text-zinc-500">Human Approval:</span>
                <span className={isApproved ? "text-emerald-400 font-semibold" : "text-rose-400"}>
                  {isApproved ? "✓ Approved" : "✗ Missing"}
                </span>
              </div>
              <div className="rounded-lg border border-white/5 bg-[#090b0e] p-2 flex items-center justify-between">
                <span className="text-zinc-500">Dual Verdict:</span>
                <span className={isVerified ? "text-emerald-400 font-semibold" : "text-rose-400"}>
                  {report.verdict}
                </span>
              </div>
            </div>

            {!isPrEligible && (
              <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 p-3 text-xs text-rose-300">
                PR creation is disabled until human approval is confirmed and dual sandbox verification yields a verified fix.
              </div>
            )}

            {error && (
              <div className="rounded-xl border border-rose-900/50 bg-rose-950/20 p-3 text-xs text-rose-300 space-y-2">
                <p className="font-semibold text-rose-200">GitHub PR Creation Failed</p>
                <p>{error}</p>
                {onOpenExportFallback && (
                  <button
                    type="button"
                    onClick={() => {
                      onClose();
                      onOpenExportFallback();
                    }}
                    className="mt-1 text-xs text-cyan-300 underline hover:text-cyan-200 block"
                  >
                    Switch to Manual Patch Export Fallback →
                  </button>
                )}
              </div>
            )}

            {/* Branch Name Field */}
            <div className="space-y-1">
              <label className="text-xs font-mono text-zinc-300 block">
                Dedicated Branch Name
              </label>
              <input
                type="text"
                required
                value={branchName}
                onChange={(e) => setBranchName(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-[#121620] px-3.5 py-2 text-xs text-white font-mono outline-none focus:border-[#BFFF00]/50"
              />
            </div>

            {/* Title Field */}
            <div className="space-y-1">
              <label className="text-xs font-mono text-zinc-300 block">
                Pull Request Title
              </label>
              <input
                type="text"
                required
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-[#121620] px-3.5 py-2 text-xs text-white outline-none focus:border-[#BFFF00]/50"
              />
            </div>

            {/* Target Base Branch Override */}
            <div className="space-y-1">
              <label className="text-xs font-mono text-zinc-300 block">
                Base Branch <span className="text-zinc-500">(Leave blank to use default branch)</span>
              </label>
              <input
                type="text"
                placeholder="e.g. main"
                value={baseBranch}
                onChange={(e) => setBaseBranch(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-[#121620] px-3.5 py-2 text-xs text-white font-mono outline-none focus:border-[#BFFF00]/50"
              />
            </div>

            {/* Optional GitHub Token */}
            <div className="space-y-1">
              <label className="text-xs font-mono text-zinc-300 block">
                GitHub Token <span className="text-zinc-500">(Optional if server GITHUB_TOKEN is configured)</span>
              </label>
              <input
                type="password"
                placeholder="ghp_… (Never logged or stored unencrypted)"
                value={githubToken}
                onChange={(e) => setGithubToken(e.target.value)}
                className="w-full rounded-xl border border-white/10 bg-[#121620] px-3.5 py-2 text-xs text-white font-mono outline-none focus:border-[#BFFF00]/50"
              />
            </div>

            {/* Actions */}
            <div className="flex items-center justify-between pt-4 border-t border-white/10">
              {onOpenExportFallback ? (
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    onOpenExportFallback();
                  }}
                  className="text-xs text-zinc-400 hover:text-white font-mono transition-colors"
                >
                  Export Patch instead
                </button>
              ) : <div />}

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!isPrEligible || isSubmitting}
                  className="rounded-xl bg-[#BFFF00] px-5 py-2 text-xs font-semibold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
                >
                  {isSubmitting ? "Opening PR on GitHub…" : "Open Pull Request"}
                </button>
              </div>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
