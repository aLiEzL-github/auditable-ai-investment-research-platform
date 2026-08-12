// Rule 状态页（G5-03，基线 B §8）
// 验收：缺失、冲突、权利和非 PASS Rule 不可隐藏；N/A 必须显示依据。
//   · 非 PASS（FAIL/INPUT_MISSING/NOT_COMPARABLE/RESTATEMENT_PENDING/NOT_RUN）
//     逐条恒定渲染，不得折叠、不得过滤（E-4 语义延伸）
//   · 每条非 PASS 显示状态 + 结果 + locator（E-5：显示为什么）
//   · NOT_APPLICABLE 必须显示预冻结适用性依据与签名（基线 G3-09）
//   · 「无非 PASS」与「空数据」可分辨（规则 ⑨）

import { useCallback } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { AsyncStateRenderer } from "../components/AsyncStateRenderer";
import { useAsync } from "../state/useAsync";
import { Card } from "../components/Basic";
import { StatusBadge } from "../components/StatusBadge";
import {
  RULE_BLOCKING,
  type RuleStatus,
  type RuleStatusRow,
} from "../types";

const STATUS_TONE: Record<RuleStatus, "ok" | "fail" | "warn" | "neutral"> = {
  PASS: "ok",
  FAIL: "fail",
  INPUT_MISSING: "fail",
  NOT_COMPARABLE: "fail",
  RESTATEMENT_PENDING: "fail",
  NOT_RUN: "warn",
  NOT_APPLICABLE: "neutral",
};

export function RulesPage() {
  const { api } = useWorkbench();
  const load = useCallback(() => api.getRulesView(), [api]);
  const { state, retry } = useAsync(load, (v) =>
    v.rows.length === 0 ? "无 Rule 状态数据" : null,
  );

  if (state.phase !== "READY") {
    return <AsyncStateRenderer state={state} onRetry={retry} renderValue={() => null} />;
  }
  const view = state.value;
  const blockingRows = view.rows.filter((r) => RULE_BLOCKING.includes(r.status));
  const naRows = view.rows.filter((r) => r.status === "NOT_APPLICABLE");

  return (
    <div className="page">
      <h1>Rule 状态（R01—R10）</h1>

      {/* 非 PASS 汇总横幅：恒定可见，数字可机检（⑨） */}
      <Card title={`非 PASS Rule —— ${blockingRows.length} 条（全部列出，不可隐藏）`}>
        {blockingRows.length === 0 ? (
          <p data-testid="no-blocking">全部适用规则为 PASS</p>
        ) : (
          <ul data-testid="blocking-rules">
            {blockingRows.map((r) => (
              <li key={r.rule_id} data-testid="blocking-rule" data-status={r.status}>
                <StatusBadge tone={STATUS_TONE[r.status]} label={r.status} />
                <strong>{r.rule_id}</strong> {r.title} —— {r.result}
                <span className="rule-locator">locator: {r.locator}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* 全部规则明细 */}
      <Card title="R01—R10 明细">
        <table className="rules-table" data-testid="rules-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>标题</th>
              <th>状态</th>
              <th>适用分母</th>
              <th>输入</th>
              <th>结果</th>
              <th>locator</th>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((r) => (
              <RuleRow key={r.rule_id} row={r} />
            ))}
          </tbody>
        </table>
      </Card>

      {/* N/A 依据区：N/A 必须显示依据与签名，缺一条即整区报错 */}
      <Card title={`N/A 依据 —— ${naRows.length} 条`}>
        {naRows.length === 0 ? (
          <p data-testid="no-na">无 N/A 规则</p>
        ) : (
          <ul data-testid="na-bases">
            {naRows.map((r) => (
              <li key={r.rule_id} data-testid="na-basis">
                <strong>{r.rule_id}</strong> {r.title}
                <p className="na-basis__text">{r.applicability.basis}</p>
                <p className="na-basis__sig">签名: {r.applicability.signature}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

function RuleRow({ row }: { row: RuleStatusRow }) {
  return (
    <tr data-testid="rule-row" data-status={row.status}>
      <td>{row.rule_id}</td>
      <td>{row.title}</td>
      <td>
        <StatusBadge tone={STATUS_TONE[row.status]} label={row.status} />
      </td>
      <td>{row.denominator}</td>
      <td>{row.inputs.join("、") || "—"}</td>
      <td>{row.result}</td>
      <td className="rule-locator">{row.locator}</td>
    </tr>
  );
}
