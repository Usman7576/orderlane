import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, CheckCircle2, FileText, Mic, Plus, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { ProductPicker } from "../components/ProductPicker";
import { useToast } from "../lib/toast";
import { Badge, Button, Card, CardHeader, ErrorNote, Input, Select, Spinner, StatusBadge } from "../components/ui";
import { cn } from "../lib/cn";
import { api } from "../lib/api";
import { formatDateTime, formatMoney, formatQty, humanize, issueLabel } from "../lib/format";
import type { ChatMessage, OrderDetail, OrderLine, Product } from "../lib/types";

interface EditableLine {
  key: string;
  sku: string | null;
  name: string | null;
  unit: string | null;
  units: string[];
  qty: string;
  sourceText: string | null;
  saveAlias: boolean;
  changedProduct: boolean;
  needsReview: boolean;
  alternatives: OrderLine["alternatives"];
  lineTotal: string | null;
}

function toEditable(line: OrderLine): EditableLine {
  return {
    key: String(line.id),
    sku: line.sku,
    name: line.product_name,
    unit: line.unit,
    units: line.units,
    qty: formatQty(line.qty),
    sourceText: line.source_text,
    saveAlias: false,
    changedProduct: false,
    needsReview: line.needs_review,
    alternatives: line.alternatives,
    lineTotal: line.line_total,
  };
}

