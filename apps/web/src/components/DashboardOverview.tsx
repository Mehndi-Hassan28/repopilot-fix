"use client";

import { FormEvent, useState } from "react";
import { Repository, IndexStatus, WorkspaceView, AuthUser } from "../lib/types";
import { GithubIcon, PlusIcon } from "./Icons";

interface DashboardOverviewProps {
  currentUser: AuthUser | null;
  repositories: Repository[];
  indexes: Record<number, IndexStatus>;
  loading: boolean;
  onRefresh: () => void;
  onImportRepo: (url: string) => Promise<void>;
  onSelectRepo: (repo: Repository) => void;
  onSelectView: (view: WorkspaceView) => void;
  onReindex: (repoId: number) => void;
  indexingId: number | null;
  onSignIn: () => void;
}

export function DashboardOverview({
  currentUser,
  repositories,
  indexes,
  loading,
  onRefresh,
  onImportRepo,
  onSelectRepo,
  onSelectView,
  onReindex,
  indexingId,
  onSignIn,
}: DashboardOverviewProps) {
  const [importUrl, setImportUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState("");
  const [importSuccess, setImportSuccess] = useState("");
  const [showImportDialog, setShowImportDialog] = useState(false);

  const handleImport = async (e: FormEvent) => {
    e.preventDefault();
    if (!importUrl.trim()) return;
    setImporting(true);
    setImportError("");
    setImportSuccess("");
    try {
      await onImportRepo(importUrl.trim());
      setImportSuccess("Repository imported and registered.");
      setImportUrl("");
      setShowImportDialog(false);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Failed to import repository.");
    } finally {
      setImporting(false);
    }
  };

  const totalFiles = Object.values(indexes).reduce((acc, curr) => acc + (curr.file_count || 0), 0);
  const totalSymbols = Object.values(indexes).reduce((acc, curr) => acc + (curr.symbol_count || 0), 0);
  const readyCount = Object.values(indexes).filter((i) => i.status === "ready").length;

  return (
    <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-8 bg-[#050505] relative custom-scrollbar">
      {/* Background Decor Glows */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-[-5%] right-[-5%] w-[45%] h-[35%] bg-lime-500/5 blur-[120px] rounded-full" />
        <div className="absolute bottom-[-5%] left-[-5%] w-[35%] h-[30%] bg-cyan-500/5 blur-[100px] rounded-full" />
      </div>

      {/* Guest Mode Banner if unauthenticated */}
      {!currentUser && (
        <div className="relative z-10 rounded-2xl border border-white/10 bg-[#0e1117] p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 shadow-xl">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="size-2 rounded-full bg-cyan-400 animate-pulse" />
              <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-cyan-300">
                Guest Workspace Preview
              </span>
            </div>
            <p className="text-sm font-semibold text-zinc-200">
              Explore RepoPilot Fix Developer Hub
            </p>
            <p className="text-xs text-zinc-400">
              Sign in with your GitHub account to connect, index, and analyze your repositories.
            </p>
          </div>
          <button
            onClick={onSignIn}
            className="shrink-0 flex items-center gap-2 rounded-xl bg-white text-black px-4 py-2 text-xs font-semibold hover:bg-[#BFFF00] transition-colors"
          >
            <GithubIcon className="size-4" />
            <span>Sign in with GitHub</span>
          </button>
        </div>
      )}

      {/* Dashboard Top Hero */}
      <div className="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-white/5 pb-6">
        <div>
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-[#BFFF00] animate-pulse" />
            <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#BFFF00]">
              Developer Intelligence Hub
            </span>
          </div>
          <h1 className="mt-1 text-2xl md:text-3xl font-semibold tracking-tight text-[#F5F5F5]">
            Workspace Overview
          </h1>
          <p className="mt-1 text-xs text-zinc-400">
            Real-time repository index state, AST symbol graphs, and differential verification.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={onRefresh}
            className="rounded-xl border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-zinc-300 hover:text-white hover:border-white/20 transition-all font-mono"
            title="Refresh repository list"
          >
            ↻ Refresh
          </button>
          <button
            onClick={() => setShowImportDialog(true)}
            className="rounded-xl bg-white text-black px-3.5 py-1.5 text-xs font-semibold hover:bg-[#BFFF00] transition-all flex items-center gap-1.5"
          >
            <PlusIcon className="size-3.5" />
            <span>Import Repository</span>
          </button>
          <button
            onClick={() => onSelectView("chat")}
            className="rounded-xl bg-[#BFFF00] px-4 py-1.5 text-xs font-semibold text-black hover:brightness-110 shadow-lg shadow-[#BFFF00]/10 transition-all"
          >
            Open AI Workspace
          </button>
        </div>
      </div>

      {/* KPI Stats Grid */}
      <div className="relative z-10 grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="rounded-2xl border border-white/5 bg-[#0e1117]/80 backdrop-blur-md p-4 md:p-5 shadow-lg">
          <p className="text-[11px] font-mono text-zinc-400 uppercase tracking-wider">Repositories</p>
          <p className="mt-2 text-2xl md:text-3xl font-bold font-mono text-[#F5F5F5]">{repositories.length}</p>
          <p className="mt-1 text-[11px] text-zinc-500 font-sans">Connected in workspace</p>
        </div>

        <div className="rounded-2xl border border-white/5 bg-[#0e1117]/80 backdrop-blur-md p-4 md:p-5 shadow-lg">
          <p className="text-[11px] font-mono text-zinc-400 uppercase tracking-wider">Indexed Status</p>
          <p className="mt-2 text-2xl md:text-3xl font-bold font-mono text-[#BFFF00]">{readyCount} Ready</p>
          <p className="mt-1 text-[11px] text-zinc-500 font-sans">AST semantic graphs</p>
        </div>

        <div className="rounded-2xl border border-white/5 bg-[#0e1117]/80 backdrop-blur-md p-4 md:p-5 shadow-lg">
          <p className="text-[11px] font-mono text-zinc-400 uppercase tracking-wider">Indexed Files</p>
          <p className="mt-2 text-2xl md:text-3xl font-bold font-mono text-cyan-300">{totalFiles.toLocaleString()}</p>
          <p className="mt-1 text-[11px] text-zinc-500 font-sans">Parsed source files</p>
        </div>

        <div className="rounded-2xl border border-white/5 bg-[#0e1117]/80 backdrop-blur-md p-4 md:p-5 shadow-lg">
          <p className="text-[11px] font-mono text-zinc-400 uppercase tracking-wider">Indexed Symbols</p>
          <p className="mt-2 text-2xl md:text-3xl font-bold font-mono text-purple-300">{totalSymbols.toLocaleString()}</p>
          <p className="mt-1 text-[11px] text-zinc-500 font-sans">Functions, classes & vars</p>
        </div>
      </div>

      {/* Import Modal Dialog / Banner */}
      {showImportDialog && (
        <div className="relative z-20 rounded-2xl border border-white/10 bg-[#0d1016] p-5 shadow-2xl space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-zinc-200">Import Public GitHub Repository</h2>
            <button
              onClick={() => setShowImportDialog(false)}
              className="text-xs text-zinc-400 hover:text-white font-mono"
            >
              ✕ Close
            </button>
          </div>

          <form onSubmit={handleImport} className="flex flex-col sm:flex-row gap-2.5">
            <input
              type="url"
              required
              value={importUrl}
              onChange={(e) => setImportUrl(e.target.value)}
              placeholder="https://github.com/owner/repository"
              className="flex-1 rounded-xl border border-white/10 bg-[#141822] px-3.5 py-2 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-[#BFFF00]/50 font-mono"
            />
            <button
              type="submit"
              disabled={importing}
              className="rounded-xl bg-[#BFFF00] px-5 py-2 text-xs font-semibold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
            >
              {importing ? "Importing…" : "Import Repository"}
            </button>
          </form>

          {importError && (
            <p className="text-xs text-rose-300 font-mono">{importError}</p>
          )}
          {importSuccess && (
            <p className="text-xs text-[#BFFF00] font-mono">{importSuccess}</p>
          )}
        </div>
      )}

      {/* Connected Repositories Section */}
      <div className="relative z-10 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-[#F5F5F5]">
              Connected Repositories ({repositories.length})
            </h2>
            <span className="text-[11px] text-zinc-500 font-mono">Live registry</span>
          </div>
          <span className="text-xs text-zinc-400 font-mono">
            {readyCount} of {repositories.length} indexed
          </span>
        </div>

        {loading ? (
          <div className="rounded-2xl border border-white/5 bg-[#0e1117] p-12 text-center text-xs text-zinc-500 font-mono">
            Loading repository registry…
          </div>
        ) : repositories.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-white/10 bg-[#0e1117]/50 p-12 text-center space-y-3">
            <p className="text-sm font-medium text-zinc-300">No repositories imported yet</p>
            <p className="text-xs text-zinc-500 max-w-sm mx-auto">
              Import a public GitHub repository to index its files, extract AST symbols, and unlock grounded AI engineering.
            </p>
            <button
              onClick={() => setShowImportDialog(true)}
              className="rounded-xl bg-white text-black px-4 py-2 text-xs font-semibold hover:bg-[#BFFF00] transition-colors"
            >
              Import your first repository
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {repositories.map((repo) => {
              const status = indexes[repo.id];
              const isIndexing = indexingId === repo.id;

              return (
                <div
                  key={repo.id}
                  className="rounded-2xl border border-white/5 bg-[#0e1117]/90 backdrop-blur-sm p-5 flex flex-col justify-between hover:border-white/20 transition-all lime-glow group"
                >
                  <div>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <span className="text-[10px] font-mono text-zinc-500 truncate block">
                          {repo.owner}
                        </span>
                        <h3 className="text-sm font-semibold text-zinc-200 group-hover:text-[#BFFF00] truncate transition-colors">
                          {repo.name}
                        </h3>
                      </div>
                      <span className="shrink-0 rounded-lg bg-white/5 border border-white/10 px-2 py-0.5 text-[10px] font-mono text-zinc-400">
                        ★ {repo.stars.toLocaleString()}
                      </span>
                    </div>

                    <p className="mt-2.5 text-xs text-zinc-400 line-clamp-2 min-h-8 leading-relaxed">
                      {repo.description || "No repository description provided."}
                    </p>

                    <div className="mt-4 pt-3 border-t border-white/5 space-y-2 font-mono text-[11px] text-zinc-400">
                      <div className="flex justify-between items-center">
                        <span className="text-zinc-500">Status:</span>
                        <span
                          className={`font-semibold capitalize flex items-center gap-1.5 ${
                            isIndexing
                              ? "text-amber-400 animate-pulse"
                              : status?.status === "ready"
                              ? "text-[#BFFF00]"
                              : "text-zinc-400"
                          }`}
                        >
                          <span
                            className={`size-1.5 rounded-full ${
                              isIndexing
                                ? "bg-amber-400 animate-ping"
                                : status?.status === "ready"
                                ? "bg-[#BFFF00]"
                                : "bg-zinc-500"
                            }`}
                          />
                          {isIndexing ? "Indexing…" : status?.status || "Unindexed"}
                        </span>
                      </div>

                      {status && (
                        <>
                          <div className="flex justify-between items-center">
                            <span className="text-zinc-500">Commit SHA:</span>
                            <span className="text-cyan-300 font-bold">
                              {status.commit_sha ? status.commit_sha.slice(0, 7) : "None"}
                            </span>
                          </div>
                          <div className="flex justify-between items-center">
                            <span className="text-zinc-500">Indexed Files:</span>
                            <span className="text-zinc-300">{status.file_count} files</span>
                          </div>
                        </>
                      )}
                    </div>
                  </div>

                  <div className="mt-5 pt-3 border-t border-white/5 flex items-center justify-between gap-2">
                    <button
                      onClick={() => {
                        onSelectRepo(repo);
                        onSelectView("repos");
                      }}
                      className="text-xs text-cyan-300 hover:text-cyan-200 font-mono transition-colors flex items-center gap-1"
                    >
                      <span>Browse Code</span>
                      <span>→</span>
                    </button>

                    <button
                      onClick={() => onReindex(repo.id)}
                      disabled={isIndexing}
                      className="rounded-lg bg-white/5 border border-white/10 px-3 py-1 text-[11px] font-mono text-zinc-300 hover:bg-white/10 hover:text-white disabled:opacity-50 transition-colors"
                    >
                      {isIndexing ? "Indexing…" : status ? "Reindex" : "Index"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Quick Navigation Cards */}
      <div className="relative z-10 pt-4 border-t border-white/5">
        <h3 className="text-xs font-mono uppercase tracking-widest text-zinc-500 mb-4">
          Core Workspaces
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
          <button
            onClick={() => onSelectView("chat")}
            className="rounded-xl border border-white/5 bg-[#0c0e14] p-4 text-left hover:border-cyan-400/30 transition-all group"
          >
            <span className="text-cyan-400 text-xs font-mono block mb-1">01 / AI Chat</span>
            <span className="text-sm font-semibold text-zinc-200 group-hover:text-cyan-300 block">
              Grounded Q&A
            </span>
            <span className="text-[11px] text-zinc-500 mt-1 block">
              Ask questions backed by AST citations.
            </span>
          </button>

          <button
            onClick={() => onSelectView("issues")}
            className="rounded-xl border border-white/5 bg-[#0c0e14] p-4 text-left hover:border-amber-400/30 transition-all group"
          >
            <span className="text-amber-400 text-xs font-mono block mb-1">02 / Issues</span>
            <span className="text-sm font-semibold text-zinc-200 group-hover:text-amber-300 block">
              Root-Cause Analysis
            </span>
            <span className="text-[11px] text-zinc-500 mt-1 block">
              Synthesize bug hypotheses from issues.
            </span>
          </button>

          <button
            onClick={() => onSelectView("patch")}
            className="rounded-xl border border-white/5 bg-[#0c0e14] p-4 text-left hover:border-[#BFFF00]/30 transition-all group"
          >
            <span className="text-[#BFFF00] text-xs font-mono block mb-1">03 / Patch Review</span>
            <span className="text-sm font-semibold text-zinc-200 group-hover:text-[#BFFF00] block">
              Unified & Split Diff
            </span>
            <span className="text-[11px] text-zinc-500 mt-1 block">
              Review diffs before sandbox execution.
            </span>
          </button>

          <button
            onClick={() => onSelectView("verification")}
            className="rounded-xl border border-white/5 bg-[#0c0e14] p-4 text-left hover:border-purple-400/30 transition-all group"
          >
            <span className="text-purple-400 text-xs font-mono block mb-1">04 / Sandbox</span>
            <span className="text-sm font-semibold text-zinc-200 group-hover:text-purple-300 block">
              Test Verification
            </span>
            <span className="text-[11px] text-zinc-500 mt-1 block">
              Execute test suites in Docker containers.
            </span>
          </button>
        </div>
      </div>
    </div>
  );
}
