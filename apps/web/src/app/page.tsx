"use client";

import { useCallback, useEffect, useRef, useState, FormEvent } from "react";
import {
  Repository,
  IndexStatus,
  IndexedFileItem,
  ChatSession,
  ChatMessage,
  IssueAnalysisResponse,
  WorkspaceView,
  AuthUser,
  OpenTab,
  FileContentResponse,
} from "../lib/types";
import { apiRequest, getApiUrl } from "../lib/api";
import { ToastProvider, useToast } from "../components/Toast";
import { CommandPalette } from "../components/CommandPalette";
import { NavigationRail } from "../components/NavigationRail";
import { Header } from "../components/Header";
import { ExplorerPanel } from "../components/ExplorerPanel";
import { CodeViewer } from "../components/CodeViewer";
import { DashboardOverview } from "../components/DashboardOverview";
import { IssueWorkspace } from "../components/IssueWorkspace";
import { PatchReviewWorkspace } from "../components/PatchReviewWorkspace";
import { VerificationWorkspace } from "../components/VerificationWorkspace";
import { ActivityWorkspace } from "../components/ActivityWorkspace";
import { SettingsWorkspace } from "../components/SettingsWorkspace";
import { AiChatPanel } from "../components/AiChatPanel";
import { BottomPanel } from "../components/BottomPanel";



