"use client";

import { FormEvent, useState } from "react";
import { Repository, IssueAnalysisResponse } from "../lib/types";

interface IssueWorkspaceProps {
  repositories: Repository[];
  activeRepo: Repository | null;
  onSelectRepo: (repo: Repository) => void;
  analysisResult: IssueAnalysisResponse | null;
  onAnalyzeIssue: (repoId: number, issueNumber: number) => Promise<void>;
  analyzing: boolean;
  analysisError: string;
  onOpenCitation: (path: string, start: number, end: number) => void;
  onNavigateToPatch: () => void;
  onNavigateToVerification: () => void;
}

export function IssueWorkspace({
  repositories,
  activeRepo,
  onSelectRepo,
  analysisResult,
  onAnalyzeIssue,
  analyzing,
  analysisError,
  onOpenCitation,
  onNavigateToPatch,
  onNavigateToVerification,
}: IssueWorkspaceProps) {
  const [issueNumInput, setIssueNumInput] = useState("");

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!activeRepo) return;
    const num = parseInt(issueNumInput.trim(), 10);
    if (isNaN(num) || num < 1) return;
    void onAnalyzeIssue(activeRepo.id, num);
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6 bg-[#050505]">
      {/* Workspace Header */}
      <div className="border-b border-white/10 pb-5">
        <div className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-cyan-400" />
          <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#00F0FF]">
            Grounded Investigation Pipeline
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-semibold text-[#F5F5F5]">
          Issue Investigation Workspace
        </h1>
        <p className="mt-1 text-xs text-zinc-400">
          Formulate root cause hypotheses, extract verified source citations, and generate reviewable patch diffs.
        </p>
      </div>

      {/* Control Bar: Select Repo + Issue Number Form */}
      <div className="rounded-xl border border-white/10 bg-[#0d1016] p-4 shadow-xl">
        <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row items-end gap-3">
          <div className="w-full sm:w-1/2 space-y-1.5">
            <label className="text-[11px] font-mono text-zinc-400 uppercase">
              Target Repository
            </label>
            <select
              value={activeRepo?.id || ""}
              onChange={(e) => {
                const found = repositories.find((r) => r.id === Number(e.target.value));
                if (found) onSelectRepo(found);
              }}
              className="w-full rounded-lg border border-white/10 bg-[#141822] px-3 py-2 text-xs text-zinc-200 outline-none focus:border-[#BFFF00]/50 font-sans"
            >
              {repositories.length === 0 ? (
                <option value="">No repositories available</option>
              ) : (
                repositories.map((repo) => (
                  <option key={repo.id} value={repo.id}>
                    {repo.full_name}
                  </option>
                ))
              )}
            </select>
          </div>

          <div className="w-full sm:w-1/3 space-y-1.5">
            <label className="text-[11px] font-mono text-zinc-400 uppercase">
              GitHub Issue Number
            </label>
            <div className="relative">
              <span className="absolute left-3 top-2 text-xs text-zinc-500 font-mono">#</span>
              <input
                type="number"
                min="1"
                required
                value={issueNumInput}
                onChange={(e) => setIssueNumInput(e.target.value)}
                placeholder="e.g. 1"
                className="w-full rounded-lg border border-white/10 bg-[#141822] pl-7 pr-3 py-2 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-[#00F0FF]/50 font-mono"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={analyzing || !activeRepo}
            className="w-full sm:w-auto shrink-0 rounded-lg bg-[#00F0FF] px-5 py-2 text-xs font-semibold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
          >
            {analyzing ? "Analyzing Evidence…" : "Analyze Issue"}
          </button>
        </form>

        {analysisError && (
          <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-950/20 p-3 text-xs text-rose-300 font-mono">
            {analysisError}
          </div>
        )}
      </div>

      {/* Analysis Results View */}
      {analyzing ? (
        <div className="rounded-2xl border border-white/10 bg-[#0d1016] p-12 text-center space-y-3">
          <div className="inline-block size-8 rounded-full border-2 border-cyan-400 border-t-transparent animate-spin" />
          <h3 className="text-sm font-semibold text-zinc-200">Retrieving Grounded Repository Evidence</h3>
          <p className="text-xs text-zinc-400 max-w-md mx-auto">
            Extracting AST definitions, running BM25 index search, and formulating root-cause hypothesis against indexed commit.
          </p>
        </div>
      ) : analysisResult ? (
        <div className="space-y-6">
          {/* Issue Header Info Card */}
          <div className="rounded-2xl border border-white/10 bg-[#0d1016] p-5 shadow-xl">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-4">
              <div>
                <span className="text-[10px] font-mono text-[#00F0FF] uppercase tracking-wider">
                  Issue #{analysisResult.issue_number}
                </span>
                <h2 className="mt-1 text-lg font-semibold text-[#F5F5F5]">
                  {analysisResult.issue_title}
                </h2>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-mono uppercase font-bold border ${
                    analysisResult.is_mock ?? analysisResult.analysis.is_mock
                      ? "bg-purple-950/40 border-purple-500/40 text-purple-300"
                      : "bg-emerald-950/40 border-emerald-500/40 text-emerald-300"
                  }`}
                  title={
                    analysisResult.is_mock ?? analysisResult.analysis.is_mock
                      ? "Generated via deterministic mock provider"
                      : "Generated via live LLM provider"
                  }
                >
                  {analysisResult.is_mock ?? analysisResult.analysis.is_mock
                    ? "Mock AI Execution"
                    : "Live AI Model"}
                </span>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-[11px] font-mono uppercase font-bold border ${
                    analysisResult.analysis.confidence === "high"
                      ? "bg-lime-950/40 border-lime-500/40 text-lime-400"
                      : analysisResult.analysis.confidence === "medium"
                      ? "bg-cyan-950/40 border-cyan-500/40 text-cyan-300"
                      : "bg-amber-950/40 border-amber-500/40 text-amber-300"
                  }`}
                >
                  {analysisResult.analysis.confidence} Confidence
                </span>
                {analysisResult.issue_url && (
                  <a
                    href={analysisResult.issue_url}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded border border-white/10 bg-white/5 px-2.5 py-1 text-xs text-zinc-300 hover:text-white font-mono"
                  >
                    GitHub ↗
                  </a>
                )}
              </div>
            </div>

            <p className="mt-3 text-xs leading-relaxed text-zinc-300 font-sans whitespace-pre-wrap">
              {analysisResult.issue_body || "No issue description provided in GitHub payload."}
            </p>
          </div>

          {/* Root Cause Hypothesis Card */}
          <div className="rounded-2xl border border-amber-500/30 bg-amber-950/10 p-5">
            <div className="flex items-center gap-2">
              <span className="rounded bg-amber-500/20 border border-amber-500/40 px-2 py-0.5 text-[10px] font-mono font-bold uppercase text-amber-300">
                Hypothesis
              </span>
              <h3 className="text-xs font-semibold uppercase tracking-wider text-amber-200">
                Suspected Root Cause
              </h3>
            </div>
            <p className="mt-2.5 text-xs md:text-sm leading-relaxed text-amber-100 font-sans">
              {analysisResult.analysis.root_cause_hypothesis}
            </p>
            {analysisResult.analysis.confidence_reasoning && (
              <p className="mt-2 text-xs text-amber-200/70 font-mono">
                Reasoning: {analysisResult.analysis.confidence_reasoning}
              </p>
            )}
          </div>

          {/* Validated Files & Citations */}
          {analysisResult.analysis.relevant_files.length > 0 && (
            <div className="rounded-2xl border border-white/10 bg-[#0d1016] p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                  Relevant Files & Validated Lines ({analysisResult.analysis.relevant_files.length})
                </h3>
                <span className="text-[10px] font-mono text-zinc-500">
                  Click to inspect in Code Viewer
                </span>
              </div>

              <div className="flex flex-wrap gap-2">
                {analysisResult.analysis.relevant_files.map((file, idx) => (
                  <button
                    key={`${file.path}-${idx}`}
                    onClick={() => onOpenCitation(file.path, file.start_line, file.end_line)}
                    className="group flex items-center gap-2 rounded-lg border border-cyan-500/30 bg-cyan-950/20 px-3 py-1.5 text-xs font-mono text-zinc-300 hover:border-cyan-400 hover:text-white transition-all"
                  >
                    <span className="text-[#00F0FF]">{file.path}</span>
                    <span className="text-zinc-500">
                      L{file.start_line}–L{file.end_line}
                    </span>
                    <span className="text-[10px] text-cyan-400 group-hover:translate-x-0.5 transition-transform">
                      →
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Proposed Fix & Diff Preview */}
          <div className="rounded-2xl border border-white/10 bg-[#0d1016] p-5 shadow-xl">
            <div className="flex items-center justify-between border-b border-white/10 pb-4">
              <div>
                <span className="text-[10px] font-mono text-[#BFFF00] uppercase tracking-wider">
                  Synthesized Fix
                </span>
                <h3 className="mt-0.5 text-base font-semibold text-[#F5F5F5]">
                  Proposed Fix & Code Patch
                </h3>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={onNavigateToPatch}
                  className="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-mono text-zinc-200 hover:text-white hover:bg-white/10 transition-colors"
                >
                  Open in Patch Review →
                </button>
                <button
                  onClick={onNavigateToVerification}
                  className="rounded-lg bg-[#BFFF00] px-3 py-1.5 text-xs font-semibold text-black hover:brightness-110 transition-colors"
                >
                  Verify Fix in Docker Sandbox
                </button>
              </div>
            </div>

            <p className="mt-4 text-xs leading-relaxed text-zinc-300 font-sans">
              {analysisResult.analysis.proposed_fix.explanation}
            </p>

            {/* Diffs */}
            <div className="mt-5 space-y-4">
              {analysisResult.analysis.proposed_fix.patches.map((patch, idx) => (
                <div
                  key={`${patch.file_path}-${idx}`}
                  className="rounded-xl border border-white/10 bg-[#090b0e] overflow-hidden"
                >
                  <div className="border-b border-white/10 bg-[#10131a] px-4 py-2 flex items-center justify-between text-xs font-mono">
                    <span className="text-[#BFFF00] font-semibold">{patch.file_path}</span>
                    <span className="text-zinc-400 truncate max-w-sm">{patch.explanation}</span>
                  </div>

                  <div className="overflow-x-auto p-3 font-mono text-xs leading-5">
                    {patch.diff.split("\n").map((line, lIdx) => {
                      let style = "text-zinc-400";
                      if (line.startsWith("+") && !line.startsWith("+++")) {
                        style = "text-emerald-300 bg-emerald-950/40 -mx-3 px-3 block";
                      } else if (line.startsWith("-") && !line.startsWith("---")) {
                        style = "text-rose-300 bg-rose-950/40 -mx-3 px-3 block";
                      } else if (line.startsWith("@@")) {
                        style = "text-cyan-300 font-bold";
                      }
                      return (
                        <div key={lIdx} className={style}>
                          {line}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>

            {/* Limitations */}
            {analysisResult.analysis.limitations.length > 0 && (
              <div className="mt-5 border-t border-white/10 pt-4">
                <h4 className="text-[11px] font-mono uppercase tracking-wider text-zinc-400">
                  Limitations & Caveats
                </h4>
                <ul className="mt-2 list-inside list-disc space-y-1 text-xs text-zinc-400 font-sans">
                  {analysisResult.analysis.limitations.map((lim, lIdx) => (
                    <li key={lIdx}>{lim}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border border-dashed border-white/10 bg-[#0d1016]/40 p-12 text-center select-none">
          <div className="mx-auto grid size-12 place-items-center rounded-2xl border border-white/10 bg-white/5 text-xl text-[#00F0FF]">
            ⊙
          </div>
          <h3 className="mt-4 text-sm font-semibold text-zinc-200">Ready to Investigate Issue</h3>
          <p className="mt-1 text-xs text-zinc-400 max-w-md mx-auto">
            Select a repository and provide an issue number above to extract evidence and generate a verified root cause analysis.
          </p>
        </div>
      )}
    </div>
  );
}
