// API client —— 后端是唯一控制层（G5 §3.1 E-1/E-2/E-3）
// 本文件只声明客户端契约，不含任何业务判定逻辑。
// release_eligible 只能由后端返回，前端不做计算、不做改写。

import type {
  Claim,
  EvidenceRecord,
  EvidenceLedger,
  FactRecord,
  MetricSpecView,
  OpenItem,
  ReleaseEligibility,
  ReleaseRecord,
  ResearchContract,
  ResearchContractStatus,
  ResearchLaunchResult,
  RulesView,
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
  ping(): Promise<boolean>;
}
