// G5-01 领域类型 —— 与 contracts/schema/*.schema.json 对齐（规则 ⑱ 夹具形状与真实契约一致）
// 仅声明「展示层读到的形状」，不含任何写原语。

export type ClaimStatus = "DRAFT" | "SUPPORTED" | "DISPUTED";
export type ClaimCategory = "F" | "D" | "A" | "P" | "C" | "L";
export type Materiality = "MATERIAL" | "NON_MATERIAL" | "UNCLASSIFIED";
export type OpenItemStatus = "OPEN" | "CLOSED";
export type FactComparability = "COMPARABLE" | "NOT_COMPARABLE";

export interface Claim {
  schema_version: string;
  id: string;
  statement: string;
  refs: string[];
  status: ClaimStatus;
  category: ClaimCategory;
  materiality: Materiality;
}

export interface EvidenceRecord {
  id: string;
  artifact_id: string;
  snapshot_id: string;
  schema_ver: string;
  parser_version: string;
  sha256: string;
  content: string;
}

export interface FactRecord {
  id: string;
  artifact_id: string;
  metric: string;
  value: string;
  unit: string;
  period: string;
  scope: string;
  basis: string;
  vintage: string;
  locator: string;
  parser_version: string;
  comparability?: FactComparability;
}

export interface OpenItem {
  id: string;
  title: string;
  status: OpenItemStatus;
  material: boolean;
  blocks?: string[];
}

export interface ReleaseRecord {
  id: string;
  version: string;
  parent_cas: string;
  released_at: string;
  approval_id: string;
  current_pointer?: boolean;
}

// —— 阻断态展示模型（G5 §3.2 E-4/E-5/E-6）——
// 该模型只能由 API 响应构造；UI 无任何本地写入路径（E-1/E-3）。

export type BlockingStatus =
  | "NOT_CHECKED" // 尚未检查（E-6：与「已检查无阻断」必须可分辨）
  | "CLEAR" // 已检查，无阻断（E-6）
  | "BLOCKED" // 被阻断（E-4：不可隐藏）
  | "ERROR"; // 证据链断裂 / 后端不可达（E-8：不得静默显示结论）

export interface BlockingReason {
  code: string;
  detail: string;
}

export interface ReleaseEligibility {
  status: BlockingStatus;
  reasons: BlockingReason[];
  checked_at: string | null;
  source: "BACKEND" | "MOCK";
}

// —— G5-02 新建研究（ResearchContract，与 backend/app/claim_engine.py 逐字对齐，规则 ⑱）——
// 基线 B §8 G5-02 交付件：市场、证券、as-of、期限、模型、预算。
// 验收：缺 ResearchContract 不能启动 —— 启动动作必须携带完整契约，缺字段即拒绝。

export type ResearchWorkflow =
  | "a-share-single-company-research"
  | "system-design-plan";

export interface ResearchContract {
  scope: string; // 如 600089（范围 ID）
  period: string; // 如 2026（研究期间）
  unit: string; // 如 CNY_million（计量单位）
  vintage: string; // 数据 vintage，如 2026-08
  snapshot: string; // 冻结快照 ID
  security_code: string; // 证券代码
  company_id: string; // 公司 ID
  as_of: string; // as-of 日期（YYYY-MM-DD）
  version: string; // 版本号，如 v0.1.0
  workflow: ResearchWorkflow; // 工作流白名单（G3-02）
}

// 新建研究页的表单输入（交付件六项）
export interface ResearchForm {
  market: string; // 市场，如 A-share
  security: string; // 证券，如 600089.SH
  as_of: string; // as-of
  horizon: string; // 期限
  model: string; // 模型
  budget: string; // 预算
}

export type ResearchLaunchResult =
  | { ok: true; run_id: string; state: string }
  | { ok: false; error: string }; // 缺 contract / 后端拒绝 —— 显式失败，不静默

export type ResearchContractStatus =
  | "MISSING" // 未取得契约 —— 不得启动
  | "VALID"; // 契约完整 —— 可启动
