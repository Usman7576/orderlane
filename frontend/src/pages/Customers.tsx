import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";

import { Badge, Card, CardHeader, EmptyState, ErrorNote, Input, PageHeader, Spinner, StatusBadge } from "../components/ui";
import { cn } from "../lib/cn";
import { api } from "../lib/api";
import { formatDateTime, formatMoney, humanize, timeAgo } from "../lib/format";
import type { CustomerDetail, Customer } from "../lib/types";

function creditUse(c: Customer): number {
  const limit = Number(c.credit_limit);
  return limit > 0 ? Math.min(100, Math.round((Number(c.balance) / limit) * 100)) : 100;
}

export function Customers() {
  const [params, setParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const selected = params.get("id");

  const customers = useQuery({
    queryKey: ["customers", search],
    queryFn: () => api<{ items: Customer[]; total: number }>("/api/customers", { params: { q: search, limit: 200 } }),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader title="Customers" subtitle="Shops that order on WhatsApp, with balances and credit limits." />
      <form
        className="relative mb-4 w-full sm:w-80"
        onSubmit={(e) => {
          e.preventDefault();
          setSearch(query.trim());
        }}
      >
        <Search className="absolute top-3 left-3 size-4 text-ink-400" aria-hidden />
        <Input className="pl-9" placeholder="Search name or phone" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search customers" />
      </form>

      <div className={cn("grid gap-6", selected && "lg:grid-cols-[minmax(0,1fr)_26rem]")}>
        <Card>
          {customers.isPending ? (
            <Spinner />
          ) : customers.error ? (
            <div className="p-5">
              <ErrorNote error={customers.error} />
            </div>
          ) : customers.data.items.length === 0 ? (
            <EmptyState title="No customers yet" hint="Customers appear when they first message the WhatsApp number." />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-ink-100 text-left text-xs font-medium tracking-wide text-ink-500 uppercase">
                  <tr>
                    <th className="px-5 py-3">Customer</th>
                    <th className="px-5 py-3">Tier</th>
                    <th className="px-5 py-3">Credit used</th>
                    <th className="px-5 py-3 text-right">Balance</th>
                    <th className="px-5 py-3 text-right">Last order</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {customers.data.items.map((c) => {
                    const used = creditUse(c);
                    return (
                      <tr
                        key={c.id}
                        onClick={() => setParams({ id: String(c.id) })}
                        className={cn("cursor-pointer hover:bg-ink-50", selected === String(c.id) && "bg-brand-50/60")}
                      >
                        <td className="px-5 py-3">
                          <p className="font-medium text-ink-900">
                            {c.name} {c.is_new && <Badge className="ml-1">New</Badge>}
                          </p>
                          <p className="text-xs text-ink-400">{c.phone}</p>
                        </td>
                        <td className="px-5 py-3 text-ink-600">{c.price_tier}</td>
                        <td className="px-5 py-3">
                          <div className="h-1.5 w-28 rounded-full bg-ink-100" role="meter" aria-valuenow={used} aria-valuemin={0} aria-valuemax={100} aria-label="Credit used">
                            <div
                              className={cn("h-1.5 rounded-full", used >= 90 ? "bg-red-500" : used >= 70 ? "bg-amber-500" : "bg-brand-500")}
                              style={{ width: `${used}%` }}
                            />
                          </div>
                          <p className="mt-1 text-xs text-ink-500">{used}% of {formatMoney(c.credit_limit)}</p>
                        </td>
                        <td className="px-5 py-3 text-right font-medium tabular-nums">{formatMoney(c.balance)}</td>
                        <td className="px-5 py-3 text-right text-ink-500">{c.last_order_at ? timeAgo(c.last_order_at) : "-"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        {selected && <CustomerPanel id={selected} onClose={() => setParams({})} />}
      </div>
    </>
  );
}

function CustomerPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const customer = useQuery({
    queryKey: ["customer", id],
    queryFn: () => api<CustomerDetail>(`/api/customers/${id}`),
  });

  return (
    <Card className="h-fit lg:sticky lg:top-8">
      <CardHeader
        title={customer.data?.name ?? "Customer"}
        action={
          <button type="button" onClick={onClose} aria-label="Close" className="text-ink-400 hover:text-ink-700">
            <X className="size-4" />
          </button>
        }
      />
      {customer.isPending ? (
        <Spinner />
      ) : customer.error ? (
        <div className="p-5">
          <ErrorNote error={customer.error} />
        </div>
      ) : (
        <div className="space-y-6 p-5 text-sm">
          <dl className="grid grid-cols-2 gap-3">
            <div>
              <dt className="text-xs text-ink-500">Balance</dt>
              <dd className="text-lg font-semibold tabular-nums">{formatMoney(customer.data.balance)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-500">Credit limit</dt>
              <dd className="text-lg font-semibold tabular-nums">{formatMoney(customer.data.credit_limit)}</dd>
            </div>
          </dl>
          <div>
            <h3 className="mb-2 text-xs font-medium tracking-wide text-ink-500 uppercase">Orders</h3>
            {customer.data.orders.length === 0 ? (
              <p className="text-ink-500">No orders yet.</p>
            ) : (
              <ul className="divide-y divide-ink-100">
                {customer.data.orders.map((o) => (
                  <li key={o.id} className="flex items-center justify-between gap-2 py-2">
                    <Link to={`/orders/${o.id}`} className="font-medium text-ink-800 hover:underline">
                      #{o.number}
                    </Link>
                    <StatusBadge status={o.status} />
                    <span className="ml-auto tabular-nums">{formatMoney(o.total)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h3 className="mb-2 text-xs font-medium tracking-wide text-ink-500 uppercase">Ledger</h3>
            <ul className="divide-y divide-ink-100">
              {customer.data.ledger.map((e) => (
                <li key={e.id} className="flex items-center justify-between gap-2 py-2">
                  <span>
                    <span className="text-ink-800">{humanize(e.type)}</span>
                    <span className="ml-2 text-xs text-ink-400">{e.ref}</span>
                  </span>
                  <span className="text-right">
                    <span className="block tabular-nums">{formatMoney(e.amount)}</span>
                    <span className="text-xs text-ink-400">{formatDateTime(e.created_at)}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Card>
  );
}
