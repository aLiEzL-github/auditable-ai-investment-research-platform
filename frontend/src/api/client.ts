// API client —— 后端是唯一控制层（G5 §3.1 E-1/E-2/E-3）
// 本文件只声明客户端契约，不含任何业务判定逻辑。
// release_eligible 只能由后端返回，前端不做计算、不做改写。

import type {
  ApprovalsView,
  AssumptionsView,
  AuditOverview,
  CalcView,
  Claim,
  ClaimsView,
  ClosureView,
  EvidenceRecord,
  EvidenceLedger,
  FactRecord,
  MacroView,
  MetricSpecView,
  OpenItem,
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

export interface EvidenceView {
  claims: Claim[];
  evidence: EvidenceRecord[];
  facts: FactRecord[];
  openItems: OpenItem[];
}

export interface WorkbenchApi {
  getEvidenceView(): Promise<EvidenceView>;
  getReleaseEligibility(): Promise<ReleaseEligibility>;
  getReleases(): Promise<ReleaseRecord[]>;
  getResearchContract(): Promise<{
    status: ResearchContractStatus;
    contract: ResearchContract | null;
    missing_fields: string[];
  }>;
  launchResearch(form: ResearchContract): Promise<ResearchLaunchResult>;
  getEvidenceLedger(): Promise<EvidenceLedger>;
  getRulesView(): Promise<RulesView>;
  getMetricSpecView(): Promise<MetricSpecView>;
  getMacroView(): Promise<MacroView>;
  getCalcView(): Promise<CalcView>;
  getClaimsView(): Promise<ClaimsView>;
  getAssumptionsView(): Promise<AssumptionsView>;
  getScenariosView(): Promise<ScenariosView>;
  getOpenItemsView(): Promise<OpenItemsView>;
  getAuditOverview(): Promise<AuditOverview>;
  getReleasesView(): Promise<ReleaseView>;
  getClosureView(): Promise<ClosureView>;
  getPredictionsView(): Promise<PredictionsView>;
  getApprovalsView(): Promise<ApprovalsView>;
  ping(): Promise<boolean>;
}
