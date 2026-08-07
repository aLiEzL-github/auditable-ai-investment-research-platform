"""settings.py —— 统一配置（G1-05）。

零外部依赖（stdlib dataclass + os.environ）：
  · 全部配置从环境变量读取，无硬编码默认值（除显式安全默认）
  · 密钥类配置（如 DATABASE_PASSWORD）不得出现在 .env.example
  · 本地默认只绑定 loopback（BIND_HOST=127.0.0.1）；容器场景显式传 0.0.0.0
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class Settings:
    app_port: int = field(default_factory=lambda: int(os.environ.get("APP_PORT", "8080")))
    bind_host: str = field(default_factory=lambda: os.environ.get("BIND_HOST", "127.0.0.1"))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", ""))
    # 密钥类配置：仅从环境读取，不提供默认值（缺失即空，由调用方 fail-closed）
    database_password: str = field(default_factory=lambda: os.environ.get("DATABASE_PASSWORD", ""))
    session_token_ttl_seconds: int = field(
        default_factory=lambda: int(os.environ.get("SESSION_TOKEN_TTL", "3600")))

    def validate(self) -> List[str]:
        """配置校验：非法值返回错误清单（fail-closed）。"""
        errors = []
        if not (0 < self.app_port < 65536):
            errors.append(f"APP_PORT 非法: {self.app_port}")
        if self.bind_host not in ("127.0.0.1", "0.0.0.0", "::1"):
            errors.append(f"BIND_HOST 非法: {self.bind_host}")
        if self.log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            errors.append(f"LOG_LEVEL 非法: {self.log_level}")
        return errors


def get_settings() -> Settings:
    return Settings()
