import { test, expect, type Page, type Locator } from "@playwright/test";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { titles, type View } from "../src/data";

type Row = Record<string, any>;
const directory = process.env.P31_RUN_DIR!;
const base = "http://127.0.0.1:18789";
const views = Object.keys(titles) as View[];
const sourceNames = ["reported", "derived", "estimated", "missing", "unknown"];
const fmt = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 6 });
const checks = join(directory, "checks");
const shots = join(directory, "screenshots");
let fixture: { now: string; rows: Row[]; costs: Row[]; counters: Row };
const failures = new Map<
  string,
  { pageErrors: number; blocked: number; methods: number }
>();
const display = (v: any) =>
  v == null ? "—" : typeof v === "number" ? fmt.format(v) : String(v);
const mode = (value: string) =>
  writeFileSync(join(directory, "mode.txt"), value);
const panel = (page: Page, name: string) =>
  page.locator(".panel").filter({
    has: page.getByRole("heading", { level: 2, name, exact: true }),
  });
const select = (page: Page, name: string) =>
  page.getByRole("combobox", { name, exact: true });

function grouped(rows: Row[], keys: string[]) {
  const groups = new Map<string, Row>();
  for (const row of rows) {
    const key = JSON.stringify(keys.map((k) => row[k]));
    const group = groups.get(key) ?? {
      ...Object.fromEntries(keys.map((k) => [k, row[k]])),
      requests: 0,
      known: 0,
      tokens: null,
    };
    group.requests++;
    if (row.total_tokens != null) {
      group.known++;
      group.tokens = (group.tokens ?? 0) + row.total_tokens;
    }
    groups.set(key, group);
  }
  return [...groups.values()];
}
const trendRows = (rows: Row[]) =>
  grouped(
    rows.map((r) => ({ ...r, hour: r.started_at.slice(0, 13) + ":00:00Z" })),
    ["hour", "usage_source"],
  );
const sortRows = (rows: Row[]) =>
  rows
    .map((r) =>
      JSON.stringify(
        Object.fromEntries(
          Object.entries(r).sort(([a], [b]) => a.localeCompare(b)),
        ),
      ),
    )
    .sort();

