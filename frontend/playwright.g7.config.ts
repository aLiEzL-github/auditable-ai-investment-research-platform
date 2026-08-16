import { defineConfig } from "@playwright/test";

// G7-01 真实后端 E2E（frontend/e2e/g7/g7-01-real-backend.spec.ts）
// 与 G5 mock 套件（playwright.config.ts）分离：
//   · 起真实 Python 后端（G7_E2E_MODE=1，合成 golden fixture，仅 G7 端点）
//   · Vite 以 g7-e2e mode 构建/预览（VITE_API_MODE=http，绝不使用 MockApi）
//   · preview 的 /api 代理到 127.0.0.1:8080 后端
// 后端必须新鲜启动且带 G7 旗标 —— 不可复用未知进程（复用即可能
// 接到不带 G7 端点的旧进程），故后端 reuseExistingServer=false。

export default defineConfig({
  testDir: "./e2e/g7",
  timeout: 45_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:4173",
    headless: true,
  },
  webServer: [
    {
      command: "python3 app/main.py",
      cwd: "../backend",
      env: { G7_E2E_MODE: "1", APP_PORT: "8080", BIND_HOST: "127.0.0.1" },
      url: "http://127.0.0.1:8080/livez",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "npm run build -- --mode g7-e2e && npm run preview -- --mode g7-e2e --port 4173",
      url: "http://127.0.0.1:4173",
      // 证明必须来自本次全新构建的浏览器→后端 HTTP 流：复用旧 preview
      // 进程会引入未知 mode 的陈旧产物 —— 恒 false（含 CI 与本地）。
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
