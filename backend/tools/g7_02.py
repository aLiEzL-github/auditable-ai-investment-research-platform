#!/usr/bin/env python3
"""g7_02.py —— G7-02 本地 CLI（600089 全链真实候选 + NBS 第二真实来源冒烟）。

层级：L3 取数/工具层（backend/tools/，可出网）。网络只在此层出现，且任何
出网动作必须先经 `RightsGuard` 权利决定（非 ALLOWED 零请求/正文/写入）。

子命令：
  nbs-acquire  经 RightsGuard(FETCH) 取得 NBS 官方页面，原始页面与宏观
               manifest 写入仓外内容寻址对象库，并输出 manifest 到 --out。
               acquire 先要求当前 Git checkout 干净，并把实际 HEAD
               commit/tree 与 source_url/cutoff 写入 manifest。
  nbs-smoke    同一权利门与取得路径，只打印结构化取得状态（source_id/
               family/raw_sha256/bytes/publication_date/verdict），不落库。
  freeze       消费仓外 600089 登记输入 + NBS macro manifest，冻结受管 G6A
               final candidate 并产出内容寻址 G7-02 candidate pack。
  verify       离线复验 pack 与全部依赖（含外部输入重算哈希、G6A bundle、
               权利门当前矩阵、source revision 漂移、发布轴计数）。

生产 CLI 边界（G7-02 首轮审查）：
  · 网络只允许官方域名 https://www.stats.gov.cn，scope 只允许官方数据发布页
    路径形状；禁止任意 scheme/host、userinfo、端口、query/fragment、绝对
    URL、路径穿越；测试回环网络只经显式注入 adapter，无任何 CLI 测试绕口；
  · --store / --out / --company-input / --macro-manifest / --macro-raw 解析后
    必须在**任意 Git worktree/repository** 之外（不只当前 ROOT；含 linked
    worktree 与 `.git` 为文件的 worktree；对尚不存在的 store/out 从最近存在
    父目录向上探测）；拒绝仓内路径与 symlink 穿越；manifest 采用
    O_NOFOLLOW + 排他仓外写，禁止覆盖既有文件；
  · freeze 的 --run-id 无固定可复用默认：未提供时生成 G7-02-<UTC秒级>-<随机
    后缀>，contract_id 同 run_id 派生（C-600089-<run_id>）；测试固定时可显式传入；
  · strict JSON：json.loads 经 parse_constant 拒绝 NaN/Infinity，canonical
    dumps allow_nan=False；异常与 stdout/stderr 不含材料事实原值或原始时间
    字面量；输入正文永不进入 stdout/stderr；
  · nbs-acquire/smoke 在取得阶段即接收并检查 cutoff（publication_date > cutoff
    失败关闭）；freeze/verify 绑定外部 JSON 原始字节 SHA 与 canonical 语义 SHA。

生产 freeze/verify 要求当前 Git checkout 干净，并把实际 HEAD commit/tree
作为唯一代码版本来源。真实本地冒烟只登记对象 ID/SHA-256/状态/数量/命令与
退出码，不把正文写入治理材料。
"""
import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
APP = os.path.join(BACKEND, "app")
sys.path.insert(0, APP)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from artifact_store import ArtifactStore  # noqa: E402
from g7_02_service import (  # noqa: E402
    G7_02Error,
    KIND_MACRO_RAW,
    NBS_SOURCE_FAMILY,
    NBS_SOURCE_ID,
    freeze_pack,
    verify_pack,
)
from macro_adapter import MacroAdapter  # noqa: E402
from rights_guard import GuardDenied, RightsGuard  # noqa: E402

NBS_FETCH_ACTION = "FETCH"
NBS_MANIFEST_KIND = "g7_02_macro_manifest"


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True)
    except OSError as exc:
        raise G7_02Error(
            f"E-G7-02-030: git 不可执行（{type(exc).__name__}）") from exc
    if result.returncode != 0:
        raise G7_02Error(
            f"E-G7-02-030: git {' '.join(args)} 失败（rc={result.returncode}）")
    return result.stdout.strip()


