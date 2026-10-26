import type { OrderStatus } from "./types";

const money = new Intl.NumberFormat("en-PK", { maximumFractionDigits: 0 });

export function formatMoney(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "-";
  return `Rs ${money.format(Number(value))}`;
}

/** "2.00" -> "2", "0.50" -> "0.5" */
export function formatQty(value: string | number): string {
  return String(Number(value));
}

export function formatDateTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function timeAgo(iso: string, now: Date = new Date()): string {
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export const STATUS_LABEL: Record<OrderStatus, string> = {
  draft: "Draft",
  pending_review: "Needs review",
  awaiting_customer: "Waiting for customer",
  confirmed: "Confirmed",
  invoiced: "Invoiced",
  cancelled: "Cancelled",
};

export const STATUS_TONE: Record<OrderStatus, "amber" | "blue" | "green" | "gray" | "red"> = {
  draft: "gray",
  pending_review: "amber",
  awaiting_customer: "blue",
  confirmed: "green",
  invoiced: "green",
  cancelled: "red",
};

const ISSUE_LABEL: Record<string, string> = {
  first_order: "First order",
  over_credit_limit: "Over credit limit",
  large_order: "Large order",
  low_confidence: "Unclear message",
  llm_unavailable: "AI unavailable",
  out_of_stock: "Out of stock",
  out_of_stock_at_confirm: "Stock ran out",
  no_price: "Missing price",
  ambiguous_product: "Product unclear",
  unclear_quantity: "Quantity unclear",
  unit_assumed: "Unit assumed",
  unit_not_sold: "Unit not sold",
  no_match: "Product not found",
};

export function issueLabel(code: string): string {
  return ISSUE_LABEL[code] ?? code.replaceAll("_", " ");
}

export function humanize(value: string): string {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
