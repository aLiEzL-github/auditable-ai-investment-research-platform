// 全局状态（G5-01 交付件「状态」）
// 阻断态只经 API 响应写入（E-1/E-3）：store 不提供任何
// 手动改写阻断态的方法 —— 组件无法把 BLOCKED 改成 CLEAR。

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { WorkbenchApi, EvidenceView } from "../api/client";
import { createApi } from "../api";
import type { ReleaseEligibility } from "../types";

export type EligibilityLoad =
  | { phase: "NOT_CHECKED" } // 尚未检查（E-6）
  | { phase: "LOADING" }
  | { phase: "LOADED"; value: ReleaseEligibility } // 已检查（含 CLEAR 与 BLOCKED）
  | { phase: "ERROR"; message: string }; // E-8：断链显式报错

export interface WorkbenchState {
  api: WorkbenchApi;
  eligibility: EligibilityLoad;
  evidence: EvidenceView | null;
  evidenceError: string | null;
  checkEligibility: () => Promise<void>;
  refreshEvidence: () => Promise<void>;
}

const WorkbenchContext = createContext<WorkbenchState | null>(null);

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  const api = useMemo(() => createApi(), []);
  const [eligibility, setEligibility] = useState<EligibilityLoad>({
    phase: "NOT_CHECKED",
  });
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);

  const checkEligibility = useCallback(async () => {
    setEligibility({ phase: "LOADING" });
    try {
      const value = await api.getReleaseEligibility();
      setEligibility({ phase: "LOADED", value });
    } catch (err) {
      setEligibility({
        phase: "ERROR",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  }, [api]);

  const refreshEvidence = useCallback(async () => {
    setEvidenceError(null);
    try {
      setEvidence(await api.getEvidenceView());
    } catch (err) {
      setEvidenceError(err instanceof Error ? err.message : String(err));
    }
  }, [api]);

  useEffect(() => {
    void refreshEvidence();
  }, [refreshEvidence]);

  const value = useMemo<WorkbenchState>(
    () => ({ api, eligibility, evidence, evidenceError, checkEligibility, refreshEvidence }),
    [api, eligibility, evidence, evidenceError, checkEligibility, refreshEvidence],
  );

  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench(): WorkbenchState {
  const ctx = useContext(WorkbenchContext);
  if (!ctx) {
    throw new Error("useWorkbench must be used within WorkbenchProvider");
  }
  return ctx;
}
