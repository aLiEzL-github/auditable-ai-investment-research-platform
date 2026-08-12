// E-4 验收：「阻断态不可隐藏」DOM 断言 + 截图留档
// 截图保存于 e2e/screenshots/，作为阻断态可见性的视觉证据。

import { expect, test } from "@playwright/test";

test("E-4 截图留档：审计页阻断态（release_eligible=false + 阻断原因 + 发布禁用）", async ({ page }) => {
  await page.goto("/audit");
  await expect(page.getByTestId("release-eligible")).toBeVisible();
  await expect(page.getByTestId("audit-failures")).toBeVisible();
  await expect(page.getByTestId("publish-button")).toBeDisabled();
  await page.screenshot({
    path: "e2e/screenshots/audit-blocked.png",
    fullPage: true,
  });
});

test("E-4 截图留档：规则页非 PASS 列表（阻断态恒定可见）", async ({ page }) => {
  await page.goto("/rules");
  await expect(page.getByTestId("blocking-rule").first()).toBeVisible();
  await page.screenshot({
    path: "e2e/screenshots/rules-blocking.png",
    fullPage: true,
  });
});
