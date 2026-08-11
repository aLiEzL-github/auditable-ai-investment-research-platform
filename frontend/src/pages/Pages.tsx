// 各 Gate 页面的骨架占位与导出

import { Card, EmptyState } from "../components/Basic";

export { ResearchNewPage } from "./ResearchNewPage";
export { EvidenceLedgerPage as EvidencePage } from "./EvidenceLedgerPage";
export { RulesPage } from "./RulesPage";
export { MetricSpecPage } from "./MetricSpecPage";
export { MacroPage } from "./MacroPage";
export { ClaimsPage } from "./ClaimsPage";
export { ScenariosPage } from "./ScenariosPage";
export { AuditPage } from "./AuditPage";

export function EligibilityPage() {
  return (
    <div className="page">
      <h1>发布资格详情</h1>
      <Card title="release_eligible（E-2：后端唯一计算点）">
        <EmptyState label="发布资格详情已并入「审计与发布」页（/audit）" />
      </Card>
    </div>
  );
}
