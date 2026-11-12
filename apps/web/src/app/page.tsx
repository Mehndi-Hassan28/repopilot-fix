"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

type HealthState = "checking" | "online" | "offline";

const apiUrl = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export default function Home() {
  const [health, setHealth] = useState<HealthState>("checking");
  const [checkedAt, setCheckedAt] = useState<string | null>(null);

  const checkHealth = useCallback(async () => {
    setHealth("checking");
    try {
      const response = await fetch(`${apiUrl}/health`, { cache: "no-store" });
      if (!response.ok) throw new Error("Health check failed");
      setHealth("online");
    } catch {
      setHealth("offline");
    } finally {
      setCheckedAt(new Date().toLocaleTimeString());
    }
  }, []);

  useEffect(() => { void checkHealth(); }, [checkHealth]);

  const statusColor = health === "online" ? "bg-emerald-400" : health === "offline" ? "bg-rose-400" : "bg-amber-300";
  const statusText = health === "online" ? "API connected" : health === "offline" ? "API unreachable" : "Checking API";

  return (
    <main className="min-h-screen px-6 py-8 md:px-12">
      <div className="mx-auto flex max-w-6xl items-center justify-between border-b border-[var(--line)] pb-5">
        <Link href="/" className="flex items-center gap-3 font-semibold tracking-tight">
          <span className="grid size-9 place-items-center rounded-xl bg-[var(--accent)] text-sm font-black text-[#092018]">RP</span>
          <span>RepoPilot <span className="text-[var(--accent)]">Fix</span></span>
        </Link>
        <div className="flex items-center gap-2 rounded-full border border-[var(--line)] px-3 py-2 text-xs text-[var(--muted)]" aria-live="polite">
          <span className={`size-2 rounded-full ${statusColor}`} />{statusText}
        </div>
      </div>

      <section className="mx-auto grid min-h-[75vh] max-w-6xl items-center gap-12 py-16 md:grid-cols-[1.1fr_0.9fr]">
        <div>
          <p className="mb-5 text-xs font-semibold uppercase tracking-[0.22em] text-[var(--accent)]">Codebase intelligence · Day 1</p>
          <h1 className="max-w-2xl text-5xl font-semibold leading-[1.08] tracking-tight md:text-7xl">Understand your code. <span className="text-[var(--muted)]">Ship the fix.</span></h1>
          <p className="mt-6 max-w-xl text-lg leading-8 text-[var(--muted)]">RepoPilot is the foundation for an AI agent that maps your codebase, finds issues, and helps you deliver safe, reviewable fixes.</p>
          <div className="mt-9 flex flex-wrap items-center gap-4">
            <button onClick={() => void checkHealth()} className="rounded-lg bg-[var(--accent)] px-5 py-3 text-sm font-semibold text-[#092018] transition hover:brightness-110">Check backend</button>
            <span className="text-sm text-[var(--muted)]">FastAPI · Next.js · PostgreSQL ready</span>
          </div>
        </div>

        <div className="rounded-2xl border border-[var(--line)] bg-[#0e141b] p-6 shadow-2xl shadow-black/20">
          <div className="mb-6 flex items-center justify-between">
            <div><p className="text-sm font-medium">System status</p><p className="mt-1 text-xs text-[var(--muted)]">Local development environment</p></div>
            <span className={`size-3 rounded-full ${statusColor}`} />
          </div>
          <div className="space-y-3">
            {[{ name: "Web application", detail: "Next.js + TypeScript", state: "Ready" }, { name: "API service", detail: "FastAPI · GET /health", state: statusText }, { name: "Data layer", detail: "PostgreSQL configuration", state: "Configured" }].map((item) => (
              <div key={item.name} className="flex items-center justify-between gap-4 rounded-xl border border-[var(--line)] bg-[#111922] p-4">
                <div><p className="text-sm font-medium">{item.name}</p><p className="mt-1 text-xs text-[var(--muted)]">{item.detail}</p></div>
                <span className="shrink-0 text-xs text-[var(--accent)]">{item.state}</span>
              </div>
            ))}
          </div>
          <p className="mt-5 text-xs text-[var(--muted)]">{checkedAt ? `Last checked at ${checkedAt}` : `API target: ${apiUrl}`}</p>
        </div>
      </section>
      <footer className="mx-auto max-w-6xl border-t border-[var(--line)] pt-5 text-xs text-[var(--muted)]">Built for safer, faster code changes.</footer>
    </main>
  );
}
