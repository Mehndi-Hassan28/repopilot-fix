"use client";

import { WorkspaceView, AuthUser } from "../lib/types";
import {
  AnchorIcon,
  LayoutGridIcon,
  FolderGitIcon,
  CpuIcon,
  CircleDotIcon,
  GitPullRequestIcon,
  CheckSquareIcon,
  ActivityIcon,
  SettingsIcon,
  GithubIcon,
  FlaskConicalIcon,
  NetworkIcon,
} from "./Icons";

interface NavigationRailProps {
  currentView: WorkspaceView;
  onSelectView: (view: WorkspaceView) => void;
  currentUser: AuthUser | null;
  onOpenCommandPalette: () => void;
  onSignOut: () => void;
  onSignIn: () => void;
}

interface NavItem {
  id: WorkspaceView;
  label: string;
  icon: (active: boolean) => React.ReactNode;
}

const NAV_ITEMS: NavItem[] = [
  {
    id: "overview",
    label: "Overview",
    icon: () => <LayoutGridIcon className="size-5" />,
  },
  {
    id: "demo",
    label: "Interactive Demo",
    icon: () => <FlaskConicalIcon className="size-5" />,
  },
  {
    id: "repos",
    label: "Repositories",
    icon: () => <FolderGitIcon className="size-5" />,
  },
  {
    id: "graph",
    label: "Code Intelligence",
    icon: () => <NetworkIcon className="size-5" />,
  },
  {
    id: "chat",
    label: "AI Workspace",
    icon: () => <CpuIcon className="size-5" />,
  },
  {
    id: "issues",
    label: "Issues",
    icon: () => <CircleDotIcon className="size-5" />,
  },
  {
    id: "patch",
    label: "Patch Review",
    icon: () => <GitPullRequestIcon className="size-5" />,
  },
  {
    id: "verification",
    label: "Verification",
    icon: () => <CheckSquareIcon className="size-5" />,
  },
  {
    id: "activity",
    label: "Activity",
    icon: () => <ActivityIcon className="size-5" />,
  },
];

