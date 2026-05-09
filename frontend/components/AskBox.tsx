"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EvidenceList, type EvidenceItem } from "./EvidenceList";
import { MarkdownBody } from "./MarkdownBody";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

type ChatMessage = { role: "user" | "assistant"; content: string; evidence?: EvidenceItem[] };

type ThreadMeta = {
  id: string;
  updatedAt: number;
  preview: string;
};

type ApiThreadRow = {
  conversation_id: string;
  updated_at?: string;
  message_count: number;
  preview: string;
};

function threadStateKey(ticker: string): string {
  return `filings_memory_agent_thread_state_${ticker.toUpperCase()}`;
}

function legacyChatKey(ticker: string): string {
  return `filings_memory_agent_chat_${ticker.toUpperCase()}`;
}

function loadThreadState(ticker: string): { activeId: string | null; threads: ThreadMeta[] } {
  if (typeof window === "undefined") {
    return { activeId: null, threads: [] };
  }
  const t = ticker.toUpperCase();
  try {
    const raw = window.localStorage.getItem(threadStateKey(t));
    if (raw) {
      const p = JSON.parse(raw) as { activeId?: string | null; threads?: ThreadMeta[] };
      return {
        activeId: p.activeId ?? null,
        threads: Array.isArray(p.threads) ? p.threads : [],
      };
    }
    const leg = window.localStorage.getItem(legacyChatKey(t));
    if (leg) {
      const one: ThreadMeta = { id: leg, updatedAt: Date.now(), preview: "" };
      const st = { activeId: leg, threads: [one] };
      window.localStorage.setItem(threadStateKey(t), JSON.stringify(st));
      window.localStorage.removeItem(legacyChatKey(t));
      return st;
    }
  } catch {
    /* ignore */
  }
  return { activeId: null, threads: [] };
}

function saveThreadState(ticker: string, state: { activeId: string | null; threads: ThreadMeta[] }) {
  try {
    window.localStorage.setItem(threadStateKey(ticker.toUpperCase()), JSON.stringify(state));
  } catch {
    /* ignore */
  }
}

function newConversationId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

function mergeThreads(local: ThreadMeta[], api: ApiThreadRow[]): ThreadMeta[] {
  const map = new Map<string, ThreadMeta>();
  for (const x of local) {
    map.set(x.id, { ...x });
  }
  for (const a of api) {
    const ts = a.updated_at ? Date.parse(a.updated_at) : Date.now();
    const cur = map.get(a.conversation_id);
    if (!cur) {
      map.set(a.conversation_id, {
        id: a.conversation_id,
        updatedAt: Number.isNaN(ts) ? Date.now() : ts,
        preview: a.preview || "(empty)",
      });
    } else {
      cur.updatedAt = Math.max(cur.updatedAt, Number.isNaN(ts) ? cur.updatedAt : ts);
      if ((!cur.preview || cur.preview === "(new chat)") && a.preview) cur.preview = a.preview;
    }
  }
  return Array.from(map.values()).sort((a, b) => b.updatedAt - a.updatedAt);
}

function normalizeMessages(
  raw: Array<{ role?: string; content?: string; evidence?: EvidenceItem[] }>
): ChatMessage[] {
  return raw
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      role: m.role as "user" | "assistant",
      content: String(m.content ?? ""),
      evidence: m.evidence,
    }));
}

async function fetchMessages(conversationId: string, ticker: string): Promise<ChatMessage[]> {
  const r = await fetch(
    `${API}/api/chat/${encodeURIComponent(conversationId)}?ticker=${encodeURIComponent(ticker)}`
  );
  if (!r.ok) return [];
  const data = await r.json();
  const raw = (data.messages ?? []) as Array<{
    role?: string;
    content?: string;
    evidence?: EvidenceItem[];
  }>;
  return normalizeMessages(raw);
}

