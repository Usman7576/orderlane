/** Shapes returned by the OrderLane API (see backend/app/api). Decimals arrive as strings. */

export type Money = string;

export interface User {
  id: number;
  email: string;
  name: string | null;
  role: "owner" | "staff";
  business_id: number;
}

export type OrderStatus =
  | "draft"
  | "pending_review"
  | "awaiting_customer"
  | "confirmed"
  | "invoiced"
  | "cancelled";

export interface OrderSummary {
  id: number;
  number: number;
  status: OrderStatus;
  total: Money;
  customer_id: number;
  customer_name: string;
  line_count: number;
  issues: string[];
  created_at: string;
  updated_at: string;
}

export interface OrderPage {
  items: OrderSummary[];
  total: number;
  counts: Partial<Record<OrderStatus, number>>;
}

export interface Alternative {
  sku: string;
  name: string;
}

export interface OrderLine {
  id: number;
  sku: string | null;
  product_name: string | null;
  unit: string | null;
  units: string[];
  qty: Money;
  unit_price: Money | null;
  line_total: Money | null;
  source_text: string | null;
  match_method: string | null;
  match_confidence: number | null;
  needs_review: boolean;
  alternatives: Alternative[];
}

export interface ChatMessage {
  id: number;
  direction: "in" | "out";
  type: string;
  body: string | null;
  transcript: string | null;
  status: string;
  has_audio: boolean;
  kind: string | null;
  created_at: string;
}

export interface OrderEvent {
  actor: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentStep {
  node: string;
  duration_ms: number | null;
  output: Record<string, unknown> | null;
}

export interface AgentRun {
  id: number;
  message_id: number | null;
  status: string;
  intent: string | null;
  route: string | null;
  prompt_version: string | null;
  latency_ms: number | null;
  tokens_in: number;
  tokens_out: number;
  cost_estimate: Money;
  error: string | null;
  created_at: string;
  steps: AgentStep[];
}

export interface OrderDetail {
  id: number;
  number: number;
  status: OrderStatus;
  total: Money;
  issues: string[];
  created_at: string;
  updated_at: string;
  confirmed_at: string | null;
  customer: {
    id: number;
    name: string;
    phone: string;
    price_tier: string;
    credit_limit: Money;
    is_new: boolean;
  };
  lines: OrderLine[];
  messages: ChatMessage[];
  events: OrderEvent[];
  runs: AgentRun[];
  invoice: { number: string; url: string } | null;
  can_approve: boolean;
  review_blockers: string[];
}

export interface Product {
  sku: string;
  name: string;
  brand: string | null;
  category: string | null;
  default_unit: string;
  units: { name: string; base_qty: number }[];
  prices: Record<string, Record<string, Money>>;
  available: number | null;
  alias_count: number;
}

export interface ProductPage {
  items: Product[];
  total: number;
  categories: string[];
}

export interface ProductDetail extends Product {
  aliases: { id: number; text: string; source: string; created_at: string }[];
}

export interface Customer {
  id: number;
  name: string;
  phone: string;
  price_tier: string;
  credit_limit: Money;
  balance: Money;
  is_new: boolean;
  order_count: number;
  last_order_at: string | null;
}

export interface CustomerDetail extends Customer {
  ledger: { id: number; type: string; amount: Money; ref: string | null; created_at: string }[];
  orders: { id: number; number: number; status: OrderStatus; total: Money; created_at: string }[];
}

export interface Job {
  id: number;
  kind: string;
  status: string;
  attempts: number;
  last_error: string | null;
  message_id: number | null;
  message_preview: string | null;
  created_at: string;
  updated_at: string;
}

export interface Summary {
  orders_by_status: Partial<Record<OrderStatus, number>>;
  needs_review: number;
  failed_jobs: number;
  open_messages: number;
  messages_today: number;
  agent_runs_today: number;
  avg_latency_ms: number | null;
  llm_cost_today: Money;
}

export interface InboxItem {
  id: number;
  customer: { id: number; name: string; phone: string };
  channel: string;
  type: string;
  body: string;
  attention: "reply" | "complaint";
  created_at: string;
  handled_at: string | null;
  handled_by: string | null;
}

export interface InboxPage {
  items: InboxItem[];
  open: number;
}

export interface InboxDetail extends InboxItem {
  conversation: { id: number; direction: "in" | "out"; body: string; status: string; kind: string | null; created_at: string }[];
  reply_window_open: boolean;
}
