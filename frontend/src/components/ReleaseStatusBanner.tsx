// 阻断态横幅（G5 §3.2 E-4/E-5/E-6）
// 设计约束（本文件注释即验收依据，测试断言 DOM）：
//  · E-4 阻断态不可隐藏：组件恒定渲染，不折叠、不依赖交互展开、不用颜色作唯一区分
//  · E-5 阻断原因可见：BLOCKED 时逐条列出 reason.code + reason.detail
//  · E-6 三种状态可分辨：NOT_CHECKED / CLEAR / BLOCKED 文本互不相同
//  · E-8 断链显式报错：ERROR 态显示 message，不得显示「无阻断」

import type { EligibilityLoad } from "../state/WorkbenchContext";

export function ReleaseStatusBanner({ load }: { load: EligibilityLoad }) {
  const phase = load.phase;
  const statusLabel = phase === "LOADED" ? load.value.status : phase;
  return (
    <section
      data-testid="release-status"
      data-status={statusLabel}
      aria-live="polite"
      className={`release-banner release-banner--${String(statusLabel).toLowerCase()}`}
    >
      <h2 className="release-banner__title">{bannerTitle(load)}</h2>
      {phase === "LOADED" && load.value.status === "BLOCKED" && (
        <ul className="release-banner__reasons">
          {load.value.reasons.map((r: { code: string; detail: string }) => (
            <li key={r.code} data-testid="blocking-reason">
              <span className="release-banner__code">{r.code}</span>
              <span className="release-banner__detail">{r.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {phase === "ERROR" && (
        <p className="release-banner__error" data-testid="blocking-error">
          {load.message}
        </p>
      )}
    </section>
  );
}

function bannerTitle(load: EligibilityLoad): string {
  switch (load.phase) {
    case "NOT_CHECKED":
      return "发布资格：尚未检查";
    case "LOADING":
      return "发布资格：检查中…";
    case "ERROR":
      return "发布资格：检查失败（无法判定，不得发布）";
    case "LOADED":
      switch (load.value.status) {
        case "NOT_CHECKED":
          return "发布资格：尚未检查";
        case "CLEAR":
          return "发布资格：已检查，无阻断";
        case "BLOCKED":
          return "发布资格：被阻断（不可发布）";
        case "ERROR":
          return "发布资格：检查失败（无法判定，不得发布）";
      }
      return "发布资格：状态未知";
  }
}
