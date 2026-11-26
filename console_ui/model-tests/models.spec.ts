import { test, expect, type Page } from "@playwright/test";
import { readFileSync, readdirSync, mkdirSync } from "node:fs";
import { resolve, join } from "node:path";
const shots = resolve("../.cache/model-screenshots");
mkdirSync(shots, { recursive: true });
const headers = { "X-Playground": "1", Origin: "http://127.0.0.1:18791" };
const base = "http://127.0.0.1:18792/v1";
async function nav(page: Page, name: string) {
  await page
    .getByRole("navigation")
    .getByRole("button", { name, exact: true })
    .click();
}
async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(shots, name + ".png"),
    fullPage: true,
  });
}
async function add(
  page: Page,
  name: string,
  model: string,
  key = "synthetic-key-a",
) {
  await page.getByRole("button", { name: "添加模型", exact: true }).click();
  await page.getByLabel("显示名称", { exact: true }).fill(name);
  await page.getByLabel("模型 ID", { exact: true }).fill(model);
  await page.getByLabel("API Base URL", { exact: true }).fill(base);
  await page.getByLabel("API Key", { exact: true }).fill(key);
  await page.getByRole("button", { name: "保存模型", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("模型已保存");
}
async function send(page: Page, prompt: string, expected: string) {
  await page.getByLabel("消息", { exact: true }).fill(prompt);
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "发送消息", exact: true }),
  ).toBeVisible({ timeout: 80000 });
  await expect(page.locator(".pg-message.assistant").last()).toContainText(
    expected,
  );
}

test("add models, real proxy multi-turn path and isolated model switching", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "我的模型");
  await expect(
    page.getByText("还没有添加模型。添加后即可开始对话。"),
  ).toBeVisible();
  await shot(page, "models-empty");
  await add(page, "测试模型 A", "model-a");
  await add(page, "测试模型 B", "model-b", "synthetic-key-b");
  await shot(page, "models-normal");
  await nav(page, "模型对话");
  const select = page.getByRole("combobox", { name: "对话模型" });
  await select.selectOption({ label: "测试模型 A · model-a" });
  await send(page, "private-user-body-sentinel", "model-a，收到 1 条消息");
  await send(page, "继续上一轮", "model-a，收到 3 条消息");
  await shot(page, "chat-normal");
  await select.selectOption({ label: "测试模型 B · model-b" });
  await expect(page.locator(".pg-message")).toHaveCount(0);
  await send(page, "模型切换测试", "model-b，收到 1 条消息");
  const catalog = (
    await (await page.request.get("/api/local/models", { headers })).json()
  ).models;
  const changed = catalog.find((m: { model: string }) => m.model === "model-b");
  await page.request.put("/api/local/models/" + changed.id, {
    headers,
    data: {
      name: changed.name,
      model: changed.model,
      base_url: changed.base_url,
      key: "",
      expected_revision: changed.revision,
    },
  });
  await page.getByLabel("消息", { exact: true }).fill("旧版本上下文不得发送");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("本次没有发送消息");
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.locator(".pg-message")).toHaveCount(0);
  await page.reload();
  await nav(page, "我的模型");
  await expect(page.locator(".pg-model-card")).toHaveCount(2);
  const response = await page.request.get("/api/local/models", { headers });
  expect(await response.text()).not.toContain("synthetic-key");
  const counts = await page.request.get("http://127.0.0.1:18792/__stats");
  expect(await counts.json()).toEqual({ calls: 3, credential_mismatches: 0 });
});

test("edit retains key, changed destination rejects reuse and deletion updates list", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "我的模型");
  const card = page.locator(".pg-model-card").filter({ hasText: "测试模型 B" });
  await card.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByLabel("API Key", { exact: true })).toHaveValue("");
  await page
    .getByLabel("API Base URL", { exact: true })
    .fill("http://127.0.0.1:18793/v1");
  await page.getByRole("button", { name: "保存模型", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "旧 Key 不会发送到新地址",
  );
  await shot(page, "models-error");
  await page.getByLabel("API Base URL", { exact: true }).fill(base);
  await page.getByLabel("显示名称", { exact: true }).fill("改名模型 B");
  await page.getByRole("button", { name: "保存模型", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("模型已保存");
  const changed = page
    .locator(".pg-model-card")
    .filter({ hasText: "改名模型 B" });
  await changed.getByRole("button", { name: "删除", exact: true }).click();
  await changed.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.locator(".pg-model-card")).toHaveCount(1);
});

