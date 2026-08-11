// Claim 与假设页（G5-04，基线 B §8）
// 交付件：Claim 图与 emission map、AssumptionProposal/Snapshot、OpenItem。
// 验收：未批准假设、无 Claim 绑定可见内容和材料性 OpenItem 有醒目标识。
//   · 无绑定可见内容（bound=false）醒目异常（E-8 延伸：不得静默）
//   · 未批准假设（PENDING）醒目
//   · 材料性 OpenItem 醒目

import { useEffect, useState } from "react";
import { useWorkbench } from "../state/WorkbenchContext";
import { Card, EmptyState, ErrorState } from "../components/Basic";
import { StatusBadge } from "../components/StatusBadge";
import type { AssumptionsView, ClaimsView, OpenItemsView } from "../types";

export function ClaimsPage() {
  const { api } = useWorkbench();
  const [claims, setClaims] = useState<ClaimsView | null>(null);
  const [assumptions, setAssumptions] = useState<AssumptionsView | null>(null);
  const [openItems, setOpenItems] = useState<OpenItemsView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.getClaimsView(), api.getAssumptionsView(), api.getOpenItemsView()])
      .then(([c, a, o]) => {
        if (!cancelled) {
          setClaims(c);
          setAssumptions(a);
          setOpenItems(o);
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
  if (claims == null || assumptions == null || openItems == null)
    return <EmptyState label="Claim 与假设加载中…" />;

  const unbound = claims.nodes.filter((n) => !n.bound);
  const pending = assumptions.rows.filter((r) => r.status === "PENDING");
  const materialOpen = openItems.rows.filter((r) => r.material && r.status === "OPEN");

  return (
    <div className="page">
      <h1>Claim 与假设</h1>

      {/* 无 Claim 绑定的可见内容：醒目异常 */}
      <Card title={`无 Claim 绑定可见内容 —— ${claims.unbound_count} 条（醒目异常）`}>
        {claims.unbound_count === 0 ? (
          <p data-testid="no-unbound">全部可见内容均有 Claim 绑定</p>
        ) : (
          <ul data-testid="unbound-list">
            {unbound.map((n) => (
              <li key={n.ref_id} data-testid="unbound-item" className="alert-item">
                <strong>{n.ref_id}</strong> 无 Claim 绑定 —— 可见内容「{n.rendered_value}」
                <p className="source-sha">span: {n.visible_span}</p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Claim 图 */}
      <Card title={`Claim 图（${claims.nodes.length} 节点）`}>
        <table className="rules-table" data-testid="claims-table">
          <thead>
            <tr>
              <th>节点</th>
              <th>类型</th>
              <th>内容</th>
              <th>材料性</th>
              <th>证据/公式/假设引用</th>
              <th>emission span</th>
            </tr>
          </thead>
          <tbody>
            {claims.nodes.map((n) => (
              <tr key={n.ref_id} data-testid="claim-node" data-type={n.node_type}>
                <td>{n.ref_id}</td>
                <td>[{n.node_type}]</td>
                <td>{n.rendered_value}</td>
                <td>{n.materiality}</td>
                <td>
                  {n.evidence_refs.length > 0 && `证据: ${n.evidence_refs.join(",")} `}
                  {n.formula_ref != null && `公式: ${n.formula_ref} `}
                  {n.assumption_ref != null && `假设: ${n.assumption_ref}`}
                </td>
                <td className="source-sha">{n.visible_span}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* 假设：未批准醒目 */}
      <Card title={`假设（${assumptions.rows.length} 条）`}>
        {pending.length > 0 && (
          <div data-testid="pending-assumptions" className="alert-item">
            <strong>{pending.length} 条未批准假设 —— 醒目（PENDING）</strong>
          </div>
        )}
        <ul data-testid="assumption-list">
          {assumptions.rows.map((r) => (
            <li key={r.proposal_id} data-testid="assumption" data-status={r.status}>
              <StatusBadge
                tone={r.status === "APPROVED" ? "ok" : r.status === "PENDING" ? "warn" : "neutral"}
                label={r.status}
              />
              <strong>{r.proposal_id}</strong> {r.payload_summary}
              {r.status === "APPROVED" && r.snapshot_sha256 != null && (
                <p className="source-sha">不可变快照: {r.snapshot_sha256} · approved_at: {r.approved_at}</p>
              )}
              {r.status === "PENDING" && (
                <p className="source-sha">未批准 —— 不得作为计算输入（醒目）</p>
              )}
            </li>
          ))}
        </ul>
      </Card>

      {/* 材料性 OpenItem 醒目 */}
      <Card title={`材料性 OPEN 开放项 —— ${materialOpen.length} 条（醒目）`}>
        {materialOpen.length === 0 ? (
          <p data-testid="no-material-open">无材料性 OPEN 开放项</p>
        ) : (
          <ul data-testid="material-open-list">
            {materialOpen.map((o) => (
              <li key={o.open_item_id} data-testid="material-open-item" className="alert-item">
                <strong>{o.open_item_id}</strong>（材料性）{o.description}
                <p className="source-sha">
                  owner={o.owner_role} · due={o.due_date ?? "—"} · blocks={o.blocks_gate ?? "—"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
