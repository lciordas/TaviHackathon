"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const MAILPIT_URL = process.env.NEXT_PUBLIC_MAILPIT_URL ?? "http://localhost:8025";

type Props = {
  // Override the auto-detected "Command Center" target. Pass the current
  // work-order id when we're already on a specific command-center page, or
  // the freshly-submitted id from intake so the link updates instantly.
  currentWorkOrderId?: string;
};

export function Nav({ currentWorkOrderId }: Props) {
  const pathname = usePathname();
  const [latestWorkOrderId, setLatestWorkOrderId] = useState<string | null>(
    currentWorkOrderId ?? null,
  );

  useEffect(() => {
    if (currentWorkOrderId) {
      setLatestWorkOrderId(currentWorkOrderId);
      return;
    }
    let cancelled = false;
    fetch(`${API_BASE}/admin/work_orders`)
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: Array<{ id: string; created_at: string }>) => {
        if (cancelled || !rows.length) return;
        const sorted = [...rows].sort((a, b) =>
          b.created_at.localeCompare(a.created_at),
        );
        setLatestWorkOrderId(sorted[0].id);
      })
      .catch(() => {
        /* swallow — nav just won't have a command-center target */
      });
    return () => {
      cancelled = true;
    };
  }, [currentWorkOrderId]);

  const commandHref = latestWorkOrderId
    ? `/work-orders/${latestWorkOrderId}`
    : null;

  type Item = {
    href: string | null;
    label: string;
    active: boolean;
    title?: string;
  };

  const items: Item[] = [
    { href: "/", label: "Intake", active: pathname === "/" },
    {
      href: "/admin",
      label: "DB Explorer",
      active: pathname === "/admin" || pathname.startsWith("/admin/"),
    },
    {
      href: commandHref,
      label: "Command Center",
      active: pathname.startsWith("/work-orders"),
      title: commandHref
        ? undefined
        : "No work orders yet — submit one from intake",
    },
  ];

  const base = "px-3 py-1.5 text-sm font-medium rounded-md transition-colors";
  const activeCls = "bg-slate-900 text-white";
  const idleCls = "text-slate-600 hover:text-slate-900 hover:bg-slate-100";
  const disabledCls = "text-slate-400 cursor-not-allowed";

  return (
    <nav className="flex items-center gap-1">
      {items.map((item) => {
        if (!item.href) {
          return (
            <span
              key={item.label}
              title={item.title}
              aria-disabled="true"
              className={`${base} ${disabledCls}`}
            >
              {item.label}
            </span>
          );
        }
        return (
          <Link
            key={item.label}
            href={item.href}
            className={`${base} ${item.active ? activeCls : idleCls}`}
          >
            {item.label}
          </Link>
        );
      })}
      <span className="mx-1 h-5 w-px bg-slate-200" aria-hidden="true" />
      <a
        href={MAILPIT_URL}
        target="_blank"
        rel="noreferrer"
        className={`${base} ${idleCls} flex items-center gap-1`}
        title="Open Mailpit (vendor email bus) — localhost:8025 by default"
      >
        Mailpit
        <span aria-hidden="true" className="text-xs">↗</span>
      </a>
    </nav>
  );
}
