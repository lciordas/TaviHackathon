"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { Nav } from "@/components/Nav";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Types — mirror of backend schemas
// ---------------------------------------------------------------------------

type WorkOrder = {
  id: string;
  created_at: string;
  trade: string;
  description: string;
  address_line: string;
  city: string;
  state: string;
  zip: string;
  urgency: string;
  scheduled_for: string;
  budget_cap_cents: number;
  quality_threshold: number | null;
  requires_licensed: boolean;
  requires_insured: boolean;
  loop_iteration: number;
  ready_to_schedule: boolean;
};

type NegotiationMessage = {
  id: string;
  negotiation_id: string;
  sender: "tavi" | "vendor";
  channel: "email" | "sms" | "phone";
  iteration: number;
  content: { text?: string; subject?: string };
  created_at: string;
};

type Negotiation = {
  id: string;
  work_order_id: string;
  vendor_place_id: string;
  vendor_display_name: string | null;
  vendor_cumulative_score: number | null;
  discovery_run_id: string;
  subjective_rank_score: number | null;
  subjective_rank_breakdown: Record<string, unknown> | null;
  rank: number | null;
  filtered: boolean;
  filter_reasons: string[] | null;
  state: NegotiationState;
  quoted_price_cents: number | null;
  quoted_available_at: string | null;
  escalated: boolean;
  attributes: Record<string, unknown>;
  messages: NegotiationMessage[];
  created_at: string;
  last_updated_at: string;
};

type NegotiationState =
  | "prospecting" | "contacted" | "negotiating" | "quoted"
  | "scheduled" | "completed" | "noshow" | "declined" | "cancelled";

type TickEvent = {
  negotiation_id: string;
  vendor_place_id: string;
  vendor_display_name: string | null;
  state_before: string;
  state_after: string;
  actor: string;
  outcome: string;
  message_id: string | null;
  detail: Record<string, unknown> | null;
};

type TickResponse = {
  work_order_id: string;
  iteration: number;
  events: TickEvent[];
};

