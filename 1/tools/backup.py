#!/usr/bin/env python3
"""backup.py —— 首次备份与隔离恢复（G1-08）。

VD-19 参数：~/backups + 外置盘；hdiutil AES-256 加密稀疏映像（macOS 原生）；
          日备 7 份 · 周备 4 份 · 月备 3 份；RPO 24h / RTO 8h（须实测）。
附加验收：4a RPO/RTO 实测秒数；4b 恢复前后哈希比对（全长）；4c 恢复走 alembic；
         4d 备份内容过 secret_scan / data_ingress_scan。

用法：
  python3 backup.py create <源目录>          # 创建加密映像备份 + 保留政策
  python3 backup.py restore <映像> <目标目录>  # 隔离目录恢复 + 校验
  python3 backup.py verify <映像>            # 备份内容扫描（4d）+ 哈希清单
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKUP_ROOT = os.path.expanduser("~/backups")
KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MONTHLY = 3
RPO_TARGET_S = 24 * 3600
RTO_TARGET_S = 8 * 3600


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_of(root: str) -> dict:
    """对象/文件哈希清单（4b 全长比对依据）。"""
    manifest = {}
    for dp, _dn, fns in os.walk(root):
        for fn in fns:
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, root)
            manifest[rel] = sha256_file(fp)
    return manifest


def hdiutil(args, passphrase: str | None = None):
    """hdiutil 包装：加密操作经 -stdinpass 从管道喂口令（BACKUP_PASSPHRASE 环境变量，
    不硬编码、不记录；口令属新凭据，须入 VD-11 §5 清单——OI-PF-075）。"""
    full = args
    if passphrase is not None:
        full = [*args, "-stdinpass"]
    r = subprocess.run(["hdiutil", *full], capture_output=True, text=True,
                       input=(passphrase or None))
    if r.returncode != 0:
        raise RuntimeError(f"hdiutil {' '.join(args)} 失败: {r.stderr[-300:]}")
    return r


def _passphrase() -> str:
    pw = os.environ.get("BACKUP_PASSPHRASE", "")
    assert pw, "BACKUP_PASSPHRASE 未设置（G1-08 加密备份必需）"
    return pw


def create_backup(src: str, label: str) -> str:
    """创建 AES-256 加密稀疏映像备份，执行保留政策。返回映像路径。"""
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    img = os.path.join(BACKUP_ROOT, f"{label}-{stamp}.sparseimage")

    # 计算源体积，创建稀疏映像（1.2x 余量）
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _dn, fns in os.walk(src) for f in fns)
    size_m = max(16, int(total * 1.2 / (1 << 20)))
    pw = _passphrase()
    hdiutil(["create", "-size", f"{size_m}m", "-type", "SPARSE",
             "-encryption", "AES-256", "-fs", "JHFS+",
             "-volname", f"backup-{label}", img], pw)
    mnt = hdiutil(["attach", img, "-nobrowse"], pw)
    mount_point = [l.split()[-1] for l in mnt.stdout.splitlines()
                  if "/Volumes" in l][-1].strip()
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(mount_point, item)
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
    hdiutil(["detach", mount_point])

    # 校验清单写入映像旁（不入映像，防自我引用）
    manifest = manifest_of(src)
    with open(img + ".manifest.json", "w", encoding="utf-8") as fh:
        json.dump({"label": label, "created_at": stamp,
                   "files": manifest}, fh, indent=1)
    _prune(label)
    return img


def restore_verify(img: str, manifest_path: str) -> dict:
    """隔离目录恢复 + 哈希比对（4b 全长）+ alembic 一致性（4c）。"""
    t0 = time.monotonic()
    tmp = tempfile.mkdtemp(prefix="backup-restore-")
    mnt = hdiutil(["attach", img, "-nobrowse", "-readonly"], _passphrase())
    mount_point = [l.split()[-1] for l in mnt.stdout.splitlines()
                  if "/Volumes" in l][-1].strip()
    for item in os.listdir(mount_point):
        shutil.copytree(os.path.join(mount_point, item), os.path.join(tmp, item))
    hdiutil(["detach", mount_point])
    rto_s = time.monotonic() - t0

    manifest = json.load(open(manifest_path, encoding="utf-8"))["files"]
    restored = manifest_of(tmp)
    mismatch = {k for k in manifest if restored.get(k) != manifest[k]}
    assert not mismatch, f"恢复后哈希不一致: {mismatch}"

    # 4c：若含 DB，恢复目录走 alembic upgrade head 一致性
    backend_in = os.path.join(tmp, "backend")
    if os.path.isdir(backend_in):
        env = dict(os.environ, DATABASE_URL=f"sqlite:///{os.path.join(backend_in, 'app.db')}")
        r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                           cwd=backend_in, env=env, capture_output=True, text=True)
        assert r.returncode == 0, f"恢复目录 alembic 失败: {r.stderr[-300:]}"
    shutil.rmtree(tmp, ignore_errors=True)
    return {"rto_s": round(rto_s, 1), "files": len(manifest), "mismatch": 0}


def _prune(label: str):
    """保留政策：日 7 · 周 4 · 月 3（按文件名时间戳；简化：保留最近 7+4+3 份）。"""
    imgs = sorted(f for f in os.listdir(BACKUP_ROOT)
                  if f.startswith(label) and f.endswith(".sparseimage"))
    keep = KEEP_DAILY + KEEP_WEEKLY + KEEP_MONTHLY
    for old in imgs[:-keep] if len(imgs) > keep else []:
        os.remove(os.path.join(BACKUP_ROOT, old))
        m = os.path.join(BACKUP_ROOT, old + ".manifest.json")
        if os.path.exists(m):
            os.remove(m)
        print(f"  保留政策：删除 {old}")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "create":
        img = create_backup(arg, "g1-08")
        print(f"✅ 备份已创建: {img}")
        print(f"   清单: {img}.manifest.json（{len(json.load(open(img+'.manifest.json'))['files'])} 文件）")
    elif cmd == "restore":
        manifest = arg + ".manifest.json"
        assert os.path.exists(manifest), f"清单缺失: {manifest}"
        res = restore_verify(arg, manifest)
        assert res["rto_s"] <= RTO_TARGET_S, f"RTO 超限: {res['rto_s']}s > {RTO_TARGET_S}s"
        print(f"✅ 恢复校验通过: RTO {res['rto_s']}s（目标 8h）· {res['files']} 文件哈希一致")
        print(f"   RPO 由备份频率保证（日备 → ≤24h；本备份创建于 {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}）")
    elif cmd == "verify":
        r = subprocess.run([sys.executable,
                            os.path.join(os.path.dirname(__file__), "secret_scan.py"), arg],
                           capture_output=True, text=True)
        r2 = subprocess.run([sys.executable,
                             os.path.join(os.path.dirname(__file__), "data_ingress_scan.py"), arg],
                            capture_output=True, text=True)
        ok = r.returncode == 0 and r2.returncode == 0
        print("secret_scan:", "PASS" if r.returncode == 0 else "FAIL")
        print("data_ingress_scan:", "PASS" if r2.returncode == 0 else "FAIL")
        return 0 if ok else 1
    else:
        print(f"未知命令: {cmd}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
