"use client";

import { useEffect, useRef, useState, useMemo } from "react";
import { IndexedFileItem, OpenTab, CodeSymbolItem } from "../lib/types";

interface CodeViewerProps {
  repoName: string;
  openTabs: OpenTab[];
  activeTabPath: string | null;
  onSelectTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  onClearHighlight?: (path: string) => void;
  onSelectSymbol?: (path: string, line: number) => void;
  indexedFiles: IndexedFileItem[];
  evidenceSnippets?: Array<{ path: string; start_line: number; end_line: number; content: string }>;
}

const KEYWORDS = new Set([
  "def", "class", "return", "import", "from", "as", "if", "elif", "else", "while",
  "for", "in", "is", "not", "and", "or", "try", "except", "finally", "with", "lambda",
  "yield", "raise", "pass", "continue", "break", "async", "await", "global", "nonlocal",
  "assert", "del", "True", "False", "None", "self", "cls",
  "function", "const", "let", "var", "export", "default", "type", "interface",
  "extends", "implements", "public", "private", "protected", "readonly", "static",
  "new", "this", "super", "switch", "case", "throw", "catch", "typeof", "instanceof",
  "void", "null", "undefined", "true", "false",
]);

function highlightToken(token: string): { text: string; className: string } {
  if (KEYWORDS.has(token)) {
    return { text: token, className: "text-sky-400 font-semibold" };
  }
  if (/^\d+(\.\d+)?$/.test(token)) {
    return { text: token, className: "text-purple-300 font-mono" };
  }
  if (/^[A-Z][a-zA-Z0-9_]*$/.test(token)) {
    return { text: token, className: "text-amber-300 font-medium" };
  }
  if (token.startsWith("@")) {
    return { text: token, className: "text-pink-400" };
  }
  return { text: token, className: "text-zinc-300" };
}

function renderCodeLine(line: string, language: string) {
  // Simple regex for string literals, comments, or words
  if (!line) return <span>&nbsp;</span>;

  // Comment check
  const commentIdx = line.indexOf(language === "python" ? "#" : "//");
  if (commentIdx !== -1) {
    const codePart = line.slice(0, commentIdx);
    const commentPart = line.slice(commentIdx);
    return (
      <>
        {renderTokens(codePart)}
        <span className="text-zinc-500 italic">{commentPart}</span>
      </>
    );
  }

  return renderTokens(line);
}

function renderTokens(code: string) {
  // Split on strings or identifier/symbol boundaries
  const regex = /(".*?"|'.*?'|`.*?`|\b\w+\b|[^\w\s]|\s+)/g;
  const parts = code.match(regex) || [code];

  return parts.map((part, idx) => {
    if (
      (part.startsWith('"') && part.endsWith('"')) ||
      (part.startsWith("'") && part.endsWith("'")) ||
      (part.startsWith("`") && part.endsWith("`"))
    ) {
      return (
        <span key={idx} className="text-emerald-300">
          {part}
        </span>
      );
    }
    if (/^\s+$/.test(part)) {
      return <span key={idx}>{part}</span>;
    }
    const tokenStyle = highlightToken(part);
    return (
      <span key={idx} className={tokenStyle.className}>
        {tokenStyle.text}
      </span>
    );
  });
}

