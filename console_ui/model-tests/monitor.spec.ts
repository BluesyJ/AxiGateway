import { test, expect, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
const shots = resolve("../.cache/monitor-screenshots");
mkdirSync(shots, { recursive: true });
const headers = { "X-Playground": "1", Origin: "http://127.0.0.1:18791" };
const profile = {
  name: "监控测试模型",
  model: "model-a",
  base_url: "http://127.0.0.1:18792/v1",
  key: "synthetic-key-a",
  expected_revision: 0,
};
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

test("monitoring empty and audit empty states", async ({ page }) => {
  await page.route("**/api/local/records?*", (route) =>
    route.fulfill({
      json: {
        total: 0,
        success: 0,
        errors: 0,
        page: 1,
        size: 20,
        items: [],
        models: [],
        usage: [],
        catalog: [],
      },
    }),
  );
  await page.route("**/api/local/audit?*", (route) =>
    route.fulfill({ json: { items: [], total: 0, page: 1, size: 20 } }),
  );
  await page.goto("/");
  await nav(page, "调用记录");
  await expect(page.getByText("此筛选范围内暂无调用记录。")).toBeVisible({
    timeout: 5000,
  });
  await shot(page, "records-empty");
  await nav(page, "治理审计");
  await expect(page.getByText("暂无模型配置操作记录。")).toBeVisible();
  await shot(page, "audit-empty");
});

test("conversation usage, persisted detail, model deletion and actual audit", async ({
  page,
}) => {
  const created = await (
    await page.request.post("/api/local/models", { headers, data: profile })
  ).json();
  await page.goto("/");
  await nav(page, "模型对话");
  await page.getByLabel("对话模型").selectOption(created.id);
  await page.getByRole("checkbox", { name: /保存本次对话内容/ }).check();
  await page.getByLabel("消息", { exact: true }).fill("monitor-body-sentinel");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".pg-message.assistant").last()).toContainText(
    "测试回复",
    { timeout: 80000 },
  );
  await expect(page.locator(".monitor-chat-usage")).toContainText("17");
  await expect(
    page.getByRole("checkbox", { name: /保存本次对话内容/ }),
  ).not.toBeChecked();
  await shot(page, "chat-usage");
  await nav(page, "调用记录");
  await page.getByLabel("筛选模型").selectOption(created.id);
  await expect(page.locator(".monitor-metric").first()).toContainText("1");
  await expect(
    page.getByText("本机操作员（未登录）", { exact: true }).last(),
  ).toBeVisible();
  await shot(page, "records-normal");
  await page.getByRole("button", { name: /查看调用详情/ }).click();
  await expect(page.getByRole("dialog")).toContainText("monitor-body-sentinel");
  await expect(page.getByRole("dialog")).not.toContainText("synthetic-key");
  await shot(page, "detail-content");
  await page.getByRole("button", { name: "关闭详情" }).click();
  await page.request.put("/api/local/models/" + created.id, {
    headers,
    data: { ...profile, name: "监控测试模型改名", expected_revision: 1 },
  });
  await page.request.delete("/api/local/models/" + created.id, {
    headers,
    data: { expected_revision: 2 },
  });
  await page.reload();
  await nav(page, "调用记录");
  await page.getByLabel("筛选模型").selectOption(created.id);
  await page.getByRole("button", { name: /查看调用详情/ }).click();
  await expect(page.getByRole("dialog")).toContainText("monitor-body-sentinel");
  await page.getByRole("button", { name: "关闭详情" }).click();
  await nav(page, "治理审计");
  await expect(page.locator(".monitor-card")).toContainText("删除模型");
  await expect(page.locator(".monitor-card")).toContainText("修改模型");
  await shot(page, "audit-normal");
});

