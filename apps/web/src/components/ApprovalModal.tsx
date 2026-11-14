"use client";

import { useState } from "react";
import { VerificationRunRead } from "../lib/verification-types";

interface ApprovalModalProps {
  run: VerificationRunRead;
  isOpen: boolean;
  onClose: () => void;
  onApprove: (expectedSpecHash?: string, comment?: string) => Promise<void>;
  onReject: (reason: string) => Promise<void>;
  isSubmitting: boolean;
  error?: string | null;
}

export function ApprovalModal({
  run,
  isOpen,
  onClose,
  onApprove,
  onReject,
  isSubmitting,
  error,
}: ApprovalModalProps) {
  const [comment, setComment] = useState("");
  const [rejectionReason, setRejectionReason] = useState("");
  const [isRejecting, setIsRejecting] = useState(false);

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="approval-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm animate-in fade-in duration-200"
    >
      <div className="relative w-full max-w-2xl rounded-2xl border border-[var(--line)] bg-[#0d141b] p-6 shadow-2xl">
        <div className="flex items-start justify-between border-b border-[var(--line)] pb-4">
          <div>
            <span className="text-xs font-semibold uppercase tracking-wider text-amber-400">
              Human In The Loop
            </span>
            <h2 id="approval-modal-title" className="mt-1 text-xl font-semibold text-white">
              Authorize Verification Run #{run.id}
            </h2>
          </div>
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="rounded-lg p-1.5 text-[var(--muted)] hover:bg-[#18232c] hover:text-white"
            aria-label="Close modal"
          >
            ✕
          </button>
        </div>

        <p className="mt-4 text-sm leading-6 text-[var(--muted)]">
          RepoPilot Fix requires explicit human approval before any code execution is queued.
          This approval is cryptographically bound to the exact specification below. Any alteration will invalidate this approval.
        </p>

        {error && (
          <div className="mt-4 rounded-xl border border-rose-900/50 bg-rose-950/20 p-3 text-xs text-rose-300">
            {error}
          </div>
        )}

        <div className="mt-5 space-y-3 rounded-xl border border-[var(--line)] bg-[#080d12] p-4 text-xs font-mono">
          <div className="flex items-center justify-between border-b border-[var(--line)] pb-2">
            <span className="text-[var(--muted)]">Commit SHA:</span>
            <span className="text-emerald-400">{run.commit_sha}</span>
          </div>
          <div className="flex items-center justify-between border-b border-[var(--line)] pb-2">
            <span className="text-[var(--muted)]">Patch Hash (SHA-256):</span>
            <span className="text-sky-400 truncate max-w-xs">{run.patch_hash || "Calculated"}</span>
          </div>
          <div className="flex items-center justify-between border-b border-[var(--line)] pb-2">
            <span className="text-[var(--muted)]">Test Command:</span>
            <span className="text-amber-300">{run.test_command.join(" ")}</span>
          </div>
          <div className="flex items-center justify-between border-b border-[var(--line)] pb-2">
            <span className="text-[var(--muted)]">Sandbox Image:</span>
            <span className="text-[#a0b0c0]">{run.sandbox_image}</span>
          </div>
          <div className="flex items-center justify-between pt-1">
            <span className="font-semibold text-purple-300">Immutable Spec Hash:</span>
            <span className="font-semibold text-purple-400 truncate max-w-xs">
              {run.spec_hash}
            </span>
          </div>
        </div>

        {!isRejecting ? (
          <div className="mt-5 space-y-4">
            <div>
              <label htmlFor="approval-comment" className="block text-xs font-medium text-[var(--muted)]">
                Reviewer Comment (optional)
              </label>
              <input
                id="approval-comment"
                type="text"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="e.g. Patch diff looks clean and addresses edge case"
                className="mt-1 w-full rounded-xl border border-[var(--line)] bg-[#080d12] px-3 py-2 text-xs text-white outline-none focus:border-[var(--accent)]"
              />
            </div>

            <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                type="button"
                onClick={() => setIsRejecting(true)}
                disabled={isSubmitting}
                className="rounded-xl border border-rose-900/60 bg-rose-950/20 px-4 py-2.5 text-xs font-semibold text-rose-300 hover:bg-rose-900/30"
              >
                Reject Request…
              </button>
              <button
                type="button"
                onClick={onClose}
                disabled={isSubmitting}
                className="rounded-xl border border-[var(--line)] px-4 py-2.5 text-xs text-[var(--muted)] hover:text-white"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => onApprove(run.spec_hash ?? undefined, comment)}
                disabled={isSubmitting}
                className="rounded-xl bg-emerald-500 px-5 py-2.5 text-xs font-semibold text-[#092018] shadow-lg shadow-emerald-500/20 hover:brightness-110 disabled:opacity-50"
              >
                {isSubmitting ? "Locking & Approving…" : "Approve & Enqueue Execution"}
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-5 space-y-4 animate-in fade-in duration-150">
            <div>
              <label htmlFor="rejection-reason" className="block text-xs font-medium text-rose-300">
                Reason for Rejection *
              </label>
              <textarea
                id="rejection-reason"
                required
                rows={3}
                value={rejectionReason}
                onChange={(e) => setRejectionReason(e.target.value)}
                placeholder="Explain why this specification is rejected (e.g. Unsafe test command or incorrect root cause hypothesis)"
                className="mt-1 w-full rounded-xl border border-rose-900/50 bg-[#080d12] p-3 text-xs text-white outline-none focus:border-rose-400"
              />
            </div>

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setIsRejecting(false)}
                disabled={isSubmitting}
                className="rounded-xl border border-[var(--line)] px-4 py-2 text-xs text-[var(--muted)] hover:text-white"
              >
                Back
              </button>
              <button
                type="button"
                onClick={() => onReject(rejectionReason)}
                disabled={isSubmitting || !rejectionReason.trim()}
                className="rounded-xl bg-rose-600 px-5 py-2 text-xs font-semibold text-white hover:bg-rose-500 disabled:opacity-50"
              >
                {isSubmitting ? "Rejecting…" : "Confirm Rejection"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
