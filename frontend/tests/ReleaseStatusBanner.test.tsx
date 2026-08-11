// G5-01 阻断态横幅验收（§3.2 E-4/E-5/E-6 + §4 ② 变异注入）
// 断言全部基于 DOM 结构与可见性属性，不依赖人工目视。

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReleaseStatusBanner } from "../src/components/ReleaseStatusBanner";
import type { EligibilityLoad } from "../src/state/WorkbenchContext";

const notChecked: EligibilityLoad = { phase: "NOT_CHECKED" };
const clear: EligibilityLoad = {
  phase: "LOADED",
  value: {
    status: "CLEAR",
    reasons: [],
    checked_at: "2026-08-11T06:33:15Z",
    source: "BACKEND",
  },
};
const blocked: EligibilityLoad = {
  phase: "LOADED",
  value: {
    status: "BLOCKED",
    reasons: [
      { code: "OI-9001", detail: "存在材料性开放项" },
      { code: "APPROVAL-001", detail: "缺少批准" },
    ],
    checked_at: "2026-08-11T06:33:15Z",
    source: "BACKEND",
  },
};
const error: EligibilityLoad = { phase: "ERROR", message: "evidence chain broken" };

describe("ReleaseStatusBanner (E-4/E-5/E-6/E-8)", () => {
  it("E-4: BLOCKED 态恒定渲染于文档且可见（非折叠、非交互展开、非仅颜色）", () => {
    render(<ReleaseStatusBanner load={blocked} />);
    const el = screen.getByTestId("release-status");
    expect(el).toBeInTheDocument();
    expect(el.getAttribute("data-status")).toBe("BLOCKED");
    expect(getComputedStyle(el).display).not.toBe("none");
    expect(getComputedStyle(el).visibility).not.toBe("hidden");
    const title = el.querySelector(".release-banner__title");
    expect(title?.textContent).toContain("被阻断");
  });

  it("E-5: 阻断原因逐条可见（code + detail）", () => {
    render(<ReleaseStatusBanner load={blocked} />);
    const reasons = screen.getAllByTestId("blocking-reason");
    expect(reasons).toHaveLength(2);
    expect(reasons[0].textContent).toContain("OI-9001");
    expect(reasons[0].textContent).toContain("存在材料性开放项");
    expect(reasons[1].textContent).toContain("APPROVAL-001");
    expect(reasons[1].textContent).toContain("缺少批准");
  });

  it("E-6: 「已检查无阻断」与「尚未检查」文本互不相同", () => {
    render(<ReleaseStatusBanner load={notChecked} />);
    render(<ReleaseStatusBanner load={clear} />);
    const titles = screen.getAllByText(/发布资格/);
    const texts = titles.map((t) => t.textContent);
    expect(texts[0]).toContain("尚未检查");
    expect(texts[1]).toContain("已检查，无阻断");
    expect(texts[0]).not.toBe(texts[1]);
  });

  it("E-6: 无阻断不等于未检查 —— CLEAR 的 data-status 为 CLEAR 而非 NOT_CHECKED", () => {
    render(<ReleaseStatusBanner load={clear} />);
    const el = screen.getByTestId("release-status");
    expect(el.getAttribute("data-status")).toBe("CLEAR");
  });

  it("E-8: 证据链断裂 → 显式报错态，不得显示「无阻断」", () => {
    render(<ReleaseStatusBanner load={error} />);
    const el = screen.getByTestId("release-status");
    expect(el.getAttribute("data-status")).toBe("ERROR");
    expect(screen.getByTestId("blocking-error").textContent).toContain("evidence chain broken");
    expect(el.textContent).not.toContain("无阻断");
    expect(el.textContent).not.toContain("被阻断（不可发布）");
  });

  it("变异注入 ①: 把 BLOCKED 改为 CLEAR 的伪造响应必须被判为 CLEAR（前端不自行推导结论）", () => {
    const forged: EligibilityLoad = {
      phase: "LOADED",
      value: { status: "CLEAR", reasons: [], checked_at: null, source: "BACKEND" },
    };
    render(<ReleaseStatusBanner load={forged} />);
    const el = screen.getByTestId("release-status");
    expect(el.getAttribute("data-status")).toBe("CLEAR");
    expect(el.textContent).toContain("已检查，无阻断");
  });
});
