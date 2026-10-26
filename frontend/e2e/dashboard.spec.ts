import { expect, test } from "@playwright/test";

import { mockApi, PASSWORD } from "./mock-api.ts";

test("staff sign in, review a held order and approve it", async ({ page }) => {
  const api = await mockApi(page, { signedIn: false });
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Sign in to OrderLane" })).toBeVisible();
  await page.getByLabel("Email").fill("staff@orderlane.test");
  await page.getByLabel("Password").fill("wrong password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("Wrong email or password");

  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();

  await page.getByText("Bismillah Store").first().click();
  await expect(page.getByText("2 ctn tapal 950").first()).toBeVisible();
  await page.getByRole("button", { name: "Approve and send" }).click();

  await expect(page.getByText("Order #1007 approved")).toBeVisible();
  expect(api.requests).toContainEqual(expect.objectContaining({ method: "POST", path: "/api/orders/7/approve" }));
});

test("signed-out visitors are sent to the sign-in page", async ({ page }) => {
  await mockApi(page, { signedIn: false });
  await page.goto("/metrics");
  await expect(page.getByRole("heading", { name: "Sign in to OrderLane" })).toBeVisible();
});

test("the chat simulator sends messages and confirmation buttons", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/simulator");

  await page.getByRole("button", { name: "1 dozen ariel" }).click();
  await expect(page.getByLabel("Message")).toHaveValue("1 dozen ariel");
  await page.getByLabel("Message").fill("2 ctn tapal 950");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByText("Order #1008")).toBeVisible();
  await page.getByRole("button", { name: "Confirm", exact: true }).click();

  await expect
    .poll(() => api.requests.filter((r) => r.path === "/api/simulator/messages").map((r) => r.body))
    .toEqual([
      { phone: "923001112233", text: "2 ctn tapal 950" },
      { phone: "923001112233", button_id: "confirm:8", button_title: "Confirm" },
    ]);
});

test("the metrics page shows held-out results and live numbers", async ({ page }) => {
  await mockApi(page);
  await page.goto("/metrics");

  await expect(page.getByRole("heading", { name: "Metrics", level: 1 })).toBeVisible();
  await expect(page.getByText("93 cases")).toBeVisible();
  await expect(page.getByText("98.6%")).toBeVisible(); // held-out auto-confirm precision
  await expect(page.getByText("420 ms")).toBeVisible();

  await page.getByLabel("Dataset").selectOption({ label: "Development set" });
  await expect(page.getByText("30 cases")).toBeVisible();
});
