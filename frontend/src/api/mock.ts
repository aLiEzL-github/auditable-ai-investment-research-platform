// Mock schema 实现（G5-01 验收标准「可使用 mock schema 独立开发」）
// 数据形状与 contracts/schema/*.schema.json 逐字段一致（规则 ⑱）。
// MOCK 模式下 UI 可独立开发，但 E-1/E-3 验收必须切回真实后端断言。

import type {
  Claim,
  EvidenceRecord,
  FactRecord,
  OpenItem,
  ReleaseEligibility,
  ReleaseRecord,
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

  async ping(): Promise<boolean> {
    return true;
  }
}
