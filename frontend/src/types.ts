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

// —— G5-03 证据台账 / MetricSpec / Rule 状态（基线 B §8）——
// 与 backend/app/rule_registry.py（七态）、contracts/metric_spec.json、
// contracts/rights_matrix.json、contracts/schema/source.schema.json 对齐（规则 ⑱）

export type SourceKind = "PRIMARY" | "SECONDARY";
export type SourceStatus = "ALLOWED" | "UNKNOWN" | "PROHIBITED";

export interface SourceRecord {
  id: string;
  kind: SourceKind;
  name: string;
  status: SourceStatus;
  legal_basis: string;
  locator: string;
  sha256: string;
}

export type EvidenceConflict =
  | "NONE"
  | "VALUE_CONFLICT" // 主副源数值冲突
  | "RIGHTS_BLOCKED"; // 来源权利状态 UNKNOWN/PROHIBITED

export interface EvidenceItem {
  evidence_id: string;
  artifact_id: string;
  sha256: string;
  source: SourceRecord;
  snippet: string;
  conflict: EvidenceConflict;
  conflict_detail: string;
}

export interface EvidenceLedger {
  items: EvidenceItem[];
  sources: SourceRecord[];
}

// Rule 状态（G3-09 七态，逐字取用）
export type RuleStatus =
  | "PASS"
  | "FAIL"
  | "INPUT_MISSING"
  | "NOT_COMPARABLE"
  | "RESTATEMENT_PENDING"
  | "NOT_RUN"
  | "NOT_APPLICABLE";

// 非 PASS 状态（BLOCKING，逐字取用 BLOCKING = FAIL/INPUT_MISSING/
// NOT_COMPARABLE/RESTATEMENT_PENDING/NOT_RUN）
export const RULE_BLOCKING: readonly RuleStatus[] = [
  "FAIL",
  "INPUT_MISSING",
  "NOT_COMPARABLE",
  "RESTATEMENT_PENDING",
  "NOT_RUN",
];

export interface RuleStatusRow {
  rule_id: string; // R01…R10
  title: string;
  definition: string;
  version: string;
  status: RuleStatus;
  applicability: {
    applicable: boolean;
    basis: string; // N/A 必须显示的预冻结依据
    signature: string;
  };
  denominator: string; // 冻结适用分母
  inputs: string[]; // 输入
  result: string; // 结果
  locator: string;
}

export interface MetricSpecRow {
  metric_id: string;
  expected_origin: string;
  caliber: string;
}

export interface RulesView {
  rows: RuleStatusRow[];
}

export interface MetricSpecView {
  rows: MetricSpecRow[]; // 20 项
  frozen_sha256: string;
}

// —— G5-04 宏观/计算/Claim/假设/三情景（基线 B §8）——
// 与 backend/app/macro_snapshot.py / formula_registry.py / claim_engine.py /
// assumption_snapshot.py / valuation_engine.py / open_item_registry.py 对齐（规则 ⑱）

// 宏观快照（G3-03：published/effective/retrieved/cutoff 分离）
export interface MacroSnapshot {
  spec_sha256: string;
  published_at: string;
  effective_date: string;
  retrieved_at: string;
  cutoff_at: string;
  state: "FROZEN" | "PARTIAL" | "BLOCKED";
  gate: {
    verdict: "MACRO_GATE_PASS" | "MACRO_GATE_FAIL";
    failures: string[]; // 门失败原因（不得输出当前估值）
  };
  series: MacroSeries[];
}

export interface MacroSeries {
  series_id: string;
  name: string;
  material: boolean;
  vintage: string;
  rows: number;
  status: "OK" | "MISSING" | "EXPIRED" | "FUTURE_VINTAGE" | "ZERO_ROWS";
}

// 传导链（宏观 → 公司分析）
export interface TransmissionLink {
  macro_series_id: string;
  transmission: string; // 传导描述
  target_metric: string; // 传导至哪个指标
}

export interface MacroView {
  snapshot: MacroSnapshot;
  transmission: TransmissionLink[];
}

// CalcLedger（G3-04：确定性计算账本 —— 每笔记录输入哈希 + 公式版本）
export type CalcInputKind = "EXTERNAL_FACT" | "DERIVED";

export interface CalcEntry {
  entry_id: string;
  formula_id: string;
  formula_version: string;
  inputs: { input_key: string; kind: CalcInputKind; value: string; input_sha256: string }[];
  result: string;
  result_sha256: string;
  unit: string;
}

export interface CalcView {
  entries: CalcEntry[];
}

// Claim 图与 emission map（G3-05：六类节点 [F]/[D]/[A]/[P]/[C]/[L]）
export type ClaimNodeType = "F" | "D" | "A" | "P" | "C" | "L";

export interface ClaimNodeRow {
  node_type: ClaimNodeType;
  ref_id: string;
  rendered_value: string;
  materiality: "MATERIAL" | "IMMATERIAL" | "UNCLASSIFIED";
  evidence_refs: string[];
  formula_ref: string | null;
  assumption_ref: string | null;
  falsifier: string;
  visible_span: string; // emission map：可见内容字节区间
  bound: boolean; // 是否已绑定可见内容（无绑定 = 醒目异常）
}

export interface ClaimsView {
  nodes: ClaimNodeRow[];
  unbound_count: number; // 无 Claim 绑定的可见内容数
}

// 假设（G3-13：PENDING → APPROVED/REJECTED，不可变快照）
export type AssumptionStatus = "PENDING" | "APPROVED" | "REJECTED";

export interface AssumptionRow {
  proposal_id: string;
  payload_summary: string;
  status: AssumptionStatus;
  proposed_by: string;
  approved_at: string | null;
  snapshot_sha256: string | null; // APPROVED 后不可变快照
}

export interface AssumptionsView {
  rows: AssumptionRow[];
}

// 三情景与触发器（G3-06：PESSIMISTIC/BASE/OPTIMISTIC + 触发器）
export type ScenarioName = "PESSIMISTIC" | "BASE" | "OPTIMISTIC";

export interface ScenarioRow {
  scenario: ScenarioName;
  method: string;
  low: string;
  high: string;
  per_share: string;
  triggers: string; // 触发器
  notes: string;
}

export interface ScenariosView {
  rows: ScenarioRow[];
}

// 开放项（G3-14：材料性醒目标识）
export interface OpenItemRow {
  open_item_id: string;
  description: string;
  material: boolean;
  owner_role: string;
  due_date: string | null;
  blocks_gate: string | null;
  closure_evidence: string | null;
  status: "OPEN" | "CLOSED" | "SUPERSEDED";
  record_sha256: string;
}

export interface OpenItemsView {
  rows: OpenItemRow[];
}
