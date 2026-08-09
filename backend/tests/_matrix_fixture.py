"""测试辅助：RightsGuard 矩阵 fixture（FF-2 矩阵驱动）。"""
import json

MATRIX = {
    "schema": "rights-matrix/1.0",
    "produced_at": "2026-08-09T00:00:00Z",
    "policy": {"default": "RESEARCH_ONLY"},
    "data_sources": [
        {"id": "上交所测试", "source_key": "SRC_SSE",
         "actions": {"FETCH": "ALLOWED（测试授权）"}},
        {"id": "巨潮测试", "source_key": "SRC_CNINFO",
         "actions": {"FETCH": "ALLOWED（测试授权）"}},
        {"id": "宏观测试", "source_key": "SRC_NBS",
         "actions": {"FETCH": "ALLOWED（测试授权）"}},
        {"id": "副源测试", "source_key": "SRC_AKSHARE",
         "actions": {"FETCH": "ALLOWED（测试授权）"}},
        {"id": "禁止源", "source_key": "SRC_BAN",
         "actions": {"FETCH": "PROHIBITED（测试）"}},
        {"id": "未知源", "source_key": "SRC_UNK",
         "actions": {"FETCH": "UNKNOWN（测试）"}},
    ],
}

MATRIX_JSON = json.dumps(MATRIX, ensure_ascii=False)
