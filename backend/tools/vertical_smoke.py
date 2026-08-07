#!/usr/bin/env python3
"""vertical_smoke.py —— G1-06 垂直冒烟（K-1/T2 与 2b 附加验收）。

在 alembic upgrade head 建的库上真实跑完整链路：
  迁移 → 入队（submit）→ Worker 领取（claim_next）→ 完成（complete）→ 落库断言
  租约容器内复验（2c）：过期后原持有者 complete 须被拒（E-LEASE-002）

用法：在仓库根运行（backend/ 下含 alembic.ini）；DATABASE_URL 可覆盖。
"""

import os
import sqlite3
import subprocess
import sys

BACKEND = os.path.join(os.path.dirname(__file__), "..")
APP = os.path.join(BACKEND, "app")
_URL = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BACKEND, 'app.db')}")
_DB_PATH = _URL.replace("sqlite:///", "", 1)
DB = os.path.abspath(_DB_PATH)
ENV = dict(os.environ, DATABASE_URL=_URL)


def main() -> int:
    sys.path.insert(0, APP)
    from repository import create_repository
    from jobs import JobQueue
    from datetime import datetime, timedelta

    if os.path.exists(DB):
        os.remove(DB)
    # ① 迁移（部署路径，非 create_all）
    r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                       cwd=BACKEND, env=ENV, capture_output=True, text=True)
    assert r.returncode == 0, f"alembic upgrade head 失败: {r.stderr[-400:]}"
    print("① alembic upgrade head OK")

    repo = create_repository(_URL)
    q = JobQueue(repo)

    # ② 入队
    job = q.submit("smoke-1", '{"payload": "vertical-smoke"}')
    print(f"② 入队 OK（job id={job.id}）")

    # ③ Worker 领取
    claimed = q.claim_next("W1", lease_seconds=60)
    assert claimed is not None and claimed.id == job.id, "领取失败"
    print("③ Worker 领取 OK")

    # ④ 完成 → 落库断言
    done = q.complete(claimed.id, "W1", "vertical-smoke-done")
    assert done.status == "DONE", f"完成失败: {done.status}"
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT status, result, worker_id FROM job WHERE id=?",
                       (job.id,)).fetchone()
    conn.close()
    assert row == ("DONE", "vertical-smoke-done", "W1"), f"落库断言失败: {row}"
    print("④ 完成 + 落库 OK:", row)

    # ⑤ 租约容器内复验（2c）：过期后原持有者 complete 被拒
    q.submit("smoke-2")
    a = q.claim_next("A", lease_seconds=1)
    a.lease_until = datetime.utcnow() - timedelta(seconds=5)
    q.s.commit()
    import time
    time.sleep(0.1)
    b = q.claim_next("B")
    assert b is not None and b.worker_id == "B", "B 抢占失败"
    try:
        q.complete(b.id, "A", "过期 A 提交")
        raise AssertionError("过期持有者 complete 未被拒")
    except ValueError as e:
        assert "E-LEASE-002" in str(e), f"错误码不符: {e}"
    done2 = q.complete(b.id, "B", "B 完成")
    assert done2.status == "DONE"
    print("⑤ 租约容器内复验 OK（A 被拒 E-LEASE-002 · B 成功）")

    q.s.close()
    repo.engine.dispose()
    print("✅ vertical-smoke 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
