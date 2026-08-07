#!/usr/bin/env python3
"""migration_check.py —— K-1/T2 迁移一致性校验（CI required check）。

步骤（对应验收）：
  ① 全新库 alembic upgrade head
  ② 断言 模型 __tablename__ 集合 ⊆ 实际表集合（job 表曾缺失，K-1）
  ③ alembic downgrade base → 再 upgrade（回滚 + 空库重建）
  ④ 断言 模型表集合 − 迁移 create_table 集合 = 空（先红后绿：job 即红态用例）

用法：在仓库根运行（backend/ 下含 alembic.ini）。
"""

import os
import re
import sqlite3
import subprocess
import sys

BACKEND = os.path.join(os.path.dirname(__file__), "..")
APP = os.path.join(BACKEND, "app")
MIGRATIONS = os.path.join(BACKEND, "migrations", "versions")
DB = os.path.join(BACKEND, "app.db")


def alembic(*args):
    # 双引擎：环境已有 DATABASE_URL（如 CI 的 PG URL）则保留，否则 sqlite（R-1(a)）
    env = dict(os.environ)
    env.setdefault("DATABASE_URL", f"sqlite:///{DB}")
    r = subprocess.run([sys.executable, "-m", "alembic", *args],
                       cwd=BACKEND, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"❌ alembic {' '.join(args)} 失败:\n{r.stderr[-500:]}")
        sys.exit(1)
    return r


def url_is_pg() -> bool:
    return os.environ.get("DATABASE_URL", "").startswith(("postgresql", "postgres+"))


def _engine_tables():
    """实际库表集合：sqlite 文件或 postgres URL（G1-03 双数据库，R-1(a)）。"""
    url = os.environ.get("DATABASE_URL", "")
    if url_is_pg():
        import psycopg
        with psycopg.connect(url.replace("+psycopg", "")) as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public'").fetchall()
        return {r[0] for r in rows}
    conn = sqlite3.connect(DB)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    conn.close()
    return tables


def main() -> int:
    sys.path.insert(0, APP)
    from repository import Base
    import jobs  # noqa: F401 注册 job 表模型

    # ① upgrade head（双引擎共用：sqlite 清库 / PG 直接迁）
    if url_is_pg():
        alembic("upgrade", "head")
    else:
        if os.path.exists(DB):
            os.remove(DB)
        alembic("upgrade", "head")

    # ② 模型 ⊆ 实际（双引擎）
    actual = _engine_tables()
    model = set(Base.metadata.tables.keys())
    missing = model - actual
    assert not missing, f"模型表缺失于实际库: {sorted(missing)}"
    print(f"①+② upgrade head + 模型⊆实际 OK（{len(model)} 表）")

    # ③ 回滚 + 空库重建
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    n = len(_engine_tables())
    assert n >= 4, f"空库重建后表数异常: {n}"
    print(f"③ 回滚 + 空库重建 OK（{n} 表）")

    # ④ 模型表 − 迁移 create_table = 空
    created = set()
    for fn in os.listdir(MIGRATIONS):
        if fn.endswith(".py"):
            src = open(os.path.join(MIGRATIONS, fn), encoding="utf-8").read()
            created |= set(re.findall(r'create_table\(["\']([^"\']+)', src))
    diff = model - created
    assert not diff, f"模型有、迁移无: {sorted(diff)}（红态用例）"
    print("④ 模型/迁移差集为空 OK")
    print("✅ migration-check 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
