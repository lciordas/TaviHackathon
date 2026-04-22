"use client";

import { useEffect, useMemo, useState } from "react";

import { Nav } from "@/components/Nav";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

type TabKey = "work_orders" | "vendors" | "discovery_runs" | "negotiations";

type Counts = {
  work_orders: number;
  vendors: number;
  discovery_runs: number;
  negotiations: number;
  negotiation_messages: number;
};

type WorkOrder = {
  id: string;
  created_at: string;
  created_by: string;
  trade: string;
  description: string;
  address_line: string;
  city: string;
  state: string;
  zip: string;
  lat: number;
  lng: number;
  access_notes: string | null;
  urgency: string;
  scheduled_for: string;
  budget_cap_cents: number;
  quality_threshold: number | null;
  requires_licensed: boolean;
  requires_insured: boolean;
  loop_iteration: number;
  ready_to_schedule: boolean;
};

type Vendor = {
  place_id: string;
  display_name: string;
  formatted_address: string | null;
  lat: number;
  lng: number;
  types: string[];
  business_status: string | null;
  google_rating: number | null;
  google_user_rating_count: number | null;
  regular_opening_hours: Record<string, unknown> | null;
  utc_offset_minutes: number | null;
  international_phone_number: string | null;
  website_uri: string | null;
  price_level: number | null;
  emergency_service_24_7: boolean;
  bbb_profile_url: string | null;
  bbb_grade: string | null;
  bbb_accredited: boolean | null;
  bbb_years_accredited: number | null;
  bbb_complaints_total: number | null;
  bbb_complaints_resolved: number | null;
  years_in_business: number | null;
  cumulative_score: number | null;
  cumulative_score_breakdown: Record<string, unknown> | null;
  google_fetched_at: string;
  bbb_fetched_at: string | null;
};

type DiscoveryRun = {
  id: string;
  work_order_id: string;
  created_at: string;
  strategy: string;
  radius_miles: number;
  candidate_count: number;
  cache_hit_count: number;
  api_detail_calls: number;
  bbb_scrape_count: number;
  weight_profile: string;
  duration_ms: number | null;
};

type NegotiationMessage = {
  id: string;
  negotiation_id: string;
  sender: "tavi" | "vendor";
  channel: "email" | "sms" | "phone";
  iteration: number;
  content: Record<string, unknown>;
  created_at: string;
};

