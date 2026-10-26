import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, RotateCcw, Send } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useSearchParams } from "react-router";

import { Badge, Button, Card, CardHeader, EmptyState, ErrorNote, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { formatDateTime, timeAgo } from "../lib/format";
import { useToast } from "../lib/toast";
import type { InboxDetail, InboxItem, InboxPage } from "../lib/types";

const TABS = [
  { key: "open", label: "Open" },
  { key: "done", label: "Done" },
] as const;

/** Messages the agent handed to staff: complaints, questions it couldn't answer, photos. */
export function Messages() {
  const [params, setParams] = useSearchParams();
  const state = params.get("state") === "done" ? "done" : "open";
  const selected = Number(params.get("id")) || null;

  const inbox = useQuery({
    queryKey: ["inbox", state],
    queryFn: () => api<InboxPage>("/api/inbox", { params: { state } }),
    refetchInterval: 10_000,
  });
  const items = inbox.data?.items ?? [];
  const current = selected ?? items[0]?.id ?? null;

  function select(next: Record<string, string | null>) {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value === null) merged.delete(key);
      else merged.set(key, value);
    }
    setParams(merged, { replace: true });
  }

  return (
    <>
      <PageHeader
        title="Messages"
        subtitle="Messages the agent couldn't handle on its own. Greetings and thanks are answered automatically and never show here."
      />
      <div className="grid gap-6 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <Card className="h-fit">
          <div className="flex gap-1 border-b border-ink-100 p-2" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={state === t.key}
                onClick={() => select({ state: t.key === "open" ? null : t.key, id: null })}
                className={cn(
                  "flex-1 rounded-md px-3 py-1.5 text-sm font-medium",
                  state === t.key ? "bg-ink-100 text-ink-900" : "text-ink-500 hover:text-ink-900",
                )}
              >
                {t.label}
                {t.key === "open" && inbox.data && inbox.data.open > 0 && (
                  <span className="ml-1.5 text-xs text-ink-500">{inbox.data.open}</span>
                )}
              </button>
            ))}
          </div>
          {inbox.isPending ? (
            <Spinner />
          ) : inbox.error ? (
            <div className="p-4">
              <ErrorNote error={inbox.error} />
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              title={state === "open" ? "Nothing waiting" : "Nothing handled yet"}
              hint={state === "open" ? "Every customer message has been answered." : undefined}
            />
          ) : (
            <ul className="divide-y divide-ink-100" aria-label="Messages">
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => select({ id: String(item.id) })}
                    aria-current={item.id === current}
                    className={cn(
                      "block w-full px-4 py-3 text-left hover:bg-ink-50",
                      item.id === current && "bg-brand-50 hover:bg-brand-50",
                    )}
                  >
                    <span className="flex items-center gap-2">
                      <span className="flex-1 truncate text-sm font-medium text-ink-900">{item.customer.name}</span>
                      <span className="shrink-0 text-xs text-ink-500">{timeAgo(item.created_at)}</span>
                    </span>
                    <span className="mt-1 flex items-center gap-2">
                      {item.attention === "complaint" && <Badge tone="red">Complaint</Badge>}
                      <span className="truncate text-sm text-ink-600">{item.body}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {current ? (
          <Conversation key={current} id={current} />
        ) : (
          <Card>
            <EmptyState title="Select a message" />
          </Card>
        )}
      </div>
    </>
  );
}

function Conversation({ id }: { id: number }) {
  const client = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState("");
  const detail = useQuery({
    queryKey: ["inbox-message", id],
    queryFn: () => api<InboxDetail>(`/api/inbox/${id}`),
    refetchInterval: 5_000,
  });

  function refresh() {
    void client.invalidateQueries({ queryKey: ["inbox"] });
    void client.invalidateQueries({ queryKey: ["inbox-message", id] });
    void client.invalidateQueries({ queryKey: ["summary"] });
  }

  const reply = useMutation({
    mutationFn: (body: string) => api(`/api/inbox/${id}/reply`, { method: "POST", body: { body } }),
    onSuccess: () => {
      setText("");
      toast.success("Reply sent. The conversation is marked as done.");
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });
  const mark = useMutation({
    mutationFn: (action: "done" | "reopen") => api<InboxItem>(`/api/inbox/${id}/${action}`, { method: "POST" }),
    onSuccess: (_, action) => {
      toast.success(action === "done" ? "Marked as done." : "Moved back to open.");
      refresh();
    },
    onError: (e) => toast.error(e.message),
  });

  if (detail.isPending) return <Card><Spinner /></Card>;
  if (detail.error) return <ErrorNote error={detail.error} />;
  const m = detail.data;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (text.trim()) reply.mutate(text.trim());
  }

  return (
    <Card className="flex h-[38rem] flex-col">
      <CardHeader
        title={
          <span>
            {m.customer.name} <span className="font-normal text-ink-500">· {m.customer.phone}</span>
          </span>
        }
        action={
          m.handled_at ? (
            <Button variant="secondary" size="sm" loading={mark.isPending} onClick={() => mark.mutate("reopen")}>
              <RotateCcw className="size-3.5" aria-hidden /> Reopen
            </Button>
          ) : (
            <Button variant="secondary" size="sm" loading={mark.isPending} onClick={() => mark.mutate("done")}>
              <CheckCheck className="size-3.5" aria-hidden /> Mark done
            </Button>
          )
        }
      />
      <ol className="flex-1 space-y-3 overflow-y-auto bg-ink-50 p-4" aria-label="Conversation">
        {m.conversation.map((c) => (
          <li key={c.id} className={cn("flex", c.direction === "in" ? "justify-start" : "justify-end")}>
            <div
              className={cn(
                "max-w-[80%] rounded-2xl px-3.5 py-2 text-sm shadow-xs",
                c.direction === "in" ? "rounded-bl-sm bg-white ring-1 ring-ink-200" : "rounded-br-sm bg-brand-50 ring-1 ring-brand-200",
                c.id === m.id && "ring-2 ring-amber-400",
              )}
            >
              <p className="whitespace-pre-wrap text-ink-900">{c.body}</p>
              <p className="mt-1 text-right text-[11px] text-ink-500">
                {c.direction === "out" && (c.kind === "staff" ? "Staff · " : "Agent · ")}
                {formatDateTime(c.created_at)}
                {c.direction === "out" && c.status === "queued" && " · sending"}
                {c.direction === "out" && c.status === "failed" && " · not sent"}
              </p>
            </div>
          </li>
        ))}
      </ol>
      {m.handled_at && (
        <p className="border-t border-ink-100 px-4 py-2 text-xs text-ink-500">
          Handled by {m.handled_by ?? "staff"} · {formatDateTime(m.handled_at)}
        </p>
      )}
      {m.reply_window_open ? (
        <form onSubmit={submit} className="flex gap-2 border-t border-ink-100 p-3">
          <textarea
            rows={2}
            placeholder="Write a reply to the customer"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) submit(e);
            }}
            maxLength={1000}
            aria-label="Reply"
            className="min-h-10 flex-1 resize-none rounded-lg border-0 px-3 py-2 text-sm ring-1 ring-ink-200 ring-inset focus:ring-2 focus:ring-brand-600"
          />
          <Button type="submit" loading={reply.isPending} disabled={!text.trim()} aria-label="Send reply">
            <Send className="size-4" aria-hidden />
          </Button>
        </form>
      ) : (
        <p className="border-t border-ink-100 px-4 py-3 text-sm text-amber-800" role="note">
          More than 24 hours since the customer's last message, so WhatsApp won't deliver a reply. Call the customer,
          then mark this as done.
        </p>
      )}
    </Card>
  );
}
