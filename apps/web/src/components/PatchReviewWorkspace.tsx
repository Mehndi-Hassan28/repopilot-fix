"use client";

import { useState } from "react";
import { PatchProposal, IssueAnalysisResponse } from "../lib/types";

interface PatchReviewWorkspaceProps {
  analysisResult: IssueAnalysisResponse | null;
  onLaunchVerification: () => void;
  onOpenCitation: (path: string, start: number, end: number) => void;
}

export function PatchReviewWorkspace({
  analysisResult,
  onLaunchVerification,
  onOpenCitation,
}: PatchReviewWorkspaceProps) {
  const [selectedPatchIndex, setSelectedPatchIndex] = useState(0);
  const [diffMode, setDiffMode] = useState<"unified" | "split">("unified");

  const patches: PatchProposal[] = analysisResult?.analysis.proposed_fix.patches || [];
  const activePatch = patches[selectedPatchIndex] || null;

  if (!analysisResult || patches.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto p-8 flex flex-col items-center justify-center bg-[#050505] text-center select-none">
        <div className="grid size-14 place-items-center rounded-2xl border border-white/10 bg-[#12151c] text-2xl text-[#BFFF00] shadow-xl shadow-[#BFFF00]/5">
          ⑂
        </div>
        <h3 className="mt-4 text-base font-semibold text-zinc-200">No Pending Patch for Review</h3>
        <p className="mt-1.5 max-w-md text-xs leading-relaxed text-zinc-400">
          Analyze a GitHub issue or synthesize a fix proposal to inspect side-by-side or unified code diffs before launching container verification.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col h-full bg-[#050505] overflow-hidden">
      {/* Top Review Bar */}
      <div className="border-b border-white/10 bg-[#0c0e14] px-6 py-4 flex flex-wrap items-center justify-between gap-4 shrink-0">
        <div>
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-[#BFFF00]" />
            <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
              Patch Review Workspace
            </span>
          </div>
          <h1 className="mt-1 text-lg font-semibold text-[#F5F5F5] flex items-center gap-2">
            <span>Fix Proposal for Issue #{analysisResult.issue_number}</span>
            <span className="text-xs font-mono font-normal text-zinc-400">
              ({patches.length} modified {patches.length === 1 ? "file" : "files"})
            </span>
          </h1>
        </div>

        <div className="flex items-center gap-3">
          {/* Unified / Split Toggle */}
          <div role="group" aria-label="Diff view mode" className="flex rounded-lg border border-white/10 bg-[#141822] p-0.5 text-xs font-mono">
            <button
              onClick={() => setDiffMode("unified")}
              aria-pressed={diffMode === "unified"}
              className={`rounded px-2.5 py-1 transition-colors ${
                diffMode === "unified"
                  ? "bg-white/10 text-white font-semibold"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Unified
            </button>
            <button
              onClick={() => setDiffMode("split")}
              aria-pressed={diffMode === "split"}
              className={`rounded px-2.5 py-1 transition-colors ${
                diffMode === "split"
                  ? "bg-white/10 text-white font-semibold"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              Side-by-Side
            </button>
          </div>

          {/* Launch Verification CTA */}
          <button
            onClick={onLaunchVerification}
            className="rounded-lg bg-[#BFFF00] px-4 py-2 text-xs font-semibold text-black hover:brightness-110 shadow-lg shadow-[#BFFF00]/10 transition-all font-sans"
          >
            Authorize & Verify in Sandbox →
          </button>
        </div>
      </div>

      {/* Main Review Body */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Changed Files Navigator */}
        <div className="w-72 border-r border-white/10 bg-[#090b0e] flex flex-col shrink-0 select-none">
          <div className="p-3 border-b border-white/10 text-[11px] font-mono font-semibold text-zinc-400 uppercase tracking-wider">
            Changed Files ({patches.length})
          </div>

          <div role="tablist" aria-label="Changed files" className="flex-1 overflow-y-auto p-2 space-y-1">
            {patches.map((patch, idx) => {
              const isSelected = idx === selectedPatchIndex;
              const addCount = patch.diff.split("\n").filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
              const delCount = patch.diff.split("\n").filter((l) => l.startsWith("-") && !l.startsWith("---")).length;

              return (
                <button
                  key={idx}
                  role="tab"
                  aria-selected={isSelected}
                  onClick={() => setSelectedPatchIndex(idx)}
                  className={`w-full flex items-center justify-between rounded-lg p-2.5 text-left text-xs transition-colors ${
                    isSelected
                      ? "bg-[#BFFF00]/10 border border-[#BFFF00]/30 text-white"
                      : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200 border border-transparent"
                  }`}
                >
                  <div className="min-w-0 pr-2">
                    <p className={`font-mono truncate ${isSelected ? "text-[#BFFF00]" : "text-zinc-300"}`}>
                      {patch.file_path}
                    </p>
                    {patch.explanation && (
                      <p className="mt-0.5 truncate text-[10px] text-zinc-500 font-sans">
                        {patch.explanation}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 font-mono text-[10px] shrink-0">
                    <span className="text-emerald-400">+{addCount}</span>
                    <span className="text-rose-400">-{delCount}</span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Context Badge */}
          <div className="p-3 border-t border-white/10 bg-[#0c0e14] text-[11px] font-sans text-zinc-400 space-y-1">
            <span className="text-[10px] font-mono text-zinc-500 uppercase block">Confidence</span>
            <div className="flex items-center gap-1.5">
              <span className="capitalize font-semibold text-cyan-300">
                {analysisResult.analysis.confidence}
              </span>
              <span className="text-zinc-500">·</span>
              <span className="text-zinc-400 truncate">{analysisResult.analysis.summary}</span>
            </div>
          </div>
        </div>

        {/* Right Diff Viewer */}
        <div className="flex-1 flex flex-col bg-[#07090c] overflow-hidden">
          {activePatch ? (
            <>
              {/* Diff Header */}
              <div className="border-b border-white/10 bg-[#0f1218] px-4 py-2.5 flex items-center justify-between text-xs font-mono">
                <div className="flex items-center gap-2">
                  <span className="text-[#BFFF00] font-semibold">{activePatch.file_path}</span>
                  <span className="text-zinc-500">|</span>
                  <span className="text-zinc-400 font-sans text-xs">{activePatch.explanation}</span>
                </div>
                <button
                  onClick={() => onOpenCitation(activePatch.file_path, 1, 30)}
                  className="text-[11px] text-cyan-400 hover:text-cyan-300 transition-colors"
                >
                  View in Source Tree ↗
                </button>
              </div>

              {/* Diff Content */}
              <div className="flex-1 overflow-y-auto p-4 font-mono text-xs leading-relaxed">
                {diffMode === "unified" ? (
                  <div className="space-y-0.5">
                    {activePatch.diff.split("\n").map((line, lIdx) => {
                      let style = "text-zinc-300";
                      let bg = "hover:bg-white/5";
                      let prefix = " ";

                      if (line.startsWith("+") && !line.startsWith("+++")) {
                        style = "text-emerald-300";
                        bg = "bg-emerald-950/30 border-l-2 border-emerald-500";
                        prefix = "+";
                      } else if (line.startsWith("-") && !line.startsWith("---")) {
                        style = "text-rose-300";
                        bg = "bg-rose-950/30 border-l-2 border-rose-500";
                        prefix = "-";
                      } else if (line.startsWith("@@")) {
                        style = "text-cyan-300 font-semibold";
                        bg = "bg-cyan-950/20";
                      }

                      return (
                        <div key={lIdx} className={`flex items-start px-2 py-0.5 rounded ${bg} ${style}`}>
                          <span className="w-8 shrink-0 select-none text-[10px] text-zinc-600">
                            {prefix}
                          </span>
                          <pre className="flex-1 overflow-x-auto whitespace-pre font-mono text-xs">
                            {line}
                          </pre>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  /* Split / Side-by-side View */
                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded border border-rose-900/30 bg-rose-950/10 p-3">
                      <div className="text-[10px] uppercase font-bold text-rose-400 mb-2 border-b border-rose-900/30 pb-1">
                        Original (Base Commit)
                      </div>
                      <div className="space-y-0.5">
                        {activePatch.diff
                          .split("\n")
                          .filter((l) => !l.startsWith("+") || l.startsWith("+++"))
                          .map((line, idx) => (
                            <pre
                              key={idx}
                              className={`text-xs whitespace-pre ${
                                line.startsWith("-") && !line.startsWith("---")
                                  ? "text-rose-300 bg-rose-950/40 px-1 rounded"
                                  : "text-zinc-400"
                              }`}
                            >
                              {line}
                            </pre>
                          ))}
                      </div>
                    </div>

                    <div className="rounded border border-emerald-900/30 bg-emerald-950/10 p-3">
                      <div className="text-[10px] uppercase font-bold text-emerald-400 mb-2 border-b border-emerald-900/30 pb-1">
                        Synthesized (Patched State)
                      </div>
                      <div className="space-y-0.5">
                        {activePatch.diff
                          .split("\n")
                          .filter((l) => !l.startsWith("-") || l.startsWith("---"))
                          .map((line, idx) => (
                            <pre
                              key={idx}
                              className={`text-xs whitespace-pre ${
                                line.startsWith("+") && !line.startsWith("+++")
                                  ? "text-emerald-300 bg-emerald-950/40 px-1 rounded"
                                  : "text-zinc-400"
                              }`}
                            >
                              {line}
                            </pre>
                          ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="p-8 text-center text-xs text-zinc-500 font-mono">
              Select a modified file from the left to view diff.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
