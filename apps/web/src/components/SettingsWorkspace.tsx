"use client";

import { AuthUser } from "../lib/types";

interface SettingsWorkspaceProps {
  currentUser: AuthUser | null;
  apiUrl: string;
  hasCsrf: boolean;
}

export function SettingsWorkspace({
  currentUser,
  apiUrl,
  hasCsrf,
}: SettingsWorkspaceProps) {
  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-6 bg-[#050505]">
      {/* Header */}
      <div className="border-b border-white/10 pb-5">
        <div className="flex items-center gap-2">
          <span className="size-2 rounded-full bg-zinc-400" />
          <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-zinc-400">
            Platform Configuration
          </span>
        </div>
        <h1 className="mt-1 text-2xl font-semibold text-[#F5F5F5]">
          Settings & Environment
        </h1>
        <p className="mt-1 text-xs text-zinc-400">
          Backend API endpoints, security authentication, and developer keybindings.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-5xl">
        {/* Environment Status */}
        <div className="rounded-xl border border-white/10 bg-[#0d1016] p-5 space-y-4">
          <h2 className="text-sm font-semibold text-zinc-200">System & API Status</h2>

          <div className="space-y-3 font-mono text-xs">
            <div className="flex items-center justify-between border-b border-white/5 pb-2">
              <span className="text-zinc-500">API Gateway</span>
              <span className="text-cyan-300 font-semibold">{apiUrl}</span>
            </div>
            <div className="flex items-center justify-between border-b border-white/5 pb-2">
              <span className="text-zinc-500">CSRF Protection</span>
              <span className={hasCsrf ? "text-[#BFFF00]" : "text-amber-400"}>
                {hasCsrf ? "Active & Enforced" : "Initializing…"}
              </span>
            </div>
            <div className="flex items-center justify-between border-b border-white/5 pb-2">
              <span className="text-zinc-500">User Identity</span>
              <span className="text-zinc-300">
                {currentUser ? `@${currentUser.login}` : "Unauthenticated"}
              </span>
            </div>
            <div className="flex items-center justify-between pb-1">
              <span className="text-zinc-500">Sandbox Isolation</span>
              <span className="text-[#BFFF00]">Docker Container (Dual Runner)</span>
            </div>
          </div>
        </div>

        {/* Superdesign Draft Continuation Status */}
        <div className="rounded-xl border border-white/10 bg-[#0d1016] p-5 space-y-3">
          <h2 className="text-sm font-semibold text-zinc-200">Superdesign Hub 2.0 Integration</h2>
          <p className="text-xs text-zinc-400 leading-relaxed font-sans">
            Draft continuation for Project <code className="text-cyan-300 font-mono">0db84ddb-497b-4da2-a0d0-a782e15a204a</code> / Draft <code className="text-cyan-300 font-mono">a92494c5-9d29-45cc-ab7e-42970e2cf8e9</code>.
          </p>
          <div className="rounded-lg border border-cyan-500/30 bg-cyan-950/20 p-3 text-xs text-cyan-200 font-sans leading-relaxed">
            <span className="font-semibold text-[#00F0FF]">Design Fidelity Note:</span> The Dark Luxury Developer Intelligence design tokens, visual hierarchy, neon accents, and layout have been directly codified into the Next.js production SaaS frontend.
          </div>
        </div>

        {/* Keyboard Shortcuts Cheatsheet */}
        <div className="rounded-xl border border-white/10 bg-[#0d1016] p-5 space-y-3 md:col-span-2">
          <h2 className="text-sm font-semibold text-zinc-200">Keyboard Shortcuts</h2>

          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3 font-mono text-xs">
            <div className="flex items-center justify-between rounded-lg border border-white/5 bg-[#12151c] p-2.5">
              <span className="text-zinc-400 font-sans">Command Palette</span>
              <kbd className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-cyan-300 font-mono text-[11px]">
                Ctrl/Cmd + K
              </kbd>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-white/5 bg-[#12151c] p-2.5">
              <span className="text-zinc-400 font-sans">Send Chat Message</span>
              <kbd className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-cyan-300 font-mono text-[11px]">
                Enter
              </kbd>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-white/5 bg-[#12151c] p-2.5">
              <span className="text-zinc-400 font-sans">Multiline Prompt</span>
              <kbd className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-cyan-300 font-mono text-[11px]">
                Shift + Enter
              </kbd>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-white/5 bg-[#12151c] p-2.5">
              <span className="text-zinc-400 font-sans">Jump to Citation</span>
              <kbd className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-cyan-300 font-mono text-[11px]">
                Click Badge
              </kbd>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-white/5 bg-[#12151c] p-2.5">
              <span className="text-zinc-400 font-sans">Close Palette</span>
              <kbd className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-cyan-300 font-mono text-[11px]">
                Escape
              </kbd>
            </div>
            <div className="flex items-center justify-between rounded-lg border border-white/5 bg-[#12151c] p-2.5">
              <span className="text-zinc-400 font-sans">Toggle Explorer</span>
              <kbd className="rounded border border-white/10 bg-white/5 px-2 py-0.5 text-cyan-300 font-mono text-[11px]">
                Click ◂ / ▸
              </kbd>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