test("failed upstream is visible with no invented tokens or raw error", async ({
  page,
}) => {
  const created = await (
    await page.request.post("/api/local/models", {
      headers,
      data: { ...profile, name: "失败测试模型", model: "fails" },
    })
  ).json();
  await page.goto("/");
  await nav(page, "模型对话");
  await page.getByLabel("对话模型").selectOption(created.id);
  await page.getByLabel("消息", { exact: true }).fill("error-body-sentinel");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByRole("alert")).toBeVisible({ timeout: 80000 });
  await nav(page, "调用记录");
  await page.getByLabel("筛选模型").selectOption(created.id);
  await page.getByLabel("调用结果").selectOption("error");
  await page.getByRole("button", { name: /查看调用详情/ }).click();
  await expect(page.getByRole("dialog")).toContainText("本次未开启正文保存");
  await expect(page.getByRole("dialog")).not.toContainText(
    "private-upstream-error-sentinel",
  );
  await expect(page.getByRole("dialog")).not.toContainText(
    "error-body-sentinel",
  );
  await shot(page, "detail-failure");
});

test("monitoring pages loading and failed states", async ({ page }) => {
  await page.goto("/");
  for (const [view, path] of [
    ["调用记录", "records"],
    ["治理审计", "audit"],
  ]) {
    await page.route(`**/api/local/${path}?*`, (route) =>
      route.fulfill({ status: 503, json: { error: "monitor_unavailable" } }),
    );
    await nav(page, view);
    await expect(page.getByRole("alert")).toContainText("记录加载失败");
    await shot(page, path + "-error");
    await page.unroute(`**/api/local/${path}?*`);
    let release: () => void = () => {};
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/local/${path}?*`, async (route) => {
      await wait;
      await route.continue();
    });
    await page.getByRole("button", { name: "重新加载记录" }).click();
    await expect(page.getByRole("status")).toContainText("正在加载记录");
    await shot(page, path + "-loading");
    release();
    await expect(page.getByRole("status")).toHaveCount(0);
    await page.unroute(`**/api/local/${path}?*`);
  }
});

test("filters, pagination and unknown values", async ({ page }) => {
  const row = {
    litellm_call_id: "00000000-0000-0000-0000-000000000001",
    model_id: "local-" + "a".repeat(16),
    name: "分页模型",
    started_at: "2026-11-23T05:00:00Z",
    actor: null,
    result: "unknown",
    provider_outcome: "unknown",
    client_outcome: "unknown",
    usage_source: "missing",
    prompt_tokens: null,
    completion_tokens: null,
    total_tokens: null,
    duration_ms: null,
    end_to_end_ms: null,
  };
  const urls: string[] = [];
  await page.route("**/api/local/records?*", (route) => {
    const url = new URL(route.request().url());
    urls.push(url.search);
    const second = url.searchParams.get("page") === "2";
    return route.fulfill({
      json: {
        total: 21,
        success: 0,
        errors: 0,
        usage: [],
        catalog: [{ id: row.model_id, name: row.name }],
        models: [
          {
            model_id: row.model_id,
            name: row.name,
            requests: 21,
            success: 0,
            errors: 0,
            avg_duration_ms: null,
            usage: [],
          },
        ],
        items: Array.from({ length: second ? 1 : 20 }, (_, i) => ({
          ...row,
          litellm_call_id: row.litellm_call_id + i,
        })),
      },
    });
  });
  await page.goto("/");
  await nav(page, "调用记录");
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("共 21 条 · 第 2 / 2 页")).toBeVisible();
  await page.getByLabel("用量来源").selectOption("missing");
  await expect(page.getByText("共 21 条 · 第 1 / 2 页")).toBeVisible();
  await page.getByLabel("调用人").selectOption("unknown");
  await expect.poll(() => urls.at(-1)).toContain("caller=unknown");
  await expect(page.locator('[title="未知"]').first()).toHaveText("—");
  await shot(page, "records-unknown-page");
});

test("narrow record and audit layouts", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await nav(page, "调用记录");
  await expect(page.locator(".monitor-metric").first()).toBeVisible();
  await shot(page, "records-narrow");
  await page
    .getByRole("button", { name: /查看调用详情/ })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toContainText("调用内容");
  await shot(page, "detail-narrow");
  await page.getByRole("button", { name: "关闭详情" }).click();
  await nav(page, "治理审计");
  await expect(page.getByText("模型配置操作", { exact: true })).toBeVisible();
  await shot(page, "audit-narrow");
});
