import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { resolve } from "node:path";

const shots = resolve("../.cache/playground-screenshots");
mkdirSync(shots, { recursive: true });
const headers = { "X-Playground": "1", Origin: "http://127.0.0.1:18790" };
const tabs = ["调用试验", "应用权限", "调用记录", "治理审计"];
async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(shots, name + ".png"),
    fullPage: true,
  });
}
async function tab(page: Page, name: string) {
  await page.getByRole("navigation").getByRole("button", { name }).click();
}
async function call(page: Page, status: number) {
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/api/demo/call"),
  );
  await page.getByRole("button", { name: "发送测试请求" }).click();
  expect((await response).status()).toBe(status);
  await expect(page.getByText("HTTP " + status, { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "刷新", exact: true }),
  ).toBeEnabled();
}

test("manual authorization, request-count limit, disable, history and audit", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("combobox", { name: "应用身份" })).toBeVisible();
  await shot(page, "call-empty");
  await tab(page, "调用记录");
  await expect(
    page.getByText("暂无调用记录，先发送一次获准的测试请求。"),
  ).toBeVisible();
  await shot(page, "events-empty");
  await tab(page, "调用试验");
  await call(page, 200);
  await expect(page.getByTestId("call-result")).toContainText("离线演示回复");
  await shot(page, "call-success");
  await page
    .getByRole("combobox", { name: "调用模型" })
    .selectOption("model-b");
  await call(page, 403);
  await shot(page, "call-denied");
  await tab(page, "应用权限");
  const axi = page.locator(".pg-policy").filter({ hasText: "demo-app-a" });
  await axi.getByRole("checkbox", { name: "模型 B", exact: true }).check();
  await axi.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByRole("status")).toContainText("设置已保存");
  await shot(page, "policies-normal");
  await tab(page, "调用试验");
  await call(page, 200);
  await tab(page, "应用权限");
  const knowledge = page
    .locator(".pg-policy")
    .filter({ hasText: "demo-app-b" });
  await knowledge.getByRole("spinbutton", { name: "每分钟调用上限" }).fill("1");
  await knowledge.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByRole("status")).toContainText("设置已保存");
  await tab(page, "调用试验");
  await page
    .getByRole("combobox", { name: "应用身份" })
    .selectOption("demo-app-b");
  // Keep both HTTP calls in one minute without changing internal counters.
  const remaining = 60000 - (Date.now() % 60000);
  if (remaining < 6000) await page.waitForTimeout(remaining + 50);
  await call(page, 200);
  await call(page, 429);
  await shot(page, "call-rate-limit");
  await tab(page, "应用权限");
  await axi.getByRole("checkbox", { name: "启用应用" }).uncheck();
  await axi.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByRole("status")).toContainText("设置已保存");
  await page.reload();
  await expect(
    page.getByRole("button", { name: "发送测试请求" }),
  ).toBeVisible();
  await call(page, 403);
  await tab(page, "调用记录");
  await expect(async () => {
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(page.locator("tbody tr")).toHaveCount(3);
  }).toPass({ timeout: 5000 });
  await shot(page, "events-normal");
  await tab(page, "治理审计");
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await shot(page, "audit-normal");
  const state = await page.request.get("/api/demo/state", { headers });
  expect((await state.json()).upstream_calls).toBe(3);
  expect(errors).toEqual([]);
});

test("empty states for all views", async ({ page }) => {
  await page.route("**/api/demo/state", (route) =>
    route.fulfill({
      json: { apps: [], models: [], audit: [], upstream_calls: 0 },
    }),
  );
  await page.route("**/api/demo/events", (route) =>
    route.fulfill({ json: { events: [] } }),
  );
  await page.goto("/");
  for (const name of tabs) {
    await tab(page, name);
    await expect(page.locator(".pg-empty")).toBeVisible();
    await shot(page, "empty-" + tabs.indexOf(name));
  }
});

test("service failure stays visible on every view", async ({ page }) => {
  await page.route("**/api/demo/state", (route) =>
    route.fulfill({ status: 503, json: { error: "policy_store_unavailable" } }),
  );
  await page.goto("/");
  for (const name of tabs) {
    await tab(page, name);
    await expect(page.getByRole("alert")).toContainText("加载失败");
    await shot(page, "error-" + tabs.indexOf(name));
  }
});

test("unknown metadata, loading and narrow layout", async ({ page }) => {
  let release: () => void = () => {};
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/demo/state", async (route) => {
    await waiting;
    await route.continue();
  });
  await page.route("**/api/demo/events", (route) =>
    route.fulfill({
      json: {
        events: [
          {
            litellm_call_id: "synthetic",
            started_at: null,
            key_alias: null,
            model_alias: "model-a",
            provider_outcome: "unknown",
            total_tokens: null,
            usage_source: "unknown",
            duration_ms: null,
          },
        ],
      },
    }),
  );
  await page.goto("/");
  await expect(page.getByText("正在加载演示环境…")).toBeVisible();
  await shot(page, "loading");
  release();
  await tab(page, "调用记录");
  await expect(page.locator('span[title="未知"]')).toHaveCount(2);
  await shot(page, "events-unknown");
  await page.setViewportSize({ width: 390, height: 844 });
  await tab(page, "调用试验");
  await expect(
    page.getByRole("button", { name: "发送测试请求" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await shot(page, "mobile-call");
});

test("demo credentials are never persisted by the real proxy", async () => {
  const root = resolve("../.cache/playground");
  const runs = readdirSync(root).map((name) => resolve(root, name));
  runs.sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs);
  expect(runs.length).toBeGreaterThan(0);
  expect(existsSync(resolve(runs[0], "demo/proxy/master.key.dpapi"))).toBe(
    false,
  );
});

test("browser transport failure preserves submitted identity and unknown outcome", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("combobox", { name: "应用身份" })).toBeVisible();
  let release: () => void = () => {};
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/demo/call", async (route) => {
    await pending;
    await route.abort("failed");
  });
  await page.route("**/api/demo/state", (route) => route.abort("failed"));
  await page.getByRole("button", { name: "发送测试请求" }).click();
  await expect(page.getByRole("button", { name: "正在调用…" })).toBeVisible();
  await page
    .getByRole("combobox", { name: "应用身份" })
    .selectOption("demo-app-b");
  await page
    .getByRole("combobox", { name: "调用模型" })
    .selectOption("model-b");
  release();
  await expect(page.getByTestId("call-result")).toContainText(
    "调用是否已执行未知",
  );
  await expect(page.getByText("HTTP 503", { exact: true })).toHaveCount(0);
  await expect(page.locator(".pg-result-meta")).toContainText("demo-app-a");
  await expect(page.locator(".pg-result-meta")).toContainText("model-a");
  await shot(page, "call-transport-unknown");
});
