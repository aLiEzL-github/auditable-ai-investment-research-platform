// G5-06 异步加载状态机（useAsync）
// 验收：错误不显示为成功；中断后可恢复。
// 统一四态：LOADING / EMPTY / ERROR / READY —— 四者互斥、可机检分辨（⑨）。
//  retry() 重新发起 —— 中断（网络/后端）后恢复的唯一路径。

import { useCallback, useEffect, useRef, useState } from "react";

export type AsyncState<T> =
  | { phase: "LOADING" }
  | { phase: "EMPTY"; reason: string }
  | { phase: "ERROR"; message: string }
  | { phase: "READY"; value: T };

export function useAsync<T>(loader: () => Promise<T>, emptyIf: (v: T) => string | null): {
  state: AsyncState<T>;
  retry: () => void;
} {
  const [state, setState] = useState<AsyncState<T>>({ phase: "LOADING" });
  const [tick, setTick] = useState(0);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const emptyIfRef = useRef(emptyIf);
  emptyIfRef.current = emptyIf;

  useEffect(() => {
    let cancelled = false;
    setState({ phase: "LOADING" });
    loaderRef
      .current()
      .then((value) => {
        if (cancelled) return;
        const emptyReason = emptyIfRef.current(value);
        if (emptyReason != null) {
          setState({ phase: "EMPTY", reason: emptyReason });
        } else {
          setState({ phase: "READY", value });
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setState({
          phase: "ERROR",
          message: err instanceof Error ? err.message : String(err),
        });
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const retry = useCallback(() => setTick((t) => t + 1), []);
  return { state, retry };
}