export function NavigationRail({
  currentView,
  onSelectView,
  currentUser,
  onOpenCommandPalette,
  onSignOut,
  onSignIn,
}: NavigationRailProps) {
  return (
    <aside className="w-[56px] shrink-0 border-r border-white/5 flex flex-col items-center py-5 z-30 bg-[#050505] select-none">
      {/* Top Brand Logo */}
      <div className="mb-6">
        <button
          onClick={() => onSelectView("overview")}
          className="size-8 bg-white rounded-xl flex items-center justify-center shadow-2xl overflow-hidden group motion-safe:hover:scale-105 transition-transform focus-visible:ring-2 focus-visible:ring-[#BFFF00]"
          title="RepoPilot Fix — Overview"
          aria-label="RepoPilot Fix Overview"
        >
          <AnchorIcon className="text-black size-5" />
        </button>
      </div>

      {/* Main Navigation Items */}
      <nav aria-label="Main navigation" className="flex-1 flex flex-col gap-3 w-full items-center">
        {NAV_ITEMS.map((item) => {
          const isActive = currentView === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelectView(item.id)}
              aria-current={isActive ? "page" : undefined}
              className={`group relative p-2.5 rounded-xl transition-all focus-visible:ring-2 focus-visible:ring-[#BFFF00] focus-visible:outline-none ${
                isActive
                  ? "bg-white/10 text-[#BFFF00] shadow-inner border border-white/10"
                  : "text-white/40 hover:text-white hover:bg-white/5 border border-transparent"
              }`}
              title={item.label}
              aria-label={item.label}
            >
              {item.icon(isActive)}

              {/* Active Indicator Pip */}
              {isActive && (
                <span className="absolute -left-2 top-2 h-5 w-1 rounded-r-full bg-[#BFFF00]" />
              )}

              {/* Hover Tooltip */}
              <div className="pointer-events-none absolute left-14 top-1.5 z-50 hidden rounded-md border border-white/10 bg-[#0e1117] px-2.5 py-1 text-xs font-sans text-white shadow-2xl group-hover:block whitespace-nowrap">
                {item.label}
              </div>
            </button>
          );
        })}
      </nav>

      {/* Bottom Utility Items */}
      <div className="mt-auto flex flex-col gap-3 w-full items-center pt-3 border-t border-white/5">
        {/* Command Palette Trigger */}
        <button
          onClick={onOpenCommandPalette}
          className="group relative p-2 rounded-xl text-white/40 hover:text-cyan-300 hover:bg-white/5 transition-colors font-mono text-[11px]"
          title="Command Palette (Cmd+K)"
          aria-label="Command Palette"
        >
          <span>⌘K</span>
          <div className="pointer-events-none absolute left-14 top-1 z-50 hidden rounded-md border border-white/10 bg-[#0e1117] px-2.5 py-1 text-xs font-sans text-white shadow-2xl group-hover:block whitespace-nowrap">
            Command Palette (Ctrl/Cmd+K)
          </div>
        </button>

        {/* Settings View Trigger */}
        <button
          onClick={() => onSelectView("settings")}
          aria-current={currentView === "settings" ? "page" : undefined}
          className={`group relative p-2.5 rounded-xl transition-all focus-visible:ring-2 focus-visible:ring-[#BFFF00] focus-visible:outline-none ${
            currentView === "settings"
              ? "bg-white/10 text-[#BFFF00] shadow-inner border border-white/10"
              : "text-white/40 hover:text-white hover:bg-white/5 border border-transparent"
          }`}
          title="Settings & Keybindings"
          aria-label="Settings"
        >
          <SettingsIcon className="size-5" />
          <div className="pointer-events-none absolute left-14 top-1.5 z-50 hidden rounded-md border border-white/10 bg-[#0e1117] px-2.5 py-1 text-xs font-sans text-white shadow-2xl group-hover:block whitespace-nowrap">
            Settings
          </div>
        </button>

        {/* User Account or Sign In */}
        {currentUser && !currentUser.is_guest ? (
          <div className="group relative">
            <button
              onClick={onSignOut}
              className="size-8 rounded-full border border-lime-400 p-0.5 cursor-pointer hover:scale-105 transition-transform"
              title={`Signed in as @${currentUser.login} (Click to sign out)`}
              aria-label="Sign out"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={currentUser.avatar_url || `https://api.dicebear.com/7.x/avataaars/svg?seed=${currentUser.login}`}
                alt={currentUser.login}
                className="w-full h-full rounded-full bg-white object-cover"
              />
            </button>
            <div className="pointer-events-none absolute left-14 bottom-0 z-50 hidden rounded-md border border-white/10 bg-[#0e1117] px-2.5 py-1 text-xs font-sans text-white shadow-2xl group-hover:block whitespace-nowrap">
              @{currentUser.login} (Click to sign out)
            </div>
          </div>
        ) : (
          <div className="group relative">
            <button
              onClick={onSignIn}
              className={`size-8 rounded-xl border flex items-center justify-center transition-colors ${
                currentUser?.is_guest
                  ? "bg-cyan-500/10 border-cyan-500/30 text-cyan-300 hover:bg-cyan-500/20"
                  : "bg-white/5 border-white/10 text-white/50 hover:text-white hover:bg-white/10"
              }`}
              title={
                currentUser?.is_guest
                  ? "Guest Session Active — Click to Sign in with GitHub"
                  : "Sign in with GitHub"
              }
              aria-label="Sign in with GitHub"
            >
              <GithubIcon className="size-4" />
            </button>
            <div className="pointer-events-none absolute left-14 bottom-0 z-50 hidden rounded-md border border-white/10 bg-[#0e1117] px-2.5 py-1 text-xs font-sans text-white shadow-2xl group-hover:block whitespace-nowrap">
              {currentUser?.is_guest
                ? "Guest User (Click to sign in with GitHub)"
                : "Sign in with GitHub"}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
