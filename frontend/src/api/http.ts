// 真实后端实现 —— 只做传输，不做判定（E-1/E-2/E-3）。
// 后端拒绝即透传错误，前端绝不自行构造 BLOCKED/CLEAR 结论。
// G5-02：launchResearch 的后端判定（缺 contract 拒绝）一律透传，
// 前端不得自行决定「可以启动」。

import type {
  ApprovalsView,
  AssumptionsView,
  AuditOverview,
  CalcView,
  ClaimsView,
  ClosureView,
  EvidenceLedger,
  MacroView,
  MetricSpecView,
  OpenItemsView,
  PredictionsView,
  ReleaseEligibility,
  ReleaseRecord,
  ReleaseView,
  ResearchContract,
  ResearchContractStatus,
  ResearchLaunchResult,
  RulesView,
  ScenariosView,
} from "../types";
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

  private async postJson<T>(path: string, body: unknown): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
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

  getResearchContract(): Promise<{
    status: ResearchContractStatus;
    contract: ResearchContract | null;
    missing_fields: string[];
  }> {
    return this.getJson("/api/research/contract");
  }

  launchResearch(form: ResearchContract): Promise<ResearchLaunchResult> {
    return this.postJson<ResearchLaunchResult>("/api/research/launch", form);
  }

  getEvidenceLedger(): Promise<EvidenceLedger> {
    return this.getJson<EvidenceLedger>("/api/evidence/ledger");
  }

  getRulesView(): Promise<RulesView> {
    return this.getJson<RulesView>("/api/rules");
  }

  getMetricSpecView(): Promise<MetricSpecView> {
    return this.getJson<MetricSpecView>("/api/metrics");
  }

  getMacroView(): Promise<MacroView> {
    return this.getJson<MacroView>("/api/macro");
  }

  getCalcView(): Promise<CalcView> {
    return this.getJson<CalcView>("/api/calc");
  }

  getClaimsView(): Promise<ClaimsView> {
    return this.getJson<ClaimsView>("/api/claims");
  }

  getAssumptionsView(): Promise<AssumptionsView> {
    return this.getJson<AssumptionsView>("/api/assumptions");
  }

  getScenariosView(): Promise<ScenariosView> {
    return this.getJson<ScenariosView>("/api/scenarios");
  }

  getOpenItemsView(): Promise<OpenItemsView> {
    return this.getJson<OpenItemsView>("/api/open-items");
  }

  getAuditOverview(): Promise<AuditOverview> {
    return this.getJson<AuditOverview>("/api/audit");
  }

  getReleasesView(): Promise<ReleaseView> {
    return this.getJson<ReleaseView>("/api/releases");
  }

  getClosureView(): Promise<ClosureView> {
    return this.getJson<ClosureView>("/api/closure");
  }

  getPredictionsView(): Promise<PredictionsView> {
    return this.getJson<PredictionsView>("/api/predictions");
  }

  getApprovalsView(): Promise<ApprovalsView> {
    return this.getJson<ApprovalsView>("/api/approvals");
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
