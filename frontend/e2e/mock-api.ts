import type { Page, Route } from "@playwright/test";

/** An in-memory OrderLane API for UI tests: just enough state for the flows under test.
 * Shapes follow src/lib/types.ts. */

const NOW = "2026-10-26T10:00:00Z";
const USER = { id: 1, email: "staff@orderlane.test", name: "Staff", role: "staff", business_id: 1 };
export const PASSWORD = "correct horse battery";

function makeOrder() {
  return {
    id: 7,
    number: 1007,
    status: "pending_review",
    total: "9400",
    issues: ["new_customer"],
    created_at: NOW,
    updated_at: NOW,
    confirmed_at: null as string | null,
    customer: {
      id: 3,
      name: "Bismillah Store",
      phone: "923001112233",
      price_tier: "retail",
      credit_limit: "50000",
      is_new: true,
    },
    lines: [
      {
        id: 1,
        sku: "TAP-950",
        product_name: "Tapal Danedar 950g",
        unit: "carton",
        units: ["piece", "carton"],
        qty: "2",
        unit_price: "4700",
        line_total: "9400",
        source_text: "2 ctn tapal 950",
        match_method: "alias",
        match_confidence: 1,
        needs_review: false,
        alternatives: [],
      },
    ],
    messages: [
      {
        id: 11,
        direction: "in",
        type: "text",
        body: "2 ctn tapal 950",
        transcript: null,
        status: "processed",
        has_audio: false,
        kind: null,
        created_at: NOW,
      },
    ],
    events: [{ actor: "agent", type: "draft_updated", payload: {}, created_at: NOW }],
    runs: [],
    invoice: null,
    can_approve: true,
    review_blockers: [],
  };
}

const METRICS = {
  live: {
    days: 30,
    orders: 12,
    sent_to_review: 3,
    edited_by_staff: 1,
    confirmed_by_customer: 8,
    cancelled: 1,
    agent_runs: 40,
    agent_errors: 0,
    latency_p50_ms: 420,
    latency_p95_ms: 1300,
    llm_cost: "0.0123",
    routes: { auto_confirm: 9, review: 3 },
    match_methods: { alias: 20, fuzzy: 4 },
  },
  evaluations: [
    {
      config: "rules",
      timestamp: "2026-10-26 15:49 UTC",
      git_sha: "184f7bf",
      summary: { cases: 123, order_exact_match: 0.9593, auto_confirm_precision: 0.9891 },
      breakdown: {
        "source:synthetic": { cases: 93, order_exact_match: 0.9462, auto_confirm_precision: 0.9855 },
        "source:dev": { cases: 30, order_exact_match: 1, auto_confirm_precision: 1 },
        "tag:roman_urdu": { cases: 17, order_exact_match: 0.8824 },
        "tag:english": { cases: 28, order_exact_match: 1 },
      },
    },
  ],
};

interface SimMessage {
  id: number;
  direction: "in" | "out";
  type: string;
  body: string;
  status: string;
  kind: string | null;
  order_id: number | null;
  created_at: string;
}

