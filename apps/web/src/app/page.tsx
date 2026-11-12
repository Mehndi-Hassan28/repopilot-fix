"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
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

const apiUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

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

  const loadRepositories = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(`${apiUrl}/api/repositories`, { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load repositories. Check that the API is running.");
      const items = await response.json() as Repository[];
      setRepositories(items);
      const statuses = await Promise.all(items.map(async (repo) => {
        const statusResponse = await fetch(`${apiUrl}/api/repositories/${repo.id}/index`, { cache: "no-store" });
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

  useEffect(() => { void loadRepositories(); }, [loadRepositories]);

  async function loadSessions(repositoryId: number) {
    setChatRepository(repositoryId); setActiveSession(null);
    try {
      const response = await fetch(`${apiUrl}/api/chat/sessions?repository_id=${repositoryId}`, { cache: "no-store" });
      if (!response.ok) throw new Error("Could not load chat history.");
      setSessions(await response.json() as ChatSession[]);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load chat history."); }
  }

  async function openSession(session: ChatSession) {
    setChatBusy(true);
    try {
      const response = await fetch(`${apiUrl}/api/chat/sessions/${session.id}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Could not load conversation.");
      setActiveSession(data as ChatSession);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not load conversation."); }
    finally { setChatBusy(false); }
  }

  async function newSession() {
    if (!chatRepository) return;
    setChatBusy(true); setError("");
    try {
      const response = await fetch(`${apiUrl}/api/chat/sessions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repository_id: chatRepository }) });
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
      const response = await fetch(`${apiUrl}/api/chat/sessions/${activeSession.id}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text }) });
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
      const response = await fetch(`${apiUrl}/api/repositories/import`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }),
      });
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
      const response = await fetch(`${apiUrl}/api/repositories/${repositoryId}/index`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Indexing could not be started.");
      setIndexes((state) => ({ ...state, [repositoryId]: data }));
      for (let attempt = 0; attempt < 30; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const statusResponse = await fetch(`${apiUrl}/api/repositories/${repositoryId}/index`, { cache: "no-store" });
        if (!statusResponse.ok) break;
        const status = await statusResponse.json() as IndexStatus;
        setIndexes((state) => ({ ...state, [repositoryId]: status }));
        if (!["queued", "running"].includes(status.status)) break;
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Indexing failed."); }
    finally { setIndexing(null); }
  }

  return (
    <main className="min-h-screen px-5 py-6 md:px-12 md:py-8">
      <header className="mx-auto flex max-w-6xl items-center justify-between border-b border-[var(--line)] pb-5">
        <Link href="/" className="flex items-center gap-3 font-semibold tracking-tight">
          <span className="grid size-9 place-items-center rounded-xl bg-[var(--accent)] text-sm font-black text-[#092018]">RP</span>
          <span>RepoPilot <span className="text-[var(--accent)]">Fix</span></span>
        </Link>
        <span className="flex items-center gap-2 rounded-full border border-[var(--line)] px-3 py-2 text-xs text-[var(--muted)]"><span className="size-2 rounded-full bg-emerald-400" /> Public repositories</span>
      </header>

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
            <aside className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-3"><button onClick={() => void newSession()} disabled={chatBusy || indexes[chatRepository]?.status !== "completed"} className="w-full rounded-xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-[#092018] disabled:opacity-50">New conversation</button><p className="px-2 pb-2 pt-4 text-xs uppercase tracking-wider text-[var(--muted)]">History</p>{sessions.map((session) => <button key={session.id} onClick={() => void openSession(session)} className="mb-1 w-full truncate rounded-lg px-3 py-2 text-left text-sm text-[var(--muted)] hover:bg-[#182129] hover:text-white">{session.title}</button>)}</aside>
            <div className="flex min-h-[440px] flex-col rounded-2xl border border-[var(--line)] bg-[#0e141b] p-4 sm:p-6">
              <div className="flex-1 space-y-4" aria-live="polite">{!activeSession ? <p className="pt-20 text-center text-sm text-[var(--muted)]">{indexes[chatRepository]?.status === "completed" ? "Start a conversation about this indexed repository." : "Complete source indexing before starting a conversation."}</p> : (activeSession.messages || []).map((message) => <article key={message.id} className={`max-w-3xl rounded-2xl p-4 ${message.role === "user" ? "ml-auto bg-[#1b2a2b]" : "bg-[#111a21]"}`}><p className="whitespace-pre-wrap text-sm leading-6">{message.content}</p>{message.citations.length > 0 && <div className="mt-3 flex flex-wrap gap-2" aria-label="Source citations">{message.citations.map((citation) => <span key={`${citation.path}:${citation.start_line}`} className="rounded-lg border border-[var(--line)] px-2 py-1 font-mono text-xs text-[var(--accent)]">{citation.path}:{citation.start_line}–{citation.end_line}</span>)}</div>}</article>)}{chatBusy && activeSession && <p className="text-sm text-[var(--muted)]">Searching indexed source…</p>}</div>
              {activeSession && <form onSubmit={sendQuestion} className="mt-5 flex gap-2 border-t border-[var(--line)] pt-4"><label htmlFor="chat-question" className="sr-only">Ask a question about this repository</label><input id="chat-question" value={question} onChange={(event) => setQuestion(event.target.value)} maxLength={2000} placeholder="Where is authentication handled?" className="min-w-0 flex-1 rounded-xl border border-[var(--line)] bg-[#090d12] px-4 py-3 text-sm outline-none focus:border-[var(--accent)]"/><button disabled={chatBusy || !question.trim()} className="rounded-xl bg-[var(--accent)] px-4 py-3 text-sm font-semibold text-[#092018] disabled:opacity-50">Ask</button></form>}
            </div>
          </div>}
        </section>
      </section>
      <footer className="mx-auto max-w-6xl border-t border-[var(--line)] pt-5 text-xs text-[var(--muted)]">Public repository metadata · Source code is never executed.</footer>
    </main>
  );
}
