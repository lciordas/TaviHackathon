"use client";

import Link from "next/link";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

import { Nav } from "@/components/Nav";

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

type Scenario = {
  id: string;
  trade: string | null;
  urgency: string | null;
  city: string | null;
  message: string;
};

type Suggestion = {
  place_id: string;
  primary_text: string;
  secondary_text: string;
};

type SelectedAddress = {
  address_line: string;
  city: string;
  state: string;
  zip: string;
  lat: number;
  lng: number;
  formatted_address: string;
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
  address_line: "Street",
  city: "City",
  state: "State",
  zip: "ZIP",
  lat: "Latitude",
  lng: "Longitude",
  access_notes: "Access notes",
  urgency: "Urgency",
  scheduled_for: "Scheduled for",
  budget_cap_cents: "Budget cap",
  quality_threshold: "Min vendor rating",
  requires_licensed: "Licensed required",
  requires_insured: "Insured required",
};

const HIDDEN_FIELDS = new Set(["lat", "lng", "address_hint"]);

// Must match backend REQUIRED_FIELDS in schemas.py. Kept here so the Submit
// button can react immediately to local field changes (e.g. address pick)
// without waiting for the next chat turn's server-side is_ready flag.
const FRONTEND_REQUIRED_FIELDS: readonly string[] = [
  "trade",
  "description",
  "address_line",
  "city",
  "state",
  "zip",
  "lat",
  "lng",
  "urgency",
  "scheduled_for",
  "budget_cap_cents",
  "quality_threshold",
  "requires_licensed",
  "requires_insured",
];

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