export interface MockApi {
  requests: { method: string; path: string; body: unknown }[];
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

export async function mockApi(page: Page, { signedIn = true } = {}): Promise<MockApi> {
  const state: MockApi = { requests: [] };
  const order = makeOrder();
  const chat: SimMessage[] = [];
  const inbox = makeInbox();
  let user: typeof USER | null = signedIn ? USER : null;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    const body: unknown = request.postData() ? request.postDataJSON() : null;
    state.requests.push({ method, path, body });

    if (path === "/api/auth/session") return json(route, { user });
    if (path === "/api/auth/login") {
      if ((body as { password: string }).password !== PASSWORD) {
        return json(route, { detail: "Wrong email or password." }, 401);
      }
      user = USER;
      return json(route, USER);
    }
    if (!user) return json(route, { detail: "Not signed in." }, 401);

    if (path === "/api/summary") {
      return json(route, {
        orders_by_status: { [order.status]: 1 },
        needs_review: order.status === "pending_review" ? 1 : 0,
        failed_jobs: 0,
        open_messages: inbox.filter((m) => !m.handled_at).length,
        messages_today: 1,
        agent_runs_today: 1,
        avg_latency_ms: 400,
        llm_cost_today: "0",
      });
    }
    if (path === "/api/orders") {
      const statuses = (url.searchParams.get("status") ?? "").split(",").filter(Boolean);
      const visible = !statuses.length || statuses.includes(order.status);
      const summary = {
        id: order.id,
        number: order.number,
        status: order.status,
        total: order.total,
        customer_id: order.customer.id,
        customer_name: order.customer.name,
        line_count: order.lines.length,
        issues: order.issues,
        created_at: NOW,
        updated_at: NOW,
      };
      return json(route, { items: visible ? [summary] : [], total: visible ? 1 : 0, counts: { [order.status]: 1 } });
    }
    if (path === `/api/orders/${order.id}`) return json(route, order);
    if (path === `/api/orders/${order.id}/approve` && method === "POST") {
      // The real API queues this for the worker; here the worker "runs" at once.
      order.status = "awaiting_customer";
      order.can_approve = false;
      order.events.push({ actor: "staff", type: "approved", payload: {}, created_at: NOW });
      return json(route, { status: "queued" }, 202);
    }

    if (path === "/api/customers") {
      return json(route, {
        items: [
          {
            id: 3,
            name: "Bismillah Store",
            phone: "923001112233",
            price_tier: "retail",
            credit_limit: "50000",
            balance: "0",
            is_new: false,
            order_count: 4,
            last_order_at: NOW,
          },
        ],
        total: 1,
      });
    }
    if (path === "/api/simulator/conversation") return json(route, chat);
    if (path === "/api/simulator/messages" && method === "POST") {
      const sent = body as { text?: string; button_title?: string };
      const id = chat.length + 1;
      chat.push({
        id,
        direction: "in",
        type: sent.text ? "text" : "interactive",
        body: sent.text ?? sent.button_title ?? "",
        status: "processed",
        kind: null,
        order_id: null,
        created_at: NOW,
      });
      if (sent.text) {
        // The agent's reply, as the worker would store it.
        chat.push({
          id: id + 1,
          direction: "out",
          type: "interactive",
          body: "Order #1008\n2 x Tapal Danedar 950g (carton)\nTotal Rs. 9,400\nConfirm?",
          status: "sent",
          kind: "confirmation",
          order_id: 8,
          created_at: NOW,
        });
      }
      return json(route, { message_id: id }, 202);
    }
    if (path === "/api/metrics") return json(route, METRICS);

    if (path === "/api/inbox") {
      const done = url.searchParams.get("state") === "done";
      const items = inbox.filter((m) => !!m.handled_at === done).map(summaryOf);
      return json(route, { items, open: inbox.filter((m) => !m.handled_at).length });
    }
    const inboxMatch = /^\/api\/inbox\/(\d+)(?:\/(reply|done|reopen))?$/.exec(path);
    if (inboxMatch) {
      const message = inbox.find((m) => m.id === Number(inboxMatch[1]));
      if (!message) return json(route, { detail: "message not found" }, 404);
      const action = inboxMatch[2];
      if (!action) return json(route, { ...message, reply_window_open: true });
      if (action === "reply") {
        const text = (body as { body: string }).body;
        message.conversation.push({ id: 99, direction: "out", body: text, status: "queued", kind: "staff", created_at: NOW });
      }
      message.handled_at = action === "reopen" ? null : NOW;
      message.handled_by = action === "reopen" ? null : "Staff";
      return json(route, summaryOf(message), action === "reply" ? 202 : 200);
    }

    return json(route, { detail: `No mock for ${method} ${path}` }, 404);
  });
  return state;
}

/** An inbox list row: the message without its conversation. */
function summaryOf(message: ReturnType<typeof makeInbox>[number]) {
  return Object.fromEntries(Object.entries(message).filter(([key]) => key !== "conversation"));
}

function makeInbox() {
  const customer = { id: 3, name: "Bismillah Store", phone: "923001112233" };
  return [
    {
      id: 21,
      customer,
      channel: "whatsapp",
      type: "text",
      body: "kal ka oil ka tin leak tha",
      attention: "complaint",
      created_at: NOW,
      handled_at: null as string | null,
      handled_by: null as string | null,
      conversation: [
        { id: 21, direction: "in", body: "kal ka oil ka tin leak tha", status: "processed", kind: "complaint", created_at: NOW },
        {
          id: 22,
          direction: "out",
          body: "We're sorry about this. Our team will look into it and get back to you soon.",
          status: "sent",
          kind: "handoff",
          created_at: NOW,
        },
      ],
    },
  ];
}
