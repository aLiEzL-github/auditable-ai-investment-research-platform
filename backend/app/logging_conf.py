"""logging_conf.py —— JSON 结构化日志 + 秘密脱敏（G1-05）。

交付：JSON 日志 · 脱敏（SecretMaskFilter）
验收：日志和测试快照无密钥（基线验收）；合成密钥进日志须被掩码。
"""

import json
import logging
import re
import sys
import time

# 敏感字段名（值将被掩码）
# 敏感键名 —— **唯一真源**。下方正则由它派生，杜绝两表漂移。
#
# OI-PF-178：本常量原先**定义了从不使用**（去掉定义本身后出现 0 次），
# 而实际脱敏由另一份写死五个关键词的正则决定 ——
# passwd / apikey / authorization 三个**只在死清单里、不在生效正则里**。
# 危害不在死变量本身，在它造成的错觉：读代码的人会以为这八个键都被覆盖。
SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "api_key", "apikey",
                  "private_key", "authorization")

_KEYS_ALT = "|".join(re.escape(k) for k in SENSITIVE_KEYS)

# 已知秘密模式（URL 内嵌凭据 / 键值赋值 / 私钥 PEM / GitHub token）
#
# OI-PF-177：原键值正则要求关键词**紧接** = 或 : ，而 JSON 形态是
# "token": "..." —— 关键词后先跟一个引号，正则在那里断掉，**原样输出**。
# Authorization: Bearer ... 则因 authorization 不在原关键词表里而整条漏过。
# 实测九类载荷，两类真实敏感载荷未被脱敏。
#
# 本版三处改动：
#   ① 关键词表由 SENSITIVE_KEYS 派生（不再手写第二份）
#   ② 容忍键名两侧的引号
#   ③ 容忍 Bearer / Basic / Token 前缀 —— 否则 Authorization: Bearer eyJ…
#      只会脱掉前缀而把 token 留在日志里
SECRET_PATTERNS = [
    re.compile("//" + r"[^/\s:@]{2,}" + ":" + r"[^/\s:@]{2,}" + "@"),
    re.compile('(?i)"?(' + _KEYS_ALT + ')"?\\s*[=:]\\s*'
               '(?:Bearer\\s+|Basic\\s+|Token\\s+)?"?[^"\\s,}\\]]+'),
    re.compile(r"-----BEGIN (?:OPENSSH|RSA|EC|DSA|PGP|ENCRYPTED) PRIVATE KEY-----"),
    re.compile(r"(?i)ghp_[A-Za-z0-9]{20,}"),
]


class JsonFormatter(logging.Formatter):
    """先展开 args 再脱敏（脱敏必须在格式化之后、否则 %s 无法展开）。"""

    def format(self, record: logging.LogRecord) -> str:
        raw = record.getMessage()
        for pat in SECRET_PATTERNS:
            raw = pat.sub("<REDACTED>", raw)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": raw,
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger("app")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    return root
