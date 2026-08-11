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
