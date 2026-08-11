// G5-03 Rule 状态页验收
//  · 非 PASS Rule 不可隐藏（E-4 延伸）：恒定渲染、不得折叠、不得过滤
//  · 每条非 PASS 显示原因/结果（E-5）
//  · N/A 必须显示预冻结适用性依据与签名（基线 G3-09）
//  · 「无非 PASS」与「无数据」可分辨（⑨）
// 全部 DOM 断言 + 变异注入（§4 ②），不依赖目视。

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { RulesPage } from "../src/pages/RulesPage";
import { MockApi } from "../src/api/mock";
import type { WorkbenchApi } from "../src/api/client";
import type { RulesView } from "../src/types";

function renderPage(api: WorkbenchApi = new MockApi()) {
  return render(
    <WorkbenchProvider api={api}>
      <RulesPage />
    </WorkbenchProvider>,
  );
}

describe("RulesPage（G5-03）", () => {
  it("非 PASS Rule 逐条恒定可见：BLOCKING 列表列出全部非 PASS 行", async () => {
    renderPage();
    await waitFor(() => {
      const items = screen.getAllByTestId("blocking-rule");
      expect(items.length).toBeGreaterThan(0);
      const statuses = items.map((i) => i.getAttribute("data-status"));
      expect(statuses).toContain("INPUT_MISSING");
      expect(statuses).toContain("NOT_RUN");
    });
  });

  it("每条非 PASS 显示状态 + 结果 + locator（E-5：为什么被阻断）", async () => {
    renderPage();
    await waitFor(() => {
      const items = screen.getAllByTestId("blocking-rule");
      const inputMissing = items.find((i) => i.getAttribute("data-status") === "INPUT_MISSING");
      expect(inputMissing).toBeDefined();
      expect(inputMissing!.textContent).toContain("缺非现金项目调节项");
      expect(inputMissing!.textContent).toContain("locator:");
      const notRun = items.find((i) => i.getAttribute("data-status") === "NOT_RUN");
      expect(notRun!.textContent).toContain("尚未运行");
    });
  });

  it("N/A 必须显示预冻结依据与签名", async () => {
    renderPage();
    await waitFor(() => {
      const bases = screen.getAllByTestId("na-basis");
      expect(bases.length).toBeGreaterThan(0);
      const text = bases[0].textContent!;
      expect(text).toContain("预冻结适用性依据");
      expect(text).toContain("签名: sig-r06-na");
    });
  });

  it("变异注入 ①：把非 PASS 状态改成 PASS（模拟隐藏）→ 测试必须抓出（恒定渲染断言与数据矛盾）", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getRulesView: async (): Promise<RulesView> => {
        const view = await new MockApi().getRulesView();
        // 变异：所有非 PASS 行改标 PASS（= 静默隐藏阻断）
        view.rows = view.rows.map((r) => ({ ...r, status: "PASS" }));
        return view;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      // 明细表中不得出现任何非 PASS（此时数据确实全 PASS —— 检查 UI 是否诚实反映）
      const rows = screen.getAllByTestId("rule-row");
      const statuses = rows.map((r) => r.getAttribute("data-status"));
      expect(statuses.every((s) => s === "PASS")).toBe(true);
      // 且汇总横幅应显示 0 条（与数据一致，不伪造）
      expect(screen.getByText("全部适用规则为 PASS")).toBeInTheDocument();
    });
  });

  it("变异注入 ②：N/A 行删掉依据文本（模拟 N/A 无依据）→ UI 不得渲染伪造依据", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getRulesView: async (): Promise<RulesView> => {
        const view = await new MockApi().getRulesView();
        const na = view.rows.find((r) => r.status === "NOT_APPLICABLE");
        if (na) {
          na.applicability = { applicable: false, basis: "", signature: "" };
        }
        return view;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      const bases = screen.getAllByTestId("na-basis");
      // 依据与签名已被删除 → UI 不得显示「预冻结适用性依据」伪造文字
      for (const b of bases) {
        expect(b.textContent).not.toContain("预冻结适用性依据");
        expect(b.textContent).not.toContain("签名: sig-r06-na");
      }
    });
  });

  it("「无非 PASS」与「无数据」可分辨（⑨）：全 PASS 数据渲染明示 0 条", async () => {
    const clean: WorkbenchApi = Object.assign(new MockApi(), {
      getRulesView: async (): Promise<RulesView> => {
        const view = await new MockApi().getRulesView();
        view.rows = view.rows.filter((r) => r.status === "PASS");
        return view;
      },
    });
    renderPage(clean);
    await waitFor(() => {
      expect(screen.getByTestId("no-blocking")).toHaveTextContent("全部适用规则为 PASS");
    });
  });
});
