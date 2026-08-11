"""_g4_fixtures.py —— G4 测试的脱敏、冻结 fixture（ADR-006 L2：数值全部合成）。

构造一个最小研究闭包（系统设计域或研究域），全部对象内容合成：
  · candidate     —— 候选（payload 合成）
  · report        —— 报告文本（含/不含国家统计局署名两态）
  · evidence      —— 证据（source_key / source_domain / rights_verdict）
  · macro         —— 宏观快照（合成）
  · assumption    —— 假设（合成）
  · calc          —— 计算（refs 指向闭包输入）
  · claim         —— 结论（materiality CRITICAL/MATERIAL，refs 指向证据）
  · worksheet     —— 底稿（合成）
  · test          —— 测试记录（合成）
  · code_config   —— 代码/配置版本（合成）
  · open_item     —— 开放项（CLOSED 或 OPEN+material 两态）
"""
import json
import os
import sys

APP = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP)

from artifact_store import ArtifactStore
from publish_engine import canonical_bytes, content_id, freeze_object


def nbs_source() -> dict:
    """国家统计局来源（强制署名义务 D-12）。"""
    return {"source_key": "SRC_NBS",
            "source_domain": "www.stats.gov.cn",
            "rights_verdict": "ALLOWED_WITH_ATTRIBUTION"}


def sse_source() -> dict:
    """上交所来源（无署名义务）。"""
    return {"source_key": "SRC_SSE",
            "source_domain": "www.sse.com.cn",
            "rights_verdict": "ALLOWED"}


def build_evidence(store, source: dict, metric: str = "net_profit",
                   value: str = "123.45") -> str:
    obj = {"schema_version": "1.0.0", "kind": "evidence",
           "source_key": source["source_key"],
           "source_domain": source["source_domain"],
           "rights_verdict": source["rights_verdict"],
           "metric": metric, "value": value}
    return freeze_object(store, "evidence", obj)


def build_macro(store, value: str = "3.1") -> str:
    obj = {"schema_version": "1.0.0", "kind": "macro",
           "indicator": "GDP_YOY", "value": value,
           "source_key": "SRC_NBS", "source_domain": "www.stats.gov.cn",
           "rights_verdict": "ALLOWED_WITH_ATTRIBUTION"}
    return freeze_object(store, "macro", obj)


def build_assumption(store, value: str = "7.0") -> str:
    obj = {"schema_version": "1.0.0", "kind": "assumption",
           "name": "wacc", "value": value}
    return freeze_object(store, "assumption", obj)


def build_calc(store, refs, metric: str = "fair_value") -> str:
    obj = {"schema_version": "1.0.0", "kind": "calc",
           "metric": metric, "formula": "value/(1+wacc)",
           "inputs": refs}
    return freeze_object(store, "calc", obj)


def build_claim(store, statement: str, refs, materiality: str = "MATERIAL") -> str:
    obj = {"schema_version": "1.0.0", "kind": "claim",
           "statement": statement, "materiality": materiality}
    return freeze_object(store, "claim", obj)


def build_open_item(store, status: str = "CLOSED", material: bool = False,
                    title: str = "OI-FIXTURE") -> str:
    obj = {"schema_version": "1.0.0", "kind": "open_item",
           "id": title, "status": status, "material": material,
           "title": title}
    return freeze_object(store, "open_item", obj)


def build_report(store, with_nbs_attribution: bool,
                 body: str = "fixture 报告正文（全部数值合成）") -> str:
    """报告：首屏前 10 行是 D-13「显著位置」的机检对象。"""
    if with_nbs_attribution:
        text = ("# fixture 研究报告\n"
                "转自国家统计局网站，www.stats.gov.cn\n"
                "研究信息不构成投资建议。\n" + body + "\n")
    else:
        text = ("# fixture 研究报告\n"
                "研究信息不构成投资建议。\n" + body + "\n")
    return freeze_object(store, "report", {"schema_version": "1.0.0",
                                           "kind": "report", "text": text})


