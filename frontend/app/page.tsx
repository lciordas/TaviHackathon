"use client";

import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

type Role = "user" | "assistant";
type Message = { role: Role; content: string };
type Fields = Record<string, unknown>;

type ChatResponse = {
  reply: string;
  fields: Fields;
  is_ready: boolean;
  missing: string[];
};

const AFFIRMATIVE = new Set([
  "y", "yes", "yeah", "yep", "yup", "ya",
  "ok", "okay", "k",
  "sure", "confirm", "confirmed", "submit",
  "do it", "go", "go ahead", "send", "send it", "ship it",
  "looks good", "lgtm", "sounds good", "good",
  "correct", "right", "that's right", "all good",
  "affirmative", "perfect", "great", "fine",
  "absolutely", "for sure", "please do", "please",
]);

function isAffirmative(text: string): boolean {
  const norm = text.toLowerCase().trim().replace(/[.!?,]+$/, "").trim();
  if (norm.length > 30) return false;
  return AFFIRMATIVE.has(norm);
}

const FIELD_LABELS: Record<string, string> = {
  trade: "Trade",
  description: "Description",
  access_notes: "Access notes",
  urgency: "Urgency",
  scheduled_for: "Scheduled for",
  budget_cap_cents: "Budget cap",
  quality_threshold: "Min vendor rating",
  requires_licensed: "Licensed required",
  requires_insured: "Insured required",
};

function formatValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (key === "budget_cap_cents" && typeof value === "number") {
    return `$${(value / 100).toLocaleString()}`;
  }
  if (key === "scheduled_for" && typeof value === "string") {
    const d = new Date(value);
    return isNaN(d.getTime()) ? value : d.toLocaleString();
  }
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toString();
  return String(value);
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [fields, setFields] = useState<Fields>({});
  const [isReady, setIsReady] = useState(false);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [submittedId, setSubmittedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch(`${API_BASE}/intake/start`, { method: "POST" })
      .then((r) => {
        if (!r.ok) throw new Error(`start ${r.status}`);
        return r.json();
      })
      .then((d: { greeting: string; fields: Fields }) => {
        setMessages([{ role: "assistant", content: d.greeting }]);
        setFields(d.fields ?? {});
      })
      .catch((e: unknown) => {
        setError(`Can't reach backend at ${API_BASE}. Is uvicorn running? (${String(e)})`);
      });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  async function callChat(nextMessages: Message[]) {
    setPending(true);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/intake/chat`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ messages: nextMessages, fields }),
      });
      if (!r.ok) throw new Error(`chat ${r.status}`);
      const d: ChatResponse = await r.json();
      setMessages([...nextMessages, { role: "assistant", content: d.reply }]);
      setFields(d.fields ?? {});
      setIsReady(Boolean(d.is_ready));
    } catch (e: unknown) {
      setError(`Chat failed: ${String(e)}`);
    } finally {
      setPending(false);
    }
  }

  async function submit() {
    setPending(true);
    setError(null);
    try {
      const r = await fetch(`${API_BASE}/intake/confirm`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ fields }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(`can't submit: ${JSON.stringify(detail.detail ?? detail)}`);
      }
      const d: { id: string } = await r.json();
      setSubmittedId(d.id);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setPending(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || pending || submittedId) return;

    const next: Message[] = [...messages, { role: "user", content: text }];
    setInput("");
    setMessages(next);

    if (isReady && isAffirmative(text)) {
      void submit();
      return;
    }

    void callChat(next);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as FormEvent);
    }
  }

  const nonNullFields = Object.entries(fields).filter(
    ([, v]) => v !== null && v !== undefined,
  );

  return (
    <div className="flex-1 flex flex-col bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-6 py-4">
          <h1 className="text-xl font-semibold tracking-tight">Tavi — Work Order Intake</h1>
          <p className="text-sm text-slate-500">Chat with the intake agent to file a work order.</p>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-6xl px-6 py-6 grid grid-cols-1 md:grid-cols-3 gap-6">
        <section className="md:col-span-2 flex flex-col h-[calc(100vh-10rem)] bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-6 space-y-4">
            {messages.length === 0 && !error && (
              <div className="text-center text-sm text-slate-400 pt-12">Loading…</div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[80%] rounded-2xl px-4 py-2 whitespace-pre-wrap bg-slate-900 text-white"
                      : "max-w-[80%] rounded-2xl px-4 py-2 whitespace-pre-wrap bg-slate-100 text-slate-900"
                  }
                >
                  {m.content}
                </div>
              </div>
            ))}
            {pending && (
              <div className="flex justify-start">
                <div className="rounded-2xl px-4 py-2 bg-slate-100 text-slate-500 italic text-sm">
                  thinking…
                </div>
              </div>
            )}
            {submittedId && (
              <div className="flex justify-center pt-2">
                <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3 text-sm text-emerald-900">
                  Submitted. Work order ID: <code className="font-mono text-xs">{submittedId}</code>
                </div>
              </div>
            )}
            {error && (
              <div className="flex justify-center pt-2">
                <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-800">
                  {error}
                </div>
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="border-t border-slate-200 p-4 flex gap-2 items-end">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={submittedId ? "Work order submitted." : "Type your message…  (Shift+Enter for new line)"}
              disabled={pending || !!submittedId}
              rows={1}
              className="flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 disabled:bg-slate-50 disabled:text-slate-400"
            />
            <button
              type="submit"
              disabled={pending || !input.trim() || !!submittedId}
              className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-medium transition-colors hover:bg-slate-800 disabled:bg-slate-300 disabled:cursor-not-allowed"
            >
              Send
            </button>
            {isReady && !submittedId && (
              <button
                type="button"
                onClick={() => void submit()}
                disabled={pending}
                className="rounded-lg bg-emerald-600 text-white px-4 py-2 text-sm font-medium transition-colors hover:bg-emerald-700 disabled:bg-emerald-300 disabled:cursor-not-allowed"
              >
                Submit
              </button>
            )}
          </form>
        </section>

        <aside className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 h-fit">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-slate-900">Captured fields</h2>
            {isReady && !submittedId && (
              <span className="text-xs font-medium text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                Ready
              </span>
            )}
          </div>
          {nonNullFields.length === 0 ? (
            <p className="text-sm text-slate-400">Nothing captured yet.</p>
          ) : (
            <dl className="space-y-3">
              {nonNullFields.map(([k, v]) => (
                <div key={k} className="text-sm">
                  <dt className="text-xs uppercase tracking-wide text-slate-500">{FIELD_LABELS[k] ?? k}</dt>
                  <dd className="text-slate-900 break-words mt-0.5">{formatValue(k, v)}</dd>
                </div>
              ))}
            </dl>
          )}
        </aside>
      </main>
    </div>
  );
}