async function open(page: Page, view: View, query = "") {
  const pending = page.waitForResponse(
    (r) => new URL(r.url()).pathname === `/api/demo/${view}`,
  );
  await page.goto(`/?source=demo&view=${view}${query}`);
  const response = await pending;
  // The UI rejects non-2xx responses without consuming their body. Inspect the
  // status and rendered error instead of waiting on Chromium's unread body.
  return {
    response,
    data: response.ok() ? ((await response.json()) as Row) : {},
  };
}
async function action(page: Page, view: View, run: () => Promise<unknown>) {
  const pending = page.waitForResponse(
    (r) => new URL(r.url()).pathname === `/api/demo/${view}`,
  );
  await run();
  const response = await pending;
  expect(response.status()).toBe(200);
  await expect(page.locator(".content")).toHaveAttribute(
    "data-state",
    "normal",
  );
  return (await response.json()) as Row;
}
async function assertRequestRows(page: Page, data: Row, expected: Row[]) {
  expect(data.total).toBe(expected.length);
  const slice = expected.slice(
    (data.page - 1) * data.size,
    data.page * data.size,
  );
  expect(data.items.map((r: Row) => r.litellm_call_id)).toEqual(
    slice.map((r) => r.litellm_call_id),
  );
  await expect(page.locator("tbody tr")).toHaveCount(slice.length);
  await expect(panel(page, "请求记录").locator(".muted")).toHaveText(
    `${expected.length} 条`,
  );
  await expect(page.locator(".pager > div > span")).toHaveText(
    `${data.page} / ${Math.max(1, Math.ceil(expected.length / 15))}`,
  );
  for (const [index, row] of slice.entries()) {
    const cells = page.locator("tbody tr").nth(index).locator("td");
    await expect(cells.nth(1)).toContainText(row.model_alias);
    await expect(cells.nth(5).locator(".badge")).toHaveAttribute(
      "title",
      row.usage_source,
    );
    await expect(cells.nth(6)).toHaveText(display(row.total_tokens));
    if (row.total_tokens == null)
      await expect(cells.nth(6).getByLabel("未知", { exact: true })).toHaveText(
        "—",
      );
    else await expect(cells.nth(6).locator('[title="未知"]')).toHaveCount(0);
    if (row.client_outcome === "client_disconnected") {
      await expect(cells.nth(4).locator(".badge")).toHaveAttribute(
        "title",
        "client_disconnected",
      );
      await expect(cells.nth(4)).toContainText("客户端断开");
      await expect(cells.nth(4)).not.toContainText("成功");
      await expect(cells.nth(5).locator(".badge")).toHaveAttribute(
        "title",
        "estimated",
      );
    }
  }
}
async function assertView(
  page: Page,
  view: View,
  data: Row,
  rows = fixture.rows,
) {
  expect(data.view).toBe(view);
  if (view === "overview") {
    expect([data.requests, data.success, data.errors]).toEqual([
      rows.length,
      rows.filter((r) => r.provider_outcome === "success").length,
      rows.filter((r) => r.provider_outcome === "error").length,
    ]);
    for (const [i, value] of [
      data.requests,
      data.success,
      data.errors,
      1,
    ].entries())
      await expect(page.locator(".metric strong").nth(i)).toHaveText(
        display(value),
      );
    await expect(page.locator(".metric").nth(1)).toContainText("供应商成功");
    await expect(page.locator(".metric").nth(2)).toContainText("供应商失败");
    const groups = grouped(rows, ["usage_source"]);
    expect(data.usage).toHaveLength(5);
    for (const s of sourceNames) {
      const expected = groups.find((g) => g.usage_source === s) ?? {
        usage_source: s,
        requests: 0,
        known: 0,
        tokens: null,
      };
      expect(data.usage.find((g: Row) => g.usage_source === s)).toEqual(
        expected,
      );
      const card = page
        .locator(".token-grid > div")
        .filter({ has: page.getByText(s, { exact: true }) });
      await expect(card).toHaveCount(1);
      await expect(card.locator("strong")).toHaveText(display(expected.tokens));
      await expect(card.locator(".coverage")).toHaveText(
        `${expected.known} / ${expected.requests}`,
      );
    }
    if (rows.length === fixture.rows.length) {
      const costs = panel(page, "费用估算（非账单）");
      await expect(costs.locator(".muted")).toHaveText(
        "按币种、来源和价目版本",
      );
      await expect(costs.locator("tbody tr")).toHaveCount(fixture.costs.length);
      const actual = await costs
        .locator("tbody tr")
        .evaluateAll((nodes) =>
          nodes.map((n) =>
            [...n.querySelectorAll("td")].map((c) => c.textContent?.trim()),
          ),
        );
      for (const c of fixture.costs)
        expect(actual).toContainEqual([
          display(c.cost_currency),
          display(c.cost_source),
          display(c.price_version),
          display(c.amount),
          `${c.known} / ${c.requests}`,
        ]);
      expect(data.costs).toHaveLength(fixture.costs.length);
      for (const expected of fixture.costs) {
        const found = data.costs.find((c: Row) =>
          ["cost_currency", "cost_source", "price_version"].every(
            (k) => c[k] === expected[k],
          ),
        );
        expect(found).toBeDefined();
        expect([found.known, found.requests]).toEqual([
          expected.known,
          expected.requests,
        ]);
        if (expected.amount == null) expect(found.amount).toBeNull();
        else expect(found.amount).toBeCloseTo(expected.amount, 8);
      }
    }
    expect(sortRows(data.trend)).toEqual(sortRows(trendRows(rows)));
  } else if (view === "trend") {
    expect(sortRows(data.items)).toEqual(sortRows(trendRows(rows)));
    const chart = page.getByLabel("请求数时间趋势", { exact: true });
    // A horizontal SVG path has a zero-height geometry box despite its stroke.
    // Assert the stroke, visible data point, and exact hover value together.
    await expect(chart.locator(".recharts-line-curve")).toHaveAttribute(
      "stroke-width",
      "1.5",
    );
    await expect(chart.locator(".recharts-line-dot").nth(0)).toBeVisible();
    const firstHour = [
      ...new Set(rows.map((r) => r.started_at.slice(0, 13))),
    ].sort()[0];
    const count = rows.filter((r) => r.started_at.startsWith(firstHour)).length;
    await chart.locator(".recharts-line-dot").nth(0).hover();
    await expect(chart.locator(".tip b")).toHaveText(String(count));
  } else if (view === "requests") {
    await assertRequestRows(page, data, rows);
  } else if (view === "models" || view === "clients") {
    const keys =
      view === "models"
        ? ["model_alias", "usage_source"]
        : ["client_header", "client_metadata", "usage_source"];
    const groups = grouped(rows, keys);
    expect(sortRows(data.items)).toEqual(sortRows(groups));
    await expect(page.locator("tbody tr")).toHaveCount(groups.length);
    if (view === "clients") {
      await expect(
        page.getByRole("columnheader", { name: "Header 标签", exact: true }),
      ).toBeVisible();
      await expect(
        page.getByRole("columnheader", { name: "Metadata 标签", exact: true }),
      ).toBeVisible();
    }
  } else {
    expect(data.counters).toEqual(fixture.counters);
    expect([
      data.lost,
      data.gap_events,
      data.gap_windows,
      data.queue_depth,
    ]).toEqual([6, 4, 1, null]);
    await expect(
      page.locator(".metric").nth(3).getByLabel("未知", { exact: true }),
    ).toHaveText("—");
    const rejects = page
      .locator("tr")
      .filter({ has: page.getByText("auth_rejected", { exact: true }) });
    await expect(rejects.locator("td").nth(2)).toHaveText("3");
  }
}