type Negotiation = {
  id: string;
  work_order_id: string;
  vendor_place_id: string;
  vendor_display_name: string | null;
  vendor_cumulative_score: number | null;
  discovery_run_id: string;
  quoted_price_cents: number | null;
  quoted_available_at: string | null;
  escalated: boolean;
  attributes: Record<string, unknown>;
  subjective_rank_score: number | null;
  subjective_rank_breakdown: Record<string, unknown> | null;
  rank: number | null;
  filtered: boolean;
  filter_reasons: string[] | null;
  state: string;
  messages: NegotiationMessage[];
  created_at: string;
  last_updated_at: string;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function short(id: string, n: number = 8): string {
  return id.length > n ? id.slice(0, n) : id;
}

function money(cents: number): string {
  return `$${(cents / 100).toLocaleString(undefined, { minimumFractionDigits: 0 })}`;
}

function dt(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  return isNaN(d.getTime()) ? s : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function bool(b: boolean | null | undefined): string {
  if (b === null || b === undefined) return "—";
  return b ? "Yes" : "No";
}

function bbbColor(grade: string | null): string {
  if (!grade) return "bg-slate-100 text-slate-600 border-slate-200";
  if (grade.startsWith("A")) return "bg-emerald-100 text-emerald-900 border-emerald-200";
  if (grade.startsWith("B")) return "bg-yellow-100 text-yellow-900 border-yellow-200";
  if (grade.startsWith("C")) return "bg-orange-100 text-orange-900 border-orange-200";
  if (grade === "D" || grade === "F") return "bg-red-100 text-red-900 border-red-200";
  return "bg-slate-100 text-slate-600 border-slate-200"; // NR
}

function urgencyColor(u: string): string {
  return {
    emergency: "bg-red-100 text-red-900 border-red-200",
    urgent: "bg-orange-100 text-orange-900 border-orange-200",
    scheduled: "bg-blue-100 text-blue-900 border-blue-200",
    flexible: "bg-slate-100 text-slate-700 border-slate-200",
  }[u] ?? "bg-slate-100 text-slate-700 border-slate-200";
}

function statusColor(s: string): string {
  return {
    prospecting: "bg-slate-100 text-slate-700 border-slate-200",
    contacted: "bg-sky-100 text-sky-900 border-sky-200",
    negotiating: "bg-violet-100 text-violet-900 border-violet-200",
    quoted: "bg-indigo-100 text-indigo-900 border-indigo-200",
    scheduled: "bg-emerald-100 text-emerald-900 border-emerald-200",
    completed: "bg-emerald-100 text-emerald-900 border-emerald-200",
    noshow: "bg-red-100 text-red-900 border-red-200",
    declined: "bg-slate-200 text-slate-700 border-slate-300",
    cancelled: "bg-slate-200 text-slate-700 border-slate-300",
  }[s] ?? "bg-slate-100 text-slate-700 border-slate-200";
}

function tradeColor(t: string): string {
  return {
    plumbing: "bg-sky-100 text-sky-900 border-sky-200",
    hvac: "bg-amber-100 text-amber-900 border-amber-200",
    electrical: "bg-yellow-100 text-yellow-900 border-yellow-200",
    lawncare: "bg-green-100 text-green-900 border-green-200",
    handyman: "bg-purple-100 text-purple-900 border-purple-200",
    appliance_repair: "bg-pink-100 text-pink-900 border-pink-200",
  }[t] ?? "bg-slate-100 text-slate-700 border-slate-200";
}

function Badge({ children, className }: { children: React.ReactNode; className: string }) {
  return (
    <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded border ${className}`}>
      {children}
    </span>
  );
}

function Json({ data }: { data: unknown }) {
  if (data === null || data === undefined) return <span className="text-slate-400 text-xs italic">null</span>;
  return (
    <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto max-h-64">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Admin() {
  const [tab, setTab] = useState<TabKey>("work_orders");
  const [counts, setCounts] = useState<Counts | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [workOrders, setWorkOrders] = useState<WorkOrder[] | null>(null);
  const [vendors, setVendors] = useState<Vendor[] | null>(null);
  const [runs, setRuns] = useState<DiscoveryRun[] | null>(null);
  const [negotiations, setNegotiations] = useState<Negotiation[] | null>(null);

  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  async function fetchJson<T>(path: string): Promise<T> {
    const r = await fetch(`${API_BASE}${path}`);
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return (await r.json()) as T;
  }

  async function reloadCounts() {
    try {
      const c = await fetchJson<{ counts: Counts }>("/admin/overview");
      setCounts(c.counts);
    } catch {
      /* ignored — header just won't show counts */
    }
  }

  async function loadTab(t: TabKey) {
    setLoading(true);
    setError(null);
    try {
      if (t === "work_orders") setWorkOrders(await fetchJson<WorkOrder[]>("/admin/work_orders"));
      if (t === "vendors") setVendors(await fetchJson<Vendor[]>("/admin/vendors"));
      if (t === "discovery_runs") setRuns(await fetchJson<DiscoveryRun[]>("/admin/discovery_runs"));
      if (t === "negotiations") setNegotiations(await fetchJson<Negotiation[]>("/admin/negotiations"));
    } catch (e: unknown) {
      setError(`Load failed: ${String(e)}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reloadCounts();
    void loadTab("work_orders");
  }, []);

  useEffect(() => {
    setExpanded({});
    void loadTab(tab);
  }, [tab]);

  function toggle(id: string) {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  function refresh() {
    void reloadCounts();
    void loadTab(tab);
  }

  const TABS: { key: TabKey; label: string; count: number | undefined }[] = useMemo(
    () => [
      { key: "work_orders", label: "Work Orders", count: counts?.work_orders },
      { key: "vendors", label: "Vendors", count: counts?.vendors },
      { key: "discovery_runs", label: "Discovery Runs", count: counts?.discovery_runs },
      { key: "negotiations", label: "Negotiations", count: counts?.negotiations },
    ],
    [counts],
  );

  return (
    <div className="flex-1 flex flex-col bg-slate-50 text-slate-900 min-h-screen">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="mx-auto max-w-7xl px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Tavi — DB Explorer</h1>
            <p className="text-xs text-slate-500">Read-only view of <code className="font-mono">backend/tavi.db</code></p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={refresh}
              disabled={loading}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 disabled:opacity-50"
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
            <Nav />
          </div>
        </div>

        <nav className="mx-auto max-w-7xl px-6 border-t border-slate-100">
          <ul className="flex gap-0">
            {TABS.map((t) => (
              <li key={t.key}>
                <button
                  onClick={() => setTab(t.key)}
                  className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                    tab === t.key
                      ? "border-slate-900 text-slate-900"
                      : "border-transparent text-slate-500 hover:text-slate-900 hover:border-slate-300"
                  }`}
                >
                  {t.label}
                  {t.count !== undefined && (
                    <span className={`ml-2 inline-flex items-center justify-center px-1.5 py-0.5 text-xs font-medium rounded ${
                      tab === t.key ? "bg-slate-900 text-white" : "bg-slate-200 text-slate-700"
                    }`}>
                      {t.count}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main className="mx-auto max-w-7xl w-full px-6 py-6">
        {error && (
          <div className="mb-4 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {tab === "work_orders" && (
          <WorkOrdersTable rows={workOrders} expanded={expanded} toggle={toggle} />
        )}
        {tab === "vendors" && (
          <VendorsTable rows={vendors} expanded={expanded} toggle={toggle} />
        )}
        {tab === "discovery_runs" && (
          <RunsTable rows={runs} expanded={expanded} toggle={toggle} />
        )}
        {tab === "negotiations" && (
          <NegotiationsTable rows={negotiations} expanded={expanded} toggle={toggle} />
        )}

        {loading && <div className="text-center text-sm text-slate-400 py-8">Loading…</div>}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table components
// ---------------------------------------------------------------------------

type TableProps<T> = {
  rows: T[] | null;
  expanded: Record<string, boolean>;
  toggle: (id: string) => void;
};

function Empty({ msg }: { msg: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-white p-12 text-center text-sm text-slate-400">
      {msg}
    </div>
  );
}

function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">{children}</table>
      </div>
    </div>
  );
}

function Th({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <th className={`px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 border-b border-slate-200 ${className}`}>
      {children}
    </th>
  );
}

function Td({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-3 border-b border-slate-100 ${className}`}>{children}</td>;
}

// ---------------------------------------------------------------------------

function WorkOrdersTable({ rows, expanded, toggle }: TableProps<WorkOrder>) {
  if (rows === null) return null;
  if (rows.length === 0) return <Empty msg="No work orders yet. File one via the intake chat." />;
  return (
    <TableShell>
      <thead className="bg-slate-50">
        <tr>
          <Th>ID</Th>
          <Th>Trade</Th>
          <Th>Urgency</Th>
          <Th>City</Th>
          <Th>Scheduled</Th>
          <Th>Budget</Th>
          <Th>Licensed</Th>
          <Th>Insured</Th>
          <Th>Created</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((w) => (
          <ExpandableRow key={w.id} id={w.id} expanded={!!expanded[w.id]} toggle={toggle} cols={9}
            summary={
              <>
                <Td><code className="font-mono text-xs text-slate-500">{short(w.id)}</code></Td>
                <Td><Badge className={tradeColor(w.trade)}>{w.trade}</Badge></Td>
                <Td><Badge className={urgencyColor(w.urgency)}>{w.urgency}</Badge></Td>
                <Td>{w.city}, {w.state}</Td>
                <Td className="text-slate-600">{dt(w.scheduled_for)}</Td>
                <Td className="font-medium">{money(w.budget_cap_cents)}</Td>
                <Td>{bool(w.requires_licensed)}</Td>
                <Td>{bool(w.requires_insured)}</Td>
                <Td className="text-slate-500 text-xs">{dt(w.created_at)}</Td>
              </>
            }
            detail={
              <div className="grid grid-cols-2 gap-4 text-sm">
                <Field label="Full ID"><code className="font-mono text-xs">{w.id}</code></Field>
                <Field label="Created by">{w.created_by}</Field>
                <Field label="Description" className="col-span-2"><div className="whitespace-pre-wrap">{w.description}</div></Field>
                <Field label="Full address" className="col-span-2">
                  {w.address_line}, {w.city}, {w.state} {w.zip}
                  <div className="text-xs text-slate-500 mt-1 font-mono">{w.lat.toFixed(5)}, {w.lng.toFixed(5)}</div>
                </Field>
                <Field label="Access notes" className="col-span-2">{w.access_notes ?? "—"}</Field>
                <Field label="Quality threshold">{w.quality_threshold ?? "—"}</Field>
              </div>
            }
          />
        ))}
      </tbody>
    </TableShell>
  );
}

function VendorsTable({ rows, expanded, toggle }: TableProps<Vendor>) {
  if (rows === null) return null;
  if (rows.length === 0) return <Empty msg="No vendors cached yet. Run /discovery/run for a work order." />;
  return (
    <TableShell>
      <thead className="bg-slate-50">
        <tr>
          <Th>Vendor</Th>
          <Th>Location</Th>
          <Th>Google</Th>
          <Th>BBB</Th>
          <Th>Years</Th>
          <Th>Score</Th>
          <Th>24/7</Th>
          <Th>Status</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((v) => (
          <ExpandableRow key={v.place_id} id={v.place_id} expanded={!!expanded[v.place_id]} toggle={toggle} cols={8}
            summary={
              <>
                <Td>
                  <div className="font-medium">{v.display_name}</div>
                  <code className="font-mono text-xs text-slate-400">{short(v.place_id, 12)}…</code>
                </Td>
                <Td className="text-slate-600">
                  {v.formatted_address ? v.formatted_address.split(",").slice(-3, -1).join(",").trim() : "—"}
                </Td>
                <Td>
                  {v.google_rating !== null ? (
                    <>
                      <span className="font-medium">{v.google_rating.toFixed(1)}</span>
                      <span className="text-slate-500 text-xs ml-1">({v.google_user_rating_count ?? 0})</span>
                    </>
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </Td>
                <Td>
                  {v.bbb_grade ? (
                    <Badge className={bbbColor(v.bbb_grade)}>{v.bbb_grade}</Badge>
                  ) : (
                    <span className="text-slate-400 text-xs">no profile</span>
                  )}
                </Td>
                <Td>{v.years_in_business ?? "—"}</Td>
                <Td>
                  {v.cumulative_score !== null ? (
                    <ScoreBar value={v.cumulative_score} />
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </Td>
                <Td>{v.emergency_service_24_7 ? "Yes" : "No"}</Td>
                <Td>
                  <span className={`text-xs ${v.business_status === "OPERATIONAL" ? "text-emerald-700" : "text-red-700"}`}>
                    {v.business_status ?? "—"}
                  </span>
                </Td>
              </>
            }
            detail={
              <div className="grid grid-cols-2 gap-4 text-sm">
                <Field label="Place ID"><code className="font-mono text-xs">{v.place_id}</code></Field>
                <Field label="Types"><div className="text-xs">{v.types.join(", ")}</div></Field>
                <Field label="Address" className="col-span-2">{v.formatted_address ?? "—"}</Field>
                <Field label="Phone">{v.international_phone_number ?? "—"}</Field>
                <Field label="Website">
                  {v.website_uri ? (
                    <a href={v.website_uri} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline text-xs break-all">
                      {v.website_uri}
                    </a>
                  ) : "—"}
                </Field>
                <Field label="Price level">{v.price_level ?? "—"}</Field>
                <Field label="Fetched">{dt(v.google_fetched_at)}</Field>
                <Field label="BBB profile">
                  {v.bbb_profile_url ? (
                    <a href={v.bbb_profile_url} target="_blank" rel="noopener noreferrer" className="text-blue-600 underline text-xs break-all">
                      {v.bbb_profile_url}
                    </a>
                  ) : "—"}
                </Field>
                <Field label="BBB accredited">{bool(v.bbb_accredited)} {v.bbb_years_accredited ? `(${v.bbb_years_accredited}y)` : ""}</Field>
                <Field label="BBB complaints">
                  {v.bbb_complaints_total !== null ? `${v.bbb_complaints_resolved ?? 0} / ${v.bbb_complaints_total} resolved` : "—"}
                </Field>
                <Field label="BBB fetched">{dt(v.bbb_fetched_at)}</Field>
                <Field label="Score breakdown" className="col-span-2"><Json data={v.cumulative_score_breakdown} /></Field>
                <Field label="Regular hours" className="col-span-2"><Json data={v.regular_opening_hours} /></Field>
              </div>
            }
          />
        ))}
      </tbody>
    </TableShell>
  );
}

function RunsTable({ rows, expanded, toggle }: TableProps<DiscoveryRun>) {
  if (rows === null) return null;
  if (rows.length === 0) return <Empty msg="No discovery runs yet. POST /discovery/run to create one." />;
  return (
    <TableShell>
      <thead className="bg-slate-50">
        <tr>
          <Th>ID</Th>
          <Th>Work Order</Th>
          <Th>Strategy</Th>
          <Th>Profile</Th>
          <Th>Candidates</Th>
          <Th>Cache hits</Th>
          <Th>Details API</Th>
          <Th>BBB scrapes</Th>
          <Th>Duration</Th>
          <Th>Created</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <ExpandableRow key={r.id} id={r.id} expanded={!!expanded[r.id]} toggle={toggle} cols={10}
            summary={
              <>
                <Td><code className="font-mono text-xs text-slate-500">{short(r.id)}</code></Td>
                <Td><code className="font-mono text-xs text-slate-500">{short(r.work_order_id)}</code></Td>
                <Td><Badge className="bg-slate-100 text-slate-700 border-slate-200">{r.strategy}</Badge></Td>
                <Td><Badge className={urgencyColor(r.weight_profile)}>{r.weight_profile}</Badge></Td>
                <Td className="font-medium">{r.candidate_count}</Td>
                <Td className="text-emerald-700">{r.cache_hit_count}</Td>
                <Td className="text-blue-700">{r.api_detail_calls}</Td>
                <Td className="text-orange-700">{r.bbb_scrape_count}</Td>
                <Td className="text-slate-600">{r.duration_ms ? `${(r.duration_ms / 1000).toFixed(1)}s` : "—"}</Td>
                <Td className="text-slate-500 text-xs">{dt(r.created_at)}</Td>
              </>
            }
            detail={
              <div className="grid grid-cols-2 gap-4 text-sm">
                <Field label="Run ID"><code className="font-mono text-xs">{r.id}</code></Field>
                <Field label="Work order ID"><code className="font-mono text-xs">{r.work_order_id}</code></Field>
                <Field label="Radius">{r.radius_miles} miles</Field>
                <Field label="Cost note" className="col-span-2">
                  <span className="text-xs text-slate-600">
                    {r.api_detail_calls} Enterprise-tier places detail calls
                    ({r.cache_hit_count} cache hits avoided billing).
                    Free tier covers 1,000/mo.
                  </span>
                </Field>
              </div>
            }
          />
        ))}
      </tbody>
    </TableShell>
  );
}

function NegotiationsTable({ rows, expanded, toggle }: TableProps<Negotiation>) {
  if (rows === null) return null;
  if (rows.length === 0) return <Empty msg="No negotiations yet. Discovery runs create these." />;
  return (
    <>
      <div className="mb-3 rounded-lg border border-sky-200 bg-sky-50 px-4 py-2 text-xs text-sky-900">
        <strong>Ranking deferred to subpart 3.</strong> Rows here show every (work order × vendor) pair surfaced by discovery. Subjective rank + price comparison only get computed once the outreach agent collects a quote per vendor. For now, survivors are ordered by vendor quality as a pre-quote proxy.
      </div>
      <TableShell>
        <thead className="bg-slate-50">
          <tr>
            <Th>State</Th>
            <Th>Vendor</Th>
            <Th>Work Order</Th>
            <Th>Status</Th>
            <Th>Quality</Th>
            <Th>Quote</Th>
            <Th>Rank</Th>
            <Th>Filter reasons</Th>
            <Th>Created</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((n) => (
            <ExpandableRow key={n.id} id={n.id} expanded={!!expanded[n.id]} toggle={toggle} cols={9}
              summary={
                <>
                  <Td>
                    {n.filtered ? (
                      <Badge className="bg-red-100 text-red-900 border-red-200">filtered</Badge>
                    ) : (
                      <Badge className="bg-slate-100 text-slate-700 border-slate-200">prospecting</Badge>
                    )}
                  </Td>
                  <Td>
                    <div className="font-medium">{n.vendor_display_name ?? "—"}</div>
                    <code className="font-mono text-xs text-slate-400">{short(n.vendor_place_id, 12)}…</code>
                  </Td>
                  <Td><code className="font-mono text-xs text-slate-500">{short(n.work_order_id)}</code></Td>
                  <Td><Badge className={statusColor(n.state)}>{n.state}</Badge></Td>
                  <Td>
                    {n.vendor_cumulative_score !== null ? (
                      <ScoreBar value={n.vendor_cumulative_score} />
                    ) : (
                      <span className="text-slate-400">—</span>
                    )}
                  </Td>
                  <Td>
                    {n.quoted_price_cents !== null ? (
                      <span className="font-medium">${(n.quoted_price_cents / 100).toLocaleString()}</span>
                    ) : (
                      <span className="text-slate-400 text-xs" title="Awaiting vendor quote">—</span>
                    )}
                  </Td>
                  <Td>
                    {n.rank !== null ? (
                      <span className="font-mono font-semibold text-slate-900">#{n.rank}</span>
                    ) : (
                      <span className="text-slate-400 text-xs" title="Ranked after quote arrives">—</span>
                    )}
                  </Td>
                  <Td>
                    {n.filter_reasons && n.filter_reasons.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {n.filter_reasons.map((r, i) => (
                          <Badge key={i} className="bg-red-50 text-red-800 border-red-100">
                            {r}
                          </Badge>
                        ))}
                      </div>
                    ) : (
                      <span className="text-slate-400 text-xs">—</span>
                    )}
                  </Td>
                  <Td className="text-slate-500 text-xs">{dt(n.created_at)}</Td>
                </>
              }
              detail={
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <Field label="Negotiation ID"><code className="font-mono text-xs">{n.id}</code></Field>
                  <Field label="Discovery Run"><code className="font-mono text-xs">{n.discovery_run_id}</code></Field>
                  <Field label="Vendor place_id" className="col-span-2"><code className="font-mono text-xs">{n.vendor_place_id}</code></Field>
                  <Field label="Subjective breakdown" className="col-span-2">
                    {n.subjective_rank_breakdown ? <Json data={n.subjective_rank_breakdown} /> : <span className="text-slate-400 text-xs">awaiting quote (subpart 3)</span>}
                  </Field>
                  <Field label="Quoted available at">{n.quoted_available_at ? dt(n.quoted_available_at) : <span className="text-slate-400 text-xs">—</span>}</Field>
                  <Field label="Escalated">{n.escalated ? "Yes" : "No"}</Field>
                  <Field label="Attributes" className="col-span-2">
                    {n.attributes && Object.keys(n.attributes).length > 0 ? <Json data={n.attributes} /> : <span className="text-slate-400 text-xs">empty</span>}
                  </Field>
                  <Field label={`Messages (${n.messages.length})`} className="col-span-2">
                    {n.messages.length > 0 ? <Json data={n.messages} /> : <span className="text-slate-400 text-xs">empty</span>}
                  </Field>
                  <Field label="Last updated">{dt(n.last_updated_at)}</Field>
                </div>
              }
            />
          ))}
        </tbody>
      </TableShell>
    </>
  );
}

// ---------------------------------------------------------------------------
// Shared row / field primitives
// ---------------------------------------------------------------------------

function ExpandableRow({
  id, expanded, toggle, summary, detail, cols,
}: {
  id: string;
  expanded: boolean;
  toggle: (id: string) => void;
  summary: React.ReactNode;
  detail: React.ReactNode;
  cols: number;
}) {
  return (
    <>
      <tr
        className={`cursor-pointer transition-colors ${expanded ? "bg-slate-50" : "hover:bg-slate-50"}`}
        onClick={() => toggle(id)}
      >
        {summary}
      </tr>
      {expanded && (
        <tr className="bg-slate-50">
          <td colSpan={cols} className="px-4 py-4 border-b border-slate-200">
            {detail}
          </td>
        </tr>
      )}
    </>
  );
}

function Field({ label, children, className = "" }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <div className="text-xs uppercase tracking-wide text-slate-500 mb-1">{label}</div>
      <div className="text-slate-900 text-sm break-words">{children}</div>
    </div>
  );
}

function ScoreBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color = value >= 0.75 ? "bg-emerald-500" : value >= 0.5 ? "bg-yellow-500" : "bg-red-500";
  return (
    <div className="flex items-center gap-2 min-w-[96px]">
      <div className="flex-1 h-2 bg-slate-200 rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-slate-700">{value.toFixed(2)}</span>
    </div>
  );
}