function MainWorkspace() {
  const { showToast } = useToast();
  const apiUrl = getApiUrl();

  // Core Data State
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [activeRepo, setActiveRepo] = useState<Repository | null>(null);
  const [indexes, setIndexes] = useState<Record<number, IndexStatus>>({});
  const [indexingId, setIndexingId] = useState<number | null>(null);
  const [loadingRepos, setLoadingRepos] = useState(true);

  // Auth & Backend State
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [backendStatus, setBackendStatus] = useState<"ready" | "connecting" | "offline">("connecting");
  const csrfToken = useRef("");

  // Workspace Navigation & Views
  const [currentView, setCurrentView] = useState<WorkspaceView>("overview");
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);
  const [isExplorerCollapsed, setIsExplorerCollapsed] = useState(false);
  const [isChatCollapsed, setIsChatCollapsed] = useState(false);
  const [isBottomExpanded, setIsBottomExpanded] = useState(false);
  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const [modalImportUrl, setModalImportUrl] = useState("");
  const [modalImporting, setModalImporting] = useState(false);
  const [modalImportError, setModalImportError] = useState("");

  // Files & Code Viewer State
  const [indexedFiles, setIndexedFiles] = useState<IndexedFileItem[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [openTabs, setOpenTabs] = useState<OpenTab[]>([]);
  const [activeTabPath, setActiveTabPath] = useState<string | null>(null);

  // Chat State
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [chatBusy, setChatBusy] = useState(false);
  const [chatError, setChatError] = useState("");
  const [chatMode, setChatMode] = useState<"auto" | "general" | "coding" | "repository">("auto");
  const [lastFailedQuestion, setLastFailedQuestion] = useState<{ text: string; mode: string } | null>(null);

  // Issue & Patch State
  const [analysisResult, setAnalysisResult] = useState<IssueAnalysisResponse | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState("");

  // Load Repositories and Index Statuses
  const loadRepositories = useCallback(async () => {
    setLoadingRepos(true);
    try {
      const response = await apiRequest("/api/repositories", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load repositories from API.");
      const items = (await response.json()) as Repository[];
      setRepositories(items);

      if (items.length > 0 && !activeRepo) {
        setActiveRepo(items[0]);
      }

      const statuses = await Promise.all(
        items.map(async (repo) => {
          const statusRes = await apiRequest(`/api/repositories/${repo.id}/index`, { cache: "no-store" });
          if (!statusRes.ok) return null;
          return [repo.id, (await statusRes.json()) as IndexStatus] as const;
        })
      );
      setIndexes(
        Object.fromEntries(statuses.filter((item): item is NonNullable<typeof item> => item !== null))
      );
    } catch (reason) {
      showToast(reason instanceof Error ? reason.message : "Failed to load repositories", "error");
    } finally {
      setLoadingRepos(false);
    }
  }, [activeRepo, showToast]);

  // Auth & Backend Initialization
  useEffect(() => {
    void (async () => {
      try {
        const healthRes = await apiRequest("/health", { cache: "no-store" }).catch(() => null);
        if (healthRes && healthRes.ok) {
          setBackendStatus("ready");
        } else {
          setBackendStatus("offline");
        }

        const response = await apiRequest("/api/auth/session", { cache: "no-store" });
        const session = await response.json();
        if (session.authenticated) {
          setCurrentUser(session.user as AuthUser);
          csrfToken.current = session.csrf_token as string;
          await loadRepositories();
        } else {
          setRepositories([]);
          setLoadingRepos(false);
        }
      } catch (reason) {
        setBackendStatus("offline");
        showToast(reason instanceof Error ? reason.message : "Authentication check failed", "error");
        setLoadingRepos(false);
      } finally {
        setAuthLoading(false);
      }
    })();
  }, [loadRepositories, showToast]);

  // Load Indexed Files when active repository changes
  const loadIndexedFiles = useCallback(
    async (repoId: number) => {
      setLoadingFiles(true);
      try {
        const response = await apiRequest(`/api/repositories/${repoId}/files`, { cache: "no-store" });
        if (response.ok) {
          const items = (await response.json()) as IndexedFileItem[];
          setIndexedFiles(items);
        } else {
          setIndexedFiles([]);
        }
      } catch {
        setIndexedFiles([]);
      } finally {
        setLoadingFiles(false);
      }
    },
    []
  );

  // Load Chat Sessions when active repository changes
  const loadSessions = useCallback(
    async (repoId: number) => {
      try {
        const response = await apiRequest(`/api/chat/sessions?repository_id=${repoId}`, { cache: "no-store" });
        if (response.ok) {
          const items = (await response.json()) as ChatSession[];
          setSessions(items);
          if (items.length > 0) {
            // Load latest session details
            const sRes = await apiRequest(`/api/chat/sessions/${items[0].id}`, { cache: "no-store" });
            if (sRes.ok) {
              setActiveSession((await sRes.json()) as ChatSession);
            }
          } else {
            setActiveSession(null);
          }
        }
      } catch {
        setSessions([]);
        setActiveSession(null);
      }
    },
    []
  );

  // Effect: When activeRepo changes, load files and chat sessions
  useEffect(() => {
    if (activeRepo) {
      void loadIndexedFiles(activeRepo.id);
      void loadSessions(activeRepo.id);
    }
  }, [activeRepo, loadIndexedFiles, loadSessions]);

  // Auth Handlers
  const signIn = () => {
    window.location.assign(`${apiUrl}/api/auth/github/login`);
  };

  const signOut = async () => {
    try {
      const response = await apiRequest("/api/auth/logout", { method: "POST" }, csrfToken.current);
      if (!response.ok) throw new Error("Could not sign out.");
      csrfToken.current = "";
      setCurrentUser(null);
      setRepositories([]);
      setSessions([]);
      setActiveSession(null);
      showToast("Signed out successfully", "info");
    } catch (reason) {
      showToast(reason instanceof Error ? reason.message : "Sign out error", "error");
    }
  };

  // Reindex Action
  const handleReindex = async (repoId: number) => {
    setIndexingId(repoId);
    showToast("Indexing initiated in background…", "info");
    try {
      const res = await apiRequest(`/api/repositories/${repoId}/index`, { method: "POST" }, csrfToken.current);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Indexing failed to start.");
      setIndexes((prev) => ({ ...prev, [repoId]: data }));

      // Poll until finished
      for (let attempt = 0; attempt < 30; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const statusRes = await apiRequest(`/api/repositories/${repoId}/index`, { cache: "no-store" });
        if (!statusRes.ok) break;
        const status = (await statusRes.json()) as IndexStatus;
        setIndexes((prev) => ({ ...prev, [repoId]: status }));
        if (!["queued", "running"].includes(status.status)) {
          if (status.status === "ready") {
            showToast("Repository indexing completed successfully", "success");
            void loadIndexedFiles(repoId);
          }
          break;
        }
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Indexing error", "error");
    } finally {
      setIndexingId(null);
    }
  };

  // Import Repo Action
  const handleImportRepo = async (url: string) => {
    const res = await apiRequest(
      "/api/repositories/import",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      },
      csrfToken.current
    );
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Repository import failed.");
    const repo = data as Repository;
    setRepositories((prev) => (prev.some((r) => r.id === repo.id) ? prev : [repo, ...prev]));
    setActiveRepo(repo);
    showToast(`${repo.full_name} imported into workspace`, "success");
  };

  const handleModalImport = async (e: FormEvent) => {
    e.preventDefault();
    if (!modalImportUrl.trim()) return;
    setModalImporting(true);
    setModalImportError("");
    try {
      await handleImportRepo(modalImportUrl.trim());
      setModalImportUrl("");
      setIsImportModalOpen(false);
    } catch (err) {
      setModalImportError(err instanceof Error ? err.message : "Failed to import repository.");
    } finally {
      setModalImporting(false);
    }
  };

  // File & Tab Actions
  const loadFileContent = useCallback(
    async (repoId: number, path: string) => {
      setOpenTabs((prev) =>
        prev.map((t) => (t.path === path ? { ...t, loading: true, error: undefined } : t))
      );
      try {
        const res = await apiRequest(
          `/api/repositories/${repoId}/file?path=${encodeURIComponent(path)}`,
          { cache: "no-store" }
        );
        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.detail || "Could not load file content.");
        }
        const data = (await res.json()) as FileContentResponse;
        setOpenTabs((prev) =>
          prev.map((t) =>
            t.path === path
              ? {
                  ...t,
                  content: data.content,
                  symbols: data.symbols,
                  language: data.language,
                  size_bytes: data.size_bytes,
                  loading: false,
                }
              : t
          )
        );
      } catch (err) {
        setOpenTabs((prev) =>
          prev.map((t) =>
            t.path === path
              ? {
                  ...t,
                  loading: false,
                  error: err instanceof Error ? err.message : "Failed to load file content",
                }
              : t
          )
        );
      }
    },
    []
  );

  const handleOpenFile = (path: string) => {
    const existing = openTabs.find((t) => t.path === path);
    if (!existing) {
      setOpenTabs((prev) => [...prev, { path, loading: true }]);
      if (activeRepo) {
        void loadFileContent(activeRepo.id, path);
      }
    } else if (!existing.content && !existing.loading && activeRepo) {
      void loadFileContent(activeRepo.id, path);
    }
    setActiveTabPath(path);
  };

  const handleCloseTab = (path: string) => {
    setOpenTabs((prev) => prev.filter((t) => t.path !== path));
    if (activeTabPath === path) {
      const remaining = openTabs.filter((t) => t.path !== path);
      setActiveTabPath(remaining.length > 0 ? remaining[remaining.length - 1].path : null);
    }
  };

  const handleOpenCitation = (path: string, start: number, end: number) => {
    setOpenTabs((prev) => {
      const exists = prev.find((t) => t.path === path);
      if (exists) {
        return prev.map((t) =>
          t.path === path ? { ...t, highlightRange: { start, end } } : t
        );
      }
      return [...prev, { path, highlightRange: { start, end }, loading: true }];
    });
    const existing = openTabs.find((t) => t.path === path);
    if ((!existing || !existing.content) && activeRepo) {
      void loadFileContent(activeRepo.id, path);
    }
    setActiveTabPath(path);
    setCurrentView("repos");
    showToast(`Navigated to ${path}:L${start}-L${end}`, "info");
  };

  const handleClearHighlight = (path: string) => {
    setOpenTabs((prev) =>
      prev.map((t) => (t.path === path ? { ...t, highlightRange: undefined } : t))
    );
  };

  const handleSelectSymbol = (path: string, line: number) => {
    handleOpenCitation(path, line, line);
  };

  // Chat Actions
  const handleNewSession = async () => {
    if (!activeRepo) return;
    setChatBusy(true);
    setChatError("");
    setLastFailedQuestion(null);
    try {
      const res = await apiRequest(
        "/api/chat/sessions",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repository_id: activeRepo.id }),
        },
        csrfToken.current
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not create chat session.");
      const session = data as ChatSession;
      setSessions((prev) => [session, ...prev]);
      setActiveSession({ ...session, messages: [] });
      showToast("Fresh conversation initialized", "info");
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "Failed to start chat");
    } finally {
      setChatBusy(false);
    }
  };

  const handleSelectSession = async (session: ChatSession) => {
    setChatBusy(true);
    setChatError("");
    setLastFailedQuestion(null);
    try {
      const res = await apiRequest(`/api/chat/sessions/${session.id}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not load session.");
      setActiveSession(data as ChatSession);
    } catch (err) {
      setChatError(err instanceof Error ? err.message : "Failed to load session");
    } finally {
      setChatBusy(false);
    }
  };

  const handleDeleteSession = async (session: ChatSession) => {
    if (!window.confirm(`Delete conversation "${session.title}"?`)) return;
    try {
      const res = await apiRequest(`/api/chat/sessions/${session.id}`, { method: "DELETE" }, csrfToken.current);
      if (!res.ok) throw new Error("Could not delete session.");
      setSessions((prev) => prev.filter((s) => s.id !== session.id));
      if (activeSession?.id === session.id) {
        setActiveSession(null);
      }
      showToast("Conversation deleted", "info");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to delete session", "error");
    }
  };

  const handleClearSessionHistory = async () => {
    if (!activeSession || !window.confirm("Clear all messages in this conversation?")) return;
    try {
      const res = await apiRequest(
        `/api/chat/sessions/${activeSession.id}/messages`,
        { method: "DELETE" },
        csrfToken.current
      );
      if (!res.ok) throw new Error("Could not clear messages.");
      setActiveSession((prev) => (prev ? { ...prev, messages: [] } : null));
      showToast("Conversation history cleared", "info");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Failed to clear history", "error");
    }
  };

  const handleSendMessage = async (text: string) => {
    if (!activeSession || !text.trim() || chatBusy) return;
    const modeToUse = chatMode;
    setChatBusy(true);
    setChatError("");
    try {
      const res = await apiRequest(
        `/api/chat/sessions/${activeSession.id}/messages`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: text, mode: modeToUse === "auto" ? null : modeToUse }),
        },
        csrfToken.current
      );
      const data = (await res.json()) as ChatMessage & { detail?: string };
      if (!res.ok) {
        throw new Error(data.detail || "Question could not be answered.");
      }

      setActiveSession((current) =>
        current
          ? {
              ...current,
              messages: [
                ...(current.messages || []),
                { id: Date.now(), role: "user", content: text, citations: [], mode: modeToUse },
                data,
              ],
            }
          : current
      );

      setSessions((items) =>
        items.map((item) =>
          item.id === activeSession.id && item.title === "New conversation"
            ? { ...item, title: text.slice(0, 180) }
            : item
        )
      );
      setLastFailedQuestion(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Question could not be answered.";
      setChatError(msg);
      setLastFailedQuestion({ text, mode: modeToUse });
    } finally {
      setChatBusy(false);
    }
  };

  const handleRetryLastQuestion = () => {
    if (lastFailedQuestion) {
      void handleSendMessage(lastFailedQuestion.text);
    }
  };

  // Issue Analysis Action
  const handleAnalyzeIssue = async (repoId: number, issueNum: number) => {
    setAnalyzing(true);
    setAnalysisError("");
    setAnalysisResult(null);
    try {
      const res = await apiRequest(
        `/api/repositories/${repoId}/issues/analyze`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ issue_number: issueNum }),
        },
        csrfToken.current
      );
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Issue analysis failed.");
      }
      setAnalysisResult(data as IssueAnalysisResponse);
      showToast(`Analyzed issue #${issueNum} with grounded AST evidence`, "success");
    } catch (err) {
      setAnalysisError(err instanceof Error ? err.message : "Analysis failed.");
    } finally {
      setAnalyzing(false);
    }
  };

  const activeIndexStatus = activeRepo ? indexes[activeRepo.id] || null : null;

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-[#050505] text-zinc-400 font-mono text-xs">
        <div className="flex items-center gap-3">
          <span className="size-2 rounded-full bg-[#BFFF00] animate-pulse" />
          <span>Connecting to RepoPilot Intelligence Gateway…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen flex-col bg-[#050505] text-[#F5F5F5] overflow-hidden relative">
      {/* Background Decor from Superdesign v2 */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden z-0">
        <div className="absolute top-[-10%] right-[-10%] w-[40%] h-[40%] bg-lime-500/10 blur-[120px] rounded-full" />
        <div className="absolute bottom-[-10%] left-[-10%] w-[30%] h-[30%] bg-cyan-500/10 blur-[100px] rounded-full" />
        <div className="absolute inset-0 scanline opacity-30" />
      </div>

      {/* Top Main Work Area */}
      <div className="flex flex-1 overflow-hidden relative z-10">
        {/* Left 56px Navigation Rail */}
        <NavigationRail
          currentView={currentView}
          onSelectView={setCurrentView}
          currentUser={currentUser}
          onOpenCommandPalette={() => setIsCommandPaletteOpen(true)}
          onSignOut={() => void signOut()}
          onSignIn={signIn}
        />

        {/* Center + Explorer + Right Panels Column */}
        <div className="flex-1 flex flex-col min-w-0 h-full overflow-hidden">
          {/* Top Header */}
          <Header
            currentUser={currentUser}
            activeRepoName={activeRepo?.full_name}
            backendStatus={backendStatus}
            onOpenCommandPalette={() => setIsCommandPaletteOpen(true)}
            onOpenImportModal={() => setIsImportModalOpen(true)}
            isAiPanelOpen={!isChatCollapsed}
            onToggleAiPanel={() => setIsChatCollapsed((prev) => !prev)}
            onSignIn={signIn}
          />

          {/* Content Row: Explorer + Workspace Views + AI Drawer */}
          <div className="flex-1 flex min-h-0 overflow-hidden">
            {/* Left Explorer Panel (Collapsible) */}
            {currentView === "repos" && (
              <ExplorerPanel
                repositories={repositories}
                activeRepo={activeRepo}
                onSelectRepo={(repo) => setActiveRepo(repo)}
                indexStatus={activeIndexStatus}
                indexing={indexingId === activeRepo?.id}
                onReindex={handleReindex}
                files={indexedFiles}
                loadingFiles={loadingFiles}
                selectedFilePath={activeTabPath}
                onSelectFile={handleOpenFile}
                onSelectSymbol={handleSelectSymbol}
                isCollapsed={isExplorerCollapsed}
                onToggleCollapse={() => setIsExplorerCollapsed((prev) => !prev)}
              />
            )}

            {/* Center Workspace Area */}
            <main className="flex-1 flex flex-col min-w-0 overflow-hidden bg-[#07090c]">
              {currentView === "overview" && (
                <DashboardOverview
                  currentUser={currentUser}
                  repositories={repositories}
                  indexes={indexes}
                  loading={loadingRepos}
                  onRefresh={() => void loadRepositories()}
                  onImportRepo={handleImportRepo}
                  onSelectRepo={(r) => {
                    setActiveRepo(r);
                    setCurrentView("repos");
                  }}
                  onSelectView={setCurrentView}
                  onReindex={handleReindex}
                  indexingId={indexingId}
                  onSignIn={signIn}
                />
              )}

          {currentView === "repos" && (
            <CodeViewer
              repoName={activeRepo?.full_name || "Repository"}
              openTabs={openTabs}
              activeTabPath={activeTabPath}
              onSelectTab={setActiveTabPath}
              onCloseTab={handleCloseTab}
              onClearHighlight={handleClearHighlight}
              onSelectSymbol={handleSelectSymbol}
              indexedFiles={indexedFiles}
              evidenceSnippets={analysisResult?.evidence || []}
            />
          )}

          {currentView === "chat" && (
            <div className="flex-1 flex overflow-hidden">
              <div className="flex-1">
                <CodeViewer
                  repoName={activeRepo?.full_name || "Repository"}
                  openTabs={openTabs}
                  activeTabPath={activeTabPath}
                  onSelectTab={setActiveTabPath}
                  onCloseTab={handleCloseTab}
                  indexedFiles={indexedFiles}
                  evidenceSnippets={analysisResult?.evidence || []}
                />
              </div>
            </div>
          )}

          {currentView === "issues" && (
            <IssueWorkspace
              repositories={repositories}
              activeRepo={activeRepo}
              onSelectRepo={setActiveRepo}
              analysisResult={analysisResult}
              onAnalyzeIssue={handleAnalyzeIssue}
              analyzing={analyzing}
              analysisError={analysisError}
              onOpenCitation={handleOpenCitation}
              onNavigateToPatch={() => setCurrentView("patch")}
              onNavigateToVerification={() => setCurrentView("verification")}
            />
          )}

          {currentView === "patch" && (
            <PatchReviewWorkspace
              analysisResult={analysisResult}
              onLaunchVerification={() => setCurrentView("verification")}
              onOpenCitation={handleOpenCitation}
            />
          )}

          {currentView === "verification" && (
            <VerificationWorkspace
              repositories={repositories}
              activeRepo={activeRepo}
              onSelectRepo={setActiveRepo}
              commitSha={analysisResult?.commit_sha}
              issueNumber={analysisResult?.issue_number}
              patches={analysisResult?.analysis.proposed_fix.patches || []}
              hypothesis={analysisResult?.analysis.root_cause_hypothesis}
              csrfToken={csrfToken.current}
              onNavigateToIssues={() => setCurrentView("issues")}
            />
          )}

          {currentView === "activity" && (
            <ActivityWorkspace
              sessions={sessions}
              activeRepo={activeRepo}
            />
          )}

          {currentView === "settings" && (
            <SettingsWorkspace
              currentUser={currentUser}
              apiUrl={apiUrl}
              hasCsrf={!!csrfToken.current}
            />
          )}
        </main>

        {/* Right AI Panel (Persistent, Collapsible) */}
        <AiChatPanel
          sessions={sessions}
          activeSession={activeSession}
          onSelectSession={handleSelectSession}
          onNewSession={handleNewSession}
          onDeleteSession={handleDeleteSession}
          onClearSessionHistory={handleClearSessionHistory}
          chatMode={chatMode}
          onChangeMode={setChatMode}
          chatBusy={chatBusy}
          chatError={chatError}
          onSendMessage={handleSendMessage}
          onRetryLastQuestion={handleRetryLastQuestion}
          lastFailedQuestion={lastFailedQuestion}
          onOpenCitation={handleOpenCitation}
          isCollapsed={isChatCollapsed}
          onToggleCollapse={() => setIsChatCollapsed((prev) => !prev)}
        />
          </div>
        </div>
      </div>

      {/* Bottom Panel (Collapsible) */}
      <BottomPanel
        isExpanded={isBottomExpanded}
        onToggleExpand={() => setIsBottomExpanded((prev) => !prev)}
        indexStatus={activeIndexStatus}
        activeCommitSha={activeIndexStatus?.commit_sha}
        diagnostics={
          activeIndexStatus?.error
            ? [activeIndexStatus.error]
            : ["AST parser healthy", "BM25 retrieval engine ready", "Docker socket verified"]
        }
      />

      {/* Global Command Palette */}
      <CommandPalette
        isOpen={isCommandPaletteOpen}
        onClose={() => setIsCommandPaletteOpen(false)}
        repositories={repositories}
        activeRepoId={activeRepo?.id || null}
        onSelectRepo={(id) => {
          const found = repositories.find((r) => r.id === id);
          if (found) setActiveRepo(found);
        }}
        onSelectView={setCurrentView}
        indexedFiles={indexedFiles}
        onOpenFile={handleOpenFile}
        onNewChat={handleNewSession}
      />

      {/* Quick Global Import Modal */}
      {isImportModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
          <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#0e1117] p-6 shadow-2xl space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-zinc-100">Import Public GitHub Repository</h3>
              <button
                onClick={() => {
                  setIsImportModalOpen(false);
                  setModalImportError("");
                }}
                className="text-xs text-zinc-400 hover:text-white font-mono"
              >
                ✕
              </button>
            </div>
            <p className="text-xs text-zinc-400">
              Enter the URL of a public repository to clone, index, and analyze in your workspace.
            </p>
            <form onSubmit={handleModalImport} className="space-y-3">
              <input
                type="url"
                required
                value={modalImportUrl}
                onChange={(e) => setModalImportUrl(e.target.value)}
                placeholder="https://github.com/owner/repository"
                className="w-full rounded-xl border border-white/10 bg-[#141822] px-3.5 py-2.5 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-[#BFFF00]/50 font-mono"
              />
              {modalImportError && (
                <p className="text-xs text-rose-300 font-mono">{modalImportError}</p>
              )}
              <div className="flex items-center justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    setIsImportModalOpen(false);
                    setModalImportError("");
                  }}
                  className="rounded-xl px-4 py-2 text-xs font-mono text-zinc-400 hover:text-white"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={modalImporting}
                  className="rounded-xl bg-[#BFFF00] px-4 py-2 text-xs font-semibold text-black hover:brightness-110 disabled:opacity-50 transition-all font-sans"
                >
                  {modalImporting ? "Importing…" : "Import Repository"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Home() {
  return (
    <ToastProvider>
      <MainWorkspace />
    </ToastProvider>
  );
}