test.beforeAll(async ({ browser }) => {
  mkdirSync(checks, { recursive: true });
  mkdirSync(shots, { recursive: true });
  fixture = JSON.parse(readFileSync(join(directory, "fixture.json"), "utf8"));
  const listeners = execFileSync(
    "powershell.exe",
    [
      "-NoProfile",
      "-Command",
      "(Get-NetTCPConnection -State Listen -LocalPort 18789).LocalAddress",
    ],
    { encoding: "utf8" },
  )
    .trim()
    .split(/\s+/);
  expect(listeners).toEqual(["127.0.0.1"]);
  writeFileSync(
    join(directory, "browser-environment.json"),
    JSON.stringify({
      browser: "Microsoft Edge / Chromium",
      version: browser.version(),
      desktop: [1440, 1000],
      narrow: [390, 844],
      timezone: "Asia/Shanghai",
      real_mobile_device: false,
      listeners,
    }),
  );
});
test.beforeEach(async ({ page }, info) => {
  mode("normal");
  const counts = { pageErrors: 0, blocked: 0, methods: 0 };
  failures.set(info.title, counts);
  page.on("pageerror", () => counts.pageErrors++);
  await page.clock.setFixedTime(new Date(fixture.now));
  await page.route("**/*", (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (request.method() !== "GET") counts.methods++;
    if (
      url.origin !== base ||
      url.pathname.startsWith("/v1/") ||
      (url.pathname.startsWith("/api/") &&
        !url.pathname.startsWith("/api/demo/"))
    ) {
      counts.blocked++;
      return route.abort();
    }
    return route.continue();
  });
});
test.afterEach(async ({ page }, info) => {
  const counts = failures.get(info.title)!;
  expect(counts).toEqual({ pageErrors: 0, blocked: 0, methods: 0 });
  const hashes = JSON.parse(
    readFileSync(join(directory, "database-sha256.json"), "utf8"),
  );
  for (const [name, hash] of Object.entries(hashes))
    expect(
      createHash("sha256")
        .update(readFileSync(join(directory, name)))
        .digest("hex"),
    ).toBe(hash);
  const isolation = JSON.parse(
    readFileSync(join(directory, "isolation.json"), "utf8"),
  );
  expect(isolation.forbidden_database_connections).toBe(0);
  expect(isolation.live_source_requests).toBe(0);
  expect(isolation.model_requests).toBe(0);
  writeFileSync(
    join(checks, info.title.replaceAll(" ", "_") + ".json"),
    JSON.stringify({
      case: info.title,
      passed: info.status === "passed",
      ...counts,
      database_unchanged: true,
    }),
  );
});

