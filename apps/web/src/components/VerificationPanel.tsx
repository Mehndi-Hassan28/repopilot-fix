"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  approveVerification,
  cancelVerification,
  createVerification,
  executeVerification,
  getVerification,
  getVerificationAuditEvents,
  getVerificationReport,
  listVerifications,
  rejectVerification,
} from "../lib/verification-api";
import {
  PatchProposal,
  ProofReportRead,
  VerificationAuditEventRead,
  VerificationRunRead,
} from "../lib/verification-types";
import { ApprovalModal } from "./ApprovalModal";
import { AuditTimelineView } from "./AuditTimelineView";
import { EvidenceReportView } from "./EvidenceReportView";
import { VerificationStatusBadge } from "./StatusBadges";

export const PREDEFINED_TEST_PROFILES = [
  { id: "unittest", label: "unittest (Default)", cmd: ["python", "-m", "unittest", "discover"], desc: "Standard Python discover runner" },
  { id: "pytest", label: "pytest", cmd: ["pytest"], desc: "Standard pytest suite runner" },
  { id: "pytest_quiet", label: "pytest -q", cmd: ["pytest", "-q"], desc: "Compact pytest execution" },
  { id: "custom", label: "Custom command…", cmd: [], desc: "Specify custom test command and flags" },
] as const;

interface VerificationPanelProps {
  repositoryId: number;
  commitSha: string;
  issueNumber: number;
  patches: PatchProposal[];
  hypothesis?: string;
  csrfToken: string;
}

