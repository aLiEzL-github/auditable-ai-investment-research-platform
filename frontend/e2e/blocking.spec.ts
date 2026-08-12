// G5-07 阻断态不可隐藏、不可通过前端直接改写（基线 §8 G5-07 原文）
// E-3（一票否决）/E-4：浏览器级变异注入 ——
//   · 直接改 DOM 状态属性（DevTools 等效）→ 页面如实反映后端值，阻断不消失
//   · 构造伪造请求（route 拦截替换后端响应为 CLEAR）→ 阻断态仍显示后端真实值
// 这验证的是「前端不是控制层」在真实浏览器中的行为。

import { expect, test } from "@playwright/test";

test.describe("G5-07 阻断态不可隐藏 / 不可前端改写", () => {
  test("E-4: 阻断横幅恒定渲染 —— DOM 可见性属性断言（非 display:none / 非折叠）", async ({ page }) => {
    await page.goto("/audit");
    const banner = page.getByTestId("release-eligible");
    await expect(banner).toBeVisible();
    const visible = await banner.evaluate((el) => {
      const cs = getComputedStyle(el);
      return cs.display !== "none" && cs.visibility !== "hidden";
    });
    expect(visible).toBe(true);
  });

  test("E-3 变异注入 ①: DevTools 改 data-eligible 为 true → 阻断文本仍在（文本与数据属性分离）", async ({ page }) => {
    await page.goto("/audit");
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
    // 直接改写 DOM 属性（等价 DevTools 操作）
    await page.getByTestId("release-eligible").evaluate((el) => {
      el.setAttribute("data-eligible", "true");
    });
    // 后端返回的阻断信息（failures 列表）仍恒定渲染 —— 前端状态被改写不改变事实
    await expect(page.getByTestId("audit-failures")).toBeVisible();
    await expect(page.getByText(/release_eligible = false/)).toBeVisible();
  });

  test("E-3 变异注入 ②: 拦截 API 响应伪造 CLEAR → 页面显示后端真实值（BLOCKED 不消失）", async ({ page }) => {
    // 构造请求体替换：把 /api/audit 响应改成 release_eligible=true
    await page.route("**/api/audit", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.audit.release_eligible = true;
      body.audit.failures = [];
      await route.fulfill({ response, body: JSON.stringify(body) });
    });
    await page.goto("/audit");
    // mock 后端仍返回 BLOCKED —— 页面渲染的是后端结论
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
    await expect(page.getByTestId("audit-failures")).toBeVisible();
  });

  test("E-3 变异注入 ③: 直接调用页面暴露的改写逻辑（构造绕过 UI 的调用）→ 阻断不消失", async ({ page }) => {
    await page.goto("/audit");
    // 在页面上下文直接尝试「伪造」eligible 状态 —— 无任何前端 API 可改写后端结论
    const hasLocalEligibilitySetter = await page.evaluate(() => {
      // 全局无任何可改 eligible 的对象（前端无写路径）
      return typeof (window as unknown as Record<string, unknown>).setEligibility === "function";
    });
    expect(hasLocalEligibilitySetter).toBe(false);
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
  });
});
