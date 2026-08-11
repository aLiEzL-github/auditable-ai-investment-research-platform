// API 层验收（§3.1 E-2/E-3 的 client 侧形态）：
//  · release_eligible 的结论只来自后端响应，client 无任何计算逻辑
//  · mock 与 http 切换不影响接口形状（mock schema 独立开发）

import { describe, expect, it, vi } from "vitest";
import { MockApi } from "../src/api/mock";
import { HttpApi } from "../src/api/http";
import { createApi } from "../src/api";

describe("MockApi（mock schema 独立开发）", () => {
  it("返回与契约同形状的阻断态（BLOCKED + 原因）", async () => {
    const api = new MockApi();
    const e = await api.getReleaseEligibility();
    expect(e.status).toBe("BLOCKED");
    expect(e.reasons.length).toBeGreaterThan(0);
    expect(e.source).toBe("MOCK");
  });

  it("证据视图含四类对象", async () => {
    const api = new MockApi();
    const v = await api.getEvidenceView();
    expect(v.claims.length).toBeGreaterThan(0);
    expect(v.evidence.length).toBeGreaterThan(0);
    expect(v.facts.length).toBeGreaterThan(0);
    expect(v.openItems.length).toBeGreaterThan(0);
  });
});

describe("HttpApi（后端是唯一控制层 E-1）", () => {
  it("后端拒绝时抛错而非本地伪造结论", async () => {
    const api = new HttpApi("http://127.0.0.1:1");
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 403 } as Response);
    await expect(api.getReleaseEligibility()).rejects.toThrow(/403/);
    vi.restoreAllMocks();
  });

  it("前端绝不自行计算 release_eligible：client 无谓词逻辑（源码级断言）", async () => {
    const src = await import("../src/api/http?raw");
    expect(src.default).not.toMatch(/release_eligible|eligible\s*=|reasons\s*=\s*\[/);
  });
});

describe("createApi（模式切换）", () => {
  it("默认 mock 模式", () => {
    vi.stubEnv("VITE_API_MODE", undefined);
    const api = createApi();
    expect(api).toBeInstanceOf(MockApi);
  });

  it("http 模式", () => {
    vi.stubEnv("VITE_API_MODE", "http");
    const api = createApi();
    expect(api).toBeInstanceOf(HttpApi);
    vi.unstubAllEnvs();
  });
});
