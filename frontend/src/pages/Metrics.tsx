import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Card, CardHeader, ErrorNote, PageHeader, Select, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { humanize } from "../lib/format";

type Scores = Record<string, number | null>;

interface Evaluation {
  config: string;
  timestamp: string;
  git_sha: string | null;
  summary: Scores;
  breakdown: Record<string, Scores>;
}

interface LiveMetrics {
  days: number;
  orders: number;
  sent_to_review: number;
  edited_by_staff: number;
  confirmed_by_customer: number;
  cancelled: number;
  agent_runs: number;
  agent_errors: number;
  latency_p50_ms: number | null;
  latency_p95_ms: number | null;
  llm_cost: string;
  routes: Record<string, number>;
  match_methods: Record<string, number>;
}

interface MetricsResponse {
  evaluations: Evaluation[];
  live: LiveMetrics;
}

const CONFIG_LABEL: Record<string, string> = {
  rules: "Rules only",
  "agent-rules": "Agent, LLM off",
  "agent-llm": "Agent + Azure OpenAI",
};

const DATASETS = [
  { key: "source:synthetic", label: "Held-out test set" },
  { key: "source:dev", label: "Development set" },
  { key: "", label: "All cases" },
];

const METRICS: { key: string; label: string; hint: string }[] = [
  { key: "auto_confirm_precision", label: "Auto-confirm precision", hint: "Orders skipping review that were exactly right" },
  { key: "order_exact_match", label: "Order exact match", hint: "Whole final order right" },
  { key: "intent_accuracy", label: "Intent accuracy", hint: "Order, edit, cancel, question or chat" },
  { key: "line_precision", label: "Line precision", hint: "Lines with the right product and quantity" },
  { key: "line_recall", label: "Line recall", hint: "Requested lines that were found" },
  { key: "ambiguous_sent_to_review", label: "Ambiguous sent to review", hint: "Unclear requests held for staff" },
  { key: "auto_confirm_rate", label: "Auto-confirm rate", hint: "Share of orders needing no staff time" },
];

const DAY_OPTIONS = [7, 30, 90];

function pct(value: number | null | undefined): string {
  return value == null ? "–" : `${(value * 100).toFixed(1)}%`;
}

function share(part: number, whole: number): string {
  return whole ? `${Math.round((part / whole) * 100)}% of orders` : "No orders yet";
}