def source_revision() -> tuple:
    """返回当前干净 checkout 的真实 HEAD commit/tree；脏树一律拒绝。"""
    dirty = _git("status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        count = len(dirty.splitlines())
        raise G7_02Error(
            f"E-G7-02-030: 工作树非干净（{count} 项变化）—— 不得把 HEAD "
            "冒充实际执行代码")
    commit = _git("rev-parse", "--verify", "HEAD")
    tree = _git("rev-parse", "--verify", "HEAD^{tree}")
    from g7_02_service import SOURCE_REVISION_RE
    if not SOURCE_REVISION_RE.fullmatch(commit) \
            or not SOURCE_REVISION_RE.fullmatch(tree):
        raise G7_02Error("E-G7-02-030: HEAD revision 非法")
    return commit, tree


def _reject_json_constant(token: str):
    raise ValueError(f"非标准 JSON 常量 {token!r}")


def _load_json(path: str, what: str) -> tuple:
    """读取外部 JSON：strict（拒绝 NaN/Infinity）+ 返回 (object, raw_sha256)。

    异常不含原始正文或原始时间字面量（最小披露）。
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise G7_02Error(
            f"E-G7-02-031: {what} 不可读（{type(exc).__name__}）") from exc
    try:
        obj = json.loads(raw.decode("utf-8"),
                         parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError) as exc:
        raise G7_02Error(
            f"E-G7-02-031: {what} 不可读或非严格 JSON（{type(exc).__name__}）") \
            from exc
    if not isinstance(obj, dict):
        raise G7_02Error(f"E-G7-02-031: {what} 根必须为 JSON object")
    return obj, _sha256_hex(raw)


def _default_run_id() -> str:
    """不可复用默认：G7-02-<UTC秒级>-<随机后缀>（禁止固定可复用默认）。"""
    return f"G7-02-{int(time.time())}-{secrets.token_hex(4)}"


def _resolve_outside_repo(path_str: str, label: str) -> str:
    """仓外强制：拒绝位于**任意 Git worktree/repository** 内的路径。

    不只检查当前 ROOT：对尚不存在的 store/out 从最近存在的父目录向上探测，
    任一祖先位于任意 Git worktree/repository（含 linked worktree、`.git` 为
    文件的 worktree）即失败关闭（E-G7-02-035）。
    """
    if not path_str:
        raise G7_02Error(f"E-G7-02-035: {label} 路径为空")
    p = os.path.realpath(path_str)
    probe = p if os.path.isdir(p) else os.path.dirname(p)
    while probe:
        if _inside_git_worktree(probe):
            raise G7_02Error(
                f"E-G7-02-035: {label} 位于 Git worktree/repository 内 —— "
                "拒绝仓内路径与 symlink 穿越")
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    return p


def _inside_git_worktree(directory: str) -> bool:
    """directory（或最近存在祖先）是否位于任意 Git worktree/repository 内。

    覆盖：普通 worktree、`git worktree` linked worktree（.git 为文件）以及
    `.git` 元数据目录内部（--is-inside-git-dir）。
    """
    # 先用文件系统标记兜底；即使 git 不可执行或拒绝该目录，也不把仓内路径
    # 误判为仓外。linked worktree 的 `.git` 是文件，同样命中。
    if os.path.lexists(os.path.join(directory, ".git")) \
            or os.path.basename(os.path.normpath(directory)) == ".git":
        return True
    try:
        for flag in ("--is-inside-work-tree", "--is-inside-git-dir"):
            result = subprocess.run(
                ["git", "-C", directory, "rev-parse", flag],
                capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip() == "true":
                return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    return False


ERROR_CODE_RE = re.compile(r"\bE-(?:G7-02|G2-05)-\d{3}\b")


def _blocked_summary(exc: Exception) -> dict:
    """失败路径的最小化机器证据；不回显输入、正文或材料事实值。"""
    match = ERROR_CODE_RE.search(str(exc))
    return {
        "task_id": "G7-02",
        "run_outcome": "BLOCKED",
        "failure_closed": True,
        "error_code": match.group(0) if match else "G7_02_FAILED_CLOSED",
        "error_type": type(exc).__name__,
        "gate7_reached": False,
        "gate_release_eligible": False,
    }


def _write_manifest_exclusive(path: str, manifest: dict) -> None:
    """manifest 排他仓外写：O_NOFOLLOW + O_CREAT|O_EXCL，禁止覆盖既有文件。

    写入内容为 canonical 字节（allow_nan=False），任何已存在目标一律失败关闭。
    """
    p = _resolve_outside_repo(path, "--out")
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600)
    except FileExistsError:
        raise G7_02Error(
            f"E-G7-02-035: {p} 已存在 —— 禁止覆盖既有文件（排他写入）")
    except OSError as exc:
        raise G7_02Error(
            f"E-G7-02-035: {p} 无法排他创建（{type(exc).__name__}）"
            " —— 失败关闭") from exc
    with contextlib.closing(os.fdopen(fd, "wb")) as fh:
        fh.write(_canonical(manifest))


def _nbs_attribution() -> str:
    """从权利矩阵读取 NBS 强制署名条款 —— 不把来源特征串写死在代码里。"""
    try:
        with open(os.path.join(ROOT, "contracts", "rights_matrix.json"),
                  encoding="utf-8") as fh:
            matrix = json.load(fh)
    except (OSError, ValueError) as exc:
        raise G7_02Error(
            f"E-G7-02-031: 权利矩阵不可读（{type(exc).__name__}）") from exc
    for entry in matrix.get("data_sources", []):
        if entry.get("source_key") == NBS_SOURCE_ID:
            return entry.get("actions", {}).get("attribution", "")
    raise G7_02Error("E-G7-02-031: 权利矩阵无 SRC_NBS 条目")


def _guarded_fetch(guard: RightsGuard, adapter: MacroAdapter, scope: str,
                   reference_period: str = ""):
    """经权利门取得；403/429/超时/空正文一律失败关闭。返回 (point, decisions)。"""
    decisions = []
    point = adapter.fetch(
        scope, record_decision=lambda rd: decisions.append(rd.to_dict()),
        reference_period=reference_period)
    if not point.raw:
        raise G7_02Error("E-G7-02-032: NBS 取得空正文 —— 失败关闭")
    return point, decisions


def _check_cutoff(point, cutoff_at: str):
    """取得阶段即检查 cutoff：publication_date > cutoff 失败关闭。"""
    if not cutoff_at:
        raise G7_02Error("E-G7-02-032: 缺 cutoff —— 失败关闭")
    from g7_02_service import _iso_datetime
    pub = _iso_datetime(point.publication_date,
                        "point.publication_date")
    cutoff = _iso_datetime(cutoff_at, "cutoff_at")
    if pub > cutoff:
        raise G7_02Error(
            "E-G7-02-032: publication_date 晚于 cutoff —— 失败关闭")


def _build_manifest(point, decisions, *, scope, reference_period,
                    source_commit, source_tree, cutoff_at) -> dict:
    decision = decisions[0] if decisions else {}
    return {
        "schema_version": "1.0",
        "kind": NBS_MANIFEST_KIND,
        "source_id": NBS_SOURCE_ID,
        "source_family": NBS_SOURCE_FAMILY,
        "source_url": point.source_url,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "scope": scope,
        "publication_date": point.publication_date,
        "reference_period": reference_period or point.reference_period,
        "acquired_at": point.acquired_at,
        "cutoff_at": cutoff_at,
        "raw_sha256": _sha256_hex(point.raw),
        "raw_bytes": len(point.raw),
        "attribution": _nbs_attribution(),
        "rights_decision": decision,
        "gate_status": {
            "quality_status": "PARTIAL",
            "decision_use_status": "CONTEXT_ONLY",
            "gate_status": "PARTIAL",
        },
        "is_financial_dual_source_for_600089": False,
    }


def _canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _production_adapter(guard, base_url):
    """生产适配器：强制官方域名 + 官方路径 + 路径发布日（无测试绕口）。"""
    return MacroAdapter(guard, base_url=base_url, strict_origin=True,
                        publication_date_mode="path")


def _summary(result, store) -> dict:
    return {
        "pack_id": result.pack_id,
        "candidate_id": result.candidate_id,
        "request_hash": result.request_hash,
        "candidate_status": result.candidate_status,
        "company_data_status": result.company_data_status,
        "missing_periods": list(result.missing_periods),
        "missing_bindings": list(result.missing_bindings),
        "product_count": result.pack["g6a_candidate"]["product_count"],
        "quality_status": result.pack["g6a_candidate"]["quality_status"],
        "release_eligible": result.pack["g6a_candidate"]["release_eligible"],
        "reviewer_independence": result.pack["reviewer_independence"],
        "gate7_reached": False,
        "gate_release_eligible": False,
        "write_counts": dict(result.pack["write_counts"]),
        "single_source_disclosed": result.pack["single_source_disclosed"],
        "source_commit": result.pack["source_commit"],
        "source_tree": result.pack["source_tree"],
        "object_store": str(store.root),
    }


def nbs_acquire(store: ArtifactStore, base_url: str, scope: str,
                reference_period: str = "", out_path: str = "",
                cutoff_at: str = "", source_commit: str = "",
                source_tree: str = "", guard=None, adapter=None) -> dict:
    guard = guard or RightsGuard()
    adapter = adapter or _production_adapter(guard, base_url)
    point, decisions = _guarded_fetch(guard, adapter, scope, reference_period)
    _check_cutoff(point, cutoff_at)
    raw_sha = store.store(KIND_MACRO_RAW, point.raw)
    manifest = _build_manifest(point, decisions, scope=scope,
                               reference_period=reference_period,
                               source_commit=source_commit,
                               source_tree=source_tree,
                               cutoff_at=cutoff_at)
    manifest_sha = store.store(NBS_MANIFEST_KIND, _canonical(manifest))
    if out_path:
        _write_manifest_exclusive(out_path, manifest)
    return {
        "verdict": "ACQUIRED",
        "source_id": NBS_SOURCE_ID,
        "source_family": NBS_SOURCE_FAMILY,
        "source_url": point.source_url,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "cutoff_at": cutoff_at,
        "manifest_id": manifest_sha,
        "raw_sha256": raw_sha,
        "raw_bytes": len(point.raw),
        "publication_date": point.publication_date,
        "reference_period": point.reference_period,
        "scope": scope,
        "object_store": str(store.root),
    }


def nbs_smoke(base_url: str, scope: str, reference_period: str = "",
              cutoff_at: str = "", guard=None, adapter=None) -> dict:
    """真实来源冒烟：只证明另一独立来源链可取得、留痕、失败关闭。"""
    guard = guard or RightsGuard()
    adapter = adapter or _production_adapter(guard, base_url)
    point, decisions = _guarded_fetch(guard, adapter, scope, reference_period)
    _check_cutoff(point, cutoff_at)
    return {
        "verdict": "OK",
        "source_id": NBS_SOURCE_ID,
        "source_family": NBS_SOURCE_FAMILY,
        "source_url": point.source_url,
        "raw_sha256": _sha256_hex(point.raw),
        "raw_bytes": len(point.raw),
        "publication_date": point.publication_date,
        "reference_period": point.reference_period,
        "rights_decision": decisions[0] if decisions else {},
        "note": "仅独立来源冒烟：不构成 600089 财务事实的第二独立来源",
    }


def cmd_freeze(args) -> int:
    commit, tree = source_revision()
    company_path = _resolve_outside_repo(args.company_input, "--company-input")
    manifest_path = _resolve_outside_repo(args.macro_manifest,
                                          "--macro-manifest")
    store = ArtifactStore(_resolve_outside_repo(args.store, "--store"))
    company, company_raw_sha = _load_json(company_path, "company input")
    macro, macro_raw_sha = _load_json(manifest_path, "macro manifest")
    # 收口：run_id 不得使用固定可复用默认 —— 未提供时生成
    # G7-02-<UTC秒级>-<随机后缀>；contract_id 同 run_id 派生。测试固定时显式传入。
    run_id = getattr(args, "run_id", None) or _default_run_id()
    contract_id = getattr(args, "contract_id", None) or f"C-600089-{run_id}"
    result = freeze_pack(
        store, company_input=company, macro_manifest=macro,
        source_commit=commit, source_tree=tree,
        cutoff_at=args.cutoff_at, as_of_date=args.as_of_date,
        contract_id=contract_id, run_id=run_id,
        company_raw_sha256=company_raw_sha,
        macro_manifest_raw_sha256=macro_raw_sha)
    print(json.dumps(_summary(result, store), ensure_ascii=False, sort_keys=True))
    return 0


def cmd_verify(args) -> int:
    commit, tree = source_revision()
    store = ArtifactStore(_resolve_outside_repo(args.store, "--store"))
    company_path = _resolve_outside_repo(args.company_input, "--company-input")
    manifest_path = _resolve_outside_repo(args.macro_manifest,
                                          "--macro-manifest")
    company, company_raw_sha = _load_json(company_path, "company input")
    macro, macro_raw_sha = _load_json(manifest_path, "macro manifest")
    macro_raw = None
    if args.macro_raw:
        raw_path = _resolve_outside_repo(args.macro_raw, "--macro-raw")
        try:
            with open(raw_path, "rb") as fh:
                macro_raw = fh.read()
        except OSError as exc:
            raise G7_02Error(
                f"E-G7-02-031: macro raw 不可读（{type(exc).__name__}）") from exc
    result = verify_pack(
        store, args.pack, company_input=company, macro_manifest=macro,
        macro_raw=macro_raw, source_commit=commit, source_tree=tree,
        company_raw_sha256=company_raw_sha,
        macro_manifest_raw_sha256=macro_raw_sha)
    print(json.dumps({**result, "object_store": str(store.root)},
                     ensure_ascii=False, sort_keys=True))
    return 0


def cmd_nbs_acquire(args) -> int:
    store = ArtifactStore(_resolve_outside_repo(args.store, "--store"))
    commit, tree = source_revision()
    result = nbs_acquire(store, args.base_url, args.scope,
                         reference_period=args.reference_period,
                         out_path=args.out,
                         cutoff_at=args.cutoff_at,
                         source_commit=commit, source_tree=tree)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_nbs_smoke(args) -> int:
    result = nbs_smoke(args.base_url, args.scope,
                       reference_period=args.reference_period,
                       cutoff_at=args.cutoff_at)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_acquire = sub.add_parser("nbs-acquire")
    p_acquire.add_argument("--store", required=True)
    p_acquire.add_argument("--base-url", default="https://www.stats.gov.cn",
                           help="官方域名（生产仅允许 https://www.stats.gov.cn）")
    p_acquire.add_argument("--scope", required=True,
                           help="NBS 官方数据发布页路径形状（/sj/...）")
    p_acquire.add_argument("--reference-period", default="")
    p_acquire.add_argument("--cutoff-at",
                           default="2026-08-16T09:21:00Z")
    p_acquire.add_argument("--out", default="",
                           help="仓外 manifest 输出路径（排他写入，禁止覆盖）")

    p_smoke = sub.add_parser("nbs-smoke")
    p_smoke.add_argument("--base-url", default="https://www.stats.gov.cn")
    p_smoke.add_argument("--scope", required=True)
    p_smoke.add_argument("--reference-period", default="")
    p_smoke.add_argument("--cutoff-at",
                         default="2026-08-16T09:21:00Z")

    p_freeze = sub.add_parser("freeze")
    p_freeze.add_argument("--company-input", required=True)
    p_freeze.add_argument("--macro-manifest", required=True)
    p_freeze.add_argument("--store", required=True)
    p_freeze.add_argument("--cutoff-at",
                          default="2026-08-16T09:21:00Z")
    p_freeze.add_argument("--as-of-date", default="2026-08-16")
    # 收口：无固定可复用默认 —— 未提供时生成 G7-02-<UTC秒级>-<随机后缀>，
    # contract_id 同 run_id 派生；测试固定时可显式传入。
    p_freeze.add_argument("--contract-id", default=None,
                          help="未提供时由 run_id 派生（C-600089-<run_id>）")
    p_freeze.add_argument("--run-id", default=None,
                          help="未提供时生成 G7-02-<UTC秒级>-<随机后缀>")

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--pack", required=True)
    p_verify.add_argument("--store", required=True)
    p_verify.add_argument("--company-input", required=True)
    p_verify.add_argument("--macro-manifest", required=True)
    p_verify.add_argument("--macro-raw", default="",
                          help="可选：额外交叉比对 macro raw 原始页面")

    args = parser.parse_args()
    try:
        if args.command == "nbs-acquire":
            return cmd_nbs_acquire(args)
        if args.command == "nbs-smoke":
            return cmd_nbs_smoke(args)
        if args.command == "freeze":
            return cmd_freeze(args)
        return cmd_verify(args)
    except (G7_02Error, GuardDenied, RuntimeError, ValueError) as exc:
        print(json.dumps(_blocked_summary(exc), ensure_ascii=False,
                         sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
