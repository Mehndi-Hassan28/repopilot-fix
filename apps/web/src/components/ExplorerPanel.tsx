"use client";

import { useState, useMemo } from "react";
import { Repository, IndexStatus, IndexedFileItem } from "../lib/types";
import { getIndexStatusBadge, isIndexCompleted, isIndexActive, isIndexFailed } from "../lib/index-status";

interface ExplorerPanelProps {
  repositories: Repository[];
  activeRepo: Repository | null;
  onSelectRepo: (repo: Repository) => void;
  indexStatus: IndexStatus | null;
  indexing: boolean;
  onReindex: (repoId: number) => void;
  files: IndexedFileItem[];
  loadingFiles: boolean;
  selectedFilePath: string | null;
  onSelectFile: (path: string) => void;
  onSelectSymbol?: (path: string, line: number) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

interface TreeNode {
  name: string;
  fullPath: string;
  isFolder: boolean;
  children: Record<string, TreeNode>;
  fileItem?: IndexedFileItem;
}

function getFileIcon(path: string): { icon: string; color: string } {
  if (path.endsWith(".py")) return { icon: "py", color: "text-amber-400" };
  if (path.endsWith(".ts") || path.endsWith(".tsx")) return { icon: "ts", color: "text-sky-400" };
  if (path.endsWith(".js") || path.endsWith(".jsx")) return { icon: "js", color: "text-yellow-300" };
  if (path.endsWith(".json")) return { icon: "{}", color: "text-emerald-400" };
  if (path.endsWith(".md")) return { icon: "md", color: "text-cyan-300" };
  if (path.endsWith(".toml") || path.endsWith(".yaml") || path.endsWith(".yml"))
    return { icon: "cfg", color: "text-purple-400" };
  if (path.endsWith(".sh") || path.endsWith(".bash")) return { icon: "$", color: "text-lime-400" };
  return { icon: "•", color: "text-zinc-400" };
}

export function ExplorerPanel({
  repositories,
  activeRepo,
  onSelectRepo,
  indexStatus,
  indexing,
  onReindex,
  files,
  loadingFiles,
  selectedFilePath,
  onSelectFile,
  onSelectSymbol,
  isCollapsed,
  onToggleCollapse,
}: ExplorerPanelProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [openFolders, setOpenFolders] = useState<Record<string, boolean>>({});
  const [expandedFiles, setExpandedFiles] = useState<Record<string, boolean>>({});

  const toggleFolder = (folderPath: string) => {
    setOpenFolders((prev) => ({ ...prev, [folderPath]: !prev[folderPath] }));
  };

  const toggleFileSymbols = (filePath: string) => {
    setExpandedFiles((prev) => ({ ...prev, [filePath]: !prev[filePath] }));
  };

  // Build tree from files
  const fileTree = useMemo(() => {
    const root: Record<string, TreeNode> = {};

    const filtered = searchQuery.trim()
      ? files.filter(
          (f) =>
            f.path.toLowerCase().includes(searchQuery.toLowerCase()) ||
            f.symbols?.some((s) => s.name.toLowerCase().includes(searchQuery.toLowerCase()))
        )
      : files;

    for (const file of filtered) {
      const parts = file.path.split("/");
      let currentLevel = root;
      let currentPath = "";

      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        currentPath = currentPath ? `${currentPath}/${part}` : part;
        const isFolder = i < parts.length - 1;

        if (!currentLevel[part]) {
          currentLevel[part] = {
            name: part,
            fullPath: currentPath,
            isFolder,
            children: {},
            fileItem: isFolder ? undefined : file,
          };
        }
        currentLevel = currentLevel[part].children;
      }
    }

    return root;
  }, [files, searchQuery]);