// One row in the running activity log, accumulated across ticks.
type LogEntry = {
  id: string;
  tick: number;
  vendorName: string;
  outcome: string;
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ACTIVE_STATES: NegotiationState[] = [
  "prospecting", "contacted", "negotiating", "quoted", "scheduled",
];
const TERMINAL_STATES: NegotiationState[] = [
  "completed", "noshow", "declined", "cancelled",
];

const STATE_LABEL: Record<NegotiationState, string> = {
  prospecting: "Prospecting",
  contacted: "Contacted",
  negotiating: "Negotiating",
  quoted: "Quoted",
  scheduled: "Scheduled",
  completed: "Completed",
  noshow: "No-show",
  declined: "Declined",
  cancelled: "Cancelled",
};

const STATE_COLOR: Record<NegotiationState, string> = {
  prospecting: "bg-slate-100 text-slate-800 border-slate-300",
  contacted: "bg-sky-100 text-sky-900 border-sky-300",
  negotiating: "bg-violet-100 text-violet-900 border-violet-300",
  quoted: "bg-indigo-100 text-indigo-900 border-indigo-300",
  scheduled: "bg-emerald-100 text-emerald-900 border-emerald-300",
  completed: "bg-emerald-100 text-emerald-900 border-emerald-300",
  noshow: "bg-red-100 text-red-900 border-red-300",
  declined: "bg-slate-100 text-slate-600 border-slate-300",
  cancelled: "bg-slate-100 text-slate-600 border-slate-300",
};

const CHANNEL_ICON: Record<string, string> = {
  email: "✉",
  sms: "💬",
  phone: "📞",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function money(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `$${(cents / 100).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function previewText(m: NegotiationMessage): string {
  const t = m.content?.text ?? "";
  return t.length > 120 ? t.slice(0, 120) + "…" : t;
}

function lastMessage(n: Negotiation): NegotiationMessage | null {
  if (!n.messages || n.messages.length === 0) return null;
  return n.messages[n.messages.length - 1];
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function CommandCenter() {
  const params = useParams<{ id: string }>();
  const workOrderId = params.id;

  const [wo, setWo] = useState<WorkOrder | null>(null);
  const [negs, setNegs] = useState<Negotiation[] | null>(null);
  const [ticking, setTicking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastTick, setLastTick] = useState<TickResponse | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Per-negotiation "last message iteration the user has seen". Initialized
  // from the latest iteration in each neg's thread the first time negs load,
  // so nothing shows as new on initial page view. After a tick adds messages,
  // cards with iterations beyond what's here render a "new" dot until the
  // user opens them.
  const [seenIterations, setSeenIterations] = useState<Record<string, number>>({});
  const initializedSeen = useRef(false);

  // Running activity log across ticks. Prepend so newest is on top.
  const [activityLog, setActivityLog] = useState<LogEntry[]>([]);

  // Effect does the fetch async; setState only runs after the fetch resolves,
  // which keeps react-hooks/set-state-in-effect happy.
  useEffect(() => {
    const ac = new AbortController();
    (async () => {
      try {
        const [w, ns] = await Promise.all([
          fetch(`${API_BASE}/negotiations/work_order/${workOrderId}`, { signal: ac.signal })
            .then((r) => { if (!r.ok) throw new Error(`work_order ${r.status}`); return r.json() as Promise<WorkOrder>; }),
          fetch(`${API_BASE}/negotiations/by_work_order/${workOrderId}`, { signal: ac.signal })
            .then((r) => { if (!r.ok) throw new Error(`negotiations ${r.status}`); return r.json() as Promise<Negotiation[]>; }),
        ]);
        if (!ac.signal.aborted) { setWo(w); setNegs(ns); }
      } catch (e) {
        if (!ac.signal.aborted) setError(`Load failed: ${String(e)}`);
      }
    })();
    return () => ac.abort();
  }, [workOrderId]);

  // Initialize "seen" cursors to each neg's current latest iteration the
  // first time negs arrive, so the initial page view never flags anything
  // as new. After this, new messages from future ticks will exceed the
  // stored cursor and render the "new" dot on cards until clicked.
  useEffect(() => {
    if (initializedSeen.current || !negs) return;
    const init: Record<string, number> = {};
    for (const n of negs) {
      const last = n.messages.length ? n.messages[n.messages.length - 1].iteration : 0;
      init[n.id] = last;
    }
    setSeenIterations(init);
    initializedSeen.current = true;
  }, [negs]);

  const refresh = useCallback(async () => {
    try {
      const [w, ns] = await Promise.all([
        fetch(`${API_BASE}/negotiations/work_order/${workOrderId}`).then((r) => {
          if (!r.ok) throw new Error(`work_order ${r.status}`); return r.json() as Promise<WorkOrder>;
        }),
        fetch(`${API_BASE}/negotiations/by_work_order/${workOrderId}`).then((r) => {
          if (!r.ok) throw new Error(`negotiations ${r.status}`); return r.json() as Promise<Negotiation[]>;
        }),
      ]);
      setWo(w);
      setNegs(ns);
    } catch (e) {
      setError(`Load failed: ${String(e)}`);
    }
  }, [workOrderId]);

  const onTick = useCallback(async () => {
    setTicking(true);
    setError(null);

    // Poll the hydrated view every 400ms while the tick is in flight. The
    // backend commits after each per-negotiation dispatch, so each message
    // + state change appears in the board as it happens rather than all at
    // once at the end of the tick.
    const pollId = window.setInterval(() => {
      void refresh();
    }, 400);

    try {
      const r = await fetch(`${API_BASE}/negotiations/tick`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ work_order_id: workOrderId }),
      });
      if (!r.ok) throw new Error(`tick ${r.status}`);
      const payload = (await r.json()) as TickResponse;
      setLastTick(payload);
      // Stream events into the activity log one by one so the UI feels
      // busy during the tick rather than dumping everything at once.
      const toAdd: LogEntry[] = payload.events.map((ev, i) => ({
        id: `${payload.iteration}-${i}-${ev.negotiation_id}`,
        tick: payload.iteration,
        vendorName: ev.vendor_display_name ?? "—",
        outcome: ev.outcome,
      }));
      toAdd.forEach((entry, idx) => {
        window.setTimeout(() => {
          setActivityLog((prev) => [entry, ...prev].slice(0, 200));
        }, idx * 250);
      });
    } catch (e) {
      setError(`Tick failed: ${String(e)}`);
    } finally {
      window.clearInterval(pollId);
      await refresh();  // final sync so the board matches the final commit
      setTicking(false);
    }
  }, [workOrderId, refresh]);

  const active = useMemo(
    () => (negs ?? []).filter((n) => !n.filtered && (ACTIVE_STATES as string[]).includes(n.state)),
    [negs],
  );
  const terminal = useMemo(
    () => (negs ?? []).filter((n) => !n.filtered && (TERMINAL_STATES as string[]).includes(n.state)),
    [negs],
  );
  const filteredOut = useMemo(
    () => (negs ?? []).filter((n) => n.filtered),
    [negs],
  );

  const byState = useMemo(() => {
    const map: Record<string, Negotiation[]> = {};
    for (const s of ACTIVE_STATES) map[s] = [];
    for (const n of active) (map[n.state] ??= []).push(n);
    return map;
  }, [active]);

  const selected = useMemo(
    () => (negs ?? []).find((n) => n.id === selectedId) ?? null,
    [negs, selectedId],
  );

  // Per-negotiation unread counts (messages past the "seen" cursor). Used to
  // drive both the "new" ring and the numeric badge on cards.
  const unreadByNegId = useMemo(() => {
    const out: Record<string, number> = {};
    if (!negs) return out;
    for (const n of negs) {
      const seen = seenIterations[n.id] ?? -1;
      out[n.id] = n.messages.filter((m) => m.iteration > seen).length;
    }
    return out;
  }, [negs, seenIterations]);

  // Discovery spawns in the background after intake confirm. While nothing
  // has been written yet the tick button is useless, so gate on whether the
  // negs list has populated and poll slowly in the meantime.
  const discoveryPending = !negs || negs.length === 0;

  useEffect(() => {
    if (!discoveryPending) return;
    const handle = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(handle);
  }, [discoveryPending, refresh]);

  // Opening a negotiation marks its thread as read.
  const handlePick = useCallback((negId: string) => {
    setSelectedId(negId);
    const n = negs?.find((x) => x.id === negId);
    if (!n) return;
    const last = n.messages.length ? n.messages[n.messages.length - 1].iteration : 0;
    setSeenIterations((prev) => ({
      ...prev,
      [negId]: Math.max(prev[negId] ?? 0, last),
    }));
  }, [negs]);

  return (
    <div className="flex-1 flex flex-col bg-slate-50 text-slate-900 min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-[1400px] px-6 py-4 flex items-center justify-between gap-4">
          <h1 className="text-xl font-semibold tracking-tight">Tavi — Command Center</h1>
          <Nav currentWorkOrderId={workOrderId} />
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-[1400px] px-6 py-6 flex flex-col gap-4">
        {wo && (
          <WorkOrderHeader
            wo={wo}
            onTick={onTick}
            ticking={ticking}
            auctionClosed={(negs ?? []).some((n) => n.state === "scheduled")}
            discoveryPending={discoveryPending}
          />
        )}

        <ActivityLog log={activityLog} />

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {lastTick && <TickBanner tick={lastTick} />}

        {!negs && !error && (
          <div className="text-sm text-slate-500 italic">Loading…</div>
        )}

        {negs && negs.length === 0 && (
          <div className="bg-white border border-slate-200 rounded-xl p-6 flex items-center gap-4">
            <span className="inline-block h-2 w-2 rounded-full bg-sky-500 animate-pulse" aria-hidden="true" />
            <div>
              <div className="text-sm font-medium text-slate-900">
                Vendor discovery in progress
              </div>
              <div className="text-xs text-slate-500 mt-0.5">
                Searching nearby vendors, scraping BBB, and scoring — the kanban will populate here in a moment. The Tick button unlocks once candidates land.
              </div>
            </div>
          </div>
        )}

        {negs && negs.length > 0 && (
          <>
            <Kanban byState={byState} onPick={handlePick} selectedId={selectedId} unreadByNegId={unreadByNegId} />
            {terminal.length > 0 && (
              <TerminalSection negs={terminal} onPick={handlePick} selectedId={selectedId} unreadByNegId={unreadByNegId} />
            )}
            {filteredOut.length > 0 && (
              <FilteredSection negs={filteredOut} />
            )}
          </>
        )}
      </main>

      {selected && <DetailPanel neg={selected} onClose={() => setSelectedId(null)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header with tick
// ---------------------------------------------------------------------------

function WorkOrderHeader({
  wo,
  onTick,
  ticking,
  auctionClosed,
  discoveryPending,
}: {
  wo: WorkOrder;
  onTick: () => void;
  ticking: boolean;
  auctionClosed: boolean;
  discoveryPending: boolean;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm">
      <div className="px-5 py-4 flex items-start justify-between gap-6">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-xs text-slate-500 uppercase tracking-wide mb-1">
            <span>Work order {wo.id.slice(0, 8)}</span>
            <span>·</span>
            <span className="px-1.5 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-700 font-medium normal-case">
              {wo.trade}
            </span>
            <span className="px-1.5 py-0.5 rounded border border-slate-200 bg-slate-50 text-slate-700 font-medium normal-case">
              {wo.urgency}
            </span>
          </div>
          <p className="text-sm text-slate-900 mb-1.5">{wo.description}</p>
          <div className="text-xs text-slate-600 flex flex-wrap gap-x-4 gap-y-1">
            <span>{wo.address_line}, {wo.city} {wo.state} {wo.zip}</span>
            <span>requested {fmtDate(wo.scheduled_for)}</span>
            <span>budget {money(wo.budget_cap_cents)}</span>
            <span>licensed: {wo.requires_licensed ? "required" : "no"}</span>
            <span>insured: {wo.requires_insured ? "required" : "no"}</span>
            {wo.ready_to_schedule && (
              <span className="px-1.5 py-0.5 rounded border border-emerald-300 bg-emerald-50 text-emerald-900 font-medium">
                ready to schedule
              </span>
            )}
          </div>
        </div>

        <div className="flex flex-col items-end gap-2 shrink-0">
          <div className="text-right">
            <div className="text-xs uppercase tracking-wide text-slate-500">Iteration</div>
            <div className="text-3xl font-semibold tabular-nums tracking-tight">{wo.loop_iteration}</div>
          </div>
          <button
            onClick={onTick}
            disabled={ticking || auctionClosed || discoveryPending}
            title={
              auctionClosed
                ? "A vendor is booked — auction closed"
                : discoveryPending
                ? "Vendor discovery is still running — hang tight, candidates will populate here"
                : undefined
            }
            className="rounded-lg bg-slate-900 text-white px-4 py-2 text-sm font-medium hover:bg-slate-800 disabled:bg-slate-300 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {auctionClosed
              ? (<><span>Booked</span><span aria-hidden>✓</span></>)
              : discoveryPending
              ? (<><span>Discovering…</span><span aria-hidden>⏳</span></>)
              : ticking
              ? "Ticking…"
              : (<><span>Tick</span><span aria-hidden>⏩</span></>)}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity log — running feed of events across all ticks
// ---------------------------------------------------------------------------

function humanizeOutcome(outcome: string): string {
  return outcome.replace(/_/g, " ");
}

const OUTCOME_COLOR: Record<string, string> = {
  message_sent: "bg-sky-50 text-sky-800 border-sky-200",
  verification_requested: "bg-sky-50 text-sky-800 border-sky-200",
  verification_progress: "bg-sky-50 text-sky-800 border-sky-200",
  confirmation_requested: "bg-indigo-50 text-indigo-800 border-indigo-200",
  confirmation_handled: "bg-emerald-50 text-emerald-800 border-emerald-200",
  refused: "bg-amber-50 text-amber-800 border-amber-200",
  silence_timeout: "bg-red-50 text-red-800 border-red-200",
  confirmation_timeout: "bg-red-50 text-red-800 border-red-200",
  verification_timeout: "bg-red-50 text-red-800 border-red-200",
  skipped: "bg-slate-50 text-slate-600 border-slate-200",
  waiting: "bg-slate-50 text-slate-600 border-slate-200",
  queued: "bg-slate-50 text-slate-600 border-slate-200",
};

function ActivityLog({ log }: { log: LogEntry[] }) {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Keep the top-of-list visible when new entries prepend (newest at top).
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [log]);

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm">
      <div className="px-4 py-2 border-b border-slate-100 flex items-center justify-between">
        <div className="text-xs uppercase tracking-wide text-slate-500 font-medium">
          Activity log
        </div>
        <div className="text-xs text-slate-400">
          {log.length} event{log.length === 1 ? "" : "s"}
        </div>
      </div>
      <div
        ref={scrollRef}
        className="max-h-32 overflow-y-auto px-4 py-2 text-xs space-y-1"
      >
        {log.length === 0 ? (
          <div className="text-slate-400 italic py-1">
            No events yet. Tick the work order to advance the negotiation.
          </div>
        ) : (
          log.map((e) => {
            const color = OUTCOME_COLOR[e.outcome] ?? "bg-slate-50 text-slate-700 border-slate-200";
            return (
              <div key={e.id} className="flex items-center gap-2">
                <span className="font-mono text-slate-400 tabular-nums w-10 shrink-0">
                  t{e.tick}
                </span>
                <span className="font-medium text-slate-800 truncate max-w-[16rem]">
                  {e.vendorName}
                </span>
                <span className={`px-1.5 py-0.5 rounded border text-[10px] ${color}`}>
                  {humanizeOutcome(e.outcome)}
                </span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tick banner
// ---------------------------------------------------------------------------

function TickBanner({ tick }: { tick: TickResponse }) {
  const MESSAGE_OUTCOMES = new Set([
    "message_sent",
    "verification_requested", "verification_progress",
    "confirmation_requested", "confirmation_handled",
    "refused",
  ]);
  const SILENT_OUTCOMES = new Set(["skipped", "waiting", "queued"]);
  const TIMEOUT_OUTCOMES = new Set(["silence_timeout", "confirmation_timeout", "verification_timeout"]);

  const msgCount = tick.events.filter((e) => MESSAGE_OUTCOMES.has(e.outcome)).length;
  const silentCount = tick.events.filter((e) => SILENT_OUTCOMES.has(e.outcome)).length;
  const timeouts = tick.events.filter((e) => TIMEOUT_OUTCOMES.has(e.outcome));
  const refused = tick.events.filter((e) => e.outcome === "refused");
  const verifyReq = tick.events.find((e) => e.outcome === "verification_requested");
  const confirmReq = tick.events.find((e) => e.outcome === "confirmation_requested");
  const confirmAck = tick.events.find((e) => e.outcome === "confirmation_handled");

  return (
    <div className="bg-slate-900 text-slate-100 rounded-lg px-4 py-3 text-sm">
      <div className="flex items-center gap-3 mb-1">
        <span className="text-xs uppercase tracking-wide text-slate-400">
          Iteration {tick.iteration}
        </span>
        {verifyReq && (
          <span className="text-xs uppercase tracking-wide text-sky-300">
            Verifying credentials → {verifyReq.vendor_display_name ?? "—"}
          </span>
        )}
        {confirmReq && (
          <span className="text-xs uppercase tracking-wide text-indigo-300">
            Confirmation request → {confirmReq.vendor_display_name ?? "—"}
          </span>
        )}
        {confirmAck && (
          <span className="text-xs uppercase tracking-wide text-emerald-300">
            Booking confirmed — {confirmAck.vendor_display_name ?? "—"}
          </span>
        )}
      </div>
      <div className="text-xs text-slate-300 flex flex-wrap gap-x-5 gap-y-1">
        <span>{msgCount} message{msgCount === 1 ? "" : "s"} sent</span>
        <span>{silentCount} silent</span>
        {timeouts.length > 0 && (
          <span className="text-red-300">{timeouts.length} timed out</span>
        )}
        {refused.length > 0 && (
          <span className="text-amber-300">{refused.length} refused</span>
        )}
      </div>
      {timeouts.length > 0 && (
        <div className="mt-2 text-xs text-red-200 space-y-0.5">
          {timeouts.map((t) => (
            <div key={t.negotiation_id}>
              {t.outcome === "silence_timeout" ? "silence timeout" : "confirmation timeout"}
              {" — "}
              {t.vendor_display_name ?? "—"}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Kanban
// ---------------------------------------------------------------------------

function Kanban({
  byState,
  onPick,
  selectedId,
  unreadByNegId,
}: {
  byState: Record<string, Negotiation[]>;
  onPick: (id: string) => void;
  selectedId: string | null;
  unreadByNegId: Record<string, number>;
}) {
  return (
    <div className="grid grid-cols-5 gap-3 items-start">
      {ACTIVE_STATES.map((s) => (
        <Column
          key={s}
          state={s}
          negs={byState[s] ?? []}
          onPick={onPick}
          selectedId={selectedId}
          unreadByNegId={unreadByNegId}
        />
      ))}
    </div>
  );
}

function Column({
  state,
  negs,
  onPick,
  selectedId,
  unreadByNegId,
}: {
  state: NegotiationState;
  negs: Negotiation[];
  onPick: (id: string) => void;
  selectedId: string | null;
  unreadByNegId: Record<string, number>;
}) {
  return (
    <div className="flex flex-col gap-2 min-h-[200px]">
      <div className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium ${STATE_COLOR[state]}`}>
        {STATE_LABEL[state]}{" "}
        <span className="opacity-60">({negs.length})</span>
      </div>
      <div className="flex flex-col gap-2">
        {negs.map((n) => (
          <Card
            key={n.id}
            n={n}
            onPick={onPick}
            selected={n.id === selectedId}
            unread={unreadByNegId[n.id] ?? 0}
          />
        ))}
        {negs.length === 0 && (
          <div className="text-xs text-slate-400 italic px-1 py-2">—</div>
        )}
      </div>
    </div>
  );
}

