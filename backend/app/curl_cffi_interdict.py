"""curl_cffi_interdict.py —— ADR-018 §4 守卫 C：运行期拦截。

守卫 A/B 是**静态**的（扫源码），只能覆盖本仓代码。
本模块提供**运行期**保证：curl_cffi 一旦被真正导入/调用即抛错 ——
使「持有但不使用」在运行期也成立，而不只是代码审查层面。

用法：进程入口 `import curl_cffi_interdict; curl_cffi_interdict.install()`。
拆除须走 ADR（ADR-018 §4 守卫 C）。
"""
import importlib.abc
import importlib.machinery
import sys

BLOCKED = "curl_cffi"


class InterdictError(ImportError):
    """curl_cffi 被调用：ADR-008 C2 禁止规避反爬，ADR-018 §4 守卫 C 拦截。"""


class _Interdictor(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == BLOCKED or fullname.startswith(BLOCKED + "."):
            raise InterdictError(
                f"E-ADR018-C: 运行期拦截 —— 禁止导入 {fullname}。"
                f"ADR-008 C2「不规避任何反爬机制」永久有效；"
                f"ADR-018 §4 允许**持有**该库但**禁止使用**。")
        return None


def install() -> bool:
    """装入拦截器（幂等）。返回是否新装。

    OI-PF-135：`sys.meta_path` 只在模块**尚未导入**时被咨询。若 curl_cffi 已在
    `sys.modules` 中，拦截器装了也拦不住 —— 实测 install() 返回 True 之后
    `import curl_cffi` 仍拿到 0.16.0，`import akshare` 照样成功。
    故此处**失败关闭**：已被导入即抛错，不假装拦截成功。
    清 sys.modules 不足以补救 —— 先前导入者手里已经握有模块对象。
    """
    already = sorted(m for m in sys.modules
                     if m == BLOCKED or m.startswith(BLOCKED + "."))
    if already:
        raise InterdictError(
            f"E-ADR018-C-LATE: {BLOCKED} 在拦截器装入前已被导入（{already[:3]}）。"
            f"拦截器对已缓存模块无效，且先前导入者已持有模块对象 —— "
            f"拒绝以「已装入」之名给出虚假保证（OI-PF-135）。"
            f"install() 须在进程入口、任何业务导入之前调用。")
    if any(isinstance(f, _Interdictor) for f in sys.meta_path):
        return False
    sys.meta_path.insert(0, _Interdictor())
    return True


def uninstall() -> int:
    """仅供测试使用。生产代码调用即违反 ADR-018 §4。"""
    n = len(sys.meta_path)
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _Interdictor)]
    return n - len(sys.meta_path)
