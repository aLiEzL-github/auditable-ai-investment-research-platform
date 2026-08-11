// G5-04 宏观与计算页验收
//  · 派生判断和外部事实明确区分（[D:] vs [F:]）
//  · MacroGate 失败 → 不得输出当前估值（C-2 UI 形态）
//  · 宏观四时刻分离（G3-03）

import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { WorkbenchProvider } from "../src/state/WorkbenchContext";
import { MacroPage } from "../src/pages/MacroPage";
import { MockApi } from "../src/api/mock";
import type { WorkbenchApi } from "../src/api/client";

function renderPage(api: WorkbenchApi = new MockApi()) {
  return render(
    <WorkbenchProvider api={api}>
      <MacroPage />
    </WorkbenchProvider>,
  );
}

describe("MacroPage（G5-04）", () => {
  it("派生与外部事实明确区分：[F:] 与 [D:] 标签各自出现", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("calc-input-EXTERNAL_FACT").length).toBeGreaterThan(0);
      expect(screen.getAllByTestId("calc-input-DERIVED").length).toBeGreaterThan(0);
      expect(screen.getByText(/\[F:\] 营业收入/)).toBeInTheDocument();
      expect(screen.getByText(/\[D:\] FCFF/)).toBeInTheDocument();
    });
  });

  it("MacroGate 通过态可见，且四时刻分离显示", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("macro-gate-pass")).toBeInTheDocument();
      const times = screen.getByTestId("macro-times").textContent!;
      expect(times).toContain("published:");
      expect(times).toContain("effective:");
      expect(times).toContain("retrieved:");
      expect(times).toContain("cutoff:");
    });
  });

  it("变异注入 ①：宏观门失败 → 明示不得输出当前估值，不显示通过", async () => {
    const mutated: WorkbenchApi = Object.assign(new MockApi(), {
      getMacroView: async () => {
        const v = await new MockApi().getMacroView();
        v.snapshot.gate.verdict = "MACRO_GATE_FAIL";
        v.snapshot.gate.failures = ["材料性序列 GDP 过期（EXPIRED）"];
        return v;
      },
    });
    renderPage(mutated);
    await waitFor(() => {
      const fail = screen.getByTestId("macro-gate-fail");
      expect(fail.textContent).toContain("不得输出当前估值");
      expect(fail.textContent).toContain("材料性序列 GDP 过期");
      expect(screen.queryByTestId("macro-gate-pass")).toBeNull();
    });
  });

  it("CalcLedger 每笔含公式版本 + 输入/结果哈希（可回源）", async () => {
    renderPage();
    await waitFor(() => {
      const entries = screen.getAllByTestId("calc-entry");
      expect(entries[0].textContent).toContain("FCFF@1.0");
      expect(entries[0].textContent).toContain("hash=aaaa");
      expect(screen.getByText(/结果哈希逐笔可回溯/)).toBeInTheDocument();
    });
  });
});
