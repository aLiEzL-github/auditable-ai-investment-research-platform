// Mock schema 实现（G5-01 验收标准「可使用 mock schema 独立开发」）
// 数据形状与 contracts/schema/*.schema.json 逐字段一致（规则 ⑱）。
// MOCK 模式下 UI 可独立开发，但 E-1/E-3 验收必须切回真实后端断言。

import type {
  AssumptionsView,
  CalcView,
  Claim,
  ClaimsView,
  EvidenceRecord,
  EvidenceLedger,
  FactRecord,
  MacroView,
  MetricSpecView,
  OpenItem,
  OpenItemsView,
  ReleaseEligibility,
  ReleaseRecord,
  ResearchContract,
  ResearchContractStatus,
  ResearchLaunchResult,
  RulesView,
  ScenariosView,
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

// —— G5-04 mock fixture（形状对齐 G3 各模块）——

const MOCK_MACRO: MacroView = {
  snapshot: {
    spec_sha256: "f9e8d7c6b5a4938271605f4e3d2c1b0a99887766554433221100ffeeddccbbaa",
    published_at: "2026-08-01T00:00:00Z",
    effective_date: "2026-08-01",
    retrieved_at: "2026-08-10T08:00:00Z",
    cutoff_at: "2026-07-31T00:00:00Z",
    state: "FROZEN",
    gate: { verdict: "MACRO_GATE_PASS", failures: [] },
    series: [
      { series_id: "GDP", name: "国内生产总值", material: true, vintage: "2026Q2", rows: 4, status: "OK" },
      { series_id: "CPI", name: "居民消费价格指数", material: false, vintage: "2026Q2", rows: 4, status: "OK" },
    ],
  },
  transmission: [
    {
      macro_series_id: "GDP",
      transmission: "宏观增长 → 行业需求 → 公司收入增速",
      target_metric: "营业收入",
    },
  ],
};

const MOCK_CALC: CalcView = {
  entries: [
    {
      entry_id: "CALC-001",
      formula_id: "FCFF",
      formula_version: "1.0",
      inputs: [
        { input_key: "营业收入", kind: "EXTERNAL_FACT", value: "5230.00", input_sha256: "aaaa" },
        { input_key: "OCF", kind: "EXTERNAL_FACT", value: "810.00", input_sha256: "bbbb" },
      ],
      result: "810.00",
      result_sha256: "cccc",
      unit: "CNY_million",
    },
    {
      entry_id: "CALC-002",
      formula_id: "FCFE",
      formula_version: "1.0",
      inputs: [
        { input_key: "FCFF", kind: "DERIVED", value: "810.00", input_sha256: "cccc" },
        { input_key: "净负债", kind: "EXTERNAL_FACT", value: "150.00", input_sha256: "dddd" },
      ],
      result: "660.00",
      result_sha256: "eeee",
      unit: "CNY_million",
    },
  ],
};

const MOCK_CLAIMS_VIEW: ClaimsView = {
  unbound_count: 1,
  nodes: [
    {
      node_type: "F",
      ref_id: "C-001",
      rendered_value: "2026 年营业收入 5230 亿元（合并）",
      materiality: "MATERIAL",
      evidence_refs: ["E-001"],
      formula_ref: null,
      assumption_ref: null,
      falsifier: "年报重述",
      visible_span: "para-3:12-28",
      bound: true,
    },
    {
      node_type: "D",
      ref_id: "C-002",
      rendered_value: "自由现金流 810 亿元（由 OCF 与资本开支计算）",
      materiality: "MATERIAL",
      evidence_refs: ["E-003"],
      formula_ref: "FCFF",
      assumption_ref: null,
      falsifier: "OCF 口径变更",
      visible_span: "para-5:2-18",
      bound: true,
    },
    {
      node_type: "A",
      ref_id: "C-003",
      rendered_value: "维持性资本开支约为折旧的 90%",
      materiality: "MATERIAL",
      evidence_refs: [],
      formula_ref: null,
      assumption_ref: "ASM-001",
      falsifier: "—",
      visible_span: "para-7:1-16",
      bound: true,
    },
    {
      node_type: "L",
      ref_id: "UNBOUND-001",
      rendered_value: "段落文本「行业竞争格局向好」",
      materiality: "MATERIAL",
      evidence_refs: [],
      formula_ref: null,
      assumption_ref: null,
      falsifier: "—",
      visible_span: "para-9:1-10",
      bound: false, // 无 Claim 绑定 —— 醒目异常
    },
  ],
};

const MOCK_ASSUMPTIONS: AssumptionsView = {
  rows: [
    {
      proposal_id: "ASM-001",
      payload_summary: "维持性资本开支 = 折旧 × 90%",
      status: "PENDING", // 未批准 —— 醒目
      proposed_by: "analyst",
      approved_at: null,
      snapshot_sha256: null,
    },
    {
      proposal_id: "ASM-002",
      payload_summary: "WACC 基准 = 8.5%",
      status: "APPROVED",
      proposed_by: "analyst",
      approved_at: "2026-08-05T10:00:00Z",
      snapshot_sha256: "11aa22bb33cc44dd55ee66ff77889900aabbccddeeff00112233445566778899",
    },
  ],
};

const MOCK_SCENARIOS: ScenariosView = {
  rows: [
    {
      scenario: "PESSIMISTIC",
      method: "FCFF",
      low: "18.5",
      high: "21.0",
      per_share: "19.8",
      triggers: "WACC 变动 +50bp；FCFF 下修",
      notes: "需求走弱",
    },
    {
      scenario: "BASE",
      method: "FCFF",
      low: "22.0",
      high: "25.5",
      per_share: "23.8",
      triggers: "WACC 变动 ±50bp；FCFF 上/下修",
      notes: "基准",
    },
    {
      scenario: "OPTIMISTIC",
      method: "FCFF",
      low: "26.0",
      high: "30.0",
      per_share: "28.1",
      triggers: "WACC 变动 -50bp；FCFF 上修",
      notes: "需求超预期",
    },
  ],
};

const MOCK_OPEN_ITEMS_VIEW: OpenItemsView = {
  rows: [
    {
      open_item_id: "OI-9001",
      description: "副源权利判定待补充书面依据",
      material: true, // 材料性 —— 醒目
      owner_role: "DEV",
      due_date: "2026-09-01",
      blocks_gate: "G5-05",
      closure_evidence: null,
      status: "OPEN",
      record_sha256: "1111",
    },
    {
      open_item_id: "OI-9002",
      description: "非材料性格式项",
      material: false,
      owner_role: "DEV",
      due_date: null,
      blocks_gate: null,
      closure_evidence: null,
      status: "OPEN",
      record_sha256: "2222",
    },
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

  getMacroView(): Promise<MacroView> {
    return resolve(structuredClone(MOCK_MACRO));
  }

  getCalcView(): Promise<CalcView> {
    return resolve(structuredClone(MOCK_CALC));
  }

  getClaimsView(): Promise<ClaimsView> {
    return resolve(structuredClone(MOCK_CLAIMS_VIEW));
  }

  getAssumptionsView(): Promise<AssumptionsView> {
    return resolve(structuredClone(MOCK_ASSUMPTIONS));
  }

  getScenariosView(): Promise<ScenariosView> {
    return resolve(structuredClone(MOCK_SCENARIOS));
  }

  getOpenItemsView(): Promise<OpenItemsView> {
    return resolve(structuredClone(MOCK_OPEN_ITEMS_VIEW));
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
