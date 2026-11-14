"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";

type Repository = {
  id: number;
  owner: string;
  name: string;
  full_name: string;
  url: string;
  description: string | null;
  default_branch: string;
  stars: number;
};
type IndexStatus = { status: string; file_count: number; symbol_count: number; languages: string[]; commit_sha: string; error?: string | null };
type Citation = { path: string; start_line: number; end_line: number };
type ChatMessage = { id: number; role: string; content: string; citations: Citation[] };
type ChatSession = { id: number; repository_id: number; title: string; commit_sha: string; messages?: ChatMessage[] };
type AuthUser = { id: number; login: string };

type FileCitation = {
  path: string;
  start_line: number;
  end_line: number;
  explanation?: string;
};

type PatchProposal = {
  file_path: string;
  explanation: string;
  diff: string;
};

type ProposedFix = {
  explanation: string;
  patches: PatchProposal[];
};

type IssueAnalysis = {
  summary: string;
  root_cause_hypothesis: string;
  relevant_files: FileCitation[];
  proposed_fix: ProposedFix;
  confidence: "low" | "medium" | "high";
  confidence_reasoning?: string;
  limitations: string[];
};

type IssueAnalysisResponse = {
  repository_id: number;
  commit_sha: string;
  issue_number: number;
  issue_title: string;
  issue_body: string;
  issue_url: string;
  query: string;
  evidence: Array<{
    path: string;
    start_line: number;
    end_line: number;
    content: string;
    score: number;
  }>;
  analysis: IssueAnalysis;
};

const apiUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

function apiRequest(path: string, init: RequestInit = {}, csrfToken = "") {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  return fetch(`${apiUrl}${path}`, { ...init, headers, credentials: "include" });
}

