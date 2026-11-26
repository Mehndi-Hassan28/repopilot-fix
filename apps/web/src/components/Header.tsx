"use client";

import { AuthUser, PERSONAS, PersonaType } from "../lib/types";
import { SearchIcon, PlusIcon, BotIcon } from "./Icons";

interface HeaderProps {
  currentUser: AuthUser | null;
  activeRepoName?: string;
  backendStatus?: "ready" | "connecting" | "offline";
  activePersona?: PersonaType;
  onSelectPersona?: (persona: PersonaType) => void;
  onOpenCommandPalette: () => void;
  onOpenImportModal: () => void;
  isAiPanelOpen: boolean;
  onToggleAiPanel: () => void;
  onSignIn: () => void;
}

export function Header({
  currentUser,
  activeRepoName,
  backendStatus = "ready",
  activePersona = "software_engineer",
  onSelectPersona,
  onOpenCommandPalette,
  onOpenImportModal,
  isAiPanelOpen,
  onToggleAiPanel,
  onSignIn,
}: HeaderProps) {
  return (
    <header className="h-16 md:h-20 border-b border-white/5 px-4 md:px-8 flex items-center justify-between shrink-0 bg-[#050505] z-20">
      {/* Left Title & Status Badges */}
      <div className="flex items-center gap-3 md:gap-4 min-w-0">
        <h1 className="text-base md:text-xl font-semibold gradient-text truncate">
          RepoPilot Workspace
        </h1>
        {backendStatus === "ready" && (
          <span role="status" aria-live="polite" className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-0.5 bg-lime-500/10 border border-lime-500/20 text-[#BFFF00] text-[10px] font-mono font-semibold uppercase tracking-wider rounded-md">
            <span className="size-1.5 rounded-full bg-[#BFFF00] animate-pulse" aria-hidden="true" />
            Engine Ready
          </span>
        )}
        {backendStatus === "connecting" && (
          <span role="status" aria-live="polite" className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-0.5 bg-amber-500/10 border border-amber-500/20 text-amber-300 text-[10px] font-mono font-semibold uppercase tracking-wider rounded-md">
            <span className="size-1.5 rounded-full bg-amber-400 animate-pulse" aria-hidden="true" />
            Connecting…
          </span>
        )}
        {backendStatus === "offline" && (
          <span role="status" aria-live="polite" className="hidden sm:inline-flex items-center gap-1.5 px-2.5 py-0.5 bg-rose-500/10 border border-rose-500/20 text-rose-300 text-[10px] font-mono font-semibold uppercase tracking-wider rounded-md">
            <span className="size-1.5 rounded-full bg-rose-500" aria-hidden="true" />
            Engine Offline
          </span>
        )}
        {activeRepoName && (
          <span className="hidden lg:inline-flex items-center px-2.5 py-0.5 bg-white/5 border border-white/10 text-zinc-300 font-mono text-[11px] rounded-full truncate max-w-xs">
            <span className="size-1.5 rounded-full bg-[#BFFF00] mr-1.5" />
            {activeRepoName}
          </span>
        )}

        {/* Active Engineering Persona Switcher */}
        <div
          role="group"
          aria-label="Active engineering persona selection"
          className="hidden xl:flex items-center gap-1 rounded-full border border-white/10 bg-white/5 p-0.5 text-[10px] font-mono"
        >
          {(["software_engineer", "security_auditor", "qa_lead"] as const).map((p) => {
            const meta = PERSONAS[p];
            const isSelected = activePersona === p;
            return (
              <button
                key={p}
                onClick={() => onSelectPersona?.(p)}
                className={`flex items-center gap-1 px-2.5 py-0.5 rounded-full transition-all ${
                  isSelected
                    ? `${meta.bgColor} ${meta.borderColor} border font-bold text-white shadow-sm`
                    : "text-zinc-400 hover:text-zinc-200"
                }`}
                title={meta.description}
                aria-pressed={isSelected}
              >
                <span
                  className="size-1.5 rounded-full"
                  style={{ backgroundColor: meta.accentColor }}
                  aria-hidden="true"
                />
                <span>{meta.label}</span>
                <span className="text-[9px] opacity-75">({meta.badge})</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Right Search, Actions, and AI Drawer Trigger */}
      <div className="flex items-center gap-3 md:gap-5">
        {/* Search / Command Palette Input Pill */}
        <button
          type="button"
          onClick={onOpenCommandPalette}
          className="relative flex items-center cursor-pointer group bg-white/5 border border-white/10 rounded-full py-1.5 md:py-2 pl-10 pr-14 md:pr-16 w-44 sm:w-64 md:w-80 lg:w-96 text-left group-hover:border-white/20 transition-all font-sans select-none focus-visible:ring-2 focus-visible:ring-[#BFFF00]"
          aria-label="Search codebase or press Command K"
        >
          <SearchIcon className="absolute left-3.5 text-white/30 group-hover:text-white/60 transition-colors size-4" aria-hidden="true" />
          <span className="text-xs md:text-sm text-white/40 truncate">Search codebase or files...</span>
          <div className="absolute right-3 flex items-center gap-1 pointer-events-none" aria-hidden="true">
            <kbd className="bg-white/10 text-white/50 px-1.5 py-0.5 rounded text-[10px] font-mono">
              ⌘
            </kbd>
            <kbd className="bg-white/10 text-white/50 px-1.5 py-0.5 rounded text-[10px] font-mono">
              K
            </kbd>
          </div>
        </button>

        {/* Import Repository Action Button */}
        <button
          id="header-import-repo"
          onClick={onOpenImportModal}
          className="bg-white text-black px-3.5 md:px-5 py-1.5 md:py-2 rounded-full text-xs md:text-sm font-semibold hover:bg-[#BFFF00] transition-all flex items-center gap-1.5 shadow-md shadow-black/40 hover:scale-[1.02] shrink-0"
        >
          <PlusIcon className="size-3.5" />
          <span className="hidden sm:inline">Import Repository</span>
          <span className="sm:hidden">Import</span>
        </button>

        {/* Toggle AI Panel Drawer Button */}
        <button
          onClick={onToggleAiPanel}
          className={`p-2 rounded-full border transition-all ${
            isAiPanelOpen
              ? "bg-lime-500/10 border-[#BFFF00]/40 text-[#BFFF00]"
              : "bg-white/5 border-white/10 text-white/50 hover:text-white hover:border-white/20"
          }`}
          title={isAiPanelOpen ? "Close AI Assistant drawer" : "Open AI Assistant drawer"}
          aria-label="Toggle AI drawer"
          aria-expanded={isAiPanelOpen}
        >
          <BotIcon className="size-4 md:size-4.5" />
        </button>

        {/* Unauthenticated Quick Sign In Button */}
        {!currentUser && (
          <button
            onClick={onSignIn}
            className="hidden sm:inline-flex items-center text-xs font-semibold px-3 py-1.5 rounded-full border border-white/10 bg-white/5 text-zinc-300 hover:text-white hover:border-white/20 transition-all font-sans"
          >
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}
