// 各 Gate 页面的骨架占位与导出

import { Link } from "react-router-dom";
import { Card, EmptyState } from "../components/Basic";

export { ResearchNewPage } from "./ResearchNewPage";
export { EvidenceLedgerPage as EvidencePage } from "./EvidenceLedgerPage";
export { RulesPage } from "./RulesPage";
export { MetricSpecPage } from "./MetricSpecPage";
export { MacroPage } from "./MacroPage";
export { ClaimsPage } from "./ClaimsPage";
export { ScenariosPage } from "./ScenariosPage";

export function AuditPage() {
  return (
    <div className="page">
      <h1>审计与发布（G5-05 交付）</h1>
      <Card title="release_eligible（E-2：后端唯一计算点）">
        <EmptyState label="待 G4 产出 Release/Approval 对象后启用" />
        <Link to="/audit/eligibility">查看发布资格详情</Link>
      </Card>
    </div>
  );
}
