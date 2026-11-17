"use client";

import { useEffect, useRef } from "react";
import { IndexedFileItem } from "../lib/types";

interface OpenTab {
  path: string;
  content?: string;
  highlightRange?: { start: number; end: number };
}

interface CodeViewerProps {
  repoName: string;
  openTabs: OpenTab[];
  activeTabPath: string | null;
  onSelectTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  indexedFiles: IndexedFileItem[];
  evidenceSnippets?: Array<{ path: string; start_line: number; end_line: number; content: string }>;
}

export function CodeViewer({
  repoName,
  openTabs,
  activeTabPath,
  onSelectTab,
  onCloseTab,
  indexedFiles,
  evidenceSnippets = [],
}: CodeViewerProps) {
  const activeTab = openTabs.find((t) => t.path === activeTabPath);
  const activeFileItem = indexedFiles.find((f) => f.path === activeTabPath);
  const targetLineRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to highlighted range when activeTab changes with highlightRange
  useEffect(() => {
    if (activeTab?.highlightRange && targetLineRef.current) {
      targetLineRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeTab?.path, activeTab?.highlightRange]);

  // Find any evidence snippets matching this active file
  const relevantSnippets = evidenceSnippets.filter((e) => e.path === activeTabPath);

  if (openTabs.length === 0 || !activeTab) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 bg-[#0a0c0f] text-center select-none">
        <div className="grid size-14 place-items-center rounded-2xl border border-white/10 bg-[#12151c] text-2xl text-[#BFFF00] shadow-xl shadow-[#BFFF00]/5">
          ⌥
        </div>
        <h3 className="mt-4 text-base font-semibold text-zinc-200">No File Opened in Workspace</h3>
        <p className="mt-1.5 max-w-md text-xs leading-relaxed text-zinc-400">
          Select an indexed file from the Explorer tree, or click any verified citation badge in the AI chat or issue analysis to preview source code.
        </p>
        <div className="mt-6 flex items-center gap-3 text-[11px] font-mono text-zinc-500">
          <span className="rounded border border-white/10 bg-white/5 px-2 py-1">⌘K Command Palette</span>
          <span className="rounded border border-white/10 bg-white/5 px-2 py-1">Click [file:lines] to Jump</span>
        </div>
      </div>
    );
  }

  const pathParts = activeTab.path.split("/");

  return (
    <div className="flex h-full flex-col bg-[#07090c] overflow-hidden">
      {/* Tab Bar */}
      <div className="flex items-center border-b border-white/10 bg-[#0e1117] overflow-x-auto scrollbar-none select-none">
        {openTabs.map((tab) => {
          const isActive = tab.path === activeTabPath;
          const name = tab.path.split("/").pop();
          return (
            <div
              key={tab.path}
              onClick={() => onSelectTab(tab.path)}
              className={`group flex items-center gap-2 border-r border-white/10 px-3.5 py-2 text-xs cursor-pointer transition-colors ${
                isActive
                  ? "bg-[#07090c] border-t-2 border-t-[#BFFF00] text-[#F5F5F5] font-medium"
                  : "bg-[#0e1117] text-zinc-400 hover:bg-white/5 hover:text-zinc-200 border-t-2 border-t-transparent"
              }`}
            >
              <span className="font-mono text-[11px] truncate max-w-[160px]">{name}</span>
              {tab.highlightRange && (
                <span className="rounded bg-cyan-950/80 border border-cyan-500/30 px-1 text-[9px] font-mono text-cyan-300">
                  L{tab.highlightRange.start}–{tab.highlightRange.end}
                </span>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onCloseTab(tab.path);
                }}
                className="ml-1 text-zinc-500 hover:text-rose-400 text-xs transition-colors rounded p-0.5"
                title="Close tab"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>

      {/* Breadcrumbs Bar */}
      <div className="flex items-center justify-between border-b border-white/5 bg-[#090b0e] px-4 py-1.5 text-xs text-zinc-400 select-none">
        <div className="flex items-center gap-1.5 font-mono text-[11px] truncate">
          <span className="text-zinc-500">{repoName}</span>
          <span className="text-zinc-600">/</span>
          {pathParts.map((part, idx) => (
            <span key={idx} className="flex items-center gap-1.5">
              <span className={idx === pathParts.length - 1 ? "text-[#F5F5F5] font-semibold" : "text-zinc-400"}>
                {part}
              </span>
              {idx < pathParts.length - 1 && <span className="text-zinc-600">/</span>}
            </span>
          ))}
        </div>

        <div className="flex items-center gap-3 shrink-0 font-mono text-[10px] text-zinc-500">
          {activeFileItem && (
            <>
              <span className="uppercase text-cyan-400/80">{activeFileItem.language}</span>
              <span>{(activeFileItem.size_bytes / 1024).toFixed(1)} KB</span>
              <span>{activeFileItem.symbols?.length || 0} symbols</span>
            </>
          )}
        </div>
      </div>

      {/* Code / Content Area */}
      <div className="flex-1 overflow-y-auto font-mono text-xs leading-relaxed p-4">
        {/* If we have direct content or evidence content */}
        {activeTab.content ? (
          <div className="space-y-0.5">
            {activeTab.content.split("\n").map((line, idx) => {
              const lineNum = idx + 1;
              const isHighlighted =
                activeTab.highlightRange &&
                lineNum >= activeTab.highlightRange.start &&
                lineNum <= activeTab.highlightRange.end;

              return (
                <div
                  key={lineNum}
                  ref={lineNum === activeTab.highlightRange?.start ? targetLineRef : undefined}
                  className={`flex items-start gap-4 px-2 py-0.5 rounded transition-colors ${
                    isHighlighted
                      ? "bg-[#BFFF00]/15 border-l-2 border-[#BFFF00] text-lime-100"
                      : "hover:bg-white/5 text-zinc-300"
                  }`}
                >
                  <span
                    className={`w-10 shrink-0 text-right select-none text-[11px] ${
                      isHighlighted ? "text-[#BFFF00] font-bold" : "text-zinc-600"
                    }`}
                  >
                    {lineNum}
                  </span>
                  <pre className="flex-1 overflow-x-auto whitespace-pre font-mono text-xs">{line}</pre>
                </div>
              );
            })}
          </div>
        ) : relevantSnippets.length > 0 ? (
          /* Render verified evidence snippets */
          <div className="space-y-6 max-w-4xl">
            <div className="rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3 text-xs text-cyan-200">
              <span className="font-bold text-[#00F0FF]">Verified Evidence Snippet:</span> Displaying indexed code lines verified by RepoPilot retrieval engine.
            </div>

            {relevantSnippets.map((snippet, sIdx) => (
              <div key={sIdx} className="rounded-xl border border-white/10 bg-[#0d1015] overflow-hidden">
                <div className="border-b border-white/10 bg-[#12151c] px-4 py-2 flex items-center justify-between text-[11px] text-zinc-400">
                  <span className="text-cyan-300 font-semibold">
                    Lines {snippet.start_line} – {snippet.end_line}
                  </span>
                  <span className="text-zinc-500">Indexed ground truth</span>
                </div>
                <div className="p-3 bg-[#080a0d]">
                  {snippet.content.split("\n").map((line, lineIdx) => {
                    const lineNum = snippet.start_line + lineIdx;
                    return (
                      <div key={lineNum} className="flex items-start gap-4 py-0.5">
                        <span className="w-10 shrink-0 text-right text-zinc-600 select-none text-[11px]">
                          {lineNum}
                        </span>
                        <pre className="flex-1 overflow-x-auto text-zinc-200 whitespace-pre font-mono text-xs">
                          {line}
                        </pre>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        ) : (
          /* If only indexed symbols are available, display symbol map cleanly */
          <div className="max-w-3xl space-y-4">
            <div className="rounded-xl border border-white/10 bg-[#0f1218] p-4">
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">
                <span className="size-2 rounded-full bg-[#BFFF00]" />
                <span>Indexed Code Intelligence</span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-zinc-300 font-sans">
                Source file <code className="text-[#BFFF00] font-mono">{activeTab.path}</code> is indexed in the repository database ({activeFileItem?.size_bytes || 0} bytes, {activeFileItem?.language || "plain"}).
              </p>
              {activeTab.highlightRange && (
                <div className="mt-3 rounded-lg border border-cyan-500/30 bg-cyan-950/20 p-2.5 text-xs text-cyan-200 font-sans">
                  Target Citation Line Range: <span className="font-mono font-bold text-cyan-300">L{activeTab.highlightRange.start}–L{activeTab.highlightRange.end}</span>
                </div>
              )}
            </div>

            {/* Symbols list */}
            {activeFileItem?.symbols && activeFileItem.symbols.length > 0 ? (
              <div className="rounded-xl border border-white/10 bg-[#0c0e14] overflow-hidden">
                <div className="border-b border-white/10 bg-[#12151b] px-4 py-2.5 text-xs font-semibold text-zinc-300 font-sans">
                  Indexed Symbols & Definitions ({activeFileItem.symbols.length})
                </div>
                <div className="divide-y divide-white/5">
                  {activeFileItem.symbols.map((sym, idx) => (
                    <div
                      key={idx}
                      className="flex items-center justify-between px-4 py-2.5 text-xs hover:bg-white/5 transition-colors"
                    >
                      <div className="flex items-center gap-3 font-mono">
                        <span className="rounded bg-white/5 border border-white/10 px-1.5 py-0.5 text-[10px] uppercase text-zinc-400">
                          {sym.kind}
                        </span>
                        <span className="text-[#F5F5F5] font-medium">{sym.name}</span>
                      </div>
                      <span className="font-mono text-zinc-500 text-xs">Line {sym.line}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-white/10 p-6 text-center text-xs text-zinc-500 font-sans">
                No individual symbols extracted for this file. It is indexed for full-text semantic repository search.
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
