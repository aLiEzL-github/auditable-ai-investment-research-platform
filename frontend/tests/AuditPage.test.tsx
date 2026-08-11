// G5-05 审计与发布页验收（基线 B §8 + G5 执行计划 §3.1/§3.2）
//  · E-1/E-2/E-3（一票否决）：release_eligible 只展示后端返回，前端无计算点
//  · E-5：阻断原因逐条可见
//  · Gate 7 前真实研究发布控件强制禁用
//  · 预测状态（登记/到期/未决/不可裁决）+ 校准充分性
//  · 闭包：完整/缺对象可分辨（D-10 语义）
// 变异注入：前端伪造 eligible、绕过禁用控件、把闭包缺对象标完整，均须被抓出

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { AuditPage } from "../src/pages/AuditPage";
import { MockApi } from "../src/api/mock";
import type { WorkbenchApi } from "../src/api/client";
import type { AuditOverview } from "../src/types";

function renderPage(api: WorkbenchApi = new MockApi()) {
  return render(
    <WorkbenchProvider api={api}>
      <AuditPage />
    </WorkbenchProvider>,
  );
}

describe("AuditPage（G5-05）", () => {
  it("E-1/E-2: release_eligible 展示后端值（false）+ source 恒显", async () => {
    renderPage();
    await waitFor(() => {
      const el = screen.getByTestId("release-eligible");
      expect(el.getAttribute("data-eligible")).toBe("false");
      expect(el.textContent).toContain("release_eligible = false");
      expect(el.textContent).toContain("source: MOCK");
    });
  });

  it("E-5: 阻断原因逐条可见", async () => {
    renderPage();
    await waitFor(() => {
      const failures = screen.getAllByTestId("audit-failures")[0];
      expect(failures.textContent).toContain("E-G4-02-005");
      expect(failures.textContent).toContain("OI-9001");
    });
  });

  it("Gate 7 前发布控件强制禁用（按钮 disabled + 原因可见）", async () => {
    renderPage();
    await waitFor(() => {
      const btn = screen.getByTestId("publish-button");
      expect(btn).toBeDisabled();
      expect(btn.textContent).toContain("禁用");
      expect(screen.getByTestId("release-disabled").textContent).toContain("Gate 7 未达");
      expect(screen.getByTestId("publish-reason").textContent).toContain("强制禁用");
    });
    // 变异：直接点击（绕过禁用）—— 控件无 onClick 副作用，恒不可发布
    fireEvent.click(screen.getByTestId("publish-button"));
    expect(screen.getByTestId("current-keys").textContent).toContain("无（未发布）");
  });

  it("预测状态 + 校准充分性可见", async () => {
    renderPage();
    await waitFor(() => {
      const rows = screen.getAllByTestId("prediction-row");
      expect(rows.length).toBeGreaterThan(0);
      expect(rows[0].getAttribute("data-status")).toBe("REGISTERED");
      const cal = screen.getByTestId("calibration-status");
      expect(cal.textContent).toContain("未建立");
      expect(cal.textContent).toContain("CALIBRATION_PENDING");
    });
  });

  it("闭包：完整对象数可机检，缺对象态与完整态可分辨", async () => {
    renderPage();
    await waitFor(() => {
      const status = screen.getByTestId("closure-status");
      expect(status.textContent).toContain("完整: true");
      expect(status.textContent).toContain("dangling: 0");
      expect(screen.getByText(/对象闭包 —— 11 个对象/)).toBeInTheDocument();
      expect(screen.getByTestId("closure-objects").children.length).toBeGreaterThan(0);
      expect(screen.queryByTestId("closure-incomplete")).toBeNull();
    });
  });

  it("变异注入 ①: 后端返回 eligible=true → UI 如实显示 true（前端不改写）", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getAuditOverview: async (): Promise<AuditOverview> => {
        const o = await new MockApi().getAuditOverview();
        o.audit.release_eligible = true;
        o.audit.failures = [];
        o.audit.gates = o.audit.gates.map((g) => ({ ...g, verdict: "PASS" }));
        return o;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      const el = screen.getByTestId("release-eligible");
      expect(el.getAttribute("data-eligible")).toBe("true");
      expect(el.textContent).toContain("release_eligible = true");
      expect(screen.queryByTestId("audit-failures")).toBeNull();
    });
  });

  it("变异注入 ②: 前端伪造「已发布」（current 非空但后端未批准）→ UI 只信后端 current 字段", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getReleasesView: async () => {
        const r = await new MockApi().getReleasesView();
        // 变异：把 current 伪装成存在 —— 但 audit.release_eligible 仍 false
        r.keys[0].current = {
          id: "a".repeat(64),
          version: "1.0.0",
          parent_cas: "b".repeat(64),
          subject_root_hash: "c".repeat(64),
          manifest_hash: "d".repeat(64),
          released_at: "2026-08-11T00:00:00Z",
          approval_id: "APR-XXX",
          current_pointer: true,
        };
        return r;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      // 发布页按 releases 数据渲染 current —— 但 eligible 仍按 audit 显示 false
      expect(screen.getByTestId("current-keys").textContent).toContain("v1.0.0");
      expect(screen.getByTestId("release-eligible").getAttribute("data-eligible")).toBe("false");
      // Gate 7 未达 —— 按钮仍禁用，不得因 current 存在而放开
      expect(screen.getByTestId("publish-button")).toBeDisabled();
    });
  });

  it("变异注入 ③: 闭包缺对象被标 complete=true（冒充完整）→ UI 如实显示后端值，但缺对象信息仍可见", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getClosureView: async () => {
        const c = await new MockApi().getClosureView();
        // 变异：dangling>0 却声明 complete —— 前端不得自己「修复」为 incomplete
        c.complete = true;
        c.dangling = 3;
        return c;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      const status = screen.getByTestId("closure-status");
      expect(status.textContent).toContain("完整: true");
      expect(status.textContent).toContain("dangling: 3");
    });
  });
});
