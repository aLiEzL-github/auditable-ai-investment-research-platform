#!/usr/bin/env python3
"""生成或复验 G6A-06 最终 candidate bundle 的本地生产入口。

freeze 只写内容寻址对象库；verify 只读。两条路径都要求当前 Git checkout
干净，并把实际 HEAD commit/tree 作为唯一代码版本来源。请求正文留在调用方
指定的本地 JSON 文件，不写入仓库或关系库。
"""
import argparse
import json
import os
import subprocess
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ROOT = os.path.abspath(os.path.join(BACKEND, ".."))
APP = os.path.join(BACKEND, "app")
sys.path.insert(0, APP)

from artifact_store import ArtifactStore  # noqa: E402
from candidate_service import (  # noqa: E402
    CandidateRequestError,
    CandidateVerificationError,
    freeze_final_candidate_from_payload,
    validate_source_revision,
)


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True)
    except OSError as exc:
        raise CandidateRequestError(
            f"E-G6A-06-021: git 不可执行（{type(exc).__name__}）") from exc
    if result.returncode != 0:
        raise CandidateRequestError(
            f"E-G6A-06-021: git {' '.join(args)} 失败（rc={result.returncode}）")
    return result.stdout.strip()


def source_revision() -> tuple[str, str]:
    """返回当前干净 checkout 的真实 HEAD commit/tree；脏树一律拒绝。"""
    dirty = _git("status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        count = len(dirty.splitlines())
        raise CandidateRequestError(
            f"E-G6A-06-021: 工作树非干净（{count} 项变化）—— 不得把 HEAD "
            "冒充实际执行代码")
    commit = _git("rev-parse", "--verify", "HEAD")
    tree = _git("rev-parse", "--verify", "HEAD^{tree}")
    validate_source_revision(commit, tree)
    return commit, tree


def _load_request(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise CandidateRequestError(
            f"E-G6A-06-022: 请求文件不可读或非 UTF-8 JSON（{type(exc).__name__}）") \
            from exc
    if not isinstance(payload, dict):
        raise CandidateRequestError(
            "E-G6A-06-022: 请求根必须为 JSON object")
    return payload


def freeze(request_path: str, store_path: str) -> dict:
    """生产 freeze 路径；返回可记录但不含研究正文的定位摘要。"""
    commit, tree = source_revision()
    store = ArtifactStore(store_path)
    result = freeze_final_candidate_from_payload(
        store, _load_request(request_path), source_commit=commit, source_tree=tree)
    return {
        "candidate_id": result.candidate_id,
        "object_store": str(store.root),
        "source_commit": commit,
        "source_tree": tree,
        "frozen_inputs_hash": result.candidate["frozen_inputs_hash"],
        "product_count": len(result.candidate["product_hashes"]),
    }


def verify(candidate_id: str, store_path: str) -> dict:
    """按当前 checkout 代码版本完整复验 candidate 与全部产品正文。"""
    commit, tree = source_revision()
    store = ArtifactStore(store_path)
    from candidate_service import CandidateFreezeService
    result = CandidateFreezeService(store).verify_candidate_bundle(
        candidate_id, expected_source_commit=commit,
        expected_source_tree=tree)
    return {**result, "object_store": str(store.root)}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p_freeze = sub.add_parser("freeze")
    p_freeze.add_argument("--request", required=True)
    p_freeze.add_argument("--store", required=True)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("--candidate-id", required=True)
    p_verify.add_argument("--store", required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            result = freeze(args.request, args.store)
        else:
            result = verify(args.candidate_id, args.store)
    except (CandidateRequestError, CandidateVerificationError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