export default function Home() {
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [indexes, setIndexes] = useState<Record<number, IndexStatus>>({});
  const [indexing, setIndexing] = useState<number | null>(null);
  const [chatRepository, setChatRepository] = useState<number | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [question, setQuestion] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [analysisRepo, setAnalysisRepo] = useState<number | null>(null);
  const [issueNumber, setIssueNumber] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [analysisResult, setAnalysisResult] = useState<IssueAnalysisResponse | null>(null);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const csrfToken = useRef("");

  const loadRepositories = useCallback(async () => {
    setLoading(true);
    try {
      const response = await apiRequest("/api/repositories", { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load repositories. Check that the API is running.");
      const items = await response.json() as Repository[];
      setRepositories(items);
      const statuses = await Promise.all(items.map(async (repo) => {
        const statusResponse = await apiRequest(`/api/repositories/${repo.id}/index`, { cache: "no-store" });
        if (!statusResponse.ok) return null;
        return [repo.id, await statusResponse.json() as IndexStatus] as const;
      }));
      setIndexes(Object.fromEntries(statuses.filter((item): item is NonNullable<typeof item> => item !== null)));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Something went wrong.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const response = await apiRequest("/api/auth/session", { cache: "no-store" });
        const session = await response.json();
        if (session.authenticated) {
          setCurrentUser(session.user as AuthUser);
          csrfToken.current = session.csrf_token as string;
          await loadRepositories();
        } else {
          setRepositories([]);
          setLoading(false);
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not check sign-in status.");
        setLoading(false);
      } finally {
        setAuthLoading(false);
      }
    })();
  }, [loadRepositories]);

  function signIn() {
    window.location.assign(`${apiUrl}/api/auth/github/login`);
  }

  async function signOut() {
    try {
      const response = await apiRequest("/api/auth/logout", { method: "POST" }, csrfToken.current);
      if (!response.ok) throw new Error("Could not sign out.");
      csrfToken.current = "";
      setCurrentUser(null);
      setRepositories([]);
      setSessions([]);
      setActiveSession(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not sign out.");
    }
  }

  async function loadSessions(repositoryId: number) {
    setChatRepository(repositoryId); setActiveSession(null);
    try {
      const response = await apiRequest(`/api/chat/sessions?repository_id=${repositoryId}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load chat history.");
      setSessions(await response.json() as ChatSession[]);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load chat history."); }
  }

  async function openSession(session: ChatSession) {
    setChatBusy(true);
    try {
      const response = await apiRequest(`/api/chat/sessions/${session.id}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not load conversation.");
      setActiveSession(data as ChatSession);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load conversation."); }
    finally { setChatBusy(false); }
  }

  async function deleteChat(session: ChatSession) {
    if (!window.confirm(`Permanently delete “${session.title}” and its messages?`)) return;
    try {
      const response = await apiRequest(`/api/chat/sessions/${session.id}`, { method: "DELETE" }, csrfToken.current);
      if (!response.ok) throw new Error("Could not delete conversation.");
      setSessions((items) => items.filter((item) => item.id !== session.id));
      setActiveSession((current) => current?.id === session.id ? null : current);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not delete conversation."); }
  }

  async function clearChatHistory() {
    if (!activeSession || !window.confirm("Delete all messages in this conversation?")) return;
    try {
      const response = await apiRequest(
        `/api/chat/sessions/${activeSession.id}/messages`,
        { method: "DELETE" },
        csrfToken.current,
      );
      if (!response.ok) throw new Error("Could not clear conversation history.");
      setActiveSession((current) => current ? { ...current, messages: [] } : current);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not clear conversation history."); }
  }

  async function newSession() {
    if (!chatRepository) return;
    setChatBusy(true); setError("");
    try {
      const response = await apiRequest("/api/chat/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repository_id: chatRepository }) }, csrfToken.current);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not start chat.");
      const session = data as ChatSession;
      setSessions((items) => [session, ...items]); setActiveSession({ ...session, messages: [] });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not start chat."); }
    finally { setChatBusy(false); }
  }

  async function sendQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!activeSession || !question.trim()) return;
    const text = question.trim(); setQuestion(""); setChatBusy(true); setError("");
    try {
      const response = await apiRequest(`/api/chat/sessions/${activeSession.id}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text }) }, csrfToken.current);
      const data = await response.json() as ChatMessage & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "Question could not be answered.");
      setActiveSession((current) => current ? { ...current, messages: [...(current.messages || []), { id: Date.now(), role: "user", content: text, citations: [] }, data] } : current);
      setSessions((items) => items.map((item) => item.id === activeSession.id && item.title === "New conversation" ? { ...item, title: text.slice(0, 200) } : item));
    } catch (reason) { setQuestion(text); setError(reason instanceof Error ? reason.message : "Question could not be answered."); }
    finally { setChatBusy(false); }
  }

  async function importRepository(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const response = await apiRequest("/api/repositories/import", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }),
      }, csrfToken.current);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Repository import failed.");
      setRepositories((items) => items.some((item) => item.id === data.id) ? items : [data as Repository, ...items]);
      setNotice(`${data.full_name} is ready in your workspace.`);
      setUrl("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  async function startIndex(repositoryId: number) {
    setIndexing(repositoryId); setError("");
    try {
      const response = await apiRequest(`/api/repositories/${repositoryId}/index`, { method: "POST" }, csrfToken.current);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Indexing could not be started.");
      setIndexes((state) => ({ ...state, [repositoryId]: data }));
      for (let attempt = 0; attempt < 30; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const statusResponse = await apiRequest(`/api/repositories/${repositoryId}/index`, { cache: "no-store" });
        if (!statusResponse.ok) break;
        const status = await statusResponse.json() as IndexStatus;
        setIndexes((state) => ({ ...state, [repositoryId]: status }));
        if (!["queued", "running"].includes(status.status)) break;
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Indexing failed."); }
    finally { setIndexing(null); }
  }

  async function submitIssueAnalysis(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!analysisRepo || !issueNumber.trim()) return;
    const num = parseInt(issueNumber.trim(), 10);
    if (isNaN(num) || num < 1) {
      setAnalysisError("Please enter a valid positive issue number.");
      return;
    }
    setAnalyzing(true);
    setAnalysisError("");
    setAnalysisResult(null);
    try {
      const response = await apiRequest(
        `/api/repositories/${analysisRepo}/issues/analyze`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ issue_number: num }),
        },
        csrfToken.current
      );
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.detail || "Failed to analyze issue.");
      }
      setAnalysisResult(data as IssueAnalysisResponse);
    } catch (reason) {
      setAnalysisError(reason instanceof Error ? reason.message : "Analysis failed.");
    } finally {
      setAnalyzing(false);
    }
  }

  return (
    <main className="min-h-screen px-5 py-6 md:px-12 md:py-8">
      <header className="mx-auto flex max-w-6xl items-center justify-between border-b border-[var(--line)] pb-5">
        <Link href="/" className="flex items-center gap-3 font-semibold tracking-tight">
          <span className="grid size-9 place-items-center rounded-xl bg-[var(--accent)] text-sm font-black text-[#092018]">RP</span>
          <span>RepoPilot <span className="text-[var(--accent)]">Fix</span></span>
        </Link>
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-2 rounded-full border border-[var(--line)] px-3 py-2 text-xs text-[var(--muted)]"><span className="size-2 rounded-full bg-emerald-400" /> Public repositories only</span>
          {currentUser ? <><span className="text-sm text-[var(--muted)]">{currentUser.login}</span><button onClick={() => void signOut()} className="rounded-lg border border-[var(--line)] px-3 py-2 text-xs">Sign out</button></> : !authLoading && <button onClick={signIn} className="rounded-lg bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[#092018]">Sign in with GitHub</button>}
        </div>
      </header>

      {authLoading ? <p className="mx-auto max-w-6xl py-20 text-center text-sm text-[var(--muted)]">Checking sign-in…</p> : !currentUser ? <section className="mx-auto max-w-2xl py-24 text-center"><h1 className="text-3xl font-semibold">Sign in to your private workspace</h1><p className="mt-3 text-sm text-[var(--muted)]">Your repositories and conversations are visible only to your account.</p><button onClick={signIn} className="mt-7 rounded-xl bg-[var(--accent)] px-6 py-3 font-semibold text-[#092018]">Continue with GitHub</button></section> : <>
      <section className="mx-auto max-w-6xl pb-20 pt-12 md:pt-20">
        <div className="max-w-3xl">
          <p className="mb-4 text-xs font-semibold uppercase tracking-[0.22em] text-[var(--accent)]">Your code, in focus</p>
          <h1 className="text-4xl font-semibold leading-tight tracking-tight md:text-6xl">A clearer view of every codebase.</h1>
          <p className="mt-5 max-w-2xl text-base leading-7 text-[var(--muted)]">Bring a public GitHub repository into RepoPilot. Start with the structure, then move toward a safer, faster fix.</p>
        </div>

        <form onSubmit={importRepository} className="mt-10 flex flex-col gap-3 rounded-2xl border border-[var(--line)] bg-[#0e141b] p-4 shadow-2xl shadow-black/20 sm:flex-row sm:p-5">
          <label htmlFor="repository-url" className="sr-only">GitHub repository URL</label>
          <input id="repository-url" type="url" required value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://github.com/owner/repository" className="min-w-0 flex-1 rounded-xl border border-[var(--line)] bg-[#090d12] px-4 py-3 text-sm outline-none transition placeholder:text-[#64717e] focus:border-[var(--accent)]" />
          <button disabled={submitting} className="rounded-xl bg-[var(--accent)] px-6 py-3 text-sm font-semibold text-[#092018] transition hover:brightness-110 disabled:cursor-wait disabled:opacity-60">{submitting ? "Importing…" : "Import repository"}</button>
        </form>
        <div aria-live="polite" className="min-h-8 pt-2 text-sm">{error ? <p className="text-rose-300">{error}</p> : notice ? <p className="text-[var(--accent)]">{notice}</p> : null}</div>

        <section className="mt-8" aria-labelledby="repositories-heading">
          <div className="mb-5 flex items-end justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--muted)]">Workspace</p><h2 id="repositories-heading" className="mt-2 text-2xl font-semibold">Repositories <span className="ml-2 text-base font-normal text-[var(--muted)]">{repositories.length}</span></h2></div><button onClick={() => void loadRepositories()} className="text-sm text-[var(--muted)] transition hover:text-white">Refresh</button></div>
          {loading ? <div className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-8 text-sm text-[var(--muted)]">Loading your repositories…</div> : repositories.length === 0 ? <div className="rounded-2xl border border-dashed border-[var(--line)] bg-[#0e141b]/60 px-6 py-12 text-center"><div className="mx-auto grid size-12 place-items-center rounded-2xl border border-[var(--line)] text-xl text-[var(--accent)]">⌘</div><h3 className="mt-4 font-medium">Your workspace is ready</h3><p className="mt-2 text-sm text-[var(--muted)]">Import a public GitHub repository above to get started.</p></div> : <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{repositories.map((repo) => <a key={repo.id} href={repo.url} target="_blank" rel="noreferrer" className="group rounded-2xl border border-[var(--line)] bg-[#0e141b] p-5 transition hover:-translate-y-0.5 hover:border-[#496154]"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="truncate text-xs text-[var(--muted)]">{repo.owner}</p><h3 className="mt-1 truncate font-semibold group-hover:text-[var(--accent)]">{repo.name}</h3></div><span className="shrink-0 rounded-lg border border-[var(--line)] px-2 py-1 text-xs text-[var(--muted)]">★ {repo.stars.toLocaleString()}</span></div><p className="mt-4 line-clamp-2 min-h-10 text-sm leading-5 text-[var(--muted)]">{repo.description || "No description provided."}</p><div className="mt-5 flex items-center justify-between border-t border-[var(--line)] pt-4 text-xs text-[var(--muted)]"><span className="truncate">{repo.full_name}</span><span className="ml-2 shrink-0">⑂ {repo.default_branch}</span></div></a>)}</div>}
          {repositories.length > 0 && <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{repositories.map((repo) => { const status = indexes[repo.id]; return <article key={`index-${repo.id}`} className="rounded-xl border border-[var(--line)] bg-[#0e141b] p-4"><div className="flex items-center justify-between gap-3"><h3 className="truncate text-sm font-medium">{repo.full_name}</h3><button onClick={() => void startIndex(repo.id)} disabled={indexing === repo.id} className="shrink-0 rounded-lg bg-[var(--accent)] px-3 py-2 text-xs font-semibold text-[#092018] disabled:opacity-60">{indexing === repo.id ? "Indexing…" : status ? "Reindex" : "Index source"}</button></div>{status && <div className="mt-3 text-xs leading-5 text-[var(--muted)]"><p>{status.status} · {status.file_count} files · {status.symbol_count} symbols</p><p>{status.languages?.join(", ") || "Waiting for files"}</p>{status.error && <p className="text-rose-300">{status.error}</p>}</div>}</article>; })}</div>}
        </section>
        <section className="mt-14" aria-labelledby="chat-heading">
          <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--accent)]">Ask the codebase</p><h2 id="chat-heading" className="mt-2 text-2xl font-semibold">Repository chat</h2></div>
            <label className="sr-only" htmlFor="chat-repository">Choose a repository</label><select id="chat-repository" value={chatRepository ?? ""} onChange={(event) => { const id = Number(event.target.value); if (id) void loadSessions(id); else { setChatRepository(null); setActiveSession(null); setSessions([]); } }} className="rounded-xl border border-[var(--line)] bg-[#0e141b] px-4 py-3 text-sm"><option value="">Choose a repository</option>{repositories.map((repo) => <option key={repo.id} value={repo.id}>{repo.full_name}</option>)}</select>
          </div>
          {chatRepository !== null && <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
            <aside className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-3"><button onClick={() => void newSession()} disabled={chatBusy || indexes[chatRepository]?.status !== "completed"} className="w-full rounded-xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-[#092018] disabled:opacity-50">New conversation</button><p className="px-2 pb-2 pt-4 text-xs uppercase tracking-wider text-[var(--muted)]">History</p>{sessions.map((session) => <div key={session.id} className="mb-1 flex items-center gap-1"><button onClick={() => void openSession(session)} className="min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-left text-sm text-[var(--muted)] hover:bg-[#182129] hover:text-white">{session.title}</button><button aria-label={`Delete ${session.title}`} onClick={() => void deleteChat(session)} className="rounded-lg px-2 py-2 text-xs text-rose-300 hover:bg-[#182129]">Delete</button></div>)}</aside>
            <div className="flex min-h-[440px] flex-col rounded-2xl border border-[var(--line)] bg-[#0e141b] p-4 sm:p-6">
              {activeSession && <div className="mb-3 flex justify-end"><button onClick={() => void clearChatHistory()} className="rounded-lg border border-[var(--line)] px-3 py-2 text-xs text-[var(--muted)]">Clear history</button></div>}
              <div className="flex-1 space-y-4" aria-live="polite">{!activeSession ? <p className="pt-20 text-center text-sm text-[var(--muted)]">{indexes[chatRepository]?.status === "completed" ? "Start a conversation about this indexed repository." : "Complete source indexing before starting a conversation."}</p> : (activeSession.messages || []).map((message) => <article key={message.id} className={`max-w-3xl rounded-2xl p-4 ${message.role === "user" ? "ml-auto bg-[#1b2a2b]" : "bg-[#111a21]"}`}><p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p>{message.citations.length > 0 && <div className="mt-3 flex flex-wrap gap-2" aria-label="Source citations">{message.citations.map((citation) => <span key={`${citation.path}:${citation.start_line}`} className="rounded-lg border border-[var(--line)] px-2 py-1 font-mono text-xs text-[var(--accent)]">{citation.path}:{citation.start_line}–{citation.end_line}</span>)}</div>}</article>)}{chatBusy && activeSession && <p className="text-sm text-[var(--muted)]">Searching indexed source…</p>}</div>
              {activeSession && <form onSubmit={sendQuestion} className="mt-5 flex gap-2 border-t border-[var(--line)] pt-4"><label htmlFor="chat-question" className="sr-only">Ask a question about this repository</label><input id="chat-question" value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={2000} placeholder="Where is authentication handled?" className="min-w-0 flex-1 rounded-xl border border-[var(--line)] bg-[#090d12] px-4 py-3 text-sm outline-none focus:border-[var(--accent)]"/><button disabled={chatBusy || !question.trim()} className="rounded-xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-[#092018] disabled:opacity-50">Ask</button></form>}
            </div>
          </div>}
        </section>

        <section className="mt-14" aria-labelledby="issues-heading">
          <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--accent)]">
                Autonomous Diagnostics
              </p>
              <h2 id="issues-heading" className="mt-2 text-2xl font-semibold">
                Issue Analysis & Fix Proposal
              </h2>
            </div>
          </div>

          <form
            onSubmit={submitIssueAnalysis}
            className="flex flex-col gap-3 rounded-2xl border border-[var(--line)] bg-[#0e141b] p-4 sm:flex-row sm:items-center sm:p-5"
          >
            <label className="sr-only" htmlFor="analysis-repository">
              Select repository
            </label>
            <select
              id="analysis-repository"
              required
              value={analysisRepo ?? ""}
              onChange={(e) => {
                const val = Number(e.target.value);
                setAnalysisRepo(val || null);
                setAnalysisResult(null);
                setAnalysisError("");
              }}
              className="min-w-[200px] rounded-xl border border-[var(--line)] bg-[#090d12] px-4 py-3 text-sm text-white outline-none focus:border-[var(--accent)]"
            >
              <option value="">Select indexed repository</option>
              {repositories.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.full_name}
                </option>
              ))}
            </select>

            <label htmlFor="issue-number-input" className="sr-only">
              GitHub issue number
            </label>
            <input
              id="issue-number-input"
              type="number"
              min={1}
              required
              value={issueNumber}
              onChange={(e) => setIssueNumber(e.target.value)}
              placeholder="Issue # (e.g. 42)"
              className="w-full sm:w-44 rounded-xl border border-[var(--line)] bg-[#090d12] px-4 py-3 text-sm outline-none placeholder:text-[#64717e] focus:border-[var(--accent)]"
            />

            <button
              disabled={
                analyzing ||
                !analysisRepo ||
                !issueNumber ||
                indexes[analysisRepo]?.status !== "completed"
              }
              className="rounded-xl bg-[var(--accent)] px-6 py-3 text-sm font-semibold text-[#092018] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {analyzing ? "Analyzing issue…" : "Analyze Issue & Propose Fix"}
            </button>
          </form>

          {analysisError && (
            <div className="mt-3 rounded-xl border border-rose-900/50 bg-rose-950/20 p-4 text-sm text-rose-300">
              {analysisError}
            </div>
          )}

          {analysisResult && (
            <div className="mt-6 space-y-6">
              <article className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-6 shadow-xl">
                <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--line)] pb-4">
                  <div>
                    <span className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
                      Issue #{analysisResult.issue_number}
                    </span>
                    <h3 className="mt-1 text-xl font-semibold">
                      <a
                        href={analysisResult.issue_url}
                        target="_blank"
                        rel="noreferrer"
                        className="hover:text-[var(--accent)] transition"
                      >
                        {analysisResult.issue_title}
                      </a>
                    </h3>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wider ${
                        analysisResult.analysis.confidence === "high"
                          ? "bg-emerald-950/60 text-emerald-400 border border-emerald-800/40"
                          : analysisResult.analysis.confidence === "medium"
                          ? "bg-amber-950/60 text-amber-300 border border-amber-800/40"
                          : "bg-rose-950/60 text-rose-300 border border-rose-800/40"
                      }`}
                    >
                      {analysisResult.analysis.confidence} confidence
                    </span>
                  </div>
                </div>

                <div className="mt-5 space-y-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
                    Summary
                  </h4>
                  <p className="text-sm leading-relaxed text-[#c0d0d8]">
                    {analysisResult.analysis.summary}
                  </p>
                </div>

                <div className="mt-6 rounded-xl border border-amber-900/30 bg-amber-950/10 p-4">
                  <div className="flex items-center gap-2">
                    <span className="rounded-md bg-amber-500/20 px-2 py-0.5 text-xs font-bold uppercase tracking-wider text-amber-300 border border-amber-500/30">
                      Hypothesis
                    </span>
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-amber-200">
                      Suspected Root Cause
                    </h4>
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-[#dfd9cb]">
                    {analysisResult.analysis.root_cause_hypothesis}
                  </p>
                </div>

                {analysisResult.analysis.relevant_files.length > 0 && (
                  <div className="mt-6">
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
                      Relevant Files & Validated Lines
                    </h4>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {analysisResult.analysis.relevant_files.map((file, idx) => (
                        <div
                          key={`${file.path}:${file.start_line}-${idx}`}
                          className="flex items-center gap-2 rounded-lg border border-[var(--line)] bg-[#111a21] px-3 py-1.5 text-xs font-mono"
                        >
                          <span className="text-[var(--accent)]">{file.path}</span>
                          <span className="text-[var(--muted)]">
                            L{file.start_line}–L{file.end_line}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </article>

              <article className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-6 shadow-xl">
                <div className="flex items-center justify-between border-b border-[var(--line)] pb-4">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
                      Human Review Only
                    </p>
                    <h3 className="mt-1 text-lg font-semibold">Proposed Fix & Code Patch</h3>
                  </div>
                  <span className="rounded-lg border border-amber-800/40 bg-amber-950/30 px-3 py-1 text-xs text-amber-300">
                    Not automatically executed
                  </span>
                </div>

                <p className="mt-4 text-sm leading-relaxed text-[#c0d0d8]">
                  {analysisResult.analysis.proposed_fix.explanation}
                </p>

                <div className="mt-6 space-y-4">
                  {analysisResult.analysis.proposed_fix.patches.map((patch, idx) => (
                    <div
                      key={`${patch.file_path}-${idx}`}
                      className="overflow-hidden rounded-xl border border-[var(--line)] bg-[#090d12]"
                    >
                      <div className="flex items-center justify-between border-b border-[var(--line)] bg-[#0d151c] px-4 py-2.5 text-xs font-mono">
                        <span className="text-[var(--accent)]">{patch.file_path}</span>
                        {patch.explanation && (
                          <span className="text-[var(--muted)] font-sans truncate max-w-md">
                            {patch.explanation}
                          </span>
                        )}
                      </div>
                      <pre className="overflow-x-auto p-4 text-xs font-mono leading-5">
                        {patch.diff.split("\n").map((line, lineIdx) => {
                          let lineStyle = "text-[#94a3b8]";
                          if (line.startsWith("+") && !line.startsWith("+++")) {
                            lineStyle = "text-emerald-400 bg-emerald-950/30 -mx-4 px-4 block";
                          } else if (line.startsWith("-") && !line.startsWith("---")) {
                            lineStyle = "text-rose-400 bg-rose-950/30 -mx-4 px-4 block";
                          } else if (line.startsWith("@@")) {
                            lineStyle = "text-sky-400 font-semibold";
                          }
                          return (
                            <span key={lineIdx} className={lineStyle}>
                              {line}
                              {"\n"}
                            </span>
                          );
                        })}
                      </pre>
                    </div>
                  ))}
                </div>

                {analysisResult.analysis.limitations.length > 0 && (
                  <div className="mt-6 border-t border-[var(--line)] pt-4">
                    <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--muted)]">
                      Limitations & Caveats
                    </h4>
                    <ul className="mt-2 list-inside list-disc space-y-1 text-xs text-[var(--muted)]">
                      {analysisResult.analysis.limitations.map((item, idx) => (
                        <li key={idx}>{item}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </article>
            </div>
          )}
        </section>
      </section>
      </>}
      <footer className="mx-auto max-w-6xl border-t border-[var(--line)] pt-5 text-xs text-[var(--muted)]">Public repository metadata · Source code is never executed.</footer>
    </main>
  );
}
