#!/usr/bin/env python3
"""image_arch_check.py —— 镜像架构须实测断言（OI-PF-064）。

**经典 Docker 构建器静默忽略 `--platform`，产出错架构镜像并报成功。**
实测（`OI-PF-064`）：无 buildx 时 `docker build --platform linux/amd64 .` 打印
`Install the buildx component to build images with BuildKit` 后继续构建，
最终报 `Successfully built` / `Successfully tagged`，而产物仍是 arm64 ——
只有 `docker run` 时才暴露。**这比构建失败更危险：它把错架构镜像标成目标
架构并声称成功。**

故 `OI-PF-064` 的强制要求是：`G1-06` 与 `G1-07` 的构建步骤须显式断言产物架构，
**不得以 `docker build` 退出码 0 作为跨架构构建成功的证据**。本工具把该要求机器化。

不猜、不推断：只读 `docker inspect` 的 `.Architecture`/`.Os`，与期望逐字比对。
镜像不存在、docker 不可用、字段读不出 —— 一律**判红**，不得静默跳过
（「没检查」与「检查通过」必须可分辨）。

用法：
    python3 backend/tools/image_arch_check.py <image> <expected>
    <expected> 形如 amd64/linux、arm64/linux，或仅 amd64（此时只校验架构）
"""
import shutil
import subprocess
import sys

# docker 的 .Architecture 用 GOARCH 词汇；命令行 --platform 用另一套写法。
# 两套词汇不相交会让比对永远不命中 —— OI-PF-128 就是这么来的，故显式建表。
ALIAS = {
    "x86_64": "amd64", "x86-64": "amd64", "linux/amd64": "amd64",
    "aarch64": "arm64", "linux/arm64": "arm64", "arm64/v8": "arm64",
}


def norm(s: str) -> str:
    s = s.strip().lower()
    return ALIAS.get(s, s)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    image, want = sys.argv[1], sys.argv[2]
    want_arch, _, want_os = want.partition("/")
    want_arch = norm(want_arch)
    want_os = want_os.strip().lower() or None

    if not shutil.which("docker"):
        print(f"❌ docker 不可用 —— **无法断言 {image} 的架构，判红**"
              f"（E-ARCH-003：没检查 ≠ 检查通过）")
        return 1
    r = subprocess.run(
        ["docker", "inspect", "--format", "{{.Architecture}}/{{.Os}}", image],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"❌ 取不到 {image} 的架构：{(r.stderr or '').strip()[:120]}"
              f" —— **判红**（E-ARCH-002：镜像不存在或字段缺失）")
        return 1

    got = r.stdout.strip()
    got_arch, _, got_os = got.partition("/")
    got_arch, got_os = norm(got_arch), got_os.strip().lower()

    bad = []
    if got_arch != want_arch:
        bad.append(f"架构 **{got_arch}**，期望 **{want_arch}**")
    if want_os and got_os != want_os:
        bad.append(f"OS {got_os}，期望 {want_os}")
    if bad:
        print(f"❌ E-ARCH-001: 镜像 {image} 的{'；'.join(bad)}。"
              f"\n   `docker build` 退出码为 0 **不构成**跨架构构建成功的证据 ——"
              f"经典构建器会静默忽略 --platform 并把错架构镜像标成目标架构"
              f"（OI-PF-064）。")
        return 1
    print(f"✅ 镜像架构合格：检查对象 1 个镜像（{image}）= "
          f"{got_arch}{'/' + got_os if want_os else ''}，与期望一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
