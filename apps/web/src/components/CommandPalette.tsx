"use client";

import { useEffect, useState, useMemo } from "react";
import { Repository, WorkspaceView, IndexedFileItem } from "../lib/types";
import { useFocusTrap } from "../lib/useFocusTrap";

interface CommandPaletteProps {
  isOpen: boolean;
  onClose: () => void;
  repositories: Repository[];
  activeRepoId: number | null;
  onSelectRepo: (id: number) => void;
  onSelectView: (view: WorkspaceView) => void;
  indexedFiles: IndexedFileItem[];
  onOpenFile: (path: string) => void;
  onNewChat: () => void;
}

interface CommandItem {
  id: string;
  category: "Navigation" | "Repositories" | "Files" | "Actions";
  title: string;
  subtitle?: string;
  badge?: string;
  action: () => void;
}

export function CommandPalette({
  isOpen,
  onClose,
  repositories,
  activeRepoId,
  onSelectRepo,
  onSelectView,
  indexedFiles,
  onOpenFile,
  onNewChat,
}: CommandPaletteProps) {
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const modalRef = useFocusTrap({
    isOpen,
    onClose,
    closeOnEscape: true,
  });

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        onClose();
      }
    }
    if (!isOpen) return;
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  const commands = useMemo<CommandItem[]>(() => {
    const list: CommandItem[] = [
      {
        id: "nav-overview",
        category: "Navigation",
        title: "Overview Dashboard",
        subtitle: "Global workspace status, KPIs, and recent activity",
        badge: "View",
        action: () => {
          onSelectView("overview");
          onClose();
        },
      },
      {
        id: "nav-repos",
        category: "Navigation",
        title: "Repository Intelligence",
        subtitle: "Searchable file tree, symbol index, and code preview",
        badge: "View",
        action: () => {
          onSelectView("repos");
          onClose();
        },
      },
      {
        id: "nav-chat",
        category: "Navigation",
        title: "AI Workspace & Coding Assistant",
        subtitle: "Repository Q&A, citations, and grounded code reasoning",
        badge: "View",
        action: () => {
          onSelectView("chat");
          onClose();
        },
      },
      {
        id: "nav-issues",
        category: "Navigation",
        title: "Issue Investigation Workspace",
        subtitle: "Analyze GitHub issues and review root cause hypotheses",
        badge: "View",
        action: () => {
          onSelectView("issues");
          onClose();
        },
      },
      {
        id: "nav-patch",
        category: "Navigation",
        title: "Patch Review Workspace",
        subtitle: "Side-by-side or unified diffs and patch validation",
        badge: "View",
        action: () => {
          onSelectView("patch");
          onClose();
        },
      },
      {
        id: "nav-verification",
        category: "Navigation",
        title: "Verification Center",
        subtitle: "Dual Docker sandbox verification runs & proof reports",
        badge: "View",
        action: () => {
          onSelectView("verification");
          onClose();
        },
      },
      {
        id: "nav-settings",
        category: "Navigation",
        title: "Settings & Shortcuts",
        subtitle: "Workspace preferences and developer environment",
        badge: "View",
        action: () => {
          onSelectView("settings");
          onClose();
        },
      },
      {
        id: "action-new-chat",
        category: "Actions",
        title: "Start New Chat Conversation",
        subtitle: "Initialize a fresh conversation pinned to current commit",
        badge: "Action",
        action: () => {
          onNewChat();
          onSelectView("chat");
          onClose();
        },
      },
    ];

    // Add repositories
    for (const repo of repositories) {
      list.push({
        id: `repo-${repo.id}`,
        category: "Repositories",
        title: repo.full_name,
        subtitle: repo.id === activeRepoId ? "Active Repository" : `Branch: ${repo.default_branch}`,
        badge: repo.id === activeRepoId ? "Selected" : "Repo",
        action: () => {
          onSelectRepo(repo.id);
          onClose();
        },
      });
    }

    // Add indexed files (top 30 matching)
    for (const f of indexedFiles.slice(0, 30)) {
      list.push({
        id: `file-${f.path}`,
        category: "Files",
        title: f.path,
        subtitle: `${f.language} · ${(f.size_bytes / 1024).toFixed(1)} KB`,
        badge: "File",
        action: () => {
          onOpenFile(f.path);
          onSelectView("repos");
          onClose();
        },
      });
    }

    return list;
  }, [repositories, activeRepoId, indexedFiles, onSelectView, onSelectRepo, onOpenFile, onNewChat, onClose]);

  const filtered = useMemo(() => {
    if (!query.trim()) return commands;
    const lower = query.toLowerCase();
    return commands.filter(
      (c) =>
        c.title.toLowerCase().includes(lower) ||
        (c.subtitle && c.subtitle.toLowerCase().includes(lower)) ||
        c.category.toLowerCase().includes(lower)
    );
  }, [commands, query]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % Math.max(1, filtered.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + filtered.length) % Math.max(1, filtered.length));
    } else if (e.key === "Enter" && filtered[selectedIndex]) {
      e.preventDefault();
      filtered[selectedIndex].action();
    }
  };

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command Palette"
      className="fixed inset-0 z-50 flex items-start justify-center pt-20 px-4 bg-black/75 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        ref={modalRef}
        className="w-full max-w-2xl rounded-2xl border border-white/10 bg-[#0e1014] shadow-2xl shadow-black/80 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="flex items-center gap-3 border-b border-white/10 px-4 py-3 bg-[#12141a]">
          <span className="text-[#00F0FF] text-lg" aria-hidden="true">⌘</span>
          <input
            autoFocus
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-listbox"
            aria-autocomplete="list"
            aria-activedescendant={filtered[selectedIndex]?.id}
            aria-label="Type a command, search files, or switch repositories"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command, search files, switch repos, or press Esc…"
            className="w-full bg-transparent text-sm text-[#F5F5F5] placeholder-zinc-500 outline-none font-sans"
          />
          <kbd className="rounded border border-white/10 bg-zinc-900/80 px-2 py-0.5 text-[10px] font-mono text-zinc-400" aria-hidden="true">
            ESC
          </kbd>
        </div>

        <div id="command-listbox" role="listbox" aria-label="Commands and files" className="max-h-96 overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <div className="py-8 text-center text-xs text-zinc-500 font-sans" role="status">
              No matching commands or files found for &ldquo;{query}&rdquo;
            </div>
          ) : (
            <div className="space-y-1">
              {filtered.map((item, idx) => {
                const isSelected = idx === selectedIndex;
                return (
                  <button
                    key={item.id}
                    id={item.id}
                    role="option"
                    aria-selected={isSelected}
                    onClick={item.action}
                    onMouseEnter={() => setSelectedIndex(idx)}
                    className={`w-full flex items-center justify-between rounded-xl px-3 py-2.5 text-left text-xs transition-colors ${
                      isSelected
                        ? "bg-white/5 border border-[#BFFF00]/30 text-white"
                        : "text-zinc-400 hover:bg-white/5"
                    }`}
                  >
                    <div className="min-w-0 pr-3">
                      <div className="flex items-center gap-2">
                        <span
                          className={`font-medium ${
                            isSelected ? "text-[#BFFF00]" : "text-[#F5F5F5]"
                          }`}
                        >
                          {item.title}
                        </span>
                        <span className="text-[10px] uppercase tracking-wider text-zinc-500">
                          {item.category}
                        </span>
                      </div>
                      {item.subtitle && (
                        <p className="mt-0.5 truncate text-[11px] text-zinc-400 font-mono">
                          {item.subtitle}
                        </p>
                      )}
                    </div>
                    {item.badge && (
                      <span
                        className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-mono ${
                          item.badge === "Selected"
                            ? "bg-[#BFFF00]/20 text-[#BFFF00] border border-[#BFFF00]/40"
                            : "bg-zinc-800 text-zinc-400 border border-white/5"
                        }`}
                      >
                        {item.badge}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="border-t border-white/5 bg-[#0a0c0f] px-4 py-2 flex items-center justify-between text-[11px] text-zinc-500 font-mono">
          <div className="flex items-center gap-3">
            <span>↑↓ Navigate</span>
            <span>↵ Select</span>
          </div>
          <span>RepoPilot Hub 2.0</span>
        </div>
      </div>
    </div>
  );
}
