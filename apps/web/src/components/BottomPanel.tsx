"use client";

import { useState } from "react";
import { IndexStatus } from "../lib/types";

interface BottomPanelProps {
  isExpanded: boolean;
  onToggleExpand: () => void;
  indexStatus: IndexStatus | null;
  activeCommitSha?: string;
  diagnostics?: string[];
  activeTestLogs?: string;
}

export function BottomPanel({
  isExpanded,
  onToggleExpand,
  indexStatus,
  activeCommitSha,
  diagnostics = [],
  activeTestLogs = "",
}: BottomPanelProps) {
  const [activeTab, setActiveTab] = useState<"logs" | "diagnostics" | "status">("logs");

  return (
    <footer className="border-t border-white/10 bg-[#090b0e] shrink-0 select-none z-20 flex flex-col transition-all duration-200">
      {/* Status Bar / Panel Header */}
      <div className="flex h-8 items-center justify-between px-3 text-[11px] font-mono text-zinc-400 bg-[#0c0e14]">
        <div className="flex items-center gap-4">
          <button
            onClick={onToggleExpand}
            className="flex items-center gap-1.5 text-zinc-300 hover:text-white transition-colors"
          >
            <span>{isExpanded ? "▾" : "▴"}</span>
            <span className="font-sans font-semibold text-[10px] uppercase tracking-wider text-zinc-400">
              Terminal & Diagnostics
            </span>
          </button>

          {isExpanded && (
            <div className="flex items-center gap-1">
              <button
                onClick={() => setActiveTab("logs")}
                className={`rounded px-2 py-0.5 transition-colors ${
                  activeTab === "logs" ? "bg-white/10 text-white font-semibold" : "hover:text-white"
                }`}
              >
                Sandbox Logs
              </button>
              <button
                onClick={() => setActiveTab("diagnostics")}
                className={`rounded px-2 py-0.5 transition-colors ${
                  activeTab === "diagnostics" ? "bg-white/10 text-white font-semibold" : "hover:text-white"
                }`}
              >
                Diagnostics ({diagnostics.length})
              </button>
              <button
                onClick={() => setActiveTab("status")}
                className={`rounded px-2 py-0.5 transition-colors ${
                  activeTab === "status" ? "bg-white/10 text-white font-semibold" : "hover:text-white"
                }`}
              >
                Index Details
              </button>
            </div>
          )}
        </div>

        {/* Right Info Items */}
        <div className="flex items-center gap-4 text-[10px]">
          {activeCommitSha && (
            <div className="flex items-center gap-1">
              <span className="text-zinc-500">HEAD:</span>
              <span className="text-cyan-300 font-bold">{activeCommitSha.slice(0, 7)}</span>
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <span
              className={`size-1.5 rounded-full ${
                indexStatus?.status === "ready"
                  ? "bg-[#BFFF00]"
                  : indexStatus?.status === "failed"
                  ? "bg-rose-500"
                  : "bg-zinc-500"
              }`}
            />
            <span className="capitalize">{indexStatus?.status || "Idle"}</span>
          </div>
          <span className="text-zinc-600">Docker Sandbox Isolation</span>
        </div>
      </div>

      {/* Expanded Console Content */}
      {isExpanded && (
        <div className="h-48 overflow-y-auto p-3 font-mono text-xs bg-[#06080a] text-zinc-300">
          {activeTab === "logs" && (
            <div>
              {activeTestLogs ? (
                <pre className="whitespace-pre-wrap leading-relaxed text-[11px] text-zinc-300">
                  {activeTestLogs}
                </pre>
              ) : (
                <div className="py-6 text-center text-zinc-500 text-[11px]">
                  No active container execution logs in buffer. Run tests in the Verification Center to stream output.
                </div>
              )}
            </div>
          )}

          {activeTab === "diagnostics" && (
            <div className="space-y-1">
              {diagnostics.length > 0 ? (
                diagnostics.map((diag, idx) => (
                  <div key={idx} className="flex items-start gap-2 py-0.5 text-zinc-400">
                    <span className="text-cyan-400 font-bold">›</span>
                    <span className="text-[11px] leading-relaxed">{diag}</span>
                  </div>
                ))
              ) : (
                <div className="py-6 text-center text-zinc-500 text-[11px]">
                  Zero diagnostic warnings reported. Codebase index and AST definitions are clean.
                </div>
              )}
            </div>
          )}

          {activeTab === "status" && (
            <div className="space-y-2 text-[11px] max-w-lg">
              <div className="flex justify-between border-b border-white/5 pb-1">
                <span className="text-zinc-500">Indexed Commit SHA:</span>
                <span className="text-cyan-300 font-bold">{indexStatus?.commit_sha || "None"}</span>
              </div>
              <div className="flex justify-between border-b border-white/5 pb-1">
                <span className="text-zinc-500">Indexed Files:</span>
                <span>{indexStatus?.file_count || 0} files</span>
              </div>
              <div className="flex justify-between border-b border-white/5 pb-1">
                <span className="text-zinc-500">Indexed Code Symbols:</span>
                <span>{indexStatus?.symbol_count || 0} symbols</span>
              </div>
              <div className="flex justify-between pb-1">
                <span className="text-zinc-500">Detected Languages:</span>
                <span>{indexStatus?.languages?.join(", ") || "None"}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </footer>
  );
}
