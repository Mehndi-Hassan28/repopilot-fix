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
      </section>
      <footer className="mx-auto max-w-6xl border-t border-[var(--line)] pt-5 text-xs text-[var(--muted)]">Public repository metadata · Source code is never executed.</footer>
    </main>
  );
}
