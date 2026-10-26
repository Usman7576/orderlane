import { describe, expect, it } from "vitest";

import { formatMoney, formatQty, issueLabel, timeAgo } from "./format";

describe("format", () => {
  it("formats rupees without decimals", () => {
    expect(formatMoney("21103.00")).toBe("Rs 21,103");
    expect(formatMoney(null)).toBe("-");
  });

  it("trims trailing zeros from quantities", () => {
    expect(formatQty("2.00")).toBe("2");
    expect(formatQty("0.50")).toBe("0.5");
  });

  it("describes elapsed time", () => {
    const now = new Date("2026-10-26T12:00:00Z");
    expect(timeAgo("2026-10-26T11:59:40Z", now)).toBe("just now");
    expect(timeAgo("2026-10-26T11:30:00Z", now)).toBe("30 min ago");
    expect(timeAgo("2026-10-24T12:00:00Z", now)).toBe("2 d ago");
  });

  it("labels review issues for staff", () => {
    expect(issueLabel("over_credit_limit")).toBe("Over credit limit");
    expect(issueLabel("something_new")).toBe("something new");
  });
});
