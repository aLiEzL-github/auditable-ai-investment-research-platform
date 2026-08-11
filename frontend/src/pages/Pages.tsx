// 各 Gate 页面的骨架占位与导出

import { Link } from "react-router-dom";
import { Card, EmptyState } from "../components/Basic";

export { ResearchNewPage } from "./ResearchNewPage";
export { EvidenceLedgerPage as EvidencePage } from "./EvidenceLedgerPage";
export { RulesPage } from "./RulesPage";
export { MetricSpecPage } from "./MetricSpecPage";

export function MacroPage() {
  return (
    <div className="page">
      <h1>宏观与计算（G5-04 交付）</h1>
      <EmptyState label="待 G3 产出 CalcLedger / MacroSnapshot 后启用" />
    </div>
  );
}

export function ClaimsPage() {
  return (
    <div className="page">
      <h1>Claim 与假设（G5-04 交付）</h1>
      <EmptyState label="待 G3 产出 Claim 图与 AssumptionSnapshot 后启用" />
    </div>
  );
}

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