export function VerificationPanel({
  repositoryId,
  commitSha,
  issueNumber,
  patches,
  hypothesis,
  csrfToken,
}: VerificationPanelProps) {
  const [activeRun, setActiveRun] = useState<VerificationRunRead | null>(null);
  const [activeReport, setActiveReport] = useState<ProofReportRead | null>(null);
  const [auditEvents, setAuditEvents] = useState<VerificationAuditEventRead[]>([]);
  const [historyRuns, setHistoryRuns] = useState<VerificationRunRead[]>([]);
  const [isHistoryOpen, setIsHistoryOpen] = useState(false);
  const [isTimelineOpen, setIsTimelineOpen] = useState(false);
  const [isApprovalModalOpen, setIsApprovalModalOpen] = useState(false);

  const [loading, setLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [isCancelling, setIsCancelling] = useState(false);
  const [selectedProfileId, setSelectedProfileId] = useState<string>("unittest");
  const [customCommandInput, setCustomCommandInput] = useState<string>("python -m unittest discover");

  const pollingRef = useRef<NodeJS.Timeout | null>(null);

  // Load history of verification runs for this repository
  const loadHistory = useCallback(async () => {
    try {
      const runs = await listVerifications(repositoryId);
      // Filter for this issue or show all
      const issueRuns = runs.filter((r) => r.issue_number === issueNumber);
      setHistoryRuns(issueRuns.length > 0 ? issueRuns : runs);
    } catch {
      // Non-critical background fetch
    }
  }, [repositoryId, issueNumber]);

  // Load full details, report, and audit events for a selected run
  const loadRunDetails = useCallback(
    async (runId: number) => {
      try {
        const run = await getVerification(repositoryId, runId);
        setActiveRun(run);

        // Fetch report if run is completed or has report
        if (run.status === "completed" || run.baseline_exit_code !== null) {
          try {
            const report = await getVerificationReport(repositoryId, runId);
            setActiveReport(report);
          } catch {
            setActiveReport(null);
          }
        } else {
          setActiveReport(null);
        }

        // Fetch audit events
        try {
          const events = await getVerificationAuditEvents(repositoryId, runId);
          setAuditEvents(events);
        } catch {
          setAuditEvents([]);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load verification run.");
      }
    },
    [repositoryId]
  );

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  // Bounded polling for queued/running jobs (terminates when terminal state reached)
  const activeRunId = activeRun?.id;
  const activeRunStatus = activeRun?.status;

  useEffect(() => {
    if (!activeRunId || !activeRunStatus) return;

    const isRunning = activeRunStatus === "queued" || activeRunStatus === "running";
    if (!isRunning) {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
      return;
    }

    let pollAttempts = 0;
    const maxPollAttempts = 40; // Max 100s at 2.5s interval

    pollingRef.current = setInterval(async () => {
      pollAttempts += 1;
      if (pollAttempts > maxPollAttempts) {
        if (pollingRef.current) clearInterval(pollingRef.current);
        return;
      }

      try {
        const updated = await getVerification(repositoryId, activeRunId);
        setActiveRun(updated);

        if (
          ["completed", "failed", "cancelled", "timed_out", "rejected"].includes(
            updated.status
          )
        ) {
          if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
          await loadRunDetails(updated.id);
          await loadHistory();
        }
      } catch {
        if (pollingRef.current) {
          clearInterval(pollingRef.current);
          pollingRef.current = null;
        }
      }
    }, 2500);

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [activeRunId, activeRunStatus, repositoryId, loadRunDetails, loadHistory]);

  // Trigger Creation of Verification Request
  async function handleCreateVerification() {
    if (!patches || patches.length === 0) {
      setError("No patch proposals available to verify.");
      return;
    }
    setLoading(true);
    setError(null);

    const profile = PREDEFINED_TEST_PROFILES.find((p) => p.id === selectedProfileId);
    const commandTokens =
      selectedProfileId === "custom"
        ? customCommandInput.trim().split(/\s+/).filter(Boolean)
        : profile && profile.cmd.length > 0
          ? [...profile.cmd]
          : ["python", "-m", "unittest", "discover"];

    if (commandTokens.length === 0) {
      setError("Please specify a valid, non-empty test command.");
      setLoading(false);
      return;
    }

    const entrypoint = commandTokens[0];
    if (!["pytest", "python", "python3"].includes(entrypoint)) {
      setError(
        `Unauthorized test runner entrypoint '${entrypoint}'. Supported test runners: pytest, python, python3.`
      );
      setLoading(false);
      return;
    }

    try {
      const created = await createVerification(
        repositoryId,
        {
          commit_sha: commitSha,
          issue_number: issueNumber,
          test_command: commandTokens,
          patches: patches,
          hypothesis: hypothesis,
          auto_submit_approval: true,
        },
        csrfToken
      );

      setActiveRun(created);
      setIsApprovalModalOpen(true);
      await loadHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create verification request.");
    } finally {
      setLoading(false);
    }
  }

  // Human Reviewer Approval
  async function handleApprove(expectedSpecHash?: string, comment?: string) {
    if (!activeRun) return;
    setActionBusy(true);
    setError(null);

    try {
      const approved = await approveVerification(
        repositoryId,
        activeRun.id,
        {
          expected_spec_hash: expectedSpecHash,
          auto_queue: true,
          comment,
        },
        csrfToken
      );

      setActiveRun(approved);
      setIsApprovalModalOpen(false);
      await loadRunDetails(approved.id);
      await loadHistory();

      // Trigger asynchronous execution via durable manager
      try {
        await executeVerification(repositoryId, approved.id, csrfToken);
        await loadRunDetails(approved.id);
      } catch {
        // Execute may queue or run asynchronously, polling will monitor state
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve verification.");
    } finally {
      setActionBusy(false);
    }
  }

  // Human Reviewer Rejection
  async function handleReject(reason: string) {
    if (!activeRun) return;
    setActionBusy(true);
    setError(null);

    try {
      const rejected = await rejectVerification(
        repositoryId,
        activeRun.id,
        { reason },
        csrfToken
      );
      setActiveRun(rejected);
      setIsApprovalModalOpen(false);
      await loadRunDetails(rejected.id);
      await loadHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reject verification.");
    } finally {
      setActionBusy(false);
    }
  }

  // Safe Job Cancellation
  async function handleCancel() {
    if (!activeRun) return;
    setActionBusy(true);

    try {
      const cancelled = await cancelVerification(
        repositoryId,
        activeRun.id,
        { reason: cancelReason || "Cancelled by user" },
        csrfToken
      );
      setActiveRun(cancelled);
      setIsCancelling(false);
      setCancelReason("");
      await loadRunDetails(cancelled.id);
      await loadHistory();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel verification run.");
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <div className="mt-8 space-y-6">
      {/* Action Header Card */}
      <div className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-6 shadow-xl">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-[var(--accent)]" />
              <p className="text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
                Evidence-First Verification Engine
              </p>
            </div>
            <h3 className="mt-1 text-xl font-semibold text-white">
              Verify Fix & Prove What It Fixes
            </h3>
            <p className="mt-1.5 max-w-xl text-xs leading-5 text-[var(--muted)]">
              Before merging or approving, run this patch against isolated baseline and patched
              sandboxes to deterministically reproduce the bug and verify that regressions are absent.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {historyRuns.length > 0 && (
              <button
                type="button"
                onClick={() => setIsHistoryOpen(!isHistoryOpen)}
                className="rounded-xl border border-[var(--line)] bg-[#111921] px-4 py-2.5 text-xs font-medium text-[var(--muted)] hover:text-white"
              >
                {isHistoryOpen ? "Hide History" : `History (${historyRuns.length})`}
              </button>
            )}

            <button
              type="button"
              onClick={handleCreateVerification}
              disabled={loading || actionBusy}
              className="inline-flex items-center gap-2 rounded-xl bg-[var(--accent)] px-5 py-2.5 text-xs font-bold text-[#092018] shadow-lg shadow-[var(--accent)]/10 transition hover:brightness-110 disabled:opacity-50"
            >
              <span className="text-sm">⚡</span>
              {loading ? "Preparing Specification…" : "Verify Fix in Dual Sandboxes"}
            </button>
          </div>
        </div>

        {/* Test Command Profile Configuration */}
        <div className="mt-5 rounded-xl border border-[var(--line)] bg-[#090d12] p-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--accent)]">
                Sandbox Test Command Configuration
              </span>
              <p className="mt-0.5 text-xs text-[var(--muted)]">
                Choose a safe predefined test runner or specify command arguments.
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {PREDEFINED_TEST_PROFILES.map((profile) => (
                <button
                  key={profile.id}
                  type="button"
                  onClick={() => {
                    setSelectedProfileId(profile.id);
                    if (profile.id !== "custom") {
                      setCustomCommandInput(profile.cmd.join(" "));
                    }
                  }}
                  className={`rounded-lg px-2.5 py-1 text-xs font-medium transition ${
                    selectedProfileId === profile.id
                      ? "bg-[var(--accent)] text-[#092018] font-semibold"
                      : "border border-[var(--line)] bg-[#111921] text-[var(--muted)] hover:text-white"
                  }`}
                >
                  {profile.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-3">
            <div className="flex items-center gap-2">
              <label htmlFor="test-command-input" className="sr-only">
                Test Command
              </label>
              <div className="relative flex-1">
                <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center font-mono text-xs text-[var(--muted)]">
                  $
                </span>
                <input
                  id="test-command-input"
                  type="text"
                  value={
                    selectedProfileId === "custom"
                      ? customCommandInput
                      : PREDEFINED_TEST_PROFILES.find((p) => p.id === selectedProfileId)?.cmd.join(" ") || ""
                  }
                  onChange={(e) => {
                    setSelectedProfileId("custom");
                    setCustomCommandInput(e.target.value);
                  }}
                  placeholder="python -m unittest discover"
                  className="w-full rounded-xl border border-[var(--line)] bg-[#05080b] py-2 pl-7 pr-4 font-mono text-xs text-white outline-none focus:border-[var(--accent)]"
                />
              </div>
            </div>
            <p className="mt-1.5 text-[11px] text-[var(--muted)]">
              Approved entrypoints: <code className="text-emerald-400">pytest</code>,{" "}
              <code className="text-emerald-400">python -m unittest</code>,{" "}
              <code className="text-emerald-400">python -m pytest</code>. Shell metacharacters are strictly prohibited.
            </p>
          </div>
        </div>

        {error && (
          <div className="mt-4 rounded-xl border border-rose-900/50 bg-rose-950/20 p-3.5 text-xs text-rose-300">
            {error}
          </div>
        )}

        {/* Verification History Drawer */}
        {isHistoryOpen && historyRuns.length > 0 && (
          <div className="mt-5 border-t border-[var(--line)] pt-4">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
              Verification Run History
            </h4>
            <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {historyRuns.map((r) => (
                <button
                  key={r.id}
                  onClick={() => void loadRunDetails(r.id)}
                  className={`flex flex-col items-start rounded-xl border p-3 text-left transition ${
                    activeRun?.id === r.id
                      ? "border-[var(--accent)] bg-[#132227]"
                      : "border-[var(--line)] bg-[#090e13] hover:border-slate-600"
                  }`}
                >
                  <div className="flex w-full items-center justify-between text-xs">
                    <span className="font-mono font-semibold">Run #{r.id}</span>
                    <VerificationStatusBadge status={r.status} />
                  </div>
                  <p className="mt-1 truncate text-[11px] font-mono text-[var(--muted)]">
                    {r.commit_sha.slice(0, 10)} · {new Date(r.created_at).toLocaleTimeString()}
                  </p>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Active Verification Status Banner */}
      {activeRun && (
        <div className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-6 shadow-xl">
          <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--line)] pb-4">
            <div>
              <div className="flex items-center gap-3">
                <h4 className="text-base font-semibold text-white">
                  Verification Job #{activeRun.id}
                </h4>
                <VerificationStatusBadge status={activeRun.status} />
              </div>
              <p className="mt-1 font-mono text-xs text-[var(--muted)] truncate max-w-lg">
                Spec Hash: {activeRun.spec_hash}
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {activeRun.status === "pending_approval" && (
                <button
                  type="button"
                  onClick={() => setIsApprovalModalOpen(true)}
                  className="rounded-xl bg-amber-400 px-4 py-2 text-xs font-bold text-[#141209] shadow-md hover:brightness-110"
                >
                  Review & Authorize Specification
                </button>
              )}

              {["pending_approval", "approved", "queued", "running"].includes(
                activeRun.status
              ) && (
                <>
                  {!isCancelling ? (
                    <button
                      type="button"
                      onClick={() => setIsCancelling(true)}
                      className="rounded-xl border border-rose-900/60 bg-rose-950/20 px-3.5 py-2 text-xs font-semibold text-rose-300 hover:bg-rose-900/30"
                    >
                      Cancel Job…
                    </button>
                  ) : (
                    <div className="flex items-center gap-2">
                      <input
                        type="text"
                        value={cancelReason}
                        onChange={(e) => setCancelReason(e.target.value)}
                        placeholder="Cancellation reason"
                        className="rounded-lg border border-rose-900 bg-[#090d12] px-3 py-1.5 text-xs text-white outline-none"
                      />
                      <button
                        type="button"
                        onClick={handleCancel}
                        disabled={actionBusy}
                        className="rounded-lg bg-rose-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-rose-500"
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        onClick={() => setIsCancelling(false)}
                        className="rounded-lg border border-[var(--line)] px-2 py-1.5 text-xs text-[var(--muted)]"
                      >
                        ✕
                      </button>
                    </div>
                  )}
                </>
              )}

              <button
                type="button"
                onClick={() => setIsTimelineOpen(!isTimelineOpen)}
                className="rounded-xl border border-[var(--line)] bg-[#111921] px-3.5 py-2 text-xs text-[var(--muted)] hover:text-white"
              >
                {isTimelineOpen ? "Hide Audit" : "Audit Trail"}
              </button>
            </div>
          </div>

          {/* Running Indicator (Never invent progress percentages) */}
          {(activeRun.status === "queued" || activeRun.status === "running") && (
            <div className="mt-4 flex items-center gap-3 rounded-xl border border-cyan-900/40 bg-cyan-950/20 p-4">
              <span className="size-3 rounded-full bg-cyan-400 animate-ping shrink-0" />
              <div className="text-xs">
                <p className="font-semibold text-cyan-200">
                  {activeRun.status === "queued"
                    ? "Job queued in durable runner (waiting for concurrency slot)…"
                    : "Running dual-sandbox verification (Baseline reproducing -> Patched verifying)…"}
                </p>
                <p className="text-cyan-400/80 mt-0.5">
                  Real-time status updates are polled from the verification engine. No synthetic
                  progress estimates are shown.
                </p>
              </div>
            </div>
          )}

          {/* Error Message if Failed */}
          {activeRun.error_message && activeRun.status === "failed" && (
            <div className="mt-4 rounded-xl border border-rose-900/50 bg-rose-950/20 p-4 text-xs text-rose-300">
              <span className="font-semibold uppercase tracking-wider text-[10px] block mb-1">
                Execution Error
              </span>
              {activeRun.error_message}
            </div>
          )}

          {/* Rejection notice */}
          {activeRun.status === "rejected" && (
            <div className="mt-4 rounded-xl border border-rose-900/50 bg-rose-950/20 p-4 text-xs text-rose-300">
              <span className="font-semibold uppercase tracking-wider text-[10px] block mb-1">
                Rejected by Human Reviewer
              </span>
              {activeRun.rejection_reason || "Specification rejected."}
            </div>
          )}
        </div>
      )}

      {/* Evidence Report View */}
      {activeRun && activeReport && (
        <EvidenceReportView run={activeRun} report={activeReport} />
      )}

      {/* Audit Timeline */}
      {activeRun && isTimelineOpen && (
        <AuditTimelineView events={auditEvents} />
      )}

      {/* Approval Modal */}
      {activeRun && (
        <ApprovalModal
          run={activeRun}
          isOpen={isApprovalModalOpen}
          onClose={() => setIsApprovalModalOpen(false)}
          onApprove={handleApprove}
          onReject={handleReject}
          isSubmitting={actionBusy}
          error={error}
        />
      )}
    </div>
  );
}
