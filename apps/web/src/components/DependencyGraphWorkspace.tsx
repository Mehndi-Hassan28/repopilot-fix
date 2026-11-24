"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Repository,
  DependencyGraphData,
  SymbolItem,
  ImpactAnalysisData,
} from "../lib/types";
import { apiRequest } from "../lib/api";
import { NetworkIcon, SearchIcon } from "./Icons";

interface DependencyGraphWorkspaceProps {
  repositories: Repository[];
  activeRepo: Repository | null;
  onSelectRepo: (repo: Repository) => void;
  onOpenFile: (path: string, line?: number) => void;
  csrfToken: string;
}

export function DependencyGraphWorkspace({
  repositories,
  activeRepo,
  onSelectRepo,
  onOpenFile,
  csrfToken,
}: DependencyGraphWorkspaceProps) {
  const [graphData, setGraphData] = useState<DependencyGraphData | null>(null);
  const [symbols, setSymbols] = useState<SymbolItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Filter & Selection
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [nodeFilter, setNodeFilter] = useState<"all" | "source" | "tests" | "circular">("all");
  const [activeTab, setActiveTab] = useState<"graph" | "symbols" | "impact">("graph");

  // Impact Analysis State
  const [impactIssueTitle, setImpactIssueTitle] = useState("");
  const [impactIssueBody, setImpactIssueBody] = useState("");
  const [impactResult, setImpactResult] = useState<ImpactAnalysisData | null>(null);
  const [analyzingImpact, setAnalyzingImpact] = useState(false);
  const [impactError, setImpactError] = useState("");

  // Fetch Graph and Symbols
  useEffect(() => {
    if (!activeRepo) return;
    let isCancelled = false;

    async function fetchData() {
      setLoading(true);
      setError("");
      try {
        const [graphRes, symbolsRes] = await Promise.all([
          apiRequest(`/api/repositories/${activeRepo!.id}/dependency-graph`),
          apiRequest(`/api/repositories/${activeRepo!.id}/symbols`),
        ]);

        if (!graphRes.ok) {
          const err = await graphRes.json().catch(() => ({}));
          throw new Error(err.detail || "Failed to load dependency graph.");
        }
        if (!symbolsRes.ok) {
          const err = await symbolsRes.json().catch(() => ({}));
          throw new Error(err.detail || "Failed to load repository symbols.");
        }

        const gData = (await graphRes.json()) as DependencyGraphData;
        const sData = await symbolsRes.json();

        if (!isCancelled) {
          setGraphData(gData);
          setSymbols(sData.symbols || []);
          if (gData.nodes.length > 0) {
            setSelectedPath((prev) => prev || gData.nodes[0].path);
          }
        }
      } catch (err) {
        if (!isCancelled) {
          setError(err instanceof Error ? err.message : "Failed to load code intelligence.");
        }
      } finally {
        if (!isCancelled) {
          setLoading(false);
        }
      }
    }

    void fetchData();
    return () => {
      isCancelled = true;
    };
  }, [activeRepo]);

  // Run Impact Analysis
  const handleRunImpactAnalysis = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!activeRepo || !impactIssueTitle.trim()) return;

    setAnalyzingImpact(true);
    setImpactError("");
    try {
      const res = await apiRequest(
        `/api/repositories/${activeRepo.id}/impact-analysis`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            issue_title: impactIssueTitle.trim(),
            issue_body: impactIssueBody.trim(),
            target_file: selectedPath || undefined,
          }),
        },
        csrfToken
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Impact analysis failed.");
      }
      const data = (await res.json()) as ImpactAnalysisData;
      setImpactResult(data);
      setActiveTab("impact");
    } catch (err) {
      setImpactError(err instanceof Error ? err.message : "Failed to run impact analysis.");
    } finally {
      setAnalyzingImpact(false);
    }
  };

  // Filtered nodes
  const filteredNodes = useMemo(() => {
    if (!graphData) return [];
    return graphData.nodes.filter((n) => {
      const isTest = n.path.toLowerCase().includes("test");
      if (nodeFilter === "source" && isTest) return false;
      if (nodeFilter === "tests" && !isTest) return false;
      if (nodeFilter === "circular") {
        const inCycles = graphData.summary.circular_chains.some((c) => c.includes(n.path));
        if (!inCycles) return false;
      }
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchesPath = n.path.toLowerCase().includes(q);
        const matchesSym =
          n.functions.some((f) => f.toLowerCase().includes(q)) ||
          n.classes.some((c) => c.toLowerCase().includes(q));
        if (!matchesPath && !matchesSym) return false;
      }
      return true;
    });
  }, [graphData, nodeFilter, searchQuery]);

  // Selected node details
  const selectedNode = useMemo(() => {
    if (!graphData || !selectedPath) return null;
    return graphData.nodes.find((n) => n.path === selectedPath) || null;
  }, [graphData, selectedPath]);

  // Incoming and Outgoing Edges for selected node
  const incomingEdges = useMemo(() => {
    if (!graphData || !selectedPath) return [];
    return graphData.edges.filter((e) => e.target === selectedPath);
  }, [graphData, selectedPath]);

  const outgoingEdges = useMemo(() => {
    if (!graphData || !selectedPath) return [];
    return graphData.edges.filter((e) => e.source === selectedPath);
  }, [graphData, selectedPath]);

  // Filtered symbols
  const filteredSymbols = useMemo(() => {
    if (!symbols) return [];
    if (!searchQuery.trim()) return symbols.slice(0, 50);
    const q = searchQuery.toLowerCase();
    return symbols
      .filter(
        (s) =>
          s.name.toLowerCase().includes(q) ||
          s.qualified_name.toLowerCase().includes(q) ||
          s.file_path.toLowerCase().includes(q)
      )
      .slice(0, 100);
  }, [symbols, searchQuery]);

  // SVG Coordinates Computation for Interactive Graph View
  const graphLayout = useMemo(() => {
    const nodes = filteredNodes;
    if (nodes.length === 0) return { positions: new Map<string, { x: number; y: number }>(), width: 800, height: 600 };

    const positions = new Map<string, { x: number; y: number }>();
    const cols = Math.max(2, Math.ceil(Math.sqrt(nodes.length * 1.5)));
    const colWidth = 260;
    const rowHeight = 120;
    const padding = 60;

    nodes.forEach((n, idx) => {
      const col = idx % cols;
      const row = Math.floor(idx / cols);
      positions.set(n.path, {
        x: padding + col * colWidth,
        y: padding + row * rowHeight,
      });
    });

    const totalWidth = padding * 2 + cols * colWidth;
    const totalHeight = padding * 2 + Math.ceil(nodes.length / cols) * rowHeight + 50;

    return { positions, width: Math.max(900, totalWidth), height: Math.max(550, totalHeight) };
  }, [filteredNodes]);

  return (
    <div className="flex-1 flex flex-col h-full bg-[#050505] text-[#F5F5F5] overflow-hidden select-none">
      {/* Top Header & Metrics Bar */}
      <div className="border-b border-white/10 bg-[#080a0f] p-4 shrink-0">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <NetworkIcon className="size-4 text-[#BFFF00]" />
              <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
                Advanced Code Intelligence
              </span>
            </div>
            <h1 className="text-lg font-semibold text-white mt-0.5">
              Repository Dependency Graph & Impact Analysis
            </h1>
          </div>

          {/* Repo Selector */}
          <div className="flex items-center gap-2">
            <label className="text-xs text-zinc-400 font-mono">Repo:</label>
            <select
              value={activeRepo?.id || ""}
              onChange={(e) => {
                const found = repositories.find((r) => r.id === Number(e.target.value));
                if (found) onSelectRepo(found);
              }}
              className="rounded-lg border border-white/10 bg-[#121620] px-3 py-1.5 text-xs text-zinc-200 outline-none focus:border-[#BFFF00]/50 font-sans"
            >
              {repositories.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.full_name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Metrics Row */}
        {graphData && (
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2 text-xs">
            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5">
              <span className="text-[10px] font-mono uppercase text-zinc-400">Total Files</span>
              <div className="text-base font-semibold text-white font-mono mt-0.5">
                {graphData.summary.total_files}
              </div>
            </div>

            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5">
              <span className="text-[10px] font-mono uppercase text-zinc-400">AST Symbols</span>
              <div className="text-base font-semibold text-[#00F0FF] font-mono mt-0.5">
                {graphData.summary.total_symbols}
              </div>
            </div>

            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5">
              <span className="text-[10px] font-mono uppercase text-zinc-400">Total Edges</span>
              <div className="text-base font-semibold text-white font-mono mt-0.5">
                {graphData.summary.total_edges}
              </div>
            </div>

            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5">
              <span className="text-[10px] font-mono uppercase text-emerald-400">Resolved Imports</span>
              <div className="text-base font-semibold text-emerald-300 font-mono mt-0.5">
                {graphData.summary.resolved_edges}
              </div>
            </div>

            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5">
              <span className="text-[10px] font-mono uppercase text-cyan-400">Inferred Edges</span>
              <div className="text-base font-semibold text-cyan-300 font-mono mt-0.5">
                {graphData.summary.inferred_edges}
              </div>
            </div>

            <div className="rounded-lg border border-white/5 bg-white/[0.02] p-2.5">
              <span className="text-[10px] font-mono uppercase text-amber-400">Circular Dependency</span>
              <div className="text-base font-semibold font-mono mt-0.5 flex items-center gap-1.5">
                {graphData.summary.has_circular_dependency ? (
                  <span className="text-amber-400 flex items-center gap-1">
                    <span className="size-2 rounded-full bg-amber-400 animate-pulse" />
                    Detected ({graphData.summary.circular_chains.length})
                  </span>
                ) : (
                  <span className="text-emerald-400">None (Clean DAG)</span>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Main Workspace Body */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Column: Visual Graph + Tab Controls */}
        <div className="flex-1 flex flex-col min-w-0 border-r border-white/10 overflow-hidden">
          {/* Sub Navigation Bar */}
          <div className="border-b border-white/10 bg-[#0a0d14] px-4 py-2 flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-1 bg-white/5 p-1 rounded-lg border border-white/10">
              <button
                onClick={() => setActiveTab("graph")}
                className={`px-3 py-1 rounded-md font-mono text-xs transition-colors ${
                  activeTab === "graph"
                    ? "bg-[#BFFF00] text-black font-semibold"
                    : "text-zinc-400 hover:text-white"
                }`}
              >
                Dependency Graph
              </button>
              <button
                onClick={() => setActiveTab("symbols")}
                className={`px-3 py-1 rounded-md font-mono text-xs transition-colors ${
                  activeTab === "symbols"
                    ? "bg-[#00F0FF] text-black font-semibold"
                    : "text-zinc-400 hover:text-white"
                }`}
              >
                AST Symbols ({symbols.length})
              </button>
              <button
                onClick={() => setActiveTab("impact")}
                className={`px-3 py-1 rounded-md font-mono text-xs transition-colors ${
                  activeTab === "impact"
                    ? "bg-amber-400 text-black font-semibold"
                    : "text-zinc-400 hover:text-white"
                }`}
              >
                Bug Impact Sandbox
              </button>
            </div>

            {/* Filter buttons for graph view */}
            {activeTab === "graph" && (
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono text-zinc-400">Filter:</span>
                {(["all", "source", "tests", "circular"] as const).map((mode) => (
                  <button
                    key={mode}
                    onClick={() => setNodeFilter(mode)}
                    className={`rounded px-2.5 py-0.5 text-[11px] font-mono transition-colors ${
                      nodeFilter === mode
                        ? "bg-white/20 text-white font-semibold"
                        : "text-zinc-400 hover:text-white bg-white/5"
                    }`}
                  >
                    {mode}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Tab 1: Interactive SVG Dependency Graph */}
          {activeTab === "graph" && (
            <div className="flex-1 overflow-auto bg-[#040609] p-4 relative">
              {loading ? (
                <div className="h-full flex items-center justify-center text-zinc-400 font-mono text-xs">
                  <span className="size-2 rounded-full bg-[#BFFF00] animate-ping mr-2" />
                  Generating Static Dependency Graph…
                </div>
              ) : error ? (
                <div className="h-full flex items-center justify-center text-rose-400 font-mono text-xs">
                  {error}
                </div>
              ) : filteredNodes.length === 0 ? (
                <div className="h-full flex items-center justify-center text-zinc-500 font-mono text-xs">
                  No files match current filter.
                </div>
              ) : (
                <div className="relative inline-block" style={{ width: graphLayout.width, height: graphLayout.height }}>
                  {/* SVG Edges Layer */}
                  <svg
                    className="absolute inset-0 pointer-events-none"
                    width={graphLayout.width}
                    height={graphLayout.height}
                  >
                    <defs>
                      <marker
                        id="arrow-resolved"
                        viewBox="0 0 10 10"
                        refX="20"
                        refY="5"
                        markerWidth="6"
                        markerHeight="6"
                        orient="auto-start-reverse"
                      >
                        <path d="M 0 0 L 10 5 L 0 10 z" fill="#a3e635" />
                      </marker>
                      <marker
                        id="arrow-inferred"
                        viewBox="0 0 10 10"
                        refX="20"
                        refY="5"
                        markerWidth="6"
                        markerHeight="6"
                        orient="auto-start-reverse"
                      >
                        <path d="M 0 0 L 10 5 L 0 10 z" fill="#06b6d4" />
                      </marker>
                      <marker
                        id="arrow-dim"
                        viewBox="0 0 10 10"
                        refX="20"
                        refY="5"
                        markerWidth="6"
                        markerHeight="6"
                        orient="auto-start-reverse"
                      >
                        <path d="M 0 0 L 10 5 L 0 10 z" fill="#3f3f46" />
                      </marker>
                    </defs>

                    {graphData?.edges.map((edge, idx) => {
                      const srcPos = graphLayout.positions.get(edge.source);
                      const tgtPos = graphLayout.positions.get(edge.target);
                      if (!srcPos || !tgtPos) return null;

                      const isSelectedRel =
                        edge.source === selectedPath || edge.target === selectedPath;
                      const strokeColor = isSelectedRel
                        ? edge.status === "resolved"
                          ? "#a3e635"
                          : "#06b6d4"
                        : "#27272a";
                      const marker = isSelectedRel
                        ? edge.status === "resolved"
                          ? "url(#arrow-resolved)"
                          : "url(#arrow-inferred)"
                        : "url(#arrow-dim)";

                      // Bezier curve between source and target node centers
                      const x1 = srcPos.x + 110;
                      const y1 = srcPos.y + 40;
                      const x2 = tgtPos.x + 110;
                      const y2 = tgtPos.y + 40;
                      const dx = (x2 - x1) / 2;
                      const dy = (y2 - y1) / 2;
                      const pathStr = `M ${x1} ${y1} Q ${x1 + dx} ${y1 + dy - 20} ${x2} ${y2}`;

                      return (
                        <path
                          key={`edge-${idx}`}
                          d={pathStr}
                          fill="none"
                          stroke={strokeColor}
                          strokeWidth={isSelectedRel ? 2 : 1}
                          strokeDasharray={edge.status === "inferred" ? "4,4" : undefined}
                          opacity={isSelectedRel ? 1 : 0.4}
                          markerEnd={marker}
                        />
                      );
                    })}
                  </svg>

                  {/* HTML Node Cards Layer */}
                  {filteredNodes.map((node) => {
                    const pos = graphLayout.positions.get(node.path);
                    if (!pos) return null;
                    const isSelected = node.path === selectedPath;
                    const isDependent = incomingEdges.some((e) => e.source === node.path);
                    const isDependency = outgoingEdges.some((e) => e.target === node.path);
                    const isTest = node.path.toLowerCase().includes("test");

                    let borderClass = "border-white/10 bg-[#0d111a] hover:border-white/30";
                    if (isSelected) {
                      borderClass = "border-[#BFFF00] bg-[#121a12] shadow-[0_0_15px_rgba(191,255,0,0.2)]";
                    } else if (isDependent) {
                      borderClass = "border-cyan-400/80 bg-[#09151e]";
                    } else if (isDependency) {
                      borderClass = "border-lime-400/80 bg-[#121c0e]";
                    }

                    return (
                      <div
                        key={node.path}
                        onClick={() => setSelectedPath(node.path)}
                        onDoubleClick={() => onOpenFile(node.path)}
                        style={{ left: pos.x, top: pos.y, width: 220, position: "absolute" }}
                        className={`rounded-xl border p-3 cursor-pointer transition-all ${borderClass} group`}
                        title="Click to inspect, double click to open code"
                      >
                        <div className="flex items-center justify-between gap-1 text-[10px] font-mono text-zinc-400">
                          <span className={`px-1.5 py-0.5 rounded font-bold uppercase ${isTest ? "bg-purple-900/40 text-purple-300" : "bg-cyan-900/40 text-cyan-300"}`}>
                            {isTest ? "TEST" : node.language}
                          </span>
                          <span>{node.functions.length + node.classes.length} syms</span>
                        </div>

                        <div className="mt-1.5 font-mono text-xs font-semibold text-white truncate" title={node.path}>
                          {node.path.split("/").pop()}
                        </div>
                        <div className="text-[10px] font-mono text-zinc-400 truncate">
                          {node.path.split("/").slice(0, -1).join("/") || "."}
                        </div>

                        <div className="mt-2 pt-2 border-t border-white/5 flex items-center justify-between text-[10px] font-mono text-zinc-400">
                          <span title="Outgoing dependencies">→ {node.outgoing_files.length}</span>
                          <span title="Incoming dependents">← {node.incoming_files.length}</span>
                          {node.has_circular_dependency && (
                            <span className="text-amber-400 font-bold" title="In circular dependency cycle">↻ cycle</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* Tab 2: AST Symbol Explorer */}
          {activeTab === "symbols" && (
            <div className="flex-1 overflow-y-auto p-4 space-y-2 bg-[#040609]">
              <div className="sticky top-0 bg-[#040609] pb-3 border-b border-white/10 flex items-center gap-2">
                <SearchIcon className="size-4 text-zinc-500" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Filter symbols across repository by name or path…"
                  className="w-full bg-[#0c1018] border border-white/10 rounded-lg px-3 py-1.5 text-xs text-white placeholder-zinc-500 outline-none focus:border-[#00F0FF]/50 font-mono"
                />
              </div>

              <div className="space-y-1.5">
                {loading ? (
                  <div className="h-64 flex items-center justify-center text-zinc-400 font-mono text-xs">
                    <span className="size-2 rounded-full bg-[#00F0FF] animate-ping mr-2" />
                    Loading AST symbols…
                  </div>
                ) : error ? (
                  <div className="h-64 flex items-center justify-center text-rose-400 font-mono text-xs">
                    {error}
                  </div>
                ) : filteredSymbols.length === 0 ? (
                  <div className="h-64 flex items-center justify-center text-zinc-500 font-mono text-xs">
                    {symbols.length === 0
                      ? "No AST symbols found for this repository."
                      : "No symbols match query."}
                  </div>
                ) : (
                  filteredSymbols.map((sym, idx) => (
                    <div
                      key={`${sym.file_path}-${sym.name}-${idx}`}
                      onClick={() => {
                        setSelectedPath(sym.file_path);
                        onOpenFile(sym.file_path, sym.start_line);
                      }}
                      className="flex items-center justify-between p-2.5 rounded-lg border border-white/5 bg-[#0b0e16] hover:border-cyan-400/40 cursor-pointer transition-colors"
                    >
                      <div className="flex items-center gap-2">
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold uppercase ${
                            sym.kind === "class"
                              ? "bg-amber-950/40 text-amber-300 border border-amber-500/30"
                              : "bg-cyan-950/40 text-cyan-300 border border-cyan-500/30"
                          }`}
                        >
                          {sym.kind}
                        </span>
                        <span className="font-mono text-xs font-semibold text-white">
                          {sym.qualified_name}
                        </span>
                        {sym.docstring && (
                          <span className="text-xs text-zinc-400 truncate max-w-xs font-sans">
                            — {sym.docstring.split("\n")[0]}
                          </span>
                        )}
                      </div>

                      <div className="flex items-center gap-3 text-[11px] font-mono text-zinc-400">
                        <span>
                          {sym.file_path}:{sym.start_line}
                        </span>
                        <span className="text-cyan-400">→</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {/* Tab 3: Bug Impact Sandbox Tool */}
          {activeTab === "impact" && (
            <div className="flex-1 overflow-y-auto p-5 space-y-5 bg-[#040609]">
              <div className="rounded-xl border border-white/10 bg-[#0a0e16] p-4">
                <h3 className="text-sm font-semibold text-white flex items-center gap-2">
                  <span className="size-2 rounded-full bg-amber-400" />
                  Bug Blast Radius & Static Impact Analysis
                </h3>
                <p className="mt-1 text-xs text-zinc-400">
                  Analyze how a suspected bug propagates across callers, downstream dependencies, and regression test suites.
                </p>

                <form onSubmit={handleRunImpactAnalysis} className="mt-4 space-y-3">
                  <div>
                    <label className="text-[11px] font-mono text-zinc-400 uppercase">
                      Issue Title / Bug Query:
                    </label>
                    <input
                      type="text"
                      required
                      value={impactIssueTitle}
                      onChange={(e) => setImpactIssueTitle(e.target.value)}
                      placeholder="e.g. VIP discount calculation error in discounts.py"
                      className="w-full mt-1 bg-[#121722] border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-zinc-500 outline-none focus:border-amber-400/50 font-mono"
                    />
                  </div>

                  <div>
                    <label className="text-[11px] font-mono text-zinc-400 uppercase">
                      Issue Description / Stack Trace (Optional):
                    </label>
                    <textarea
                      rows={2}
                      value={impactIssueBody}
                      onChange={(e) => setImpactIssueBody(e.target.value)}
                      placeholder="Provide additional issue body or failure traceback…"
                      className="w-full mt-1 bg-[#121722] border border-white/10 rounded-lg px-3 py-2 text-xs text-white placeholder-zinc-500 outline-none focus:border-amber-400/50 font-mono resize-none"
                    />
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <span className="text-[11px] font-mono text-zinc-400">
                      Target Focus: <span className="text-[#00F0FF]">{selectedPath || "Whole Repository"}</span>
                    </span>
                    <button
                      type="submit"
                      disabled={analyzingImpact || !impactIssueTitle.trim()}
                      className="px-4 py-2 bg-amber-400 text-black font-semibold text-xs rounded-lg hover:brightness-110 disabled:opacity-50 transition-all font-mono"
                    >
                      {analyzingImpact ? "Traversing AST Dependencies…" : "Run Impact Analysis"}
                    </button>
                  </div>
                </form>
              </div>

              {impactError && (
                <div className="rounded-lg border border-rose-500/30 bg-rose-950/20 p-3 text-xs text-rose-300 font-mono">
                  {impactError}
                </div>
              )}

              {/* Impact Results */}
              {impactResult && (
                <div className="space-y-4">
                  {/* Disclaimer Alert */}
                  <div className="rounded-xl border border-amber-500/40 bg-amber-950/20 p-3.5 text-xs text-amber-200 font-mono">
                    <div className="font-bold uppercase tracking-wider mb-1">Static Impact Hypothesis</div>
                    {impactResult.disclaimer}
                  </div>

                  {/* Root Cause Candidates */}
                  <div className="rounded-xl border border-lime-500/30 bg-[#0a120c] p-4">
                    <h4 className="text-xs font-mono font-bold uppercase text-[#BFFF00]">
                      Suspected Root-Cause Files ({impactResult.likely_root_cause_files.length})
                    </h4>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {impactResult.likely_root_cause_files.map((file) => (
                        <button
                          key={file}
                          onClick={() => onOpenFile(file)}
                          className="px-3 py-1.5 rounded-lg border border-lime-500/40 bg-lime-950/30 text-lime-300 font-mono text-xs hover:border-lime-400 transition-colors"
                        >
                          {file} →
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Affected Downstream Files */}
                  <div className="rounded-xl border border-cyan-500/30 bg-[#09131a] p-4">
                    <h4 className="text-xs font-mono font-bold uppercase text-[#00F0FF]">
                      Affected Downstream Dependencies ({impactResult.affected_downstream_files.length})
                    </h4>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {impactResult.affected_downstream_files.length === 0 ? (
                        <span className="text-xs text-zinc-400 font-mono">No downstream files affected.</span>
                      ) : (
                        impactResult.affected_downstream_files.map((file) => (
                          <button
                            key={file}
                            onClick={() => onOpenFile(file)}
                            className="px-3 py-1.5 rounded-lg border border-cyan-500/40 bg-cyan-950/30 text-cyan-300 font-mono text-xs hover:border-cyan-400 transition-colors"
                          >
                            {file} →
                          </button>
                        ))
                      )}
                    </div>
                  </div>

                  {/* Suggested Regression Tests */}
                  <div className="rounded-xl border border-purple-500/30 bg-[#120a1a] p-4">
                    <h4 className="text-xs font-mono font-bold uppercase text-purple-300">
                      Suggested Regression Tests ({impactResult.suggested_regression_tests.length})
                    </h4>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {impactResult.suggested_regression_tests.map((test) => (
                        <button
                          key={test}
                          onClick={() => onOpenFile(test)}
                          className="px-3 py-1.5 rounded-lg border border-purple-500/40 bg-purple-950/30 text-purple-200 font-mono text-xs hover:border-purple-300 transition-colors"
                        >
                          ✓ {test} →
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* Empty state before impact analysis is run */}
              {!impactResult && !analyzingImpact && !impactError && (
                <div className="rounded-xl border border-white/5 bg-white/[0.01] p-8 text-center text-zinc-500 font-mono text-xs">
                  Enter an issue title or select a component to inspect blast radius and identify regression tests.
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right Side Inspector Panel: Selected Component Details */}
        <div className="w-80 shrink-0 border-l border-white/10 bg-[#080b12] p-4 overflow-y-auto space-y-4">
          <div className="border-b border-white/10 pb-3">
            <span className="text-[10px] font-mono uppercase text-zinc-400 tracking-wider">
              Component Inspector
            </span>
            <h3 className="mt-0.5 text-sm font-semibold text-white font-mono truncate" title={selectedPath || ""}>
              {selectedPath ? selectedPath.split("/").pop() : "No file selected"}
            </h3>
            {selectedPath && (
              <div className="text-[11px] text-zinc-400 font-mono truncate mt-0.5">
                {selectedPath}
              </div>
            )}
          </div>

          {selectedNode ? (
            <div className="space-y-4 text-xs font-mono">
              {/* Quick Jump Action */}
              <button
                onClick={() => onOpenFile(selectedNode.path)}
                className="w-full py-1.5 bg-white/10 hover:bg-white/20 border border-white/10 rounded-lg text-white font-mono text-xs transition-colors flex items-center justify-center gap-1.5"
              >
                <span>Inspect Source in Code Viewer</span>
                <span>→</span>
              </button>

              {/* Dependents (Who imports this?) */}
              <div>
                <span className="text-[10px] font-mono uppercase text-cyan-400 font-bold block mb-1">
                  Incoming Dependents ({incomingEdges.length})
                </span>
                {incomingEdges.length === 0 ? (
                  <span className="text-zinc-500 text-[11px]">No callers / imports to this file.</span>
                ) : (
                  <div className="space-y-1">
                    {incomingEdges.map((e, idx) => (
                      <div
                        key={`in-${idx}`}
                        onClick={() => setSelectedPath(e.source)}
                        className="p-1.5 rounded bg-white/[0.03] border border-white/5 hover:border-cyan-400/40 cursor-pointer text-[11px] truncate"
                        title={e.source}
                      >
                        <span className="text-cyan-300">{e.source.split("/").pop()}</span>
                        <span className="text-zinc-500 text-[10px] ml-1">({e.kind})</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Dependencies (Who does this import?) */}
              <div>
                <span className="text-[10px] font-mono uppercase text-lime-400 font-bold block mb-1">
                  Outgoing Dependencies ({outgoingEdges.length})
                </span>
                {outgoingEdges.length === 0 ? (
                  <span className="text-zinc-500 text-[11px]">No imports from other files.</span>
                ) : (
                  <div className="space-y-1">
                    {outgoingEdges.map((e, idx) => (
                      <div
                        key={`out-${idx}`}
                        onClick={() => setSelectedPath(e.target)}
                        className="p-1.5 rounded bg-white/[0.03] border border-white/5 hover:border-lime-400/40 cursor-pointer text-[11px] truncate"
                        title={e.target}
                      >
                        <span className="text-lime-300">{e.target.split("/").pop()}</span>
                        <span className="text-zinc-500 text-[10px] ml-1">({e.kind})</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Defined Functions & Classes */}
              <div>
                <span className="text-[10px] font-mono uppercase text-zinc-400 font-bold block mb-1">
                  Exported Symbols ({selectedNode.functions.length + selectedNode.classes.length})
                </span>
                <div className="space-y-1">
                  {selectedNode.classes.map((c) => (
                    <div key={c} className="p-1 rounded bg-amber-950/20 border border-amber-500/20 text-amber-300 text-[11px]">
                      class {c}
                    </div>
                  ))}
                  {selectedNode.functions.map((f) => (
                    <div key={f} className="p-1 rounded bg-cyan-950/20 border border-cyan-500/20 text-cyan-300 text-[11px]">
                      def {f}()
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="text-xs text-zinc-500 font-mono">
              Click a file in the graph or symbols tab to view incoming and outgoing cross-file relationships.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