function Card({
  n,
  onPick,
  selected,
  unread,
}: {
  n: Negotiation;
  onPick: (id: string) => void;
  selected: boolean;
  unread: number;
}) {
  const last = lastMessage(n);
  const recent = last ? last.iteration : null;
  const senderBadge =
    last?.sender === "tavi" ? "Tavi"
    : last?.sender === "vendor" ? "Vendor"
    : null;
  const isNew = unread > 0;
  const badgeLabel = unread > 9 ? "9+" : String(unread);
  return (
    <button
      onClick={() => onPick(n.id)}
      className={`text-left w-full bg-white rounded-lg border p-3 shadow-sm transition-colors relative ${
        selected
          ? "border-slate-900 ring-2 ring-slate-900/10"
          : isNew
          ? "border-emerald-400 ring-2 ring-emerald-400/30 hover:border-emerald-500"
          : "border-slate-200 hover:border-slate-400"
      }`}
    >
      {isNew && !selected && (
        <span
          className="absolute -top-1.5 -right-1.5 min-w-[1.25rem] h-5 px-1.5 flex items-center justify-center rounded-full bg-emerald-500 text-white text-[11px] font-semibold shadow-sm tabular-nums"
          aria-label={`${unread} new message${unread === 1 ? "" : "s"}`}
          title={`${unread} new message${unread === 1 ? "" : "s"} since you last opened this vendor`}
        >
          <span className="absolute inset-0 rounded-full bg-emerald-400 opacity-60 animate-ping" aria-hidden="true" />
          <span className="relative">{badgeLabel}</span>
        </span>
      )}
      <div className="flex items-center justify-between gap-2 mb-1">
        <div className="text-sm font-medium truncate">
          {n.vendor_display_name ?? n.vendor_place_id.slice(0, 8)}
        </div>
        {n.rank !== null && (
          <span className="text-xs font-mono text-slate-500">#{n.rank}</span>
        )}
      </div>

      {last ? (
        <>
          <div className="text-xs text-slate-500 mb-1 flex items-center gap-1.5">
            <span>{CHANNEL_ICON[last.channel] ?? last.channel}</span>
            <span className="font-medium">{senderBadge}</span>
            <span>·</span>
            <span>tick {recent}</span>
          </div>
          <div className="text-xs text-slate-700 line-clamp-3">
            {previewText(last)}
          </div>
        </>
      ) : (
        <div className="text-xs text-slate-400 italic">no messages yet</div>
      )}

      {n.quoted_price_cents !== null && (
        <div className="mt-2 text-xs text-slate-600">
          quote: <span className="font-semibold">{money(n.quoted_price_cents)}</span>
          {n.quoted_available_at && (
            <> · {fmtDate(n.quoted_available_at)}</>
          )}
        </div>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Terminal section + filtered section
// ---------------------------------------------------------------------------

function TerminalSection({
  negs,
  onPick,
  selectedId,
  unreadByNegId,
}: {
  negs: Negotiation[];
  onPick: (id: string) => void;
  selectedId: string | null;
  unreadByNegId: Record<string, number>;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)} className="bg-white border border-slate-200 rounded-xl">
      <summary className="cursor-pointer list-none px-4 py-2.5 flex items-center justify-between">
        <div className="text-sm font-medium text-slate-700">
          Concluded ({negs.length})
        </div>
        <div className="text-xs text-slate-500">{open ? "▼" : "▶"}</div>
      </summary>
      <div className="px-4 pb-3 grid grid-cols-5 gap-2">
        {negs.map((n) => (
          <Card
            key={n.id}
            n={n}
            onPick={onPick}
            selected={n.id === selectedId}
            unread={unreadByNegId[n.id] ?? 0}
          />
        ))}
      </div>
    </details>
  );
}

function FilteredSection({ negs }: { negs: Negotiation[] }) {
  return (
    <details className="bg-white border border-slate-200 rounded-xl">
      <summary className="cursor-pointer list-none px-4 py-2.5 text-sm font-medium text-slate-500">
        Excluded at discovery ({negs.length})
      </summary>
      <div className="px-4 pb-3 text-xs text-slate-600 space-y-1">
        {negs.map((n) => (
          <div key={n.id} className="flex gap-2">
            <span className="font-medium">{n.vendor_display_name ?? n.vendor_place_id.slice(0, 8)}</span>
            <span className="text-slate-400">—</span>
            <span className="italic">{(n.filter_reasons ?? []).join(", ") || "unknown"}</span>
          </div>
        ))}
      </div>
    </details>
  );
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

function DetailPanel({ neg, onClose }: { neg: Negotiation; onClose: () => void }) {
  return (
    <aside className="fixed inset-y-0 right-0 w-full max-w-xl bg-white border-l border-slate-200 shadow-xl flex flex-col">
      <div className="border-b border-slate-200 px-5 py-3 flex items-start justify-between gap-3">
        <div>
          <div className="text-xs text-slate-500 uppercase tracking-wide">
            {STATE_LABEL[neg.state]} {neg.rank !== null && <>· rank #{neg.rank}</>}
          </div>
          <h2 className="text-lg font-semibold">{neg.vendor_display_name ?? neg.vendor_place_id.slice(0, 8)}</h2>
          {neg.vendor_cumulative_score !== null && (
            <div className="text-xs text-slate-600 mt-0.5">
              quality score: <span className="font-medium">{neg.vendor_cumulative_score.toFixed(2)}</span>
            </div>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-slate-900 text-xl leading-none px-2 py-1"
          aria-label="close"
        >
          ×
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-5 space-y-4">
        {neg.quoted_price_cents !== null && (
          <section className="rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-3">
            <div className="text-xs uppercase tracking-wide text-indigo-700 mb-1">Firm quote</div>
            <div className="text-sm">
              <span className="font-semibold">{money(neg.quoted_price_cents)}</span>
              {neg.quoted_available_at && <> · available {fmtDate(neg.quoted_available_at)}</>}
            </div>
          </section>
        )}

        {Object.keys(neg.attributes ?? {}).length > 0 && (
          <section>
            <h3 className="text-xs uppercase tracking-wide text-slate-500 mb-1.5">Extracted facts</h3>
            <div className="bg-slate-50 border border-slate-200 rounded-lg p-3 text-xs font-mono space-y-0.5">
              {Object.entries(neg.attributes).map(([k, v]) => (
                <div key={k}>
                  <span className="text-slate-500">{k}:</span>{" "}
                  <span className="text-slate-900">{JSON.stringify(v)}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        <section>
          <h3 className="text-xs uppercase tracking-wide text-slate-500 mb-1.5">
            Thread ({neg.messages.length})
          </h3>
          <Thread messages={neg.messages} />
        </section>
      </div>
    </aside>
  );
}

function Thread({ messages }: { messages: NegotiationMessage[] }) {
  if (messages.length === 0) {
    return <div className="text-sm text-slate-400 italic">no messages yet</div>;
  }
  return (
    <div className="space-y-3">
      {messages.map((m, i) => {
        const prev = i > 0 ? messages[i - 1] : null;
        const gap = prev ? m.iteration - prev.iteration : 0;
        return (
          <div key={m.id}>
            {gap > 1 && (
              <div className="text-[10px] uppercase tracking-wide text-slate-400 text-center py-1">
                — {gap - 1} tick{gap - 1 === 1 ? "" : "s"} of silence —
              </div>
            )}
            <Bubble m={m} />
          </div>
        );
      })}
    </div>
  );
}

function Bubble({ m }: { m: NegotiationMessage }) {
  const me = m.sender === "tavi";
  return (
    <div className={`flex ${me ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-md rounded-lg border px-3 py-2 text-sm ${
        me
          ? "bg-slate-900 text-slate-50 border-slate-900"
          : "bg-white text-slate-900 border-slate-200"
      }`}>
        <div className={`text-[10px] uppercase tracking-wide mb-1 flex items-center gap-1.5 ${
          me ? "text-slate-400" : "text-slate-500"
        }`}>
          <span>{CHANNEL_ICON[m.channel] ?? m.channel}</span>
          <span>{me ? "Tavi" : "Vendor"}</span>
          <span>·</span>
          <span>tick {m.iteration}</span>
        </div>
        {m.content?.subject && (
          <div className={`font-medium mb-1 ${me ? "text-slate-100" : "text-slate-900"}`}>
            {m.content.subject}
          </div>
        )}
        <div className="whitespace-pre-wrap">{m.content?.text ?? ""}</div>
      </div>
    </div>
  );
}
