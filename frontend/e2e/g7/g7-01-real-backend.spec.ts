// G7-01 真实后端 E2E（合成 golden 三例，VITE_API_MODE=http，绝不使用 MockApi）
// 浏览器流量全部经 vite preview 的 /api 代理打到真实 Python 后端
// （G7_E2E_MODE=1 运行时），判定只由后端 rules_engine.evaluate 计算。
//
// 三例 canonical：
//   POSITIVE     结构正常年度例 —— 适用硬规则 R01—R10 全部 PASS，候选合格
//   RESTATEMENT  未处理重述 —— R10=RESTATEMENT_PENDING，候选与发布受阻
//   WRONG_BASIS  累计/单季、跨范围/口径错配 —— 规则 FAIL，候选与发布受阻
// 另覆盖：四预测态、后端 source 标注、发布禁用、后端不可达显式报错、
// 缺闭包对象阻断、预测错绑定失败关闭（资格端点如实 BLOCKED）、
// 客户端改写资格后端保持 false（DOM 变异后重载恢复）、
// 同一契约确定性重跑（run_id/candidate_id 稳定）与 launch source 标注。

import { expect, test } from "@playwright/test";

interface FormContract {
  market: string;
  security: string;
  as_of: string;
  horizon: string;
  model: string;
  budget: string;
}

// 与 backend/tests/fixtures/g7-01/*.json 的 contract.scope 一一对应（合成值）
const CONTRACTS: Record<string, FormContract> = {
  POSITIVE: {
    market: "A-share",
    security: "SYN-700001.SH",
    as_of: "2026-08-11",
    horizon: "2026",
    model: "v0.1.0",
    budget: "budget-2026",
  },
  RESTATEMENT: {
    market: "A-share",
    security: "SYN-700002.SH",
    as_of: "2026-08-11",
    horizon: "2026",
    model: "v0.1.0",
    budget: "budget-2026",
  },
  WRONG_BASIS: {
    market: "A-share",
    security: "SYN-700003.SH",
    as_of: "2026-08-11",
    horizon: "2026",
    model: "v0.1.0",
    budget: "budget-2026",
  },
};

// 与 frontend ResearchNewPage.toContract 逐字一致的契约构造（浏览器→后端 HTTP 流）
function toContract(c: FormContract) {
  return {
    scope: c.security.replace(/\.SH$|\.SZ$/, ""),
    period: c.horizon,
    unit: "CNY_million",
    vintage: c.as_of.slice(0, 7),
    snapshot: "SNAP-001",
    security_code: c.security,
    company_id: c.security.split(".")[0] ?? c.security,
    as_of: c.as_of,
    version: c.model,
    workflow: "a-share-single-company-research",
  };
}

async function resetAndLaunch(page: import("@playwright/test").Page, c: FormContract) {
  await page.request.post("/api/g7/reset");
  await page.goto("/research/new");
  for (const key of ["market", "security", "as_of", "horizon", "model", "budget"] as const) {
    await page.getByTestId(`form-${key}`).fill(c[key]);
  }
  await page.getByTestId("launch-button").click();
  await expect(page.getByTestId("launch-gate")).toHaveAttribute("data-state", "LAUNCHED");
}

