import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// G7-01：按构建模式分离产物目录与 preview 代理。
//   · 默认模式（production / G5 mock E2E）→ dist（VITE_API_MODE 缺省为 mock）
//   · `vite build --mode g7-e2e` → dist-g7-e2e（VITE_API_MODE=http，
//     接真实 Python 后端），避免与 G5 mock 产物互相覆盖。
// dev 恒把 /api 代理到本地后端（127.0.0.1:8080）；preview 的 /api 代理
// 只对 g7-e2e 模式启用 —— G5 mock preview 不携带不必要的后端代理。
export default defineConfig(({ mode }) => {
  const isG7 = mode === "g7-e2e";
  return {
    plugins: [react()],
    build: {
      outDir: isG7 ? "dist-g7-e2e" : "dist",
    },
    server: {
      host: "127.0.0.1",
      proxy: {
        "/api": "http://127.0.0.1:8080",
      },
    },
    preview: {
      host: "127.0.0.1",
      ...(isG7 ? { proxy: { "/api": "http://127.0.0.1:8080" } } : {}),
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./tests/setup.ts"],
      exclude: ["e2e/**", "node_modules/**"],
    },
  };
});
