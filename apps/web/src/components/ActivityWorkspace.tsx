"use client";

import { useEffect, useState } from "react";
import { Repository, ChatSession } from "../lib/types";
import { listVerifications } from "../lib/verification-api";
import { VerificationRunRead } from "../lib/verification-types";

interface ActivityWorkspaceProps {
  sessions: ChatSession[];
  activeRepo: Repository | null;
}

export function ActivityWorkspace({
  sessions,
  activeRepo,
}: ActivityWorkspaceProps) {
  const [runs, setRuns] = useState<VerificationRunRead[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!activeRepo) return;
    setLoading(true);
    listVerifications(activeRepo.id)
      .then((items) => setRuns(items))
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, [activeRepo]);

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6 bg-[#050505]">
      {/* Header */}
      <div className="border-b border-white/10 pb-5">
        <div className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-cyan-400" />
          <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#00F0FF]">
            Audit Trail & Event Log
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-semibold text-[#F5F5F5]">
          Activity Audit
        </h1>
        <p className="mt-1 text-xs text-zinc-400">
          Immutable historical audit log of indexing operations, conversations, and dual sandbox verification jobs.
        </p>
      </div>

      <div className="space-y-4 max-w-4xl">
        <h2 className="text-sm font-semibold text-zinc-200">
          Recent Events for {activeRepo?.full_name || "Workspace"}
        </h2>

        {loading ? (
          <div className="p-8 text-center text-xs text-zinc-500 font-mono">
            Loading activity events…
          </div>
        ) : runs.length === 0 && sessions.length === 0 ? (
          <div className="rounded-xl border border-dashed border-white/10 p-8 text-center text-xs text-zinc-500">
            No activity recorded yet in this workspace.
          </div>
        ) : (
          <div className="space-y-3 font-mono text-xs">
            {runs.map((run) => (
              <div
                key={`run-${run.id}`}
                className="flex items-start gap-3 rounded-xl border border-white/10 bg-[#0d1016] p-4"
              >
                <span className="mt-0.5 rounded bg-cyan-950/60 border border-cyan-500/30 px-1.5 py-0.5 text-[10px] text-cyan-300 font-bold">
                  VERIFICATION
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-zinc-200 font-sans font-medium">
                    Run #{run.id} for Issue #{run.issue_number}
                  </p>
                  <p className="mt-0.5 text-[11px] text-zinc-500 truncate">
                    Status: {run.status} · Commit: {run.commit_sha.slice(0, 7)} · Command: {run.test_command?.join(" ") || "default"}
                  </p>
                </div>
                <span className="text-[10px] text-zinc-600">
                  {new Date(run.created_at).toLocaleTimeString()}
                </span>
              </div>
            ))}

            {sessions.map((session) => (
              <div
                key={`session-${session.id}`}
                className="flex items-start gap-3 rounded-xl border border-white/10 bg-[#0d1016] p-4"
              >
                <span className="mt-0.5 rounded bg-lime-950/60 border border-lime-500/30 px-1.5 py-0.5 text-[10px] text-lime-300 font-bold">
                  CHAT
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-zinc-200 font-sans font-medium truncate">
                    {session.title || "New conversation"}
                  </p>
                  <p className="mt-0.5 text-[11px] text-zinc-500">
                    Session #{session.id} · Commit: {session.commit_sha.slice(0, 7)}
                  </p>
                </div>
                <span className="text-[10px] text-zinc-600">
                  {session.created_at ? new Date(session.created_at).toLocaleTimeString() : "Active"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
