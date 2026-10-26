import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";

import { Button, Card, CardHeader, Input, PageHeader, Select } from "../components/ui";
import { api } from "../lib/api";
import { cn } from "../lib/cn";
import { formatDateTime } from "../lib/format";
import { useToast } from "../lib/toast";
import type { Customer } from "../lib/types";

interface SimMessage {
  id: number;
  direction: "in" | "out";
  type: string;
  body: string | null;
  status: string;
  kind: string | null;
  order_id: number | null;
  created_at: string;
}

const NEW_SHOP = "new";
const EXAMPLES = [
  "2 ctn tapal 950, 1 dz surf adha kilo",
  "1 dozen ariel",
  "teen carton olpers",
  "ghee ka rate?",
  "mera balance kitna hai",
  "olpers 5 carton kar do",
  "order cancel",
];
const BUTTONS: { action: "confirm" | "edit" | "cancel"; title: string }[] = [
  { action: "confirm", title: "Confirm" },
  { action: "edit", title: "Edit" },
  { action: "cancel", title: "Cancel" },
];

/** Chat as a shop. Messages go through the real queue, worker and agent. */
export function Simulator() {
  const toast = useToast();
  const client = useQueryClient();
  const [selected, setSelected] = useState<string>("");
  const [newPhone, setNewPhone] = useState("923001234567");
  const [text, setText] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  const customers = useQuery({
    queryKey: ["customers", ""],
    queryFn: () => api<{ items: Customer[] }>("/api/customers", { params: { limit: 200 } }),
  });
  const phone = selected === NEW_SHOP ? newPhone.trim() : selected || customers.data?.items[0]?.phone || "";
  const validPhone = /^\d{8,15}$/.test(phone);

  const conversation = useQuery({
    queryKey: ["simulator", phone],
    queryFn: () => api<SimMessage[]>("/api/simulator/conversation", { params: { phone } }),
    enabled: validPhone,
    refetchInterval: 1500,
  });

  const send = useMutation({
    mutationFn: (body: { text?: string; button_id?: string; button_title?: string }) =>
      api("/api/simulator/messages", { method: "POST", body: { phone, ...body } }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["simulator", phone] });
      void client.invalidateQueries({ queryKey: ["summary"] });
    },
    onError: (e) => toast.error(e.message),
  });

  const count = conversation.data?.length ?? 0;
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [count]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    send.mutate({ text: text.trim() });
    setText("");
  }

  const messages = conversation.data ?? [];
  const latestConfirmation = [...messages].reverse().find((m) => m.kind === "confirmation");

  return (
    <>
      <PageHeader
        title="Chat simulator"
        subtitle="Order as a shop would on WhatsApp. Messages go through the real agent; replies arrive after about 10 seconds, so a burst of messages gets one answer."
      />
      <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Card className="h-fit">
          <CardHeader title="Chatting as" />
          <div className="space-y-3 p-5 text-sm">
            <Select
              className="w-full"
              value={selected}
              onChange={(e) => setSelected(e.target.value)}
              aria-label="Shop"
            >
              {customers.data?.items.map((c) => (
                <option key={c.id} value={c.phone}>
                  {c.name}
                </option>
              ))}
              <option value={NEW_SHOP}>A new shop...</option>
            </Select>
            {selected === NEW_SHOP && (
              <label className="block text-ink-700">
                WhatsApp number (digits only)
                <Input className="mt-1" value={newPhone} onChange={(e) => setNewPhone(e.target.value)} />
              </label>
            )}
            <p className="text-xs text-ink-500">
              New shops' first orders always go to staff review. Approve them under Orders, then come back here.
            </p>
            <div>
              <p className="mb-2 text-xs font-medium tracking-wide text-ink-500 uppercase">Try</p>
              <div className="flex flex-wrap gap-1.5">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => setText(example)}
                    className="rounded-md bg-ink-50 px-2 py-1 text-left text-xs text-ink-700 ring-1 ring-ink-200 hover:ring-brand-400"
                  >
                    {example}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Card>

        <Card className="flex h-[38rem] flex-col">
          <CardHeader title={validPhone ? `WhatsApp · ${phone}` : "Choose a shop"} />
          <ol className="flex-1 space-y-3 overflow-y-auto bg-[#efeae2] p-4" aria-live="polite">
            {messages.length === 0 && (
              <li className="py-10 text-center text-sm text-ink-500">No messages yet. Send an order below.</li>
            )}
            {messages.map((m) => (
              <li key={m.id} className={cn("flex", m.direction === "in" ? "justify-end" : "justify-start")}>
                <div
                  className={cn(
                    "max-w-[80%] rounded-2xl px-3.5 py-2 text-sm shadow-xs",
                    m.direction === "in" ? "rounded-br-sm bg-[#d9fdd3] text-ink-900" : "rounded-bl-sm bg-white text-ink-900",
                  )}
                >
                  <p className="whitespace-pre-wrap">{m.body ?? `[${m.type}]`}</p>
                  {m.kind === "confirmation" && m.order_id && m.id === latestConfirmation?.id && (
                    <div className="mt-2 grid grid-cols-3 gap-1.5 border-t border-ink-100 pt-2">
                      {BUTTONS.map((b) => (
                        <button
                          key={b.action}
                          type="button"
                          disabled={send.isPending}
                          onClick={() => send.mutate({ button_id: `${b.action}:${m.order_id}`, button_title: b.title })}
                          className="rounded-md py-1 text-xs font-medium text-sky-700 hover:bg-sky-50"
                        >
                          {b.title}
                        </button>
                      ))}
                    </div>
                  )}
                  <p className="mt-1 text-right text-[11px] text-ink-400">
                    {formatDateTime(m.created_at)}
                    {m.direction === "out" && m.status === "queued" && " · sending"}
                  </p>
                </div>
              </li>
            ))}
            <div ref={bottom} />
          </ol>
          <form onSubmit={submit} className="flex gap-2 border-t border-ink-100 p-3">
            <Input
              placeholder="Type an order, e.g. 2 ctn tapal 950"
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={!validPhone}
              aria-label="Message"
            />
            <Button type="submit" loading={send.isPending} disabled={!validPhone || !text.trim()} aria-label="Send">
              <Send className="size-4" aria-hidden />
            </Button>
          </form>
        </Card>
      </div>
    </>
  );
}
