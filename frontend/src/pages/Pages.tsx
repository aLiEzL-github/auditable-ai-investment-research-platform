// 各 Gate 页面的骨架占位（G5-01 只交付外壳；G5-03/04/05 填充内容）

import { Link } from "react-router-dom";
import { Card, EmptyState } from "../components/Basic";
import { useWorkbench } from "../state/WorkbenchContext";

export { ResearchNewPage } from "./ResearchNewPage";

export function EvidencePage() {
  const { evidence, evidenceError } = useWorkbench();
  return (
    <div className="page">
      <h1>证据台账（G5-03 交付）</h1>
      {evidenceError != null ? (
        <Card title="证据加载失败（E-8：显式报错，不得静默）">
          <EmptyState label={evidenceError} />
        </Card>
      ) : (
        <Card title="证据视图">
          <ul data-testid="evidence-summary">
            <li>Claim 数：{evidence?.claims.length ?? "—"}</li>
            <li>证据片段数：{evidence?.evidence.length ?? "—"}</li>
            <li>事实数：{evidence?.facts.length ?? "—"}</li>
            <li>开放项数：{evidence?.openItems.length ?? "—"}</li>
          </ul>
        </Card>
      )}
    </div>
  );
}

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
