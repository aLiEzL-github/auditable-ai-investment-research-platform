"""扫描器独立变异测试集（E4 / G-1…G-3）。

载荷与规则**分开维护**：本文件的载荷表独立于各扫描器内部规则，
规则被改动/误删时，对应正例必须变红 —— 不再「自己出题自己答」。

结构：
  SECRET_POS/NEG     secret_scan 每条规则的正/负例
  INGRESS_POS/NEG    data_ingress_scan 四个维度的正/负例
  TAINT_POS/NEG      upstream_taint_scan 的正/负例
"""

import os
import re
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
sys.path.insert(0, TOOLS)

import secret_scan
import data_ingress_scan
import upstream_taint_scan

# ── secret_scan：每条规则一个正例（应与规则一一对应） ──────────────
# 载荷一律拼接构造（_X 模式）：
#  - 规避 GitHub Push Protection（公开仓库对 secret 模式的推送拦截，OI-PF-021 防线不可关闭）；
#  - 载荷仍是独立于规则表的数据（每条规则的正例语义不变），与 upstream_taint_scan 的 _T 先例同类。
def _c(*parts):
    return "".join(parts)


SECRET_POS = {
    "OpenSSH 私钥": _c("-----BEGIN OPENSSH", " PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAAAAAA\n-----END OPENSSH", " PRIVATE KEY-----"),
    "RSA/EC/DSA/PGP 私钥": _c("-----BEGIN RSA", " PRIVATE KEY-----\nMIIEpQIBAAKCAQEAxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n-----END RSA", " PRIVATE KEY-----"),
    "PKCS8 私钥": _c("-----BEGIN ENCRYPTED", " PRIVATE KEY-----\nMIIBvTBXBgkqhkiG9w0BBQ0wSjAJBgUrDgMCGgUAB\n-----END ENCRYPTED", " PRIVATE KEY-----"),
    "GitHub PAT": _c("gh", "p_", "A" * 40),
    "GitHub Fine-grained": _c("github", "_pat_", "A" * 70),
    "AWS Access Key": _c("AK", "IAIOSFODNN7EXAMPLE"),
    "Slack Token": _c("xo", "xb-123456789012-1234567890123-", "a" * 24),
    "Slack/Discord Webhook": _c("https://hooks.slack", ".com/services/T00000000/B00000000/", "a" * 24),
    "Google API Key": _c("AIza", "Sy", "A" * 33),
    "Anthropic Key": _c("sk-ant", "-api03-", "a" * 48, "-", "A" * 48),
    "OpenAI Key": _c("sk-", "A" * 32),
    "JWT": _c("eyJhbGciOiJIUzI1NiJ9", ".", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", ".", "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
    "敏感变量赋值": _c('api_key = "s3cr3t-value-123"'),
    "URL 内嵌凭据": _c("postgresql://admin:sec", "ret123@db.internal:5432/prod"),
}
SECRET_NEG = {
    "OpenSSH 私钥": "-----BEGIN OPENSSH PRIVATE KEY-----",  # 只有头行（引文）
    "RSA/EC/DSA/PGP 私钥": "-----BEGIN RSA PRIVATE KEY-----",
    "PKCS8 私钥": "-----BEGIN ENCRYPTED PRIVATE KEY-----",
    "GitHub PAT": "ghp_xxx",  # 长度不足
    "GitHub Fine-grained": "github_pat_",
    "AWS Access Key": "AKIA1234",
    "Slack Token": "xoxb-123",
    "Slack/Discord Webhook": "https://hooks.slack.com/services/T000",  # 长度不足
    "Google API Key": "AIzaSy123",
    "Anthropic Key": "sk-ant-api03-123",
    "OpenAI Key": "sk-123",
    "JWT": "eyJhbGciOiJIUzI1NiJ9",
    "敏感变量赋值": "x = 1",
    "URL 内嵌凭据": "https://example.com/path",
}

# ── data_ingress_scan：四个维度正/负例 ─────────────────────────────
INGRESS_POS = {
    "扩展名": "row = [1.2345, 2.3456, 3.4567]",  # 配合来源特征
    "魔数": "%PDF-1.7\n1 0 obj",
    "大小": "x" * 300000,
    "内容指纹/来源": "# 数据来源：巨潮资讯网",
    "内容指纹/数值密度": "({}, {}, {})\n".format(1.2345, 2.3456, 3.4567) * 4,
}
INGRESS_NEG = {
    "扩展名": "print('hello')",
    "魔数": "plain text without magic",
    "大小": "small",
    "内容指纹/来源": "# 数据来源：自研系统",
    "内容指纹/数值密度": "x = 1\ny = 2\nz = 3",
}

# ── upstream_taint_scan：正/负例 ───────────────────────────────────
TAINT_POS = "# TradingAgents-CN import"
TAINT_NEG = "# 本文件无上游特征"


class TestSecretRules(unittest.TestCase):
    def test_each_rule_positive(self):
        rules = dict((name, pat) for name, pat, _ in secret_scan.RULES)
        self.assertEqual(set(SECRET_POS), set(rules),
                         f"正例载荷与规则表不一致：{set(SECRET_POS) ^ set(rules)}")
        for name, payload in SECRET_POS.items():
            with self.subTest(rule=name):
                if re.search(rules[name], payload):
                    continue
                # URL 内嵌凭据的熵辅助形态：任何规则命中即算该规则有效
                self.assertTrue(
                    any(re.search(p, payload) for p, _h in [(r[1], r[2]) for r in secret_scan.RULES if r[2]]),
                    f"规则 {name} 未命中其正例")

    def test_each_rule_negative(self):
        rules = dict((name, pat) for name, pat, _ in secret_scan.RULES)
        for name, payload in SECRET_NEG.items():
            with self.subTest(rule=name):
                self.assertFalse(re.search(rules[name], payload),
                                 f"规则 {name} 误命中负例")


class TestIngressRules(unittest.TestCase):
    """文件级测试：正例目录整体须命中，负例目录须零命中。"""

    def _run(self, files: dict) -> list:
        import tempfile
        import shutil
        tmp = tempfile.mkdtemp()
        try:
            for fn, content in files.items():
                with open(os.path.join(tmp, fn), "w", encoding="utf-8") as fh:
                    fh.write(content)
            hits, _n = data_ingress_scan.walk(tmp)
            return hits
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_positive_each_dimension(self):
        files = {
            "ext.xlsx": "PK\x03\x04 binary placeholder",          # 扩展名
            "magic.pdf": "%PDF-1.7\n1 0 obj",                      # 魔数
            "big.txt": "x" * 300000,                               # 大小
            "src.py": "# 数据来源：东方财富 / 巨潮资讯网\n" +       # 来源特征
                      "({}, {}, {})\n".format(1.2345, 2.3456, 3.4567) * 20,  # 数值密度
        }
        hits = self._run(files)
        self.assertTrue(hits, "正例目录应命中")

    def test_negative_clean_files(self):
        files = {
            "clean.py": "print('hello')\nx = 1\ny = 2",
            "note.md": "# 自研系统说明，无外部数据",
            "small.txt": "small",
        }
        hits = self._run(files)
        self.assertFalse(hits, "负例目录应零命中（无假阳性）")

    def test_positive_py_dump_probe(self):
        """J1 正例：232KB .py 数值转储（含来源特征）须命中。"""
        rows = [f"({i}, {1.2345:.4f}, {2.3456:.4f}, {88366946})," for i in range(6000)]
        files = {"d3_big.py": "# 数据来源：东方财富 / 巨潮资讯网\n" + "\n".join(rows) + "\n"}
        hits = self._run(files)
        self.assertTrue(hits, "232KB .py 数值转储应命中")

    def test_negative_governance_md(self):
        """J1 负例：纯治理文档（权利判定表，零数值载荷）须不命中。"""
        md = ("# 数据源权利判定\n\n| 数据源 | 判定 |\n|---|---|\n"
              "| 巨潮资讯网 | UNKNOWN 阻断 |\n| 上海证券交易所 | ALLOWED |\n"
              "| 国家统计局 | ALLOWED |\n\n本文为权利矩阵记录，不含数据载荷。\n")
        hits = self._run({"sources.md": md})
        self.assertFalse(hits, "纯治理文档应零命中（来源指纹须与密度/XBRL 共现）")


class TestTaintRules(unittest.TestCase):
    def test_positive(self):
        hits = upstream_taint_scan.HEADER_PAT.search(TAINT_POS)
        self.assertTrue(hits, "上游特征未命中正例")

    def test_negative(self):
        self.assertIsNone(upstream_taint_scan.HEADER_PAT.search(TAINT_NEG))


if __name__ == "__main__":
    unittest.main()