for (const state of ["normal", "empty", "error"] as const)
  for (const view of views) {
    test(`desktop ${view} ${state}`, async ({ page }) => {
      mode(state);
      const { response, data } = await open(page, view);
      expect(response.status()).toBe(state === "error" ? 503 : 200);
      expect(response.headers()["content-security-policy"]).toContain(
        "connect-src 'self'",
      );
      expect(response.headers()["access-control-allow-origin"]).toBeUndefined();
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(
        titles[view],
      );
      await expect(page.locator("section.content")).toHaveAttribute(
        "data-state",
        state,
      );
      expect(
        await page
          .locator("html")
          .evaluate((el) => getComputedStyle(el).backgroundColor),
      ).toBe("rgb(11, 15, 20)");
      await expect(select(page, "数据源")).toHaveValue("demo");
      await expect(select(page, "数据源").locator("option:checked")).toHaveText(
        "演示库",
      );
      await expect(page.locator("footer")).toContainText("demo-events.sqlite3");
      if (state === "normal") await assertView(page, view, data);
      if (state === "empty") {
        expect(data.empty).toBe(true);
        await expect(page.locator(".state p")).toContainText(
          view === "health" ? "尚无采集计数" : "没有请求记录",
        );
        await expect(page.locator("tbody tr")).toHaveCount(0);
        if (view === "overview") {
          await expect(page.locator(".metric strong").nth(0)).toHaveText("0");
          await expect(
            page.locator('.token-grid strong [title="未知"]'),
          ).toHaveCount(5);
        }
      }
      if (state === "error") {
        await expect(
          page.getByText("无法读取事件库，请检查所选库后刷新。", {
            exact: true,
          }),
        ).toBeVisible();
        await expect(
          page.getByRole("button", { name: "重试", exact: true }),
        ).toBeVisible();
        await expect(page.locator("tbody tr")).toHaveCount(0);
      }
      await page.screenshot({
        path: join(shots, `${view}-${state}.png`),
        fullPage: true,
        animations: "disabled",
      });
      if (state === "error") {
        mode("normal");
        await action(page, view, () =>
          page.getByRole("button", { name: "重试", exact: true }).click(),
        );
      }
    });
  }
for (const view of views)
  test(`loading ${view}`, async ({ page }) => {
    let release!: () => void;
    const wait = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route(`**/api/demo/${view}*`, async (route) => {
      await wait;
      await route.fallback();
    });
    await page.goto(`/?source=demo&view=${view}`);
    try {
      await expect(page.locator(".content")).toHaveAttribute(
        "data-state",
        "loading",
      );
      await expect(
        page.getByText("正在读取事件库…", { exact: true }),
      ).toBeVisible();
    } finally {
      release();
    }
    await expect(page.locator(".content")).toHaveAttribute(
      "data-state",
      "normal",
    );
  });

