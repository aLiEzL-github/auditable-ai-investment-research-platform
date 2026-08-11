// 真实后端实现 —— 只做传输，不做判定（E-1/E-2/E-3）。
// 后端拒绝即透传错误，前端绝不自行构造 BLOCKED/CLEAR 结论。

import type { ReleaseEligibility, ReleaseRecord } from "../types";
import type { EvidenceView, WorkbenchApi } from "./client";

export class HttpApi implements WorkbenchApi {
  constructor(private readonly base: string) {}

  private async getJson<T>(path: string): Promise<T> {
    const res = await fetch(`${this.base}${path}`);
    if (!res.ok) {
      throw new Error(`backend rejected ${path}: HTTP ${res.status}`);
    }
    return (await res.json()) as T;
  }

  getEvidenceView(): Promise<EvidenceView> {
    return this.getJson<EvidenceView>("/api/evidence");
  }

  getReleaseEligibility(): Promise<ReleaseEligibility> {
    return this.getJson<ReleaseEligibility>("/api/release/eligibility");
  }

  getReleases(): Promise<ReleaseRecord[]> {
    return this.getJson<ReleaseRecord[]>("/api/releases");
  }

  async ping(): Promise<boolean> {
    try {
      const res = await fetch(`${this.base}/livez`);
      return res.ok;
    } catch {
      return false;
    }
  }
}
