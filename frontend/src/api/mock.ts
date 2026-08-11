// Mock schema 实现（G5-01 验收标准「可使用 mock schema 独立开发」）
// 数据形状与 contracts/schema/*.schema.json 逐字段一致（规则 ⑱）。
// MOCK 模式下 UI 可独立开发，但 E-1/E-3 验收必须切回真实后端断言。

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
  SourceRecord,
} from "../types";
import type { EvidenceView, WorkbenchApi } from "./client";

const MOCK_CLAIMS: Claim[] = [
  {
    schema_version: "1.0.0",
    id: "C-001",
    statement: "600089 资产负债率处于可比公司区间",
    refs: ["E-001", "E-002"],
    status: "SUPPORTED",
    category: "D",
    materiality: "MATERIAL",
  },
];

const MOCK_EVIDENCE: EvidenceRecord[] = [
  {
    id: "E-001",
    artifact_id: "ART-001",
    snapshot_id: "SNAP-001",
    schema_ver: "1.0.0",
    parser_version: "0.1.0",
    sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    content: "资产负债表（合并）附注 1 行",
  },
];

const MOCK_FACTS: FactRecord[] = [
  {
    id: "F-001",
    artifact_id: "ART-001",
    metric: "资产负债率",
    value: "0.52",
    unit: "ratio",
    period: "2025-12-31",
    scope: "CONSOLIDATED",
    basis: "REPORTED",
    vintage: "V2026-04",
    locator: "balance-sheet#total-liabilities",
    parser_version: "0.1.0",
    comparability: "COMPARABLE",
  },
];

const MOCK_OPEN_ITEMS: OpenItem[] = [
  {
    id: "OI-9001",
    title: "mock：材料性开放项示例",
    status: "OPEN",
    material: true,
    blocks: ["G5-05"],
  },
];

const MOCK_RELEASES: ReleaseRecord[] = [];

const MOCK_ELIGIBILITY: ReleaseEligibility = {
  status: "BLOCKED",
  reasons: [
    {
      code: "OI-9001",
      detail: "mock：存在材料性开放项，未获批准（示例阻断原因）",
    },
  ],
  checked_at: "2026-08-11T00:00:00Z",
  source: "MOCK",
};

const MOCK_CONTRACT: ResearchContract = {
  scope: "600089",
  period: "2026",
  unit: "CNY_million",
  vintage: "2026-08",
  snapshot: "SNAP-001",
  security_code: "600089.SH",
  company_id: "600089",
  as_of: "2026-08-11",
  version: "v0.1.0",
  workflow: "a-share-single-company-research",
};

// —— G5-03 mock fixture（形状对齐 contracts/* 与 rule_registry 七态）——

const MOCK_SOURCES: SourceRecord[] = [
  {
    id: "SRC_STATS",
    kind: "PRIMARY",
    name: "国家统计局 www.stats.gov.cn",
    status: "ALLOWED",
    legal_basis: "条款「欢迎转载或引用」；acquire_public_statistics=ALLOWED",
    locator: "https://www.stats.gov.cn/…",
    sha256: "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b",
  },
  {
    id: "SRC_CNINFO",
    kind: "SECONDARY",
    name: "巨潮资讯网 www.cninfo.com.cn",
    status: "UNKNOWN",
    legal_basis: "automated_bulk_acquisition=UNKNOWN → 阻断（fail-closed）",
    locator: "http://www.cninfo.com.cn/…",
    sha256: "0f1e2d3c4b5a69788796a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4",
  },
];

const MOCK_LEDGER: EvidenceLedger = {
  sources: MOCK_SOURCES,
  items: [
    {
      evidence_id: "E-001",
      artifact_id: "ART-001",
      sha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      source: MOCK_SOURCES[0],
      snippet: "GDP 同比增长 5.0%（官方披露口径）",
      conflict: "NONE",
      conflict_detail: "",
    },
    {
      evidence_id: "E-002",
      artifact_id: "ART-002",
      sha256: "d4c5b6a7f8e9d0c1b2a3948576e5d4c3b2a1987654321fedcba9876543210fed",
      source: MOCK_SOURCES[1],
      snippet: "600089 资产负债率 52%（巨潮披露）",
      conflict: "RIGHTS_BLOCKED",
      conflict_detail: "来源权利状态 UNKNOWN（automated_bulk_acquisition 未获授权）",
    },
    {
      evidence_id: "E-003",
      artifact_id: "ART-003",
      sha256: "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f",
      source: MOCK_SOURCES[0],
      snippet: "归母净利润 52.3 亿元（年报合并口径）",
      conflict: "VALUE_CONFLICT",
      conflict_detail: "主源 52.3 亿元 vs 副源 52.0 亿元（口径差 0.3 亿元）",
    },
  ],
};

