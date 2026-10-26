import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./api";

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api", () => {
  beforeEach(() => {
    document.cookie = "ol_csrf=token-123";
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "ol_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("sends the CSRF cookie back as a header on writes", async () => {
    const fetchMock = mockFetch(200, { ok: true });
    await api("/api/orders/1/approve", { method: "POST" });
    const [, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("token-123");
    expect(init.credentials).toBe("same-origin");
  });

  it("does not send the CSRF header on reads and drops empty params", async () => {
    const fetchMock = mockFetch(200, []);
    await api("/api/orders", { params: { status: "pending_review", q: "" } });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
    expect(url.search).toBe("?status=pending_review");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBeUndefined();
  });

  it("turns API errors into readable messages", async () => {
    mockFetch(409, { detail: "order is invoiced, not in review" });
    await expect(api("/api/orders/1/approve", { method: "POST" })).rejects.toEqual(
      new ApiError(409, "order is invoiced, not in review"),
    );
  });
});
