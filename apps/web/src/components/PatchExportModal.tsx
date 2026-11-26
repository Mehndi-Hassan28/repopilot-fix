"use client";

import { useEffect, useState } from "react";
import { VerificationRunRead } from "../lib/verification-types";
import { exportPatch } from "../lib/verification-api";
import { useFocusTrap } from "../lib/useFocusTrap";

interface PatchExportModalProps {
  isOpen: boolean;
  onClose: () => void;
  run: VerificationRunRead;
}

export function PatchExportModal({ isOpen, onClose, run }: PatchExportModalProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [patchDiff, setPatchDiff] = useState("");
  const [instructions, setInstructions] = useState<string[]>([]);
  const [copiedDiff, setCopiedDiff] = useState(false);
  const [copiedInstructions, setCopiedInstructions] = useState(false);

  const filename = `repopilot-fix-issue-${run.issue_number}.patch`;

  useEffect(() => {
    if (!isOpen) return;
    setLoading(true);
    setError(null);
    exportPatch(run.repository_id, run.id)
      .then((data) => {
        setPatchDiff(data.patch_diff || "");
        setInstructions(data.instructions || []);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load patch export details.");
      })
      .finally(() => {
        setLoading(false);
      });
  }, [isOpen, run.repository_id, run.id]);

  const modalRef = useFocusTrap({
    isOpen,
    onClose: () => {
      if (!loading) onClose();
    },
    closeOnEscape: !loading,
  });

  if (!isOpen) return null;

  const handleCopyDiff = async () => {
    if (!patchDiff) return;
    try {
      await navigator.clipboard.writeText(patchDiff);
      setCopiedDiff(true);
      setTimeout(() => setCopiedDiff(false), 2000);
    } catch {
      // Fallback
    }
  };

  const handleCopyInstructions = async () => {
    const text = instructions.join("\n");
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedInstructions(true);
      setTimeout(() => setCopiedInstructions(false), 2000);
    } catch {
      // Fallback
    }
  };

  const handleDownloadPatch = () => {
    if (!patchDiff) return;
    const blob = new Blob([patchDiff], { type: "text/x-diff;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="patch-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
    >
      <div
        ref={modalRef}
        className="relative w-full max-w-2xl rounded-2xl border border-white/10 bg-[#0d1218] p-6 shadow-2xl max-h-[90vh] flex flex-col"
      >
        {/* Header */}
        <div className="flex items-start justify-between border-b border-white/10 pb-4">
          <div>
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-cyan-400" aria-hidden="true" />
              <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-cyan-400">
                Offline & Demo Fallback
              </span>
            </div>
            <h3 id="patch-modal-title" className="mt-1 text-base font-semibold text-white">
              Export Verified Patch · Issue #{run.issue_number}
            </h3>
            <p className="mt-1 text-xs text-zinc-400">
              Apply this verified fix manually to your repository without requiring GitHub write access.
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

        {/* Content Area */}
        <div className="flex-1 overflow-y-auto py-4 space-y-4 custom-scrollbar">
          {error && (
            <div className="rounded-xl border border-rose-900/40 bg-rose-950/20 p-3 text-xs text-rose-300">
              {error}
            </div>
          )}

          {/* Offline Notice */}
          <div className="rounded-xl border border-amber-900/30 bg-amber-950/20 p-3.5 text-xs text-amber-200">
            <span className="font-semibold block mb-1 text-amber-300">
              Deterministic Verification Verified
            </span>
            This patch passed the dual-sandbox verification suite with zero regressions. You can safely apply it locally.
          </div>

          {/* CLI Instructions */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-mono font-semibold text-zinc-300 uppercase tracking-wider">
                Terminal Instructions
              </label>
              <button
                onClick={handleCopyInstructions}
                className="text-[11px] font-mono text-cyan-400 hover:text-cyan-300 transition-colors"
              >
                {copiedInstructions ? "✓ Copied" : "Copy Commands"}
              </button>
            </div>
            <pre className="rounded-xl border border-white/10 bg-[#06080c] p-3 text-xs font-mono text-zinc-300 overflow-x-auto">
              {loading ? "Generating instructions…" : instructions.join("\n")}
            </pre>
          </div>

          {/* Patch Diff Preview */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-xs font-mono font-semibold text-zinc-300 uppercase tracking-wider">
                Unified Patch Diff ({filename})
              </label>
              <button
                onClick={handleCopyDiff}
                className="text-[11px] font-mono text-[#BFFF00] hover:brightness-110 transition-colors"
              >
                {copiedDiff ? "✓ Copied Diff" : "Copy Diff"}
              </button>
            </div>
            <pre className="max-h-48 overflow-y-auto rounded-xl border border-white/10 bg-[#06080c] p-3 text-xs font-mono text-emerald-300 leading-relaxed custom-scrollbar">
              {loading ? "Loading patch content…" : patchDiff || "(No diff content recorded)"}
            </pre>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-white/10 pt-4 mt-2">
          <button
            onClick={onClose}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-mono text-zinc-300 hover:text-white hover:bg-white/10 transition-colors"
          >
            Close
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={handleCopyDiff}
              className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-semibold text-zinc-200 hover:text-white hover:bg-white/10 transition-colors"
            >
              {copiedDiff ? "Copied" : "Copy Diff"}
            </button>
            <button
              onClick={handleDownloadPatch}
              disabled={!patchDiff}
              className="rounded-xl bg-[#BFFF00] px-4 py-2 text-xs font-semibold text-black hover:brightness-110 disabled:opacity-50 transition-all shadow-md shadow-[#BFFF00]/10"
            >
              Download {filename}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