test("upstream errors remain errors without retries or private diagnostics", async ({
  page,
}) => {
  const before = (
    await (await page.request.get("http://127.0.0.1:18792/__stats")).json()
  ).calls;
  await page.goto("/");
  await nav(page, "我的模型");
  await add(page, "失败测试", "fails");
  await nav(page, "模型对话");
  await page.getByLabel("消息", { exact: true }).fill("failure-body-sentinel");
  const pending = page.waitForResponse((r) =>
    r.url().endsWith("/api/local/chat"),
  );
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const response = await pending;
  expect(response.status()).toBe(500);
  expect(await response.text()).not.toContain(
    "private-upstream-error-sentinel",
  );
  await expect(page.getByRole("alert")).toContainText("模型服务返回错误");
  await expect(page.locator(".pg-message.assistant")).toHaveCount(0);
  await expect(page.getByLabel("消息", { exact: true })).toHaveValue(
    "failure-body-sentinel",
  );
  await shot(page, "chat-error");
  expect(
    await (await page.request.get("http://127.0.0.1:18792/__stats")).json(),
  ).toEqual({ calls: before + 1, credential_mismatches: 0 });
});

test("empty, loading, service error and narrow states", async ({ page }) => {
  let release: () => void = () => {};
  const ready = new Promise<void>((r) => {
    release = r;
  });
  await page.route("**/api/local/models", async (route) => {
    await ready;
    await route.fulfill({ json: { models: [] } });
  });
  await page.goto("/");
  await nav(page, "我的模型");
  await expect(page.getByText("正在读取本机模型…")).toBeVisible();
  await shot(page, "models-loading");
  release();
  await nav(page, "模型对话");
  await expect(
    page.getByText("请先在「我的模型」中添加一个模型。"),
  ).toBeVisible();
  await shot(page, "chat-empty");
  await page.setViewportSize({ width: 390, height: 844 });
  await shot(page, "chat-mobile");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.unroute("**/api/local/models");
  await page.route("**/api/local/models", (route) =>
    route.fulfill({ status: 503, json: { error: "model_store_unavailable" } }),
  );
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("模型配置无法读取或保存");
  await shot(page, "chat-store-error");
});

test("browser connection failure never invents a model reply", async ({
  page,
}) => {
  await page.goto("/");
  await nav(page, "模型对话");
  await expect(page.getByRole("combobox", { name: "对话模型" })).toHaveValue(
    /local-/,
  );
  await page.route("**/api/local/chat", (route) => route.abort("failed"));
  await page
    .getByLabel("消息", { exact: true })
    .fill("transport-failure-sentinel");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("操作是否已执行未知");
  await expect(page.locator(".pg-message.assistant")).toHaveCount(0);
});

test("synthetic vault, proxy configuration and events contain no plaintext credentials or bodies", async ({
  request,
}) => {
  const { directory } = JSON.parse(
    readFileSync(resolve("../.cache/local-model-browser/current.json"), "utf8"),
  );
  expect(directory.startsWith(resolve("../.cache/local-model-browser"))).toBe(
    true,
  );
  function files(path: string): string[] {
    return readdirSync(path, { withFileTypes: true }).flatMap((item) =>
      item.isDirectory()
        ? files(join(path, item.name))
        : [join(path, item.name)],
    );
  }
  for (const path of files(directory)) {
    const bytes = readFileSync(path);
    for (const sentinel of [
      "synthetic-key-a",
      "synthetic-key-b",
      "private-user-body-sentinel",
      "failure-body-sentinel",
      "private-upstream-error-sentinel",
    ])
      expect(bytes.includes(Buffer.from(sentinel)), path).toBe(false);
  }
  const response = await request.post("/api/local/models", {
    headers,
    data: {
      name: "仅保存不连接",
      base_url: "https://models.example/v1",
      model: "model",
      key: "synthetic-no-network",
      expected_revision: 0,
    },
  });
  const data = await response.json();
  expect(response.status()).toBe(201);
  const denied = await request.post("/api/local/chat", {
    headers,
    data: {
      model: data.id,
      revision: data.revision,
      messages: [{ role: "user", content: "do-not-connect" }],
      max_tokens: 1,
    },
  });
  expect(denied.status()).toBe(400);
  expect((await denied.json()).error).toBe(
    "offline_requires_loopback_upstream",
  );
});
