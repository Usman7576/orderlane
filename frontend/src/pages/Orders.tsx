import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { Badge, Card, EmptyState, ErrorNote, Input, PageHeader, Spinner, StatusBadge } from "../components/ui";
import { cn } from "../lib/cn";
import { api } from "../lib/api";
import { formatMoney, issueLabel, timeAgo } from "../lib/format";
import type { OrderPage, OrderStatus } from "../lib/types";

const TABS: { key: string; label: string; statuses: OrderStatus[] }[] = [
  { key: "review", label: "Needs review", statuses: ["pending_review"] },
  { key: "waiting", label: "Waiting for customer", statuses: ["awaiting_customer", "draft"] },
  { key: "done", label: "Confirmed", statuses: ["confirmed", "invoiced"] },
  { key: "cancelled", label: "Cancelled", statuses: ["cancelled"] },
  { key: "all", label: "All", statuses: [] },
];
const PAGE_SIZE = 25;

export function Orders() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const tab = TABS.find((t) => t.key === params.get("tab")) ?? TABS[0];
  const [query, setQuery] = useState(params.get("q") ?? "");
  const page = Number(params.get("page") ?? 0);

  const orders = useQuery({
    queryKey: ["orders", tab.key, params.get("q"), page],
    queryFn: () =>
      api<OrderPage>("/api/orders", {
        params: {
          status: tab.statuses.join(","),
          q: params.get("q") ?? undefined,
          limit: PAGE_SIZE,
          offset: page * PAGE_SIZE,
        },
      }),
    placeholderData: keepPreviousData,
    refetchInterval: 10_000,
  });

  function update(next: Record<string, string | null>) {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) merged.set(key, value);
      else merged.delete(key);
    }
    setParams(merged);
  }

  const counts = orders.data?.counts ?? {};
  const count = (statuses: OrderStatus[]) => statuses.reduce((sum, s) => sum + (counts[s] ?? 0), 0);

  return (
    <>
      <PageHeader title="Orders" subtitle="Orders from WhatsApp, newest activity first." />

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 overflow-x-auto rounded-lg bg-ink-100 p-1" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={t.key === tab.key}
              onClick={() => update({ tab: t.key, page: null })}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium whitespace-nowrap",
                t.key === tab.key ? "bg-white text-ink-900 shadow-xs" : "text-ink-600 hover:text-ink-900",
              )}
            >
              {t.label}
              {t.statuses.length > 0 && (
                <span className="text-xs text-ink-500">{count(t.statuses)}</span>
              )}
            </button>
          ))}
        </div>
        <form
          className="relative w-full sm:w-72"
          onSubmit={(e) => {
            e.preventDefault();
            update({ q: query.trim() || null, page: null });
          }}
        >
          <Search className="absolute top-3 left-3 size-4 text-ink-400" aria-hidden />
          <Input
            className="pl-9"
            placeholder="Search customer or #order"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search orders"
          />
        </form>
      </div>

      <Card>
        {orders.isPending ? (
          <Spinner />
        ) : orders.error ? (
          <div className="p-5">
            <ErrorNote error={orders.error} />
          </div>
        ) : orders.data.items.length === 0 ? (
          <EmptyState
            title={tab.key === "review" ? "Nothing to review" : "No orders here"}
            hint={tab.key === "review" ? "Orders the agent is unsure about will appear here." : undefined}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-ink-100 text-left text-xs font-medium tracking-wide text-ink-500 uppercase">
                <tr>
                  <th className="px-5 py-3">Order</th>
                  <th className="px-5 py-3">Customer</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Why</th>
                  <th className="px-5 py-3 text-right">Total</th>
                  <th className="px-5 py-3 text-right">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-100">
                {orders.data.items.map((o) => (
                  <tr
                    key={o.id}
                    className="group cursor-pointer hover:bg-ink-50"
                    onClick={() => navigate(`/orders/${o.id}`)}
                  >
                    <td className="px-5 py-3.5 font-medium text-ink-900">
                      <Link to={`/orders/${o.id}`} className="group-hover:text-brand-700" onClick={(e) => e.stopPropagation()}>
                        #{o.number}
                      </Link>
                      <span className="ml-2 text-xs text-ink-400">{o.line_count} items</span>
                    </td>
                    <td className="px-5 py-3.5 text-ink-700">{o.customer_name}</td>
                    <td className="px-5 py-3.5">
                      <StatusBadge status={o.status} />
                    </td>
                    <td className="px-5 py-3.5">
                      <div className="flex flex-wrap gap-1">
                        {o.issues.slice(0, 3).map((code) => (
                          <Badge key={code} tone="amber">
                            {issueLabel(code)}
                          </Badge>
                        ))}
                      </div>
                    </td>
                    <td className="px-5 py-3.5 text-right font-medium text-ink-900 tabular-nums">
                      {formatMoney(o.total)}
                    </td>
                    <td className="px-5 py-3.5 text-right text-ink-500">{timeAgo(o.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {orders.data && orders.data.total > PAGE_SIZE && (
        <div className="mt-4 flex items-center justify-between text-sm text-ink-600">
          <span>
            {page * PAGE_SIZE + 1}-{Math.min((page + 1) * PAGE_SIZE, orders.data.total)} of {orders.data.total}
          </span>
          <div className="flex gap-2">
            <button type="button" disabled={page === 0} onClick={() => update({ page: String(page - 1) })} className="disabled:opacity-40">
              Previous
            </button>
            <button
              type="button"
              disabled={(page + 1) * PAGE_SIZE >= orders.data.total}
              onClick={() => update({ page: String(page + 1) })}
              className="disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </>
  );
}
