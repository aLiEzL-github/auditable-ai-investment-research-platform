"""ArtifactStore —— G2-02 内容寻址工件存储。

基线验收：已登记原件不可原地改写（不可变）。
BF-03 增补：写入路径的目录逃逸与 symlink 必须失败
（`../` 穿越、指向库外的 symlink、深度嵌套路径），
写入不得离开内容寻址对象库边界。

设计：
  · 内容寻址 —— sha256 决定路径（sha[:2]/sha[2:4]/sha[4:]），同内容必同路径
  · 不可变 —— 已存在对象拒绝覆盖（E-G2-02-003）
  · 防逃逸 —— 目标经 resolve() 后必须仍在库内；写入名受正则约束
"""
import hashlib
import re
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{2,63}$")


class ArtifactStore:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _rel(digest: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"E-G2-02-002: 非法 sha256 摘要: {digest[:16]}…")
        return f"{digest[:2]}/{digest[2:4]}/{digest[4:]}"

    def _target(self, digest: str) -> Path:
        t = (self.root / self._rel(digest)).resolve()
        # 防逃逸：resolve 后（含 symlink 追出）必须仍在库内（BF-03 负例 2）
        if not t.is_relative_to(self.root):
            raise ValueError("E-G2-02-001: 写入路径逃逸对象库边界")
        return t

    def store(self, name: str, data: bytes) -> str:
        # 写入名约束：防 `../` 穿越与超长/深度嵌套（BF-03 负例 1/3）
        if not NAME_RE.fullmatch(name):
            raise ValueError(f"E-G2-02-002: 非法对象名（防目录逃逸）: {name!r}")
        digest = hashlib.sha256(data).hexdigest()
        target = self._target(digest)
        if target.exists():
            # 内容寻址去重：同内容幂等返回，不覆盖（无覆盖路径 = 不可变）
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return digest

    def load(self, digest: str) -> bytes:
        target = self._target(digest)
        if not target.exists():
            raise ValueError(f"E-G2-02-004: 对象不存在: {digest[:12]}…")
        data = target.read_bytes()
        # 不可变强制（X-8）：读取时校验内容哈希 = 摘要，防原地改写/篡改
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(
                f"E-G2-02-005: 对象内容与摘要不符（已登记原件被篡改）: {digest[:12]}…")
        return data

    def exists(self, digest: str) -> bool:
        return self._target(digest).exists()
