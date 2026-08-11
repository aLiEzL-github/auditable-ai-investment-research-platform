// 页面外壳与布局（G5-01「UI 外壳」）
// E-9：SINGLE_REVIEWER_ATTESTED 须首屏可见（与 OI-PF-070 同义），不得置于折叠区或页脚

import { NavLink, Outlet } from "react-router-dom";
import { ReleaseStatusBanner } from "./ReleaseStatusBanner";
import { useWorkbench } from "../state/WorkbenchContext";
import { useCallback } from "react";

const NAV_ITEMS = [
  { to: "/research/new", label: "新建研究" },
  { to: "/evidence", label: "证据台账" },
  { to: "/macro", label: "宏观与计算" },
  { to: "/claims", label: "Claim 与假设" },
  { to: "/audit", label: "审计与发布" },
];

export function Shell() {
  const { eligibility, checkEligibility } = useWorkbench();
  const onClickCheck = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      void checkEligibility();
    },
    [checkEligibility],
  );

  return (
    <div className="shell">
      <header className="shell__header">
        <div className="shell__brand">证据优先研究工作台</div>
        <nav className="shell__nav" aria-label="主导航">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => (isActive ? "shell__link shell__link--active" : "shell__link")}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <button type="button" className="shell__check" onClick={onClickCheck}>
          检查发布资格
        </button>
      </header>
      <div className="shell__attestation" data-testid="attestation">
        SINGLE_REVIEWER_ATTESTED：无独立第二人复核。研究信息不构成投资建议。
      </div>
      <ReleaseStatusBanner load={eligibility} />
      <main className="shell__main">
        <Outlet />
      </main>
    </div>
  );
}