export function OrderReview() {
  const { orderId } = useParams();
  const order = useQuery({
    queryKey: ["order", orderId],
    queryFn: () => api<OrderDetail>(`/api/orders/${orderId}`),
    refetchInterval: 15_000,
  });

  if (order.isPending) return <Spinner label="Loading order" />;
  if (order.error) return <ErrorNote error={order.error} />;
  const data = order.data;

  return (
    <>
      <Link to="/orders" className="mb-4 inline-flex items-center gap-1.5 text-sm text-ink-500 hover:text-ink-800">
        <ArrowLeft className="size-4" aria-hidden /> Orders
      </Link>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold text-ink-900">Order #{data.number}</h1>
            <StatusBadge status={data.status} />
          </div>
          <p className="mt-1 text-sm text-ink-500">
            <Link to={`/customers?id=${data.customer.id}`} className="font-medium text-ink-700 hover:underline">
              {data.customer.name}
            </Link>{" "}
            · {data.customer.phone} · Tier {data.customer.price_tier} · Credit limit{" "}
            {formatMoney(data.customer.credit_limit)}
            {data.customer.is_new && <Badge className="ml-2">New customer</Badge>}
          </p>
        </div>
        {data.invoice && (
          <a
            href={data.invoice.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-brand-700 ring-1 ring-brand-200 hover:bg-brand-50"
          >
            <FileText className="size-4" aria-hidden /> {data.invoice.number}
          </a>
        )}
      </div>

      {data.issues.length > 0 && data.status === "pending_review" && (
        <div className="mb-6 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <span className="font-medium">Held for review:</span>
          {data.issues.map((code) => (
            <Badge key={code} tone="amber">
              {issueLabel(code)}
            </Badge>
          ))}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <Conversation messages={data.messages} />
        <div className="space-y-6">
          <LinesEditor key={data.updated_at} order={data} />
          <AgentTrace order={data} />
        </div>
      </div>
    </>
  );
}

function Conversation({ messages }: { messages: ChatMessage[] }) {
  return (
    <Card className="flex max-h-[44rem] flex-col">
      <CardHeader title="Conversation" />
      <ol className="flex-1 space-y-3 overflow-y-auto bg-ink-50/60 p-4">
        {messages.map((m) => (
          <li key={m.id} className={cn("flex", m.direction === "out" ? "justify-end" : "justify-start")}>
            <div
              className={cn(
                "max-w-[85%] rounded-2xl px-3.5 py-2 text-sm shadow-xs",
                m.direction === "out" ? "rounded-br-sm bg-brand-600 text-white" : "rounded-bl-sm bg-white text-ink-900 ring-1 ring-ink-200",
              )}
            >
              {m.type === "audio" ? (
                <div className="space-y-2">
                  <p className="flex items-center gap-1.5 text-xs font-medium opacity-80">
                    <Mic className="size-3.5" aria-hidden /> Voice note
                  </p>
                  {m.has_audio && <audio controls preload="none" src={`/api/messages/${m.id}/audio`} className="h-8 w-56" />}
                  <p className="italic">{m.transcript ?? "No transcript"}</p>
                </div>
              ) : (
                <p className="whitespace-pre-wrap">{m.body ?? `[${m.type}]`}</p>
              )}
              <p className={cn("mt-1 text-right text-[11px]", m.direction === "out" ? "text-white/70" : "text-ink-400")}>
                {formatDateTime(m.created_at)}
                {m.direction === "out" && ` · ${m.status}`}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

const POLL_MS = 1500;
const POLL_TIMEOUT_MS = 30_000;

/** Approve/reject is queued for the worker, which resumes the paused agent run. Wait until
 * the order leaves review so staff see the outcome, not just "queued". */
async function reviewAndWait(orderId: number, action: "approve" | "reject"): Promise<OrderDetail> {
  await api(`/api/orders/${orderId}/${action}`, { method: "POST" });
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, POLL_MS));
    const latest = await api<OrderDetail>(`/api/orders/${orderId}`);
    if (latest.status !== "pending_review" || Date.now() > deadline) return latest;
  }
}

function LinesEditor({ order }: { order: OrderDetail }) {
  const client = useQueryClient();
  const toast = useToast();
  const [lines, setLines] = useState<EditableLine[]>(() => order.lines.map(toEditable));
  const [picking, setPicking] = useState<string | "new" | null>(null);
  const [dirty, setDirty] = useState(false);
  const review = useMutation({
    mutationFn: (action: "approve" | "reject") => reviewAndWait(order.id, action),
    onSuccess: (updated, action) => {
      client.setQueryData(["order", String(order.id)], updated);
      void client.invalidateQueries({ queryKey: ["orders"] });
      void client.invalidateQueries({ queryKey: ["summary"] });
      if (updated.status === "pending_review") {
        toast.error("The worker hasn't picked this up yet. Check that it is running.");
      } else if (action === "approve") {
        toast.success(`Order #${updated.number} approved. The customer was sent the summary to confirm.`);
      } else {
        toast.success(`Order #${updated.number} rejected. The customer was told it was cancelled.`);
      }
    },
    onError: (error) => toast.error(error.message),
  });
  const waitingFor = review.isPending ? review.variables : null;
  const editable = order.status === "pending_review" && !waitingFor;

  const save = useMutation({
    mutationFn: () =>
      api<OrderDetail>(`/api/orders/${order.id}/lines`, {
        method: "PUT",
        body: {
          lines: lines.map((l) => ({
            sku: l.sku,
            unit: l.unit,
            qty: l.qty,
            source_text: l.sourceText,
            save_alias: l.saveAlias,
          })),
        },
      }),
    onSuccess: (updated) => {
      client.setQueryData(["order", String(order.id)], updated);
      setDirty(false);
      toast.success("Changes saved and repriced.");
    },
    onError: (error) => toast.error(error.message),
  });

  function update(key: string, patch: Partial<EditableLine>) {
    setLines((current) => current.map((l) => (l.key === key ? { ...l, ...patch } : l)));
    setDirty(true);
  }

  function pick(key: string | "new", product: Product) {
    const unit = product.units.some((u) => u.name === product.default_unit) ? product.default_unit : product.units[0]?.name;
    const patch = {
      sku: product.sku,
      name: product.name,
      units: product.units.map((u) => u.name),
      unit: unit ?? null,
      changedProduct: true,
      lineTotal: null,
    };
    if (key === "new") {
      setLines((current) => [
        ...current,
        { ...patch, key: `new-${Date.now()}`, qty: "1", sourceText: null, saveAlias: false, needsReview: false, alternatives: [] },
      ]);
      setDirty(true);
    } else {
      update(key, patch);
    }
    setPicking(null);
  }

  const incomplete = lines.some((l) => !l.sku || !l.unit || !(Number(l.qty) > 0));
  const canApprove = editable && !dirty && order.can_approve;

  return (
    <Card>
      <CardHeader
        title="Order lines"
        action={<span className="text-sm font-semibold text-ink-900 tabular-nums">{formatMoney(order.total)}</span>}
      />
      <ul className="divide-y divide-ink-100">
        {lines.map((line) => (
          <li key={line.key} className={cn("px-5 py-4", line.needsReview && !line.changedProduct && "bg-amber-50/50")}>
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <p className="flex items-center gap-2 font-medium text-ink-900">
                  <span className="truncate">
                    {line.name ?? <span className="text-amber-700">Choose a product</span>}
                  </span>
                  {line.name && line.needsReview && !line.changedProduct && (
                    <Badge tone="amber">Agent's guess: check</Badge>
                  )}
                </p>
                {line.sourceText && <p className="mt-0.5 text-xs text-ink-500">Customer wrote: “{line.sourceText}”</p>}
                {editable && line.needsReview && !line.changedProduct && line.alternatives.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-ink-500">Did they mean:</span>
                    {line.alternatives.map((alt) => (
                      <button
                        key={alt.sku}
                        type="button"
                        onClick={() =>
                          api<Product>(`/api/products/${alt.sku}`).then((p) => pick(line.key, p))
                        }
                        className="rounded-md bg-white px-2 py-0.5 text-xs text-ink-700 ring-1 ring-ink-200 hover:ring-brand-400"
                      >
                        {alt.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  min="0"
                  step="0.5"
                  className="w-20 text-right"
                  value={line.qty}
                  disabled={!editable}
                  onChange={(e) => update(line.key, { qty: e.target.value, lineTotal: null })}
                  aria-label="Quantity"
                />
                <Select
                  value={line.unit ?? ""}
                  disabled={!editable || line.units.length === 0}
                  onChange={(e) => update(line.key, { unit: e.target.value, lineTotal: null })}
                  aria-label="Unit"
                >
                  {line.units.length === 0 && <option value="">unit</option>}
                  {line.units.map((u) => (
                    <option key={u} value={u}>
                      {u}
                    </option>
                  ))}
                </Select>
                <span className="w-24 text-right text-sm text-ink-700 tabular-nums">
                  {line.lineTotal ? formatMoney(line.lineTotal) : "-"}
                </span>
              </div>
            </div>
            {editable && (
              <div className="mt-2 flex flex-wrap items-center gap-4 text-xs">
                <button type="button" onClick={() => setPicking(line.key)} className="text-brand-700 hover:underline">
                  Change product
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setLines((current) => current.filter((l) => l.key !== line.key));
                    setDirty(true);
                  }}
                  className="inline-flex items-center gap-1 text-ink-500 hover:text-red-700"
                >
                  <Trash2 className="size-3.5" aria-hidden /> Remove
                </button>
                {line.changedProduct && line.sourceText && (
                  <label className="inline-flex items-center gap-1.5 text-ink-600">
                    <input
                      type="checkbox"
                      checked={line.saveAlias}
                      onChange={(e) => update(line.key, { saveAlias: e.target.checked })}
                      className="accent-brand-600"
                    />
                    Remember “{line.sourceText}” as this product
                  </label>
                )}
              </div>
            )}
            {picking === line.key && (
              <div className="mt-3">
                <ProductPicker
                  initialQuery={line.sourceText ?? ""}
                  onPick={(p) => pick(line.key, p)}
                  onCancel={() => setPicking(null)}
                />
              </div>
            )}
          </li>
        ))}
      </ul>

      {editable && (
        <div className="border-t border-ink-100 px-5 py-3">
          {picking === "new" ? (
            <ProductPicker onPick={(p) => pick("new", p)} onCancel={() => setPicking(null)} />
          ) : (
            <button
              type="button"
              onClick={() => setPicking("new")}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:underline"
            >
              <Plus className="size-4" aria-hidden /> Add a line
            </button>
          )}
        </div>
      )}

      {order.status === "pending_review" && (
        <div className="space-y-3 border-t border-ink-100 bg-ink-50/60 px-5 py-4">
          {!dirty && order.review_blockers.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-5 text-sm text-amber-800">
              {order.review_blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          )}
          {waitingFor && (
            <p className="text-sm text-ink-600" role="status">
              Sending to the agent... the customer will get a message in a few seconds.
            </p>
          )}
          <div className="flex flex-wrap items-center justify-end gap-2">
            {dirty && (
              <Button variant="secondary" loading={save.isPending} disabled={incomplete} onClick={() => save.mutate()}>
                Save changes
              </Button>
            )}
            <Button
              variant="danger"
              loading={waitingFor === "reject"}
              disabled={!editable}
              onClick={() => {
                if (window.confirm("Reject this order? The customer will be told it was cancelled.")) review.mutate("reject");
              }}
            >
              <XCircle className="size-4" aria-hidden /> Reject
            </Button>
            <Button
              loading={waitingFor === "approve"}
              disabled={!canApprove}
              onClick={() => review.mutate("approve")}
              title={dirty ? "Save your changes first" : undefined}
            >
              <CheckCircle2 className="size-4" aria-hidden /> Approve and send
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function AgentTrace({ order }: { order: OrderDetail }) {
  return (
    <Card>
      <CardHeader title="Timeline and agent trace" />
      <div className="space-y-5 p-5">
        <ol className="relative space-y-3 border-l border-ink-200 pl-5">
          {order.events.map((e, i) => (
            <li key={i} className="text-sm">
              <span className="absolute -left-1.5 mt-1.5 size-3 rounded-full border-2 border-white bg-ink-300" />
              <span className="font-medium text-ink-800">{humanize(e.type)}</span>
              <span className="text-ink-500"> by {e.actor}</span>
              <span className="ml-2 text-xs text-ink-400">{formatDateTime(e.created_at)}</span>
            </li>
          ))}
        </ol>

        {order.runs.map((run) => (
          <details key={run.id} className="rounded-lg border border-ink-200">
            <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-4 py-2.5 text-sm">
              <Bot className="size-4 text-ink-500" aria-hidden />
              <span className="font-medium text-ink-800">Agent run #{run.id}</span>
              {run.intent && <Badge>{humanize(run.intent)}</Badge>}
              {run.route && <Badge tone="blue">{humanize(run.route)}</Badge>}
              <span className="ml-auto text-xs text-ink-500">
                {run.latency_ms ?? "-"} ms · {run.tokens_in + run.tokens_out} tokens · ${Number(run.cost_estimate).toFixed(4)} ·{" "}
                {run.prompt_version}
              </span>
            </summary>
            <ol className="divide-y divide-ink-100 border-t border-ink-100">
              {run.steps.map((step, i) => (
                <li key={i} className="px-4 py-2">
                  <div className="flex justify-between text-sm">
                    <span className="font-mono text-xs text-ink-800">{step.node}</span>
                    <span className="text-xs text-ink-500">{step.duration_ms ?? "-"} ms</span>
                  </div>
                  {step.output && Object.keys(step.output).length > 0 && (
                    <pre className="mt-1 max-h-40 overflow-auto rounded bg-ink-50 p-2 text-[11px] text-ink-600">
                      {JSON.stringify(step.output, null, 2)}
                    </pre>
                  )}
                </li>
              ))}
            </ol>
            {run.error && <p className="border-t border-ink-100 px-4 py-2 text-sm text-red-700">{run.error}</p>}
          </details>
        ))}
      </div>
    </Card>
  );
}
