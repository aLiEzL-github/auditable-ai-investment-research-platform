"""network_probe.py —— 离线复建的断网断言探针（G4-08，D-8）。

唯一职责：证明网络确实不可达。真 TCP 连接尝试，任一探针可连即抛错
（fail-closed）—— 它不是出网能力，而是「离线」的机器断言。

架构：本模块持有 socket（M1/M4 名单内），故在 arch_import_check 的
LAYER_EXEMPT 中显式列名豁免（offline_probe 层，先例：supply_chain_refresh
的 scoped 出网豁免）；可信内核 publish_engine **不 import 本模块**，
离线复建时由调用方以回调注入探针 —— 内核保持零出网面。
"""
import socket
from typing import Sequence

OFFLINE_PROBE_HOSTS = ("1.1.1.1", "github.com", "www.stats.gov.cn")
OFFLINE_PROBE_PORT = 443
OFFLINE_PROBE_TIMEOUT = 3.0


def assert_network_unreachable(hosts: Sequence[str] = OFFLINE_PROBE_HOSTS,
                               port: int = OFFLINE_PROBE_PORT,
                               timeout: float = OFFLINE_PROBE_TIMEOUT) -> None:
    """D-8：真断网断言 —— 任一探针可连即抛错（不接受「理论上可以」）。

    探针为真实 TCP 连接尝试；在 OS 级断网（docker --network none /
    sandbox-exec deny network / unshare -n）内这些连接必然失败。
    离线复建必须先通过本断言才能执行。
    """
    reachable = []
    for host in hosts:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                reachable.append(host)
        except OSError:
            continue
    if reachable:
        raise ValueError(
            f"E-G4-08-001: 网络仍可达（{', '.join(reachable)}）—— 断网前提不成立")