/** Offline evaluation next to live traffic: is the agent as good in use as on the test set? */
export function Metrics() {
  const [days, setDays] = useState(30);
  const [config, setConfig] = useState("");
  const [dataset, setDataset] = useState(DATASETS[0].key);

  const query = useQuery({
    queryKey: ["metrics", days],
    queryFn: () => api<MetricsResponse>("/api/metrics", { params: { days } }),
  });

  if (query.isPending) return <Spinner />;
  if (query.isError) return <ErrorNote error={query.error} />;

  const { live, evaluations } = query.data;
  const evaluation = evaluations.find((e) => e.config === config) ?? evaluations[0];
  const scores = evaluation && (dataset ? evaluation.breakdown[dataset] : evaluation.summary);
  const tags = evaluation
    ? Object.entries(evaluation.breakdown)
        .filter(([name]) => name.startsWith("tag:"))
        .sort(([, a], [, b]) => (b.cases ?? 0) - (a.cases ?? 0))
    : [];

  return (
    <>
      <PageHeader title="Metrics" subtitle="How the agent performs on live orders and on the evaluation datasets." />

      <section aria-labelledby="live-heading" className="mb-8">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 id="live-heading" className="text-sm font-semibold text-ink-900">
            Live traffic
          </h2>
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Time range">
            {DAY_OPTIONS.map((d) => (
              <option key={d} value={d}>
                Last {d} days
              </option>
            ))}
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
          <Stat label="Orders" value={live.orders} hint={`${live.cancelled} cancelled`} />
          <Stat
            label="Sent to staff review"
            value={live.sent_to_review}
            hint={share(live.sent_to_review, live.orders)}
          />
          <Stat
            label="Corrected by staff"
            value={live.edited_by_staff}
            hint={share(live.edited_by_staff, live.orders)}
          />
          <Stat
            label="Confirmed by customer"
            value={live.confirmed_by_customer}
            hint={share(live.confirmed_by_customer, live.orders)}
          />
          <Stat
            label="Agent runs"
            value={live.agent_runs}
            hint={live.agent_errors ? `${live.agent_errors} failed` : "No failures"}
            alert={live.agent_errors > 0}
          />
          <Stat
            label="Agent latency (median)"
            value={live.latency_p50_ms == null ? "–" : `${live.latency_p50_ms} ms`}
            hint={live.latency_p95_ms == null ? "No runs yet" : `95th percentile ${live.latency_p95_ms} ms`}
          />
          <Stat label="LLM cost" value={`$${Number(live.llm_cost).toFixed(4)}`} hint="Estimated from tokens" />
        </div>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <Breakdown title="How messages were routed" counts={live.routes} />
          <Breakdown title="How order lines were matched" counts={live.match_methods} />
        </div>
      </section>

      <section aria-labelledby="eval-heading">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 id="eval-heading" className="text-sm font-semibold text-ink-900">
            Offline evaluation
          </h2>
          {evaluation && (
            <div className="flex flex-wrap gap-2">
              <Select value={evaluation.config} onChange={(e) => setConfig(e.target.value)} aria-label="Pipeline">
                {evaluations.map((e) => (
                  <option key={e.config} value={e.config}>
                    {CONFIG_LABEL[e.config] ?? e.config}
                  </option>
                ))}
              </Select>
              <Select value={dataset} onChange={(e) => setDataset(e.target.value)} aria-label="Dataset">
                {DATASETS.map((d) => (
                  <option key={d.key} value={d.key}>
                    {d.label}
                  </option>
                ))}
              </Select>
            </div>
          )}
        </div>

        {!evaluation || !scores ? (
          <Card>
            <p className="px-5 py-10 text-center text-sm text-ink-500">
              No saved evaluation results. Run <code className="font-mono">uv run python -m evals.run</code> in
              backend/.
            </p>
          </Card>
        ) : (
          <div className="grid gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <Card>
              <CardHeader
                title={`${scores.cases ?? 0} cases`}
                action={
                  <span className="text-xs text-ink-500">
                    {evaluation.timestamp}
                    {evaluation.git_sha && ` · commit ${evaluation.git_sha}`}
                  </span>
                }
              />
              <ul className="divide-y divide-ink-100">
                {METRICS.map((m) => (
                  <li key={m.key} className="grid grid-cols-[minmax(0,1fr)_8rem_4rem] items-center gap-4 px-5 py-3">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink-900">{m.label}</p>
                      <p className="truncate text-xs text-ink-500">{m.hint}</p>
                    </div>
                    <Meter value={scores[m.key]} />
                    <p className="text-right text-sm font-semibold text-ink-900 tabular-nums">{pct(scores[m.key])}</p>
                  </li>
                ))}
              </ul>
            </Card>
            <Card>
              <CardHeader title="Exact match by category" />
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-ink-500">
                    <th className="px-5 py-2 font-medium">Category</th>
                    <th className="px-2 py-2 text-right font-medium">Cases</th>
                    <th className="px-5 py-2 text-right font-medium">Exact</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {tags.map(([name, s]) => (
                    <tr key={name}>
                      <td className="px-5 py-2 text-ink-800">{humanize(name.slice(4))}</td>
                      <td className="px-2 py-2 text-right text-ink-600 tabular-nums">{s.cases}</td>
                      <td
                        className={cn(
                          "px-5 py-2 text-right font-medium tabular-nums",
                          (s.order_exact_match ?? 1) < 0.9 ? "text-amber-700" : "text-ink-900",
                        )}
                      >
                        {pct(s.order_exact_match)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="border-t border-ink-100 px-5 py-3 text-xs text-ink-500">
                Categories cover all cases of this run. Details in docs/eval-report.md.
              </p>
            </Card>
          </div>
        )}
      </section>
    </>
  );
}

function Stat({
  label,
  value,
  hint,
  alert,
  className,
}: {
  label: string;
  value: number | string;
  hint: string;
  alert?: boolean;
  className?: string;
}) {
  return (
    <Card className={cn("px-4 py-3.5", className)}>
      <p className="text-xs font-medium text-ink-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight text-ink-900 tabular-nums">{value}</p>
      <p className={cn("mt-0.5 text-xs", alert ? "text-red-700" : "text-ink-500")}>{hint}</p>
    </Card>
  );
}

function Meter({ value }: { value: number | null | undefined }) {
  const width = value == null ? 0 : Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="h-2 rounded-full bg-ink-100" aria-hidden>
      <div className="h-2 rounded-full bg-brand-600" style={{ width: `${width}%` }} />
    </div>
  );
}

function Breakdown({ title, counts }: { title: string; counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort(([, a], [, b]) => b - a);
  const total = entries.reduce((sum, [, n]) => sum + n, 0);
  return (
    <Card>
      <CardHeader title={title} />
      {total === 0 ? (
        <p className="px-5 py-6 text-sm text-ink-500">Nothing in this period.</p>
      ) : (
        <ul className="space-y-2.5 px-5 py-4">
          {entries.map(([name, n]) => (
            <li key={name} className="grid grid-cols-[8rem_minmax(0,1fr)_5rem] items-center gap-3 text-sm">
              <span className="truncate text-ink-700">{humanize(name)}</span>
              <Meter value={n / total} />
              <span className="text-right text-ink-900 tabular-nums">
                {n} <span className="text-xs text-ink-500">({Math.round((n / total) * 100)}%)</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
