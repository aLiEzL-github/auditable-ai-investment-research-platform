// G5-07 UI 端到端纵向验收 —— 固定 fixture 全流程
// 基线 §8 G5-07：从合同、规则、Claim/假设、预测、开放项、闭包到不可变发布的浏览器用例。
// fixture = mock schema（固定值，可复现）。

import { expect, test } from "@playwright/test";

test.describe("G5-07 固定 fixture 全流程", () => {
  test("纵向流程：新建研究 → 规则 → Claim/假设 → 预测 → 闭包 → 发布资格", async ({ page }) => {
    // ① 合同：新建研究页（六字段 + 启动）
    await page.goto("/research/new");
    await expect(page.getByRole("heading", { name: "新建研究" })).toBeVisible();
    for (const key of ["market", "security", "as_of", "horizon", "model", "budget"]) {
      await page.getByTestId(`form-${key}`).fill(
        key === "market" ? "A-share"
        : key === "security" ? "600089.SH"
        : key === "as_of" ? "2026-08-11"
        : key === "horizon" ? "2026"
        : key === "model" ? "v0.1.0"
        : "budget-2026",
      );
    }
    await expect(page.getByTestId("launch-button")).toBeEnabled();
    await page.getByTestId("launch-button").click();
    await expect(page.getByTestId("launch-gate")).toHaveAttribute("data-state", "LAUNCHED");

    // ② 规则：非 PASS Rule 恒定可见
    await page.goto("/rules");
    await expect(page.getByRole("heading", { name: /Rule 状态/ })).toBeVisible();
    const blocking = page.getByTestId("blocking-rule");
    await expect(blocking.first()).toBeVisible();
    expect(await blocking.count()).toBeGreaterThan(0);

    // ③ Claim/假设 + 开放项：无绑定 + 未批准假设 + 材料性 OPEN 醒目
    await page.goto("/claims");
    await expect(page.getByTestId("unbound-item")).toBeVisible();
    await expect(page.getByTestId("pending-assumptions")).toBeVisible();
    await expect(page.getByTestId("material-open-item")).toBeVisible();

    // ④ 预测：校准未建立（CALIBRATION_PENDING）
    await page.goto("/audit");
    await expect(page.getByTestId("calibration-status")).toContainText("未建立");

    // ⑤ 闭包：count 可机检
    await expect(page.getByText(/对象闭包 —— 11 个对象/)).toBeVisible();

    // ⑦ 不可变发布：release_eligible=false 阻断 + Gate 7 前发布禁用
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
    await expect(page.getByTestId("publish-button")).toBeDisabled();
  });
});