test("filters pagination refresh null and outcome semantics", async ({
  page,
}) => {
  let { data } = await open(page, "requests");
  await assertRequestRows(page, data, fixture.rows);
  await expect(page.locator("tbody time").nth(0)).toContainText("15:40");
  const tokenCells = await page
    .locator("tbody tr td:nth-child(7)")
    .allTextContents();
  expect(tokenCells).toContain("0");
  expect(tokenCells).toContain("—");
  const next = await action(page, "requests", () =>
    page.getByRole("button", { name: "下一页", exact: true }).click(),
  );
  expect(next.page).toBe(2);
  await assertRequestRows(page, next, fixture.rows);
  data = await action(page, "requests", () => page.reload());
  expect(data.items.map((r: Row) => r.litellm_call_id)).toEqual(
    next.items.map((r: Row) => r.litellm_call_id),
  );
  expect(data.page).toBe(2);
  await expect(select(page, "客户端结果")).toHaveCount(1);
  await select(page, "客户端结果").selectOption("client_disconnected");
  data = await action(page, "requests", () =>
    page.getByRole("button", { name: "筛选", exact: true }).click(),
  );
  const disconnected = fixture.rows.filter(
    (r) => r.client_outcome === "client_disconnected",
  );
  expect(data.page).toBe(1);
  await assertRequestRows(page, data, disconnected);
  data = await action(page, "requests", () =>
    page.getByRole("button", { name: "下一页", exact: true }).click(),
  );
  expect(data.page).toBe(2);
  data = await action(page, "requests", () => page.reload());
  expect(data.page).toBe(2);
  await expect(select(page, "客户端结果")).toHaveValue("client_disconnected");
  await assertRequestRows(page, data, disconnected);
  await page
    .getByRole("textbox", { name: "模型", exact: true })
    .fill("chat-reasoner");
  await page
    .getByRole("textbox", { name: "客户端", exact: true })
    .fill("team-b");
  await select(page, "供应商结果").selectOption("success");
  await select(page, "用量来源").selectOption("estimated");
  const combined = disconnected.filter(
    (r) =>
      r.model_alias === "chat-reasoner" &&
      (r.client_header === "team-b" || r.client_metadata === "team-b") &&
      r.provider_outcome === "success" &&
      r.usage_source === "estimated",
  );
  expect(combined.length).toBeGreaterThan(0);
  data = await action(page, "requests", () =>
    page.getByRole("button", { name: "筛选", exact: true }).click(),
  );
  expect(data.page).toBe(1);
  await assertRequestRows(page, data, combined);
  await expect(
    page.getByRole("button", { name: "下一页", exact: true }),
  ).toBeDisabled();
  data = await action(page, "requests", () => page.reload());
  await assertRequestRows(page, data, combined);
  await expect(
    page.getByRole("textbox", { name: "模型", exact: true }),
  ).toHaveValue("chat-reasoner");
  data = await action(page, "requests", () =>
    page.getByRole("button", { name: "重置", exact: true }).click(),
  );
  expect(data.page).toBe(1);
  await assertRequestRows(page, data, fixture.rows);
  expect(new URL(page.url()).searchParams.has("client_outcome")).toBe(false);
});

test("same time window across views and source specific chart values", async ({
  page,
}) => {
  const expected = fixture.rows.filter(
    (r) =>
      Date.parse(r.started_at) >= Date.parse(fixture.now) - 86400000 &&
      Date.parse(r.started_at) < Date.parse(fixture.now),
  );
  const totals: number[] = [];
  for (const view of ["overview", "trend", "requests"] as const) {
    const { response, data } = await open(page, view, "&range=24");
    expect(response.status()).toBe(200);
    await expect(select(page, "时间范围")).toHaveValue("24");
    await assertView(page, view, data, expected);
    totals.push(
      view === "overview"
        ? data.requests
        : view === "requests"
          ? data.total
          : data.items.reduce((n: number, r: Row) => n + r.requests, 0),
    );
  }
  expect(totals).toEqual([expected.length, expected.length, expected.length]);
  await open(page, "trend");
  for (const source of sourceNames) {
    // Move to the select as a user would; selectOption alone leaves the pointer
    // over the previous point and its active-dot overlay intercepts the hover.
    await select(page, "趋势用量来源").hover();
    await select(page, "趋势用量来源").selectOption(source);
    const expected = trendRows(fixture.rows)
      .filter((r) => r.usage_source === source && r.tokens !== null)
      .sort((a, b) => a.hour.localeCompare(b.hour));
    const tokenPanel = panel(page, "Token 趋势");
    if (!expected.length)
      await expect(tokenPanel.locator(".chart-empty")).toBeVisible();
    else {
      const plot = tokenPanel.getByLabel("Token 时间趋势", { exact: true });
      await plot.locator(".recharts-line-dot").nth(0).hover();
      await expect(plot.locator(".tip b")).toHaveText(
        display(expected[0].tokens),
      );
    }
  }
});