export function CodeViewer({
  repoName,
  openTabs,
  activeTabPath,
  onSelectTab,
  onCloseTab,
  onClearHighlight,
  onSelectSymbol,
  indexedFiles,
  evidenceSnippets = [],
}: CodeViewerProps) {
  const activeTab = openTabs.find((t) => t.path === activeTabPath);
  const activeFileItem = indexedFiles.find((f) => f.path === activeTabPath);
  const targetLineRef = useRef<HTMLDivElement | null>(null);

  // AST Drawer state
  const [showAstDrawer, setShowAstDrawer] = useState(false);
  const [astSearch, setAstSearch] = useState("");
  const [astFilter, setAstFilter] = useState<"all" | "class" | "function" | "import">("all");
  const [copied, setCopied] = useState(false);

  // Auto-scroll to highlighted range when activeTab changes with highlightRange
  useEffect(() => {
    if (activeTab?.highlightRange && targetLineRef.current) {
      targetLineRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [activeTab?.path, activeTab?.highlightRange]);

  // Combined symbols from tab or indexedFileItem
  const currentSymbols = useMemo<CodeSymbolItem[]>(() => {
    if (activeTab?.symbols && activeTab.symbols.length > 0) {
      return activeTab.symbols;
    }
    return activeFileItem?.symbols || [];
  }, [activeTab?.symbols, activeFileItem?.symbols]);

  // Filtered symbols for AST drawer
  const filteredSymbols = useMemo(() => {
    return currentSymbols.filter((s) => {
      const matchKind = astFilter === "all" || s.kind.toLowerCase() === astFilter;
      const matchQuery = !astSearch.trim() || s.name.toLowerCase().includes(astSearch.toLowerCase());
      return matchKind && matchQuery;
    });
  }, [currentSymbols, astFilter, astSearch]);

  const handleCopyCode = async () => {
    if (!activeTab?.content) return;
    try {
      await navigator.clipboard.writeText(activeTab.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback
    }
  };

  const handleSymbolClick = (line: number) => {
    if (onSelectSymbol && activeTabPath) {
      onSelectSymbol(activeTabPath, line);
    }
    // Directly scroll
    const el = document.getElementById(`code-line-${line}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  const relevantSnippets = evidenceSnippets.filter((e) => e.path === activeTabPath);

  if (openTabs.length === 0 || !activeTab) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-8 bg-[#07090c] text-center select-none">
        <div className="grid size-14 place-items-center rounded-2xl border border-white/10 bg-[#12151c] text-2xl text-[#BFFF00] shadow-xl shadow-[#BFFF00]/5">
          ⌥
        </div>
        <h3 className="mt-4 text-base font-semibold text-zinc-200">No File Opened in Workspace</h3>
        <p className="mt-1.5 max-w-md text-xs leading-relaxed text-zinc-400">
          Select an indexed file from the Explorer tree, search with ⌘K, or click any verified AI citation to preview exact source lines.
        </p>
        <div className="mt-6 flex items-center gap-3 text-[11px] font-mono text-zinc-500">
          <span className="rounded border border-white/10 bg-white/5 px-2.5 py-1">⌘K Quick Open</span>
          <span className="rounded border border-white/10 bg-white/5 px-2.5 py-1">AST Symbol Outline</span>
          <span className="rounded border border-white/10 bg-white/5 px-2.5 py-1">Verified Citations</span>
        </div>
      </div>
    );
  }

  const pathParts = activeTab.path.split("/");
  const language = activeTab.language || activeFileItem?.language || "plain";
  const lineCount = activeTab.content ? activeTab.content.split("\n").length : 0;

  return (
    <div className="flex h-full flex-col bg-[#07090c] overflow-hidden select-none">
      {/* Tab Bar */}
      <div className="flex items-center border-b border-white/10 bg-[#0e1117] overflow-x-auto scrollbar-none">
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
              {tab.loading && (
                <span className="size-2 animate-spin rounded-full border border-cyan-400 border-t-transparent" />
              )}
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

      {/* Breadcrumbs & Action Toolbar */}
      <div className="flex items-center justify-between border-b border-white/5 bg-[#090b0e] px-4 py-1.5 text-xs text-zinc-400">
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

        {/* Action Controls */}
        <div className="flex items-center gap-2 shrink-0 font-mono text-[10px]">
          {lineCount > 0 && (
            <span className="text-zinc-500 hidden sm:inline">
              {lineCount} lines
            </span>
          )}

          <span className="rounded border border-cyan-500/20 bg-cyan-950/30 px-1.5 py-0.5 uppercase text-cyan-300">
            {language}
          </span>

          {/* Copy Button */}
          {activeTab.content && (
            <button
              onClick={handleCopyCode}
              className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-zinc-300 hover:bg-white/10 hover:text-white transition-colors"
              title="Copy code to clipboard"
            >
              {copied ? "✓ Copied" : "Copy"}
            </button>
          )}

          {/* Toggle AST Outline */}
          <button
            onClick={() => setShowAstDrawer((prev) => !prev)}
            className={`flex items-center gap-1 rounded border px-2 py-0.5 transition-colors ${
              showAstDrawer
                ? "border-[#BFFF00]/40 bg-[#BFFF00]/10 text-[#BFFF00]"
                : "border-white/10 bg-white/5 text-zinc-300 hover:bg-white/10 hover:text-white"
            }`}
            title="Toggle AST Symbol Outline"
          >
            <span>AST Outline</span>
            {currentSymbols.length > 0 && (
              <span className="rounded-full bg-white/10 px-1 text-[9px]">
                {currentSymbols.length}
              </span>
            )}
          </button>
        </div>
      </div>

      {/* Active Citation Notification Banner */}
      {activeTab.highlightRange && (
        <div className="flex items-center justify-between border-b border-cyan-500/20 bg-cyan-950/30 px-4 py-1.5 text-xs text-cyan-200">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-[#00F0FF] animate-pulse" />
            <span className="font-sans">
              Grounded Citation Range:{" "}
              <strong className="font-mono text-cyan-300">
                Lines L{activeTab.highlightRange.start} – L{activeTab.highlightRange.end}
              </strong>
            </span>
          </div>
          <div className="flex items-center gap-2 font-mono text-[10px]">
            <button
              onClick={() => {
                if (targetLineRef.current) {
                  targetLineRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
                }
              }}
              className="rounded bg-cyan-500/20 px-2 py-0.5 text-cyan-200 hover:bg-cyan-500/30 transition-colors"
            >
              Jump to Lines
            </button>
            {onClearHighlight && (
              <button
                onClick={() => onClearHighlight(activeTab.path)}
                className="rounded bg-white/5 px-2 py-0.5 text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        </div>
      )}

      {/* Main Workspace Body: Code View + Optional AST Drawer */}
      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Code Content Column */}
        <div className="flex-1 overflow-y-auto font-mono text-xs leading-relaxed p-4 select-text">
          {activeTab.loading ? (
            <div className="flex h-full flex-col items-center justify-center space-y-3 py-16 text-zinc-500 font-sans">
              <span className="size-6 animate-spin rounded-full border-2 border-[#BFFF00] border-t-transparent" />
              <p className="text-xs">Fetching indexed source code from repository database…</p>
            </div>
          ) : activeTab.error ? (
            <div className="rounded-xl border border-rose-500/30 bg-rose-950/20 p-6 text-center text-xs text-rose-300 font-sans">
              <p className="font-semibold">{activeTab.error}</p>
              <p className="mt-1 text-zinc-400">
                Verify this repository is fully indexed and the file is tracked in git.
              </p>
            </div>
          ) : activeTab.content ? (
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
                    id={`code-line-${lineNum}`}
                    ref={lineNum === activeTab.highlightRange?.start ? targetLineRef : undefined}
                    className={`flex items-start gap-4 px-2 py-0.5 rounded transition-all duration-300 ${
                      isHighlighted
                        ? "bg-[#BFFF00]/15 border-l-2 border-[#BFFF00] text-lime-100 shadow-[inset_0_0_12px_rgba(191,255,0,0.06)]"
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
                    <pre className="flex-1 overflow-x-auto whitespace-pre font-mono text-xs">
                      {renderCodeLine(line, language)}
                    </pre>
                  </div>
                );
              })}
            </div>
          ) : relevantSnippets.length > 0 ? (
            /* Render verified evidence snippets */
            <div className="space-y-6 max-w-4xl font-sans">
              <div className="rounded-xl border border-cyan-500/30 bg-cyan-950/20 p-3 text-xs text-cyan-200">
                <span className="font-bold text-[#00F0FF]">Verified Evidence Snippet:</span> Displaying indexed code lines verified by RepoPilot retrieval engine.
              </div>

              {relevantSnippets.map((snippet, sIdx) => (
                <div key={sIdx} className="rounded-xl border border-white/10 bg-[#0d1015] overflow-hidden">
                  <div className="border-b border-white/10 bg-[#12151c] px-4 py-2 flex items-center justify-between text-[11px] text-zinc-400">
                    <span className="text-cyan-300 font-semibold font-mono">
                      Lines {snippet.start_line} – {snippet.end_line}
                    </span>
                    <span className="text-zinc-500">Indexed ground truth</span>
                  </div>
                  <div className="p-3 bg-[#080a0d] font-mono text-xs">
                    {snippet.content.split("\n").map((line, lineIdx) => {
                      const lineNum = snippet.start_line + lineIdx;
                      return (
                        <div key={lineNum} className="flex items-start gap-4 py-0.5">
                          <span className="w-10 shrink-0 text-right text-zinc-600 select-none text-[11px]">
                            {lineNum}
                          </span>
                          <pre className="flex-1 overflow-x-auto text-zinc-200 whitespace-pre font-mono text-xs">
                            {renderCodeLine(line, language)}
                          </pre>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            /* Metadata fallback */
            <div className="max-w-3xl space-y-4 font-sans">
              <div className="rounded-xl border border-white/10 bg-[#0f1218] p-4">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-zinc-400">
                  <span className="size-2 rounded-full bg-[#BFFF00]" />
                  <span>Indexed Code Intelligence</span>
                </div>
                <p className="mt-2 text-xs leading-relaxed text-zinc-300">
                  Source file <code className="text-[#BFFF00] font-mono">{activeTab.path}</code> is indexed in the repository database ({activeFileItem?.size_bytes || 0} bytes, {language}).
                </p>
                {activeTab.highlightRange && (
                  <div className="mt-3 rounded-lg border border-cyan-500/30 bg-cyan-950/20 p-2.5 text-xs text-cyan-200">
                    Target Citation Line Range: <span className="font-mono font-bold text-cyan-300">L{activeTab.highlightRange.start}–L{activeTab.highlightRange.end}</span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* AST Symbol Outline Drawer (Right Sidebar) */}
        {showAstDrawer && (
          <aside className="w-80 shrink-0 border-l border-white/10 bg-[#0c0e12] flex flex-col min-h-0 select-none">
            {/* Drawer Header */}
            <div className="p-3 border-b border-white/10 bg-[#0e1117] flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold uppercase tracking-wider text-zinc-300 font-sans">
                  AST Outline
                </span>
                <span className="rounded-full bg-white/10 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
                  {filteredSymbols.length}
                </span>
              </div>
              <button
                onClick={() => setShowAstDrawer(false)}
                className="text-zinc-500 hover:text-white text-xs"
                title="Close AST Outline"
              >
                ✕
              </button>
            </div>

            {/* Filter Tabs */}
            <div className="flex border-b border-white/10 bg-[#0a0c10] text-[10px] font-mono">
              {(["all", "class", "function", "import"] as const).map((filterKey) => (
                <button
                  key={filterKey}
                  onClick={() => setAstFilter(filterKey)}
                  className={`flex-1 py-1.5 text-center capitalize transition-colors ${
                    astFilter === filterKey
                      ? "border-b-2 border-b-[#BFFF00] text-[#BFFF00] font-bold bg-white/5"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {filterKey}
                </button>
              ))}
            </div>

            {/* Search Input */}
            <div className="p-2 border-b border-white/5">
              <input
                type="text"
                placeholder="Filter symbols in file…"
                value={astSearch}
                onChange={(e) => setAstSearch(e.target.value)}
                className="w-full rounded-md border border-white/10 bg-[#141820] px-2.5 py-1 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-[#BFFF00]/50 font-sans"
              />
            </div>

            {/* Symbols List */}
            <div className="flex-1 overflow-y-auto divide-y divide-white/5 font-mono text-xs">
              {filteredSymbols.length === 0 ? (
                <div className="p-6 text-center text-xs text-zinc-500 font-sans">
                  No symbols matching &quot;{astSearch || astFilter}&quot;
                </div>
              ) : (
                filteredSymbols.map((sym, idx) => {
                  const isClass = sym.kind.toLowerCase() === "class";
                  const isFunc = sym.kind.toLowerCase() === "function";
                  const isImport = sym.kind.toLowerCase() === "import";

                  return (
                    <button
                      key={`${sym.name}-${idx}`}
                      onClick={() => handleSymbolClick(sym.line)}
                      className="w-full flex items-center justify-between p-2.5 text-left hover:bg-white/5 transition-colors group"
                    >
                      <div className="flex items-center gap-2 min-w-0 pr-2">
                        <span
                          className={`rounded px-1.5 py-0.5 text-[9px] uppercase font-bold shrink-0 ${
                            isClass
                              ? "bg-purple-950/60 border border-purple-500/30 text-purple-300"
                              : isFunc
                              ? "bg-sky-950/60 border border-sky-500/30 text-sky-300"
                              : isImport
                              ? "bg-amber-950/60 border border-amber-500/30 text-amber-300"
                              : "bg-white/5 border border-white/10 text-zinc-400"
                          }`}
                        >
                          {sym.kind.slice(0, 4)}
                        </span>
                        <span className="truncate text-zinc-200 group-hover:text-cyan-300 transition-colors text-[11px]">
                          {sym.name}
                        </span>
                      </div>
                      <span className="text-[10px] text-zinc-500 shrink-0 font-mono">
                        L{sym.line}
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