test.describe("G7-01 真实后端 E2E（三例合成 golden）", () => {
  test("POSITIVE: 年度正常例 —— R01-R10 全 PASS、eligible=true、四预测态、发布禁用", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.POSITIVE);

    await page.goto("/rules");
    await expect(page.getByTestId("no-blocking")).toBeVisible();
    const statuses = await page
      .locator('[data-testid="rule-row"]')
      .evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
    expect(statuses.length).toBe(10);
    expect(statuses.every((s) => s === "PASS")).toBe(true);

    await page.goto("/audit");
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "true");
    await expect(page.getByTestId("release-eligible")).toContainText("source: BACKEND");

    // 四预测态：REGISTERED / DUE / PENDING_DECISION / UNDECIDABLE
    const predStatuses = await page
      .locator('[data-testid="prediction-row"]')
      .evaluateAll((els) => els.map((e) => e.getAttribute("data-status")));
    expect(new Set(predStatuses)).toEqual(
      new Set(["REGISTERED", "DUE", "PENDING_DECISION", "UNDECIDABLE"]),
    );

    // 闭包完整 + 发布禁用（Gate 7 未达）
    await expect(page.getByTestId("closure-status")).toContainText("完整: true");
    await expect(page.getByTestId("closure-status")).toContainText("dangling: 0");
    await expect(page.getByTestId("publish-button")).toBeDisabled();
    await expect(page.getByTestId("release-disabled")).toContainText("Gate 7 未达");
    await expect(page.getByTestId("current-keys")).toContainText("无（未发布）");
  });

  test("RESTATEMENT: 未处理重述 —— R10=RESTATEMENT_PENDING、候选与发布受阻", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.RESTATEMENT);

    await page.goto("/rules");
    await expect(
      page.getByTestId("blocking-rule").filter({ hasText: "R10" }),
    ).toHaveAttribute("data-status", "RESTATEMENT_PENDING");

    await page.goto("/audit");
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
    await expect(page.getByTestId("audit-failures")).toBeVisible();
    await expect(page.getByTestId("audit-failures")).toContainText("RESTATEMENT_PENDING");
    await expect(page.getByTestId("publish-button")).toBeDisabled();

    // 发布资格横幅：后端 source 标注 + BLOCKED
    await page.getByRole("button", { name: "检查发布资格" }).click();
    await expect(page.getByTestId("release-status")).toHaveAttribute("data-status", "BLOCKED");
    await expect(page.getByTestId("blocking-reason")).toContainText("R10");
  });

  test("WRONG_BASIS: 累计/单季 + 跨范围/口径错配 —— 规则 FAIL、候选与发布受阻", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.WRONG_BASIS);

    await page.goto("/rules");
    await expect(
      page.getByTestId("blocking-rule").filter({ hasText: "R01" }),
    ).toHaveAttribute("data-status", "FAIL");
    await expect(
      page.getByTestId("blocking-rule").filter({ hasText: "R06" }),
    ).toHaveAttribute("data-status", "FAIL");
    await expect(
      page.getByTestId("blocking-rule").filter({ hasText: "R08" }),
    ).toHaveAttribute("data-status", "FAIL");

    await page.goto("/audit");
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
    await expect(
      page.getByTestId("audit-gate").filter({ hasText: "rules" }),
    ).toHaveAttribute("data-verdict", "FAIL");
    await expect(page.getByTestId("audit-failures")).toBeVisible();
    await expect(page.getByTestId("publish-button")).toBeDisabled();
  });

  test("确定性重跑 + launch 后端标注（浏览器→后端 HTTP 流）", async ({ page }) => {
    await page.request.post("/api/g7/reset");
    const body = toContract(CONTRACTS.POSITIVE);
    const first = await page.request.post("/api/research/launch", { data: body });
    expect(first.status()).toBe(200);
    const j1 = (await first.json()) as {
      source: string;
      run_id: string;
      candidate_id: string;
    };
    // 显式 launch 后端标注：source=backend（判定与身份来自后端运行时）
    expect(j1.source).toBe("backend");
    expect(j1.run_id).toMatch(/^run-g7-01-/);
    expect(j1.candidate_id).toMatch(/^[0-9a-f]{64}$/);

    // 同一契约确定性重跑 → 同一 run_id/candidate_id（身份来自 canonical 字节）
    await page.request.post("/api/g7/reset");
    const second = await page.request.post("/api/research/launch", { data: body });
    expect(second.status()).toBe(200);
    const j2 = (await second.json()) as { run_id: string; candidate_id: string };
    expect(j2.run_id).toBe(j1.run_id);
    expect(j2.candidate_id).toBe(j1.candidate_id);
  });

  test("后端不可达 → UI 显式报错（不静默显示任何结论）", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.POSITIVE);
    await page.route("**/api/**", (route) => route.abort());
    await page.goto("/audit");
    await expect(page.getByTestId("error-state")).toBeVisible();
  });

  test("缺闭包对象 → 闭包不完整且阻断（不冒充完整复验）", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.POSITIVE);
    const resp = await page.request.post("/api/g7/mutations", {
      data: { selector: "drop_closure_object" },
    });
    expect(resp.status()).toBe(200);

    await page.goto("/audit");
    await expect(page.getByTestId("closure-incomplete")).toBeVisible();
    await expect(page.getByTestId("closure-status")).toContainText("完整: false");
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
  });

  test("预测 claim 错绑定 → 读取端失败关闭且资格端点如实 BLOCKED", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.POSITIVE);
    const resp = await page.request.post("/api/g7/mutations", {
      data: { selector: "misbind_prediction" },
    });
    expect(resp.status()).toBe(200);

    // 读取端失败关闭：audit 页整体显式报错，绝不展示错绑预测
    await page.goto("/audit");
    await expect(page.getByTestId("error-state")).toBeVisible();
    // 直接读 predictions 端点同样是后端失败关闭（500 E-G7-01-006）
    const preds = await page.request.get("/api/predictions");
    expect(preds.status()).toBe(500);
    // 资格端点不得停留在 CLEAR —— 重算后必须 BLOCKED（E-G7-01-006 并入阻断）
    const elig = await page.request.get("/api/release/eligibility");
    expect(elig.status()).toBe(200);
    const eligBody = (await elig.json()) as {
      status: string;
      reasons: { code: string }[];
    };
    expect(eligBody.status).toBe("BLOCKED");
    expect(eligBody.reasons.some((r) => r.code === "E-G7-01-006")).toBe(true);
    // 浏览器横幅如实反映 BLOCKED（点击「检查发布资格」触发后端拉取）
    await page.getByRole("button", { name: "检查发布资格" }).click();
    await expect(page.getByTestId("release-status")).toHaveAttribute("data-status", "BLOCKED");
  });

  test("预测状态非法 → 读取端失败关闭且资格端点如实 BLOCKED", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.POSITIVE);
    const resp = await page.request.post("/api/g7/mutations", {
      data: { selector: "corrupt_prediction_status" },
    });
    expect(resp.status()).toBe(200);

    await page.goto("/audit");
    await expect(page.getByTestId("error-state")).toBeVisible();
    const preds = await page.request.get("/api/predictions");
    expect(preds.status()).toBe(500);
    const elig = await page.request.get("/api/release/eligibility");
    expect(elig.status()).toBe(200);
    const eligBody = (await elig.json()) as {
      status: string;
      reasons: { code: string }[];
    };
    expect(eligBody.status).toBe("BLOCKED");
    expect(eligBody.reasons.some((r) => r.code === "E-G7-01-006")).toBe(true);
    await page.getByRole("button", { name: "检查发布资格" }).click();
    await expect(page.getByTestId("release-status")).toHaveAttribute("data-status", "BLOCKED");
  });

  test("客户端改写 release_eligible → 后端保持 false，DOM 变异后重载恢复（E-3）", async ({ page }) => {
    await resetAndLaunch(page, CONTRACTS.RESTATEMENT);

    // ① HTTP 写入/查询串伪造 → 后端拒绝（default-deny）
    const writeResp = await page.request.post("/api/release/eligibility", {
      data: { release_eligible: true, reasons: [] },
    });
    expect(writeResp.status()).toBeGreaterThanOrEqual(400);
    const queryResp = await page.request.get("/api/release/eligibility?release_eligible=true");
    expect(queryResp.status()).toBeGreaterThanOrEqual(400);

    // ② 后端结论恒为 BLOCKED（source=BACKEND）
    const getResp = await page.request.get("/api/release/eligibility");
    expect(getResp.status()).toBe(200);
    const body = (await getResp.json()) as { status: string; source: string };
    expect(body.status).toBe("BLOCKED");
    expect(body.source).toBe("BACKEND");

    // ③ DOM 属性改写（DevTools 等效）：先证明变异确实发生，
    //    再重载/重新拉取 —— 后端 false/BLOCKED 恢复显示
    await page.goto("/audit");
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
    await page.getByTestId("release-eligible").evaluate((el) => {
      el.setAttribute("data-eligible", "true");
    });
    // 证明 DOM 变异真的发生了（否则「改写不生效」的断言可能是假绿）
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "true");
    // 重新拉取（重载）→ 后端结论恢复：文本事实与数据属性都回到 false
    await page.reload();
    await expect(page.getByTestId("release-eligible")).toHaveAttribute("data-eligible", "false");
    await expect(page.getByTestId("release-eligible")).toContainText("release_eligible = false");
    const reget = await page.request.get("/api/release/eligibility");
    const reBody = (await reget.json()) as { status: string; source: string };
    expect(reBody.status).toBe("BLOCKED");
    expect(reBody.source).toBe("BACKEND");
  });
});