  // Recursively render tree nodes
  const renderTree = (nodes: Record<string, TreeNode>, depth = 0) => {
    const entries = Object.entries(nodes).sort(([, a], [, b]) => {
      if (a.isFolder === b.isFolder) return a.name.localeCompare(b.name);
      return a.isFolder ? -1 : 1;
    });

    return entries.map(([, node]) => {
      if (node.isFolder) {
        // Auto open all folders when searching
        const isOpen = searchQuery.trim() ? true : openFolders[node.fullPath] ?? (depth < 1);
        return (
          <div key={node.fullPath} className="select-none">
            <button
              onClick={() => toggleFolder(node.fullPath)}
              aria-expanded={isOpen}
              aria-label={`Toggle folder ${node.name}`}
              style={{ paddingLeft: `${depth * 12 + 8}px` }}
              className="flex w-full items-center gap-1.5 py-1 text-left text-xs text-zinc-300 hover:bg-white/5 transition-colors focus-visible:ring-1 focus-visible:ring-[#BFFF00]"
            >
              <span className="text-[10px] text-zinc-500 font-mono">
                {isOpen ? "▾" : "▸"}
              </span>
              <span className="text-zinc-400">📁</span>
              <span className="truncate font-sans font-medium">{node.name}</span>
            </button>
            {isOpen && <div>{renderTree(node.children, depth + 1)}</div>}
          </div>
        );
      }

      const fileItem = node.fileItem;
      const isSelected = selectedFilePath === node.fullPath;
      const { icon, color } = getFileIcon(node.name);
      const hasSymbols = fileItem && fileItem.symbols && fileItem.symbols.length > 0;
      const isSymbolsOpen = expandedFiles[node.fullPath];

      return (
        <div key={node.fullPath} className="select-none">
          <div
            style={{ paddingLeft: `${depth * 12 + 8}px` }}
            className={`group flex items-center justify-between py-1 pr-2 text-xs transition-colors ${
              isSelected
                ? "bg-[#BFFF00]/10 border-l-2 border-[#BFFF00] text-white"
                : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
            }`}
          >
            <button
              onClick={() => onSelectFile(node.fullPath)}
              className="flex min-w-0 flex-1 items-center gap-2 text-left"
            >
              <span className={`text-[10px] font-mono font-bold ${color}`}>{icon}</span>
              <span className="truncate font-mono">{node.name}</span>
            </button>

            {hasSymbols && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  toggleFileSymbols(node.fullPath);
                }}
                aria-expanded={isSymbolsOpen}
                aria-label={`${fileItem?.symbols?.length} AST symbols in ${node.name}`}
                className="shrink-0 rounded px-1 text-[10px] text-zinc-500 hover:text-cyan-300 focus-visible:ring-1 focus-visible:ring-cyan-400"
                title={`${fileItem?.symbols?.length} symbols (click to toggle)`}
              >
                {fileItem?.symbols?.length}s
              </button>
            )}
          </div>

          {/* Symbol children if expanded */}
          {hasSymbols && isSymbolsOpen && (
            <div className="bg-black/20 py-0.5">
              {fileItem?.symbols?.map((sym, sIdx) => (
                <button
                  key={`${sym.name}-${sIdx}`}
                  onClick={() => {
                    onSelectFile(node.fullPath);
                    if (onSelectSymbol) onSelectSymbol(node.fullPath, sym.line);
                  }}
                  style={{ paddingLeft: `${depth * 12 + 28}px` }}
                  className="flex w-full items-center gap-2 py-0.5 text-left text-[11px] text-zinc-400 hover:text-cyan-300 hover:bg-white/5 transition-colors font-mono"
                >
                  <span className="text-[9px] uppercase tracking-wider text-zinc-500">
                    {sym.kind.slice(0, 3)}
                  </span>
                  <span className="truncate">{sym.name}</span>
                  <span className="ml-auto pr-2 text-[10px] text-zinc-600">L{sym.line}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      );
    });
  };

  if (isCollapsed) {
    return (
      <div className="flex h-full flex-col items-center border-r border-white/10 bg-[#0c0e12] py-3 w-8 select-none">
        <button
          onClick={onToggleCollapse}
          aria-label="Expand Explorer Panel"
          className="text-xs text-zinc-400 hover:text-white transition-colors focus-visible:ring-1 focus-visible:ring-[#BFFF00] p-1"
          title="Expand Explorer Panel"
        >
          ▸
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-r border-white/10 bg-[#0c0e12] overflow-hidden select-none">
      {/* Top Header: Repo Switcher */}
      <div className="p-3 border-b border-white/10 bg-[#0f1217]">
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-zinc-400">
            Explorer
          </span>
          <button
            onClick={onToggleCollapse}
            aria-label="Collapse Explorer"
            className="text-zinc-500 hover:text-white text-xs p-1 focus-visible:ring-1 focus-visible:ring-[#BFFF00]"
            title="Collapse Explorer"
          >
            ◂
          </button>
        </div>

        {/* Repository Selector */}
        <select
          aria-label="Select active repository"
          value={activeRepo?.id || ""}
          onChange={(e) => {
            const found = repositories.find((r) => r.id === Number(e.target.value));
            if (found) onSelectRepo(found);
          }}
          className="w-full rounded-lg border border-white/10 bg-[#161a22] px-2.5 py-1.5 text-xs text-zinc-200 outline-none focus:border-[#BFFF00]/50 font-sans"
        >
          {repositories.length === 0 ? (
            <option value="">No repositories imported</option>
          ) : (
            repositories.map((repo) => (
              <option key={repo.id} value={repo.id}>
                {repo.full_name}
              </option>
            ))
          )}
        </select>
      </div>

      {/* Index Status Card */}
      {activeRepo && (() => {
        const badge = getIndexStatusBadge(indexStatus?.status, indexing);
        return (
          <div className="p-3 border-b border-white/10 bg-[#0a0c0f] text-xs">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <span
                  className={`size-2 rounded-full ${badge.dotClass} ${
                    badge.isAnimated ? "animate-pulse" : ""
                  }`}
                />
                <span className={`font-medium capitalize font-mono text-[11px] ${badge.colorClass}`}>
                  {badge.label}
                </span>
              </div>

              <button
                onClick={() => onReindex(activeRepo.id)}
                disabled={indexing || isIndexActive(indexStatus?.status)}
                className="rounded bg-white/5 border border-white/10 px-2 py-0.5 text-[10px] font-mono text-zinc-300 hover:bg-white/10 hover:text-white disabled:opacity-50 transition-colors"
              >
                {indexing || isIndexActive(indexStatus?.status) ? "Running" : "Reindex"}
              </button>
            </div>

            {indexStatus && (
              <div className="mt-2 space-y-1 font-mono text-[11px] text-zinc-400">
                <div className="flex justify-between">
                  <span className="text-zinc-500">Commit:</span>
                  <span className="text-cyan-300 truncate max-w-[120px]" title={indexStatus.commit_sha}>
                    {indexStatus.commit_sha ? indexStatus.commit_sha.slice(0, 7) : "None"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Files / Symbols:</span>
                  <span>
                    {indexStatus.file_count} / {indexStatus.symbol_count ?? 0}
                  </span>
                </div>
              </div>
            )}

            {indexStatus?.error && (
              <div className="mt-2 rounded border border-rose-900/40 bg-rose-950/20 p-2 text-[10px] text-rose-300">
                {indexStatus.error}
              </div>
            )}
          </div>
        );
      })()}

      {/* File Search Input */}
      <div className="p-2 border-b border-white/10 bg-[#0d0f14]">
        <div className="relative">
          <input
            type="text"
            aria-label="Filter indexed files"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filter files or symbols…"
            className="w-full rounded-md border border-white/10 bg-[#141820] px-2.5 py-1 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-cyan-400/50 font-mono"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-2 top-1 text-xs text-zinc-400 hover:text-white"
            >
              ×
            </button>
          )}
        </div>
      </div>

      {/* File Tree Area */}
      <div className="flex-1 overflow-y-auto py-2">
        {loadingFiles ? (
          <div className="p-4 text-center text-xs text-zinc-500">Loading indexed file tree…</div>
        ) : files.length === 0 ? (
          <div className="p-4 text-center text-xs text-zinc-500">
            {activeRepo
              ? isIndexCompleted(indexStatus?.status)
                ? "No indexed files in this repository."
                : isIndexActive(indexStatus?.status) || indexing
                ? "Repository is currently being indexed…"
                : isIndexFailed(indexStatus?.status)
                ? "Indexing failed for this repository."
                : "Index repository source to browse code tree."
              : "Select a repository to explore."}
          </div>
        ) : (
          renderTree(fileTree)
        )}
      </div>

      {/* Footer statistics */}
      <div className="border-t border-white/10 bg-[#090b0e] px-3 py-1.5 flex items-center justify-between text-[10px] text-zinc-500 font-mono">
        <span>{files.length} indexed files</span>
        <span>Tree view</span>
      </div>
    </div>
  );
}