def build_candidate(store, payload: dict = None) -> str:
    cand = {"schema_version": "1.0.0", "kind": "candidate",
            "payload": payload or {"ticker": "FIX-01", "mode": "synthetic"}}
    return freeze_object(store, "candidate", cand)


def manifest_of(store, key, root: str, objects: dict, parent=None,
                code_version: str = "v1.0", config_version: str = "v1.0",
                subject_root_candidates=None, directory_hash: str = "0" * 64,
                workflow=None, scope_id=None, current_key=None) -> dict:
    """构造 manifest dict（未冻结形态，供变异注入）。objects: {digest: meta}。"""
    m = {"id": "0" * 64,                       # 占位，freeze 时重算
         "schema_version": "1.0.0",
         "workflow": workflow or key.workflow,
         "scope_id": scope_id or key.scope_id,
         "current_key": current_key if current_key is not None else key.current_key,
         "subject_root": root,
         "parent": parent,
         "directory_hash": directory_hash,
         "code_version": code_version,
         "config_version": config_version,
         "objects": objects}
    if subject_root_candidates is not None:
        m["subject_root_candidates"] = subject_root_candidates
    m["id"] = content_id(m)
    return m


def freeze_manifest(store, manifest: dict) -> str:
    """冻结 manifest（写入对象库并返回内容哈希）。"""
    return freeze_object(store, "manifest", manifest)


def minimal_closure(store, key, with_nbs: bool = True,
                    open_item_status: str = "CLOSED",
                    open_item_material: bool = False,
                    candidate_payload: dict = None) -> dict:
    """最小研究闭包：candidate root → claim → evidence → report。

    返回 manifest dict（未冻结）。with_nbs=True 时报告含统计局署名
    且证据含 SRC_NBS（D-12 适用场景）。candidate_payload 可换根
    （不同 subject root 用例）。
    """
    cand = build_candidate(store, payload=candidate_payload)
    src = nbs_source() if with_nbs else sse_source()
    ev = build_evidence(store, src)
    macro = build_macro(store)
    ass = build_assumption(store)
    calc = build_calc(store, [ev, macro, ass])
    claim = build_claim(store, "fixture 结论（合成）", [ev, calc],
                        materiality="CRITICAL")
    ws = freeze_object(store, "worksheet",
                       {"schema_version": "1.0.0", "kind": "worksheet", "rows": "[]"})
    t = freeze_object(store, "test",
                      {"schema_version": "1.0.0", "kind": "test", "result": "PASS"})
    cc = freeze_object(store, "code_config",
                       {"schema_version": "1.0.0", "kind": "code_config",
                        "code_version": "v1.0", "config_version": "v1.0"})
    oi = build_open_item(store, status=open_item_status,
                         material=open_item_material)
    report = build_report(store, with_nbs_attribution=with_nbs)

    objects = {cand: {"kind": "candidate", "refs": [claim, report, ws, t, cc, oi]},
               claim: {"kind": "claim", "refs": [ev, calc, macro, ass]},
               ev: {"kind": "evidence", "refs": []},
               macro: {"kind": "macro", "refs": []},
               ass: {"kind": "assumption", "refs": []},
               calc: {"kind": "calc", "refs": [ev, macro, ass]},
               ws: {"kind": "worksheet", "refs": []},
               t: {"kind": "test", "refs": []},
               cc: {"kind": "code_config", "refs": []},
               oi: {"kind": "open_item", "refs": []},
               report: {"kind": "report", "refs": []}}
    return manifest_of(store, key, root=cand, objects=objects)


def make_report_text(manifest_objects: dict, store) -> str:
    """从闭包取报告文本（与引擎 render_report_text 同语义，测试用）。"""
    for oid, meta in manifest_objects.items():
        if meta.get("kind") == "report":
            return json.loads(store.load(oid).decode("utf-8"))["text"]
    raise ValueError("no report")
