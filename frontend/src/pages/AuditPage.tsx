// 审计与发布页（G5-05，基线 B §8）—— 本 Gate 的 E 组核心
// 交付件：Audit、Approval、Release、CurrentKey、UpdateDiff、closure map、
//        Prediction、Calibration。
// 验收（E-1/E-2/E-3，一票否决）：UI 无法绕过后端 release_eligible；
//   · release_eligible 只展示后端返回值（source 恒显），前端无计算点
//   · 阻断原因逐条可见（E-5）
//   · Gate 7 前真实研究发布控件强制禁用（基线 §8 G5-05 原文）
//   · 预测显示登记/到期/未决/不可裁决 + 校准充分性

import { useEffect, useState } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { Card, EmptyState, ErrorState } from "../components/Basic";
import { StatusBadge } from "../components/StatusBadge";
import type { AuditOverview, ClosureView, PredictionsView, ReleaseView } from "../types";

export function AuditPage() {
  const { api } = useWorkbench();
  const [overview, setOverview] = useState<AuditOverview | null>(null);
  const [releases, setReleases] = useState<ReleaseView | null>(null);
  const [predictions, setPredictions] = useState<PredictionsView | null>(null);
  const [closure, setClosure] = useState<ClosureView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.getAuditOverview(),
      api.getReleasesView(),
      api.getPredictionsView(),
      api.getClosureView(),
    ])
      .then(([o, r, p, c]) => {
        if (!cancelled) {
          setOverview(o);
          setReleases(r);
          setPredictions(p);
          setClosure(c);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  if (error != null) return <ErrorState message={error} />;
  if (overview == null || releases == null || predictions == null || closure == null)
    return <EmptyState label="审计与发布加载中…" />;

  return (
    <div className="page">
      <h1>审计与发布</h1>

      <AuditCard overview={overview} />
      <ClosureCard closure={closure} />
      <ApprovalsCard overview={overview} />
      <ReleaseCard overview={overview} releases={releases} />
      <PredictionsCard predictions={predictions} />
    </div>
  );
}

function AuditCard({ overview }: { overview: AuditOverview }) {
  const a = overview.audit;
  const eligible = a.release_eligible;
  return (
    <Card title="审计（七门 + release_eligible —— 后端唯一来源 E-1/E-2）">
      <div
        data-testid="release-eligible"
        data-eligible={String(eligible)}
        className={eligible ? "eligible-ok" : "eligible-fail"}
      >
        <strong>release_eligible = {String(eligible)}</strong>
        <span className="source-sha"> · source: {a.source}</span>
      </div>
      {!eligible && (
        <ul data-testid="audit-failures">
          {a.failures.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      )}
      <table className="rules-table" data-testid="audit-gates">
        <thead>
          <tr>
            <th>门</th>
            <th>判定</th>
            <th>检查对象数</th>
          </tr>
        </thead>
        <tbody>
          {a.gates.map((g) => (
            <tr key={g.gate} data-testid="audit-gate" data-gate={g.gate} data-verdict={g.verdict}>
              <td>{g.gate}</td>
              <td>
                <StatusBadge tone={g.verdict === "PASS" ? "ok" : "fail"} label={g.verdict} />
              </td>
              <td>{g.checked}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ClosureCard({ closure }: { closure: ClosureView }) {
  return (
    <Card title={`对象闭包 —— ${closure.count} 个对象`}>
      <p data-testid="closure-status">
        完整: {String(closure.complete)} · dangling: {closure.dangling} · subject_root:{" "}
        {closure.subject_root}
      </p>
      {!closure.complete && (
        <div className="alert-item" data-testid="closure-incomplete">
          闭包不完整 —— 缺对象不得冒充完整复验（D-10）
        </div>
      )}
      <ul data-testid="closure-objects">
        {closure.objects.map((o) => (
          <li key={o.id}>
            [{o.kind}] {o.id}
            <span className="source-sha"> {o.sha256.slice(0, 16)}…</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ApprovalsCard({ overview }: { overview: AuditOverview }) {
  const rows = overview.approvals.rows;
  return (
    <Card title={`批准（${rows.length} 条）`}>
      <ul data-testid="approval-list">
        {rows.map((r) => (
          <li key={r.id} data-testid="approval" data-status={r.status}>
            <StatusBadge
              tone={r.status === "ACTIVE" ? "ok" : r.status === "PENDING" ? "warn" : "fail"}
              label={r.status}
            />
            <strong>{r.id}</strong> {r.current_key} · inputs_hash: {r.inputs_hash.slice(0, 16)}…
            <p className="source-sha">token: {r.token} · approver: {r.approver}</p>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ReleaseCard({ overview, releases }: { overview: AuditOverview; releases: ReleaseView }) {
  const gate7 = overview.gate7_reached;
  return (
    <Card title="发布与 CurrentKey">
      {!gate7 && (
        <div className="alert-item" data-testid="release-disabled">
          <strong>Gate 7 未达 —— 真实研究发布控件强制禁用（基线 §8 G5-05）</strong>
        </div>
      )}
      <ul data-testid="current-keys">
        {releases.keys.map((k) => (
          <li key={k.key} data-testid="current-key">
            <strong>{k.key}</strong>
            {k.current == null ? (
              <span> · current: 无（未发布）</span>
            ) : (
              <span> · current: v{k.current.version}（{k.current.id.slice(0, 16)}…）</span>
            )}
          </li>
        ))}
      </ul>
      <button
        type="button"
        data-testid="publish-button"
        disabled={!gate7}
        onClick={() => {}}
      >
        {gate7 ? "发布" : "发布（禁用 —— Gate 7 前不可用）"}
      </button>
      <p className="source-sha" data-testid="publish-reason">
        {gate7 ? "Gate 7 已到达，可发布" : "Gate 7 未达 —— 真实研究发布控件强制禁用（E-5：禁用原因可见）"}
      </p>
    </Card>
  );
}

function PredictionsCard({ predictions }: { predictions: PredictionsView }) {
  const rows = predictions.rows;
  return (
    <Card title={`预测（${rows.length} 条）`}>
      <p
        data-testid="calibration-status"
        className={predictions.calibration_sufficient ? "eligible-ok" : "eligible-fail"}
      >
        校准充分性: {predictions.calibration_sufficient ? "已建立" : "未建立"} ——{" "}
        {predictions.calibration_note}
      </p>
      <table className="rules-table" data-testid="predictions-table">
        <thead>
          <tr>
            <th>预测</th>
            <th>claim</th>
            <th>horizon</th>
            <th>probability</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} data-testid="prediction-row" data-status={r.status}>
              <td>{r.id}</td>
              <td>{r.claim_id}</td>
              <td>{r.horizon}</td>
              <td>{r.probability}</td>
              <td>
                <StatusBadge
                  tone={r.status === "REGISTERED" ? "ok" : r.status === "DUE" || r.status === "PENDING_DECISION" ? "warn" : "neutral"}
                  label={r.status}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
