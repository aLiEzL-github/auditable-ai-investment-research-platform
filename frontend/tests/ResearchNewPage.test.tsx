// G5-02 新建研究页验收（基线 B §8：缺 ResearchContract 不能启动）
// DOM 断言 + 变异注入（§4 ②），不依赖目视。

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { ResearchNewPage } from "../src/pages/ResearchNewPage";
import type { WorkbenchApi } from "../src/api/client";
import { MockApi } from "../src/api/mock";

function renderPage(api: WorkbenchApi = new MockApi()) {
  return render(
    <WorkbenchProvider api={api}>
      <ResearchNewPage />
    </WorkbenchProvider>,
  );
}

function fillForm(values: Record<string, string>) {
  for (const [key, value] of Object.entries(values)) {
    fireEvent.change(screen.getByTestId(`form-${key}`), { target: { value } });
  }
}

const COMPLETE_FORM: Record<string, string> = {
  market: "A-share",
  security: "600089.SH",
  as_of: "2026-08-11",
  horizon: "2026",
  model: "v0.1.0",
  budget: "budget-2026",
};

describe("ResearchNewPage（G5-02）", () => {
  it("交付件：六个表单字段齐备（市场/证券/as-of/期限/模型/预算）", () => {
    renderPage();
    for (const key of Object.keys(COMPLETE_FORM)) {
      expect(screen.getByTestId(`form-${key}`)).toBeInTheDocument();
    }
  });

  it("验收：空表单 → 启动按钮禁用 + 显式列出全部缺项（不能启动）", () => {
    renderPage();
    const button = screen.getByTestId("launch-button");
    expect(button).toBeDisabled();
    const gate = screen.getByTestId("launch-gate");
    expect(gate.getAttribute("data-state")).toBe("INCOMPLETE");
    expect(gate.textContent).toContain("缺字段");
    expect(gate.textContent).toContain("市场");
    expect(gate.textContent).toContain("证券");
  });

  it("变异注入 ①：只填五个字段仍不能启动（缺项随已填字段缩减）", () => {
    renderPage();
    fillForm({
      market: "A-share",
      as_of: "2026-08-11",
      horizon: "2026",
      model: "v0.1.0",
      budget: "b",
    });
    const gate = screen.getByTestId("launch-gate");
    expect(gate.getAttribute("data-state")).toBe("INCOMPLETE");
    expect(screen.getByTestId("launch-button")).toBeDisabled();
    expect(gate.textContent).toContain("证券");
  });

  it("变异注入 ②：字段齐备后清空一个字段（模拟状态被改写）→ 立即转回禁用", () => {
    renderPage();
    fillForm(COMPLETE_FORM);
    expect(screen.getByTestId("launch-gate").getAttribute("data-state")).toBe("COMPLETE");
    fireEvent.change(screen.getByTestId("form-as_of"), { target: { value: "" } });
    expect(screen.getByTestId("launch-gate").getAttribute("data-state")).toBe("INCOMPLETE");
    expect(screen.getByTestId("launch-button")).toBeDisabled();
    expect(screen.getByTestId("launch-gate").textContent).toContain("as-of");
  });

  it("字段齐备时启动成功（mock 返回 run_id + DRAFT）", async () => {
    renderPage();
    fillForm(COMPLETE_FORM);
    fireEvent.click(screen.getByTestId("launch-button"));
    await waitFor(() => {
      const gate = screen.getByTestId("launch-gate");
      expect(gate.getAttribute("data-state")).toBe("LAUNCHED");
      expect(gate.textContent).toContain("run-");
      expect(gate.textContent).toContain("DRAFT");
    });
  });

  it("变异注入 ③：mock 后端拒绝（缺字段 payload）→ UI 显式失败态，不显示成功（E-8）", async () => {
    const failing: WorkbenchApi = Object.assign(new MockApi(), {
      launchResearch: vi.fn().mockResolvedValue({
        ok: false,
        error: "E-G5-02-001: 缺 ResearchContract 字段: workflow",
      }) as WorkbenchApi["launchResearch"],
    });
    renderPage(failing);
    fillForm(COMPLETE_FORM);
    fireEvent.click(screen.getByTestId("launch-button"));
    await waitFor(() => {
      const gate = screen.getByTestId("launch-gate");
      expect(gate.getAttribute("data-state")).toBe("FAILED");
      expect(gate.textContent).toContain("E-G5-02-001");
      expect(gate.textContent).not.toContain("研究已启动");
    });
  });

  it("变异注入 ④：后端抛错（断链）→ 显式失败态而非静默", async () => {
    const broken: WorkbenchApi = Object.assign(new MockApi(), {
      launchResearch: vi.fn().mockRejectedValue(
        new Error("backend unreachable"),
      ) as WorkbenchApi["launchResearch"],
    });
    renderPage(broken);
    fillForm(COMPLETE_FORM);
    fireEvent.click(screen.getByTestId("launch-button"));
    await waitFor(() => {
      const gate = screen.getByTestId("launch-gate");
      expect(gate.getAttribute("data-state")).toBe("FAILED");
      expect(gate.textContent).toContain("backend unreachable");
      expect(gate.textContent).not.toContain("研究已启动");
    });
  });
});
