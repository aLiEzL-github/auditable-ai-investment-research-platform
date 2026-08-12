import { defineConfig } from "@playwright/test";

// G5-07 UI 端到端纵向验收
// 固定 fixture = mock schema（VITE_API_MODE=mock，G5-01 验收「可使用 mock schema
// 独立开发」），vite preview 提供产物。浏览器用例覆盖基线 §8 G5-07 原文：
// 从合同、规则、Claim/假设、预测、开放项、闭包到不可变发布的浏览器用例。

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:4173",
    headless: true,
  },
  webServer: {
    command: "npm run build && npm run preview",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
