"use client";

import { FormEvent, useState, useRef, useEffect } from "react";
import { ChatSession, Citation } from "../lib/types";

interface AiChatPanelProps {
  sessions: ChatSession[];
  activeSession: ChatSession | null;
  onSelectSession: (session: ChatSession) => void;
  onNewSession: () => void;
  onDeleteSession: (session: ChatSession) => void;
  onClearSessionHistory: () => void;
  chatMode: "auto" | "general" | "coding" | "repository";
  onChangeMode: (mode: "auto" | "general" | "coding" | "repository") => void;
  chatBusy: boolean;
  chatError: string;
  onSendMessage: (text: string) => Promise<void>;
  onRetryLastQuestion: () => void;
  lastFailedQuestion: { text: string; mode: string } | null;
  onOpenCitation: (path: string, start: number, end: number) => void;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
}

export function AiChatPanel({
  sessions,
  activeSession,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  onClearSessionHistory,
  chatMode,
  onChangeMode,
  chatBusy,
  chatError,
  onSendMessage,
  onRetryLastQuestion,
  lastFailedQuestion,
  onOpenCitation,
  isCollapsed,
  onToggleCollapse,
}: AiChatPanelProps) {
  const [inputText, setInputText] = useState("");
  const [showSessionsDrawer, setShowSessionsDrawer] = useState(false);
  const [copiedId, setCopiedId] = useState<number | string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to bottom of messages
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeSession?.messages, chatBusy]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!inputText.trim() || chatBusy) return;
    const msg = inputText.trim();
    setInputText("");
    void onSendMessage(msg);
  };

  const copyToClipboard = (text: string, id: number | string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (isCollapsed) {
    return (
      <div className="flex h-full flex-col items-center border-l border-white/10 bg-[#090b0e] py-3 w-8 select-none">
        <button
          onClick={onToggleCollapse}
          className="text-xs text-zinc-400 hover:text-white transition-colors"
          title="Expand AI Assistant Panel"
        >
          ◂
        </button>
      </div>
    );
  }

  return (
    <div className="flex h-full w-96 shrink-0 flex-col border-l border-white/10 bg-[#090b0e] overflow-hidden select-none">
      {/* Header */}
      <div className="p-3 border-b border-white/10 bg-[#0d0f14] flex flex-col gap-2.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="size-2 rounded-full bg-[#00F0FF] animate-pulse" />
            <span className="text-[10px] font-mono font-bold uppercase tracking-widest text-[#00F0FF]">
              AI Workspace
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => setShowSessionsDrawer((prev) => !prev)}
              className={`rounded px-2 py-0.5 text-[10px] font-mono border transition-colors ${
                showSessionsDrawer
                  ? "bg-white/10 border-white/20 text-white"
                  : "border-white/5 bg-white/5 text-zinc-400 hover:text-zinc-200"
              }`}
              title="Toggle conversation list"
            >
              History ({sessions.length})
            </button>
            <button
              onClick={onNewSession}
              disabled={chatBusy}
              className="rounded bg-[#BFFF00]/10 border border-[#BFFF00]/30 px-2 py-0.5 text-[10px] font-mono font-semibold text-[#BFFF00] hover:bg-[#BFFF00]/20 disabled:opacity-50 transition-colors"
              title="New Conversation"
            >
              + New
            </button>
            <button
              onClick={onToggleCollapse}
              className="text-zinc-500 hover:text-white text-xs px-1"
              title="Collapse Panel"
            >
              ▸
            </button>
          </div>
        </div>

        {/* Mode Selector Tabs */}
        <div className="grid grid-cols-4 gap-1 rounded-lg border border-white/10 bg-[#12151c] p-0.5 text-[10px] font-mono">
          {(["auto", "general", "coding", "repository"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => onChangeMode(mode)}
              className={`rounded py-1 text-center capitalize transition-colors ${
                chatMode === mode
                  ? "bg-[#00F0FF]/20 text-[#00F0FF] font-bold border border-[#00F0FF]/40"
                  : "text-zinc-400 hover:text-zinc-200"
              }`}
            >
              {mode}
            </button>
          ))}
        </div>
      </div>

      {/* History Drawer if toggled */}
      {showSessionsDrawer && (
        <div className="border-b border-white/10 bg-[#0e1117] p-2 max-h-48 overflow-y-auto space-y-1">
          <div className="flex items-center justify-between px-2 py-1 text-[10px] font-mono text-zinc-400">
            <span>Conversations</span>
            {activeSession && (
              <button
                onClick={onClearSessionHistory}
                className="text-rose-400 hover:text-rose-300 transition-colors"
              >
                Clear Messages
              </button>
            )}
          </div>
          {sessions.map((s) => (
            <div
              key={s.id}
              className={`flex items-center justify-between rounded-lg px-2.5 py-1.5 text-xs transition-colors ${
                s.id === activeSession?.id
                  ? "bg-white/10 text-white font-medium"
                  : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
              }`}
            >
              <button
                onClick={() => {
                  onSelectSession(s);
                  setShowSessionsDrawer(false);
                }}
                className="min-w-0 flex-1 text-left truncate font-sans text-xs"
              >
                {s.title || "Untitled Session"}
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onDeleteSession(s);
                }}
                className="text-zinc-600 hover:text-rose-400 ml-2 text-xs"
                title="Delete conversation"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Chat Messages Scroll Area */}
      <div className="flex-1 overflow-y-auto p-3 space-y-3 font-sans">
        {!activeSession ? (
          <div className="p-6 text-center text-xs text-zinc-500 font-sans">
            No active conversation. Click &ldquo;+ New&rdquo; above to start asking repository questions.
          </div>
        ) : activeSession.messages && activeSession.messages.length > 0 ? (
          activeSession.messages.map((msg, idx) => {
            const isUser = msg.role === "user";
            return (
              <div
                key={msg.id || idx}
                className={`flex flex-col space-y-1 ${isUser ? "items-end" : "items-start"}`}
              >
                {/* Role / Mode label */}
                <div className="flex items-center gap-1.5 text-[10px] font-mono text-zinc-500 px-1">
                  <span>{isUser ? "You" : "RepoPilot"}</span>
                  {msg.mode && (
                    <span className="rounded bg-white/5 border border-white/10 px-1 uppercase text-zinc-400 text-[9px]">
                      {msg.mode}
                    </span>
                  )}
                </div>

                {/* Bubble */}
                <div
                  className={`rounded-xl px-3.5 py-2.5 text-xs leading-relaxed max-w-[95%] break-words ${
                    isUser
                      ? "bg-[#182017] border border-[#BFFF00]/30 text-lime-100"
                      : "bg-[#10131a] border border-white/10 text-zinc-200"
                  }`}
                >
                  {/* Message Content formatted */}
                  <div className="whitespace-pre-wrap font-sans space-y-2">
                    {msg.content}
                  </div>

                  {/* Clickable Source Citations */}
                  {msg.citations && msg.citations.length > 0 && (
                    <div className="mt-3 pt-2.5 border-t border-white/10">
                      <div className="text-[10px] font-mono uppercase tracking-wider text-cyan-400 mb-1.5">
                        Verified Evidence Citations:
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {msg.citations.map((c: Citation, cIdx: number) => (
                          <button
                            key={cIdx}
                            onClick={() => onOpenCitation(c.path, c.start_line, c.end_line)}
                            className="flex items-center gap-1 rounded bg-cyan-950/60 border border-cyan-500/30 px-2 py-0.5 text-[10px] font-mono text-cyan-300 hover:border-cyan-400 hover:text-white transition-colors"
                            title={`Inspect ${c.path} (lines ${c.start_line}-${c.end_line}) in Code Viewer`}
                          >
                            <span>{c.path}</span>
                            <span className="text-zinc-500">
                              L{c.start_line}–{c.end_line}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Copy button */}
                  {!isUser && (
                    <div className="mt-2 flex justify-end">
                      <button
                        onClick={() => copyToClipboard(msg.content, msg.id || idx)}
                        className="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 transition-colors"
                      >
                        {copiedId === (msg.id || idx) ? "✓ Copied" : "Copy"}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })
        ) : (
          <div className="p-6 text-center text-xs text-zinc-500 space-y-2">
            <p className="font-semibold text-zinc-400">Fresh Conversation Initialized</p>
            <p className="text-[11px] leading-relaxed">
              Ask questions grounded in the repository source code, AST symbols, or commit history.
            </p>
          </div>
        )}

        {/* Loading spinner */}
        {chatBusy && (
          <div className="flex items-center gap-2 p-3 text-xs text-cyan-300 font-mono animate-pulse">
            <span className="size-2 rounded-full bg-cyan-400" />
            <span>Retrieving verified code evidence & synthesizing answer…</span>
          </div>
        )}

        {/* Chat Error & Retry */}
        {chatError && (
          <div className="rounded-xl border border-rose-900/40 bg-rose-950/20 p-3 text-xs text-rose-300 space-y-2 font-mono">
            <div>{chatError}</div>
            {lastFailedQuestion && (
              <button
                onClick={onRetryLastQuestion}
                className="rounded bg-rose-900/40 border border-rose-500/30 px-2 py-1 text-[11px] text-white hover:bg-rose-900/60 transition-colors"
              >
                Retry Request
              </button>
            )}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input Form */}
      <form onSubmit={handleSubmit} className="p-3 border-t border-white/10 bg-[#0d0f14]">
        <div className="relative">
          <textarea
            rows={2}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSubmit(e);
              }
            }}
            placeholder={
              chatMode === "repository"
                ? "Ask a question about the repository (requires grounded evidence)…"
                : "Ask anything, explore codebase, or draft code…"
            }
            className="w-full resize-none rounded-xl border border-white/10 bg-[#141822] p-2.5 text-xs text-zinc-100 placeholder-zinc-500 outline-none focus:border-[#00F0FF]/50 font-sans"
          />

          <div className="flex items-center justify-between mt-1 text-[10px] text-zinc-500 font-mono">
            <span>↵ Enter to send · Shift+↵ newline</span>
            <button
              type="submit"
              disabled={chatBusy || !inputText.trim() || !activeSession}
              className="rounded-lg bg-[#00F0FF] px-3 py-1 font-semibold text-black hover:brightness-110 disabled:opacity-40 transition-all font-sans"
            >
              Send
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
