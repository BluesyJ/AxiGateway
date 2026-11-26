import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
const root = fileURLToPath(new URL("..", import.meta.url));
export default defineConfig({
  testDir: "./model-tests",
  workers: 1,
  retries: 0,
  timeout: 90000,
  outputDir: resolve(root, ".cache/model-browser-results"),
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:18791",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    serviceWorkers: "block",
  },
  webServer: {
    command: `"${resolve(root, ".venv-litellm/Scripts/python.exe")}" -I -S tests_p7/serve_models.py`,
    cwd: root,
    url: "http://127.0.0.1:18791/",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
