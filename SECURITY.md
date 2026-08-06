# SECURITY.md —— 安全政策

## 支持范围

本仓库处于**治理期/开发期**：Gate 0 已通过，G1 起逐步实现。实现完成前，
功能性与安全性承诺均不成立。

| 版本 | 支持 |
|---|---|
| 主分支（main） | 不支持——公开仅供治理透明；未发布 release |

## 报告安全漏洞

**请勿**创建公开 Issue 描述漏洞细节。

- 私有报告渠道：联系仓库所有者 GitHub 账号（aLiEzL-github）
- 期望响应时间：72 小时内确认收到；修复时间依严重性而定

## 本仓库的已知限制（Gate 0 已如实登记）

- 24 项架构负测（N01—N24）**尚未实现**（G0-04 设计合同已冻结，实现属 G2/G7）
- 29 项 RequestRightsGuard 负测 **NOT_EXECUTED**（G0-03，实现属 G2）
- 解析器隔离为**进程级**（subprocess + setrlimit + sandbox-exec，ADR-007），
  容器级隔离是恢复义务（OI-PF-045）
- 本机 amd64 构建 BLOCKED（buildx 供应链验证失败，OI-PF-063）；验收移至 GitHub runner

## 数据与隐私

本仓库为公开 source-available（VD-05，专有许可）。**原始数据、汇编、批量转储
禁止入仓**（ADR-006 五层防护）；仓库内只允许代码与指针（locator + SHA-256）。

**研究信息不构成投资建议。**