// Render the agent's markdown-formatted replies. Styled to fit the chat bubble;
// headings downgraded since they look weird inline.
function AssistantMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
        ul: ({ children }) => <ul className="list-disc pl-5 mb-2 last:mb-0 space-y-1">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal pl-5 mb-2 last:mb-0 space-y-1">{children}</ol>,
        li: ({ children }) => <li>{children}</li>,
        code: ({ children }) => (
          <code className="font-mono text-xs bg-slate-200 px-1 py-0.5 rounded">{children}</code>
        ),
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noreferrer" className="text-blue-600 underline">
            {children}
          </a>
        ),
        h1: ({ children }) => <p className="font-semibold mb-1">{children}</p>,
        h2: ({ children }) => <p className="font-semibold mb-1">{children}</p>,
        h3: ({ children }) => <p className="font-semibold mb-1">{children}</p>,
        hr: () => <hr className="my-2 border-slate-300" />,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [fields, setFields] = useState<Fields>({});
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [submittedId, setSubmittedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Address autocomplete state
  const [addressQuery, setAddressQuery] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [addressPending, setAddressPending] = useState(false);
  const [addressError, setAddressError] = useState<string | null>(null);
  const [selectedAddress, setSelectedAddress] = useState<SelectedAddress | null>(null);
  const [hintApplied, setHintApplied] = useState(false);

  // Pre-built demo scenarios from requests.json. Fetched once on mount.
  const [scenarios, setScenarios] = useState<Scenario[]>([]);

  // Derived, always-fresh: do we have every required field? Recomputes on
  // every fields change (including address pick), so the Submit button
  // appears immediately without needing another chat turn.
  const isReady = useMemo(() => {
    return FRONTEND_REQUIRED_FIELDS.every((f) => {
      const v = fields[f];
      return v !== null && v !== undefined && v !== "";
    });
  }, [fields]);

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

  // Pull the pre-built demo scenarios. Silent failure: panel just won't render.
  useEffect(() => {
    fetch(`${API_BASE}/intake/scenarios`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d: Scenario[]) => setScenarios(Array.isArray(d) ? d : []))
      .catch(() => {
        /* scenario panel simply hides */
      });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, pending]);

  // Auto-grow the message textarea up to a cap; scroll inside past that.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [input]);

  // Seed the autocomplete input from an LLM-extracted chat address ONCE, only
  // if the user hasn't selected or typed anything yet. Lets the LLM surface
  // addresses it notices in chat without clobbering manual edits.
  useEffect(() => {
    if (hintApplied || selectedAddress || addressQuery) return;
    const hint = fields.address_hint;
    if (typeof hint === "string" && hint.trim().length >= 4) {
      setAddressQuery(hint.trim());
      setHintApplied(true);
    }
  }, [fields, selectedAddress, addressQuery, hintApplied]);

  // Debounced autocomplete lookup
  useEffect(() => {
    if (selectedAddress) return;
    const q = addressQuery.trim();
    if (q.length < 4) {
      setSuggestions([]);
      setAddressError(null);
      return;
    }
    const timer = setTimeout(async () => {
      setAddressPending(true);
      setAddressError(null);
      try {
        const r = await fetch(`${API_BASE}/intake/places/autocomplete`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ query: q }),
        });
        if (!r.ok) {
          const detail = await r.json().catch(() => ({}));
          throw new Error(typeof detail.detail === "string" ? detail.detail : `autocomplete ${r.status}`);
        }
        const d: { suggestions: Suggestion[] } = await r.json();
        setSuggestions(d.suggestions ?? []);
      } catch (e: unknown) {
        setAddressError(`Address lookup failed: ${String(e)}`);
        setSuggestions([]);
      } finally {
        setAddressPending(false);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [addressQuery, selectedAddress]);

  async function pickSuggestion(place_id: string) {
    setAddressPending(true);
    setAddressError(null);
    try {
      const r = await fetch(`${API_BASE}/intake/places/select`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ place_id }),
      });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        throw new Error(typeof detail.detail === "string" ? detail.detail : `select ${r.status}`);
      }
      const d: SelectedAddress = await r.json();
      setSelectedAddress(d);
      setSuggestions([]);
      setAddressQuery("");
      setFields((prev) => ({
        ...prev,
        address_line: d.address_line,
        city: d.city,
        state: d.state,
        zip: d.zip,
        lat: d.lat,
        lng: d.lng,
      }));
    } catch (e: unknown) {
      setAddressError(`Couldn't resolve that address: ${String(e)}`);
    } finally {
      setAddressPending(false);
    }
  }

  function clearAddress() {
    setSelectedAddress(null);
    setFields((prev) => {
      const next = { ...prev };
      for (const k of ["address_line", "city", "state", "zip", "lat", "lng"]) {
        next[k] = null;
      }
      return next;
    });
    // isReady auto-derives from fields — no explicit reset needed.
  }

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
      // isReady is derived from fields, no explicit set.
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

  // Fire a pre-built scenario message as if the user typed and sent it.
  function useScenario(message: string) {
    if (pending || submittedId) return;
    const text = message.trim();
    if (!text) return;
    const next: Message[] = [...messages, { role: "user", content: text }];
    setMessages(next);
    void callChat(next);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e as unknown as FormEvent);
    }
  }

  const nonNullFields = Object.entries(fields).filter(
    ([k, v]) => v !== null && v !== undefined && !HIDDEN_FIELDS.has(k),
  );

  return (
    <div className="flex-1 flex flex-col bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-6xl px-6 py-4 flex items-center justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Tavi — Work Order Intake</h1>
            <p className="text-sm text-slate-500">Pick the service address, then chat with the agent.</p>
          </div>
          <Nav currentWorkOrderId={submittedId ?? undefined} />
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-6xl px-6 py-6 grid grid-cols-1 md:grid-cols-3 gap-6">
        <section className="md:col-span-2 flex flex-col bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          {/* Address autocomplete card */}
          <div className="border-b border-slate-200 bg-slate-50 p-4">
            <label className="block text-xs uppercase tracking-wide text-slate-500 mb-1.5">
              Service address
            </label>
            {selectedAddress ? (
              <div className="flex items-start justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2">
                <div className="text-sm">
                  <div className="font-medium text-emerald-900">{selectedAddress.formatted_address}</div>
                  <div className="text-xs text-emerald-700 mt-0.5">
                    {selectedAddress.lat.toFixed(5)}, {selectedAddress.lng.toFixed(5)}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={clearAddress}
                  disabled={!!submittedId}
                  className="text-xs text-emerald-800 underline hover:no-underline disabled:text-emerald-400"
                >
                  Change
                </button>
              </div>
            ) : (
              <div className="relative">
                <input
                  type="text"
                  value={addressQuery}
                  onChange={(e) => setAddressQuery(e.target.value)}
                  placeholder="Start typing the service address…"
                  disabled={!!submittedId}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 disabled:bg-slate-100"
                />
                {addressPending && (
                  <div className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">
                    looking…
                  </div>
                )}
                {suggestions.length > 0 && (
                  <ul className="absolute z-10 mt-1 w-full rounded-lg border border-slate-200 bg-white shadow-lg max-h-72 overflow-y-auto">
                    {suggestions.map((s) => (
                      <li key={s.place_id}>
                        <button
                          type="button"
                          onClick={() => void pickSuggestion(s.place_id)}
                          className="w-full text-left px-3 py-2 text-sm hover:bg-slate-100 focus:bg-slate-100 focus:outline-none"
                        >
                          <div className="font-medium text-slate-900">{s.primary_text}</div>
                          <div className="text-xs text-slate-500">{s.secondary_text}</div>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {addressError && (
                  <div className="mt-2 text-xs text-red-700">{addressError}</div>
                )}
              </div>
            )}
          </div>

          {/* Chat area */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-6 space-y-4 h-[calc(100vh-20rem)]">
            {messages.length === 0 && !error && (
              <div className="text-center text-sm text-slate-400 pt-12">Loading…</div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                {m.role === "user" ? (
                  <div className="max-w-[80%] rounded-2xl px-4 py-2 whitespace-pre-wrap bg-slate-900 text-white">
                    {m.content}
                  </div>
                ) : (
                  <div className="max-w-[80%] rounded-2xl px-4 py-2 bg-slate-100 text-slate-900">
                    <AssistantMarkdown content={m.content} />
                  </div>
                )}
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
                <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3 text-sm text-emerald-900 max-w-md">
                  <div className="font-medium">Submitted.</div>
                  <div className="mt-1 text-xs text-emerald-800">
                    Work order <code className="font-mono">{submittedId.slice(0, 8)}</code> created. Vendor discovery is running in the background — once it finishes, advance the negotiations tick by tick in the command center.
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <Link
                      href={`/work-orders/${submittedId}`}
                      className="inline-block text-xs font-medium text-emerald-900 underline hover:no-underline"
                    >
                      Open command center →
                    </Link>
                    <Link
                      href="/admin"
                      className="inline-block text-xs font-medium text-emerald-900 underline hover:no-underline"
                    >
                      DB Explorer →
                    </Link>
                  </div>
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
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={submittedId ? "Work order submitted." : "Type your message…  (Shift+Enter for new line)"}
              disabled={pending || !!submittedId}
              rows={1}
              className="flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 disabled:bg-slate-50 disabled:text-slate-400 max-h-[200px] overflow-y-auto"
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

        <aside className="flex flex-col gap-4 h-fit">
          {scenarios.length > 0 && !submittedId && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
              <h2 className="text-sm font-semibold text-slate-900 mb-1">
                Choose a scenario
              </h2>
              <p className="text-xs text-slate-500 mb-3">
                Click one to fire it as your first message, or type your own below.
              </p>
              <div className="space-y-1.5 max-h-[24rem] overflow-y-auto pr-1">
                {scenarios.map((s) => (
                  <button
                    key={s.id}
                    type="button"
                    onClick={() => useScenario(s.message)}
                    disabled={pending || !!submittedId}
                    className="block w-full text-left rounded-md border border-slate-200 hover:border-slate-400 hover:bg-slate-50 p-2.5 text-xs transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-slate-500 mb-1 flex-wrap">
                      {s.trade && <span>{s.trade.replace(/_/g, " ")}</span>}
                      {s.trade && s.urgency && <span aria-hidden>·</span>}
                      {s.urgency && <span>{s.urgency}</span>}
                      {(s.trade || s.urgency) && s.city && <span aria-hidden>·</span>}
                      {s.city && <span>{s.city}</span>}
                    </div>
                    <div className="text-slate-700 line-clamp-2 leading-snug">
                      {s.message}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
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
          </div>
        </aside>
      </main>
    </div>
  );
}
