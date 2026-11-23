import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
const root = fileURLToPath(new URL("..", import.meta.url));
export default defineConfig({
  testDir: "./playground-tests",
  workers: 1,
  retries: 0,
  timeout: 45000,
  outputDir: resolve(root, ".cache/playground-browser"),
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:18790",
    channel: "msedge",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    serviceWorkers: "block",
  },
  webServer: {
    command: `"${resolve(root, ".venv-litellm/Scripts/python.exe")}" -I -S scripts/start_playground.py --port 18790`,
    cwd: root,
    url: "http://127.0.0.1:18790/",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