const MOCK_RULES: RulesView = {
  rows: [
    {
      rule_id: "R01",
      title: "分部收入",
      definition: "合并营业收入与分部外部收入、内部交易和抵消项勾稽",
      version: "1.0",
      status: "PASS",
      applicability: { applicable: true, basis: "分部披露存在", signature: "sig-r01" },
      denominator: "2026-12-31 冻结",
      inputs: ["分部收入", "内部交易"],
      result: "差异 0.00 亿元",
      locator: "annual-report#segment",
    },
    {
      rule_id: "R04",
      title: "间接法 OCF",
      definition: "OCF 与净利润、非现金项目、营运资本及其他调节项勾稽",
      version: "1.0",
      status: "INPUT_MISSING",
      applicability: { applicable: true, basis: "披露框架适用", signature: "sig-r04" },
      denominator: "2026-12-31 冻结",
      inputs: ["净利润"],
      result: "缺非现金项目调节项",
      locator: "cash-flow#indirect",
    },
    {
      rule_id: "R06",
      title: "资产负债",
      definition: "资产 = 负债 + 权益",
      version: "1.0",
      status: "NOT_APPLICABLE",
      applicability: {
        applicable: false,
        basis: "预冻结适用性依据：本报告仅覆盖利润表口径，不勾稽资产负债表（2026-07-01 冻结，签名 sig-r06-na）",
        signature: "sig-r06-na",
      },
      denominator: "N/A（不适用）",
      inputs: [],
      result: "N/A",
      locator: "—",
    },
    {
      rule_id: "R10",
      title: "期间连续",
      definition: "本期期初与上期期末按同一口径比较",
      version: "1.0",
      status: "NOT_RUN",
      applicability: { applicable: true, basis: "跨期数据已取得", signature: "sig-r10" },
      denominator: "2026-12-31 冻结",
      inputs: ["期初权益"],
      result: "尚未运行",
      locator: "equity#prior",
    },
  ],
};

const MOCK_METRIC_SPEC: MetricSpecView = {
  frozen_sha256: "ddcc542469a880d287f1dcd2c63d526c0759b51f0f1a874a06efc2415d04c5f8",
  rows: [
    { metric_id: "营业收入", expected_origin: "REPORTED", caliber: "合并、期间、币种" },
    { metric_id: "归母净利润", expected_origin: "REPORTED", caliber: "合并、归属" },
    { metric_id: "经营活动现金流净额", expected_origin: "REPORTED", caliber: "累计/年度" },
    { metric_id: "自由现金流", expected_origin: "DERIVED", caliber: "OCF−维持性资本开支与 OCF−总资本开支分开" },
    { metric_id: "资产负债率", expected_origin: "DERIVED", caliber: "总负债/总资产" },
  ],
};

function resolve<T>(value: T, ms = 0): Promise<T> {
  return new Promise((r) => setTimeout(() => r(value), ms));
}

export class MockApi implements WorkbenchApi {
  getEvidenceView(): Promise<EvidenceView> {
    return resolve({
      claims: MOCK_CLAIMS,
      evidence: MOCK_EVIDENCE,
      facts: MOCK_FACTS,
      openItems: MOCK_OPEN_ITEMS,
    });
  }

  getReleaseEligibility(): Promise<ReleaseEligibility> {
    return resolve(MOCK_ELIGIBILITY);
  }

  getReleases(): Promise<ReleaseRecord[]> {
    return resolve(MOCK_RELEASES);
  }

  getResearchContract(): Promise<{
    status: ResearchContractStatus;
    contract: ResearchContract | null;
    missing_fields: string[];
  }> {
    return resolve({
      status: "VALID",
      contract: MOCK_CONTRACT,
      missing_fields: [],
    });
  }

  getEvidenceLedger(): Promise<EvidenceLedger> {
    return resolve(structuredClone(MOCK_LEDGER));
  }

  getRulesView(): Promise<RulesView> {
    return resolve(structuredClone(MOCK_RULES));
  }

  getMetricSpecView(): Promise<MetricSpecView> {
    return resolve(structuredClone(MOCK_METRIC_SPEC));
  }

  launchResearch(form: ResearchContract): Promise<ResearchLaunchResult> {
    const missing = Object.entries(form)
      .filter(([, v]) => !v)
      .map(([k]) => k);
    if (missing.length > 0) {
      return resolve({
        ok: false,
        error: `E-G5-02-001: 缺 ResearchContract 字段: ${missing.join(", ")}`,
      });
    }
    if (form.workflow !== "a-share-single-company-research") {
      return resolve({
        ok: false,
        error: `E-G3-02-004: workflow 不在白名单: ${form.workflow}`,
      });
    }
    return resolve({
      ok: true,
      run_id: `run-${Date.now()}-mock-0001`,
      state: "DRAFT",
    });
  }

  async ping(): Promise<boolean> {
    return true;
  }
}
