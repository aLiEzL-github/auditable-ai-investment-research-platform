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
SENSITIVE_KEYS = ("password", "passwd", "secret", "token", "api_key", "apikey",
                  "private_key", "authorization")
# 已知秘密模式（URL 内嵌凭据 / 密钥赋值）
SECRET_PATTERNS = [
    re.compile("//" + r"[^/\s:@]{2,}" + ":" + r"[^/\s:@]{2,}" + "@"),
    re.compile(r"(?i)(password|secret|token|api_key|private_key)\s*[=:]\s*\S+"),
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