for (const view of views)
  test(`narrow ${view}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page, "overview");
    if (view === "overview")
      await action(page, "trend", () =>
        page
          .getByRole("navigation", { name: "页面", exact: true })
          .getByRole("button", { name: titles.trend, exact: true })
          .click(),
      );
    const navigate = page
      .getByRole("navigation", { name: "页面", exact: true })
      .getByRole("button", { name: titles[view], exact: true });
    const data = await action(page, view, () => navigate.click());
    await expect(navigate).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      titles[view],
    );
    await assertView(page, view, data);
    const layout = await page.evaluate(() => ({
      width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      outside: [...document.querySelectorAll("body *")]
        .map((el) => ({
          tag: el.tagName,
          className: String(el.className),
          position: getComputedStyle(el).position,
          left: el.getBoundingClientRect().left,
          right: el.getBoundingClientRect().right,
        }))
        .filter((el) => el.left < 0 || el.right > innerWidth),
    }));
    writeFileSync(
      join(directory, `layout-${view}.json`),
      JSON.stringify(layout),
    );
    expect(layout.scrollWidth).toBeLessThanOrEqual(layout.width);
    await expect(select(page, "数据源")).toHaveValue("demo");
    for (const control of await page
      .locator(
        ".toolbar button, .toolbar select, .filters button, .filters input, .filters select, .pager button",
      )
      .all()) {
      await control.scrollIntoViewIfNeeded();
      const box = await control.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(391);
    }
    for (const scroll of await page.locator(".scroll").all()) {
      const result = await scroll.evaluate((el) => {
        const html = el as HTMLElement;
        html.scrollLeft = html.scrollWidth;
        return {
          scrollable: html.scrollWidth > html.clientWidth,
          left: html.scrollLeft,
          overflow: getComputedStyle(html).overflowX,
        };
      });
      if (result.scrollable) {
        expect(result.left).toBeGreaterThan(0);
        expect(result.overflow).toBe("auto");
      }
      await scroll.evaluate((el) => {
        el.scrollLeft = 0;
      });
    }
    if (view === "requests") {
      await action(page, view, () =>
        page.getByRole("button", { name: "下一页", exact: true }).click(),
      );
      await select(page, "客户端结果").selectOption("client_disconnected");
      const filtered = await action(page, view, () =>
        page.getByRole("button", { name: "筛选", exact: true }).click(),
      );
      expect(filtered.page).toBe(1);
      await assertRequestRows(
        page,
        filtered,
        fixture.rows.filter((r) => r.client_outcome === "client_disconnected"),
      );
      const outcome = page
        .locator("tbody tr")
        .nth(0)
        .locator('.badge[title="client_disconnected"]');
      const source = page
        .locator("tbody tr")
        .nth(0)
        .locator('.badge[title="estimated"]');
      await outcome.scrollIntoViewIfNeeded();
      await source.scrollIntoViewIfNeeded();
      await expect(outcome).toBeInViewport({ ratio: 1 });
      await expect(source).toBeInViewport({ ratio: 1 });
    }
    if (view === "trend") {
      await select(page, "趋势用量来源").selectOption("estimated");
      await expect(
        panel(page, "Token 趋势").locator(".recharts-line-curve"),
      ).toBeVisible();
    }
    await page.screenshot({
      path: join(shots, `${view}-narrow.png`),
      fullPage: true,
      animations: "disabled",
    });
  });
