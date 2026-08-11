// API 工厂 —— 按运行模式选择实现（mock schema 独立开发 vs 真实后端）
// VITE_API_MODE=mock（默认，独立开发）| http（接真实后端）

import type { WorkbenchApi } from "./client";
import { HttpApi } from "./http";
import { MockApi } from "./mock";

export function createApi(): WorkbenchApi {
  const mode = import.meta.env.VITE_API_MODE ?? "mock";
  if (mode === "http") {
    const base = import.meta.env.VITE_API_BASE ?? "";
    return new HttpApi(base);
  }
  return new MockApi();
}