export function AskBox({ ticker }: { ticker: string }) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ThreadMeta[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    let cancelled = false;
    const t = ticker.toUpperCase();

    async function hydrate() {
      const local = loadThreadState(ticker);
      let apiRows: ApiThreadRow[] = [];
      try {
        const tr = await fetch(`${API}/api/chat/threads/${encodeURIComponent(t)}`);
        if (tr.ok) {
          const data = await tr.json();
          apiRows = (data.threads ?? []) as ApiThreadRow[];
        }
      } catch {
        /* offline */
      }

      const merged = mergeThreads(local.threads, apiRows);
      if (cancelled) return;

      let chosenId = local.activeId;
      if (!chosenId || !merged.some((x) => x.id === chosenId)) {
        chosenId = merged.length > 0 ? merged[0].id : null;
      }

      if (!chosenId) {
        const nid = newConversationId();
        merged.unshift({ id: nid, updatedAt: Date.now(), preview: "(new chat)" });
        chosenId = nid;
      }

      const msgs = await fetchMessages(chosenId, ticker);
      if (cancelled) return;

      setThreads(merged);
      setConversationId(chosenId);
      setMessages(msgs);
      saveThreadState(ticker, { activeId: chosenId, threads: merged });
      setHydrated(true);
    }

    hydrate();
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  const persistThreads = useCallback(
    (nextThreads: ThreadMeta[], activeId: string | null) => {
      setThreads(nextThreads);
      saveThreadState(ticker, { activeId, threads: nextThreads });
    },
    [ticker]
  );

  const selectThread = useCallback(
    async (id: string) => {
      if (!id || id === conversationId || switching) return;
      setSwitching(true);
      setError(null);
      try {
        const msgs = await fetchMessages(id, ticker);
        setConversationId(id);
        setMessages(msgs);
        setThreads((prev) => {
          saveThreadState(ticker, { activeId: id, threads: prev });
          return prev;
        });
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load conversation");
      } finally {
        setSwitching(false);
      }
    },
    [conversationId, switching, ticker]
  );

  const startNewChat = useCallback(() => {
    let next = [...threads];
    if (conversationId && messages.length > 0) {
      const preview =
        messages.find((m) => m.role === "user")?.content?.trim().slice(0, 80) ||
        `Chat · ${new Date().toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" })}`;
      const idx = next.findIndex((x) => x.id === conversationId);
      const meta: ThreadMeta = {
        id: conversationId,
        updatedAt: Date.now(),
        preview,
      };
      if (idx >= 0) next[idx] = meta;
      else next.unshift(meta);
    }
    const nid = newConversationId();
    next = [{ id: nid, updatedAt: Date.now(), preview: "(new chat)" }, ...next.filter((x) => x.id !== nid)];
    persistThreads(next, nid);
    setConversationId(nid);
    setMessages([]);
    setError(null);
  }, [conversationId, messages, persistThreads, threads]);

  const deleteCurrentChat = useCallback(async () => {
    if (!conversationId) return;
    const id = conversationId;
    try {
      await fetch(`${API}/api/chat/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: id }),
      });
    } catch {
      /* offline */
    }

    const remaining = threads.filter((x) => x.id !== id);
    if (remaining.length === 0) {
      const nid = newConversationId();
      const fresh = [{ id: nid, updatedAt: Date.now(), preview: "(new chat)" }];
      persistThreads(fresh, nid);
      setConversationId(nid);
      setMessages([]);
      return;
    }

    const nextActive = remaining[0].id;
    persistThreads(remaining, nextActive);
    setConversationId(nextActive);
    const msgs = await fetchMessages(nextActive, ticker);
    setMessages(msgs);
  }, [conversationId, persistThreads, threads, ticker]);

  async function submit() {
    const q = input.trim();
    if (!q || loading || !hydrated) return;
    let id = conversationId;
    if (!id) {
      id = newConversationId();
      setConversationId(id);
    }
    setInput("");
    setError(null);
    setMessages((m) => [...m, { role: "user", content: q }]);
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker,
          question: q,
          conversation_id: id,
        }),
      });
      if (!r.ok) {
        const t = await r.text();
        throw new Error(t || r.statusText);
      }
      const data = await r.json();
      const finalId = (data.conversation_id as string) || id;
      if (finalId !== conversationId) {
        setConversationId(finalId);
      }

      const preview = q.slice(0, 80);
      setThreads((prev) => {
        const idx = prev.findIndex((x) => x.id === finalId);
        const meta: ThreadMeta = {
          id: finalId,
          updatedAt: Date.now(),
          preview,
        };
        let next: ThreadMeta[];
        if (idx >= 0) {
          next = [...prev];
          next[idx] = { ...next[idx], preview, updatedAt: Date.now() };
        } else {
          next = [{ ...meta }, ...prev];
        }
        saveThreadState(ticker, { activeId: finalId, threads: next });
        return next;
      });

      const ev = (data.evidence ?? []) as EvidenceItem[];
      setMessages((m) => [
        ...m,
        { role: "assistant", content: data.answer ?? "", evidence: ev },
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: "Something went wrong. Check the API and try again.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  const threadLabel = (th: ThreadMeta) => {
    const p = th.preview?.trim() || "";
    const short = p.length > 42 ? `${p.slice(0, 42)}…` : p || th.id.slice(0, 8);
    return short;
  };

  return (
    <div className="rounded-2xl border border-surface-border bg-surface-card p-6 shadow-lg shadow-black/40 flex flex-col max-h-[min(85vh,720px)]">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
        <div>
          <h3 className="text-lg font-semibold text-white">Ask Across Filings</h3>
          <p className="text-xs text-slate-500 mt-1 max-w-xl">
            Conversations stay on the server. Use{" "}
            <span className="text-slate-400">Previous chats</span> to switch threads —{" "}
            <span className="text-slate-400">New chat</span> keeps older threads intact.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 shrink-0">
          <button
            type="button"
            onClick={() => startNewChat()}
            className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-1.5 text-xs text-accent hover:bg-accent/15"
          >
            New chat
          </button>
          <button
            type="button"
            onClick={() => deleteCurrentChat()}
            disabled={!conversationId || switching}
            className="rounded-lg border border-surface-border px-3 py-1.5 text-xs text-slate-500 hover:bg-white/5 disabled:opacity-40"
          >
            Delete thread
          </button>
        </div>
      </div>

      <div className="mb-3 flex flex-col sm:flex-row gap-2 sm:items-center">
        <label className="text-[10px] uppercase text-slate-500 shrink-0">Previous chats</label>
        <select
          className="flex-1 rounded-lg border border-surface-border bg-black/30 px-3 py-2 text-xs text-slate-200 focus:outline-none focus:ring-2 focus:ring-accent/40 disabled:opacity-50"
          value={conversationId ?? ""}
          disabled={!hydrated || switching}
          onChange={(e) => void selectThread(e.target.value)}
        >
          {threads.map((th) => (
            <option key={th.id} value={th.id}>
              {threadLabel(th)}
            </option>
          ))}
        </select>
        {switching && <span className="text-[10px] text-slate-500">Loading…</span>}
      </div>

      <div className="flex-1 min-h-[200px] overflow-y-auto rounded-xl border border-surface-border bg-black/20 p-3 space-y-4 mb-3">
        {!hydrated && (
          <p className="text-sm text-slate-500 text-center py-8 animate-pulse">Loading chat…</p>
        )}
        {hydrated && messages.length === 0 && !loading && (
          <p className="text-sm text-slate-500 text-center py-8">
            Ask about risks, revenue, or year-over-year changes. Follow-ups stay in this thread.
          </p>
        )}
        {messages.map((msg, i) => (
          <div
            key={`${conversationId}-${i}`}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[92%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                msg.role === "user"
                  ? "bg-accent/20 text-slate-100 border border-accent/30"
                  : "bg-slate-800/80 text-slate-200 border border-surface-border"
              }`}
            >
              <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                {msg.role === "user" ? "You" : "Assistant"}
              </p>
              {msg.role === "user" ? (
                <p className="whitespace-pre-wrap">{msg.content}</p>
              ) : (
                <MarkdownBody content={msg.content} />
              )}
              {msg.role === "assistant" && msg.evidence && msg.evidence.length > 0 && (
                <div className="mt-3 pt-3 border-t border-slate-700/80">
                  <p className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">
                    Evidence
                  </p>
                  <EvidenceList items={msg.evidence} />
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="rounded-2xl border border-surface-border bg-slate-800/50 px-4 py-3 text-sm text-slate-500 animate-pulse">
              Thinking…
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && <p className="text-xs text-red-400 mb-2">{error}</p>}

      <div className="flex flex-col sm:flex-row gap-2 sm:items-end">
        <textarea
          className="flex-1 rounded-xl border border-surface-border bg-black/30 px-4 py-3 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-accent/40 min-h-[52px] max-h-32 resize-y"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question or follow up…"
          disabled={!hydrated}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button
          type="button"
          onClick={() => submit()}
          disabled={loading || !input.trim() || !hydrated}
          className="rounded-xl bg-accent px-5 py-3 text-sm font-medium text-surface transition hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed sm:shrink-0"
        >
          Send
        </button>
      </div>
      <p
        className="text-[10px] text-slate-600 mt-2 font-mono truncate"
        title={conversationId ?? undefined}
      >
        Session: {conversationId ? `${conversationId.slice(0, 8)}…` : "…"} · {threads.length}{" "}
        saved thread{threads.length === 1 ? "" : "s"}
      </p>
    </div>
  );
}
