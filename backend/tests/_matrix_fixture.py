"""测试辅助：RightsGuard 矩阵 fixture（FF-2 矩阵驱动）。

**OI-PF-128 修正**：本 fixture 原用守卫词汇（`FETCH`）作 actions 键，
而真实 `contracts/rights_matrix.json` 用的是领域键
（`automated_acquisition` / `manual_download_by_human` / …）。
形状不一致使 195 个测试在「查询永不命中」的缺陷下**全部通过** ——
与 OI-PF-101（镜像未装依赖）、OI-PF-106（job 表无迁移）同类：
**夹具形状 ≠ 真实契约形状，测试因此看不见缺陷。**

现改为使用与真实矩阵**同一套领域键**，经 contracts/rights_action_map.json 解析。
"""
import json


def _src(sid, key, fetch, imp="ALLOWED（测试）", parse="ALLOWED（测试）",
         export="PROHIBITED（测试：正文永不入公开仓）"):
    """按**真实矩阵的领域键**构造，而非守卫词汇。"""
    return {"id": sid, "source_key": key, "actions": {
        "automated_acquisition": fetch,          # ← FETCH 的候选键
        "manual_download_by_human": imp,         # ← IMPORT
        "parse": parse,                          # ← PARSE
        "redistribute_in_public_repo": export,   # ← EXPORT
    }}


MATRIX = {
    "schema": "rights-matrix/1.0",
    "produced_at": "2026-08-09T00:00:00Z",
    "policy": {"default": "RESEARCH_ONLY"},
    "data_sources": [
        _src("上交所测试", "SRC_SSE", "ALLOWED（测试授权）"),
        _src("巨潮测试", "SRC_CNINFO", "ALLOWED（测试授权）"),
        _src("宏观测试", "SRC_NBS", "ALLOWED（测试授权）"),
        _src("副源测试", "SRC_AKSHARE", "ALLOWED（测试授权）"),
        _src("禁止源", "SRC_BAN", "PROHIBITED（测试）"),
        _src("未知源", "SRC_UNK", "UNKNOWN（测试）"),
    ],
}

MATRIX_JSON = json.dumps(MATRIX, ensure_ascii=False)
