# 仓库治理与 PR 流程（G1-01）

## 目录结构（Monorepo）

```text
backend/       Python 后端：app/（服务）、tools/（扫描器/生成器）、tests/
frontend/      React 前端（G5 起）
contracts/     canonical 领域 Schema / OpenAPI / 错误码（G1-02 起，唯一权威契约源）
tests/         （顶层不再放测试——统一在 backend/tests、frontend/tests）
docs/          本文件与后续文档
infra/         Docker / compose / 部署定义
.github/       CI 工作流
```

## Quickstart（原生路径，Gate 1 E1 / OI-PF-114）

macOS arm64 原生一条命令启动空壳服务（默认只绑 loopback）：

```bash
# 可选环境变量：APP_PORT（默认 8080）· BIND_HOST（默认 127.0.0.1）· LOG_LEVEL（默认 INFO）
python3 backend/app/main.py
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8080/livez    # 就绪（空壳存活）
curl -fsS http://127.0.0.1:8080/readyz   # 迁移与依赖就绪
```

非默认端口示例：`APP_PORT=18099 python3 backend/app/main.py --bind 127.0.0.1`。
`--bind` 为 main.py 的 CLI 参数；环境变量与 CLI 均可覆盖（Settings 校验 APP_PORT/BIND_HOST 非法值即退出）。

## 变更规则（G1-01 验收「后续改动只经分支/PR」）

1. **main 禁止直推** —— 唯一豁免是 578ac18e（空仓初始分支创建），已用掉；
2. 全部变更走 **分支 → PR → CI 全绿 → 合并**（ruleset 7 条 required checks + require_code_owner_review）；
3. **contracts/ 是权威契约源**：Schema/API 变更必须先改 contracts/，实现侧（backend/）只做消费与实现；
4. 扫描器与 CI 的变更必须复跑全部回归（E 组变异测试集）。

## 目录所有权（CODEOWNERS）

单人项目下全部归仓库主；目录划分供第 2 名自然人接入时对齐（VD-02 重开条款）。

**研究信息不构成投资建议。**
