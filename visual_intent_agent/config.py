"""配置加载（公共基础设施，架构步骤交付，唯一允许提前实现的模块）。

规则（对 Step 01～09 的实现 Agent 有约束力）：

- 所有 Provider 连接信息、模型选择、超时、重试、数据库路径、输出目录
  一律经本模块读取；业务代码不得自行读取环境变量或 .env。
- 环境变量优先，项目根目录 .env 文件回退。
- API key 以 pydantic.SecretStr 持有；任何日志、异常消息、Artifact、
  测试中都不得出现明文 key。
- 默认测试（tests/ 下非 smoke）必须能用伪造环境变量构造 Settings，
  不得依赖真实 .env。

配置项命名表（环境变量名 -> Settings 字段）见 docs/ARCHITECTURE.md 第 3 节。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

#: 配置字段名 -> 环境变量名（唯一命名表，文档与代码以此为准）
ENV_VAR_NAMES: dict[str, str] = {
    "provider_base_url": "VIA_PROVIDER_BASE_URL",
    "provider_api_key": "VIA_PROVIDER_API_KEY",
    "llm_model": "VIA_LLM_MODEL",
    "image_model": "VIA_IMAGE_MODEL",
    "llm_timeout_seconds": "VIA_LLM_TIMEOUT_SECONDS",
    "image_timeout_seconds": "VIA_IMAGE_TIMEOUT_SECONDS",
    "http_max_retries": "VIA_HTTP_MAX_RETRIES",
    "db_path": "VIA_DB_PATH",
    "output_dir": "VIA_OUTPUT_DIR",
}

_REQUIRED_FIELDS = frozenset({"provider_base_url", "provider_api_key"})


class ConfigurationError(Exception):
    """配置缺失或非法时抛出。消息中绝不包含 key 值本身。"""


class Settings(BaseModel):
    """运行时配置。frozen：构造后不可变。"""

    model_config = ConfigDict(frozen=True)

    provider_base_url: str
    provider_api_key: SecretStr
    llm_model: str = "qwen3.8-max"
    image_model: str = "qwen-image-3.0"
    llm_timeout_seconds: float = 60.0
    image_timeout_seconds: float = 120.0
    http_max_retries: int = 2
    db_path: Path = PROJECT_ROOT / "data" / "visual_intent.db"
    output_dir: Path = PROJECT_ROOT / "outputs" / "generations"

    @field_validator("provider_base_url")
    @classmethod
    def _base_url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("provider_base_url must start with http:// or https://")
        return value.rstrip("/")


def _parse_env_file(path: Path) -> dict[str, str]:
    """解析最小 .env 格式：KEY=VALUE 行、# 注释、可选引号、可选 export 前缀。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise ConfigurationError(f"{path}:{lineno}: invalid line, expected KEY=VALUE")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_settings(
    env: Mapping[str, str] | None = None,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> Settings:
    """加载配置。

    优先级：env（默认 os.environ） > env_file（默认项目根 .env） > 字段默认值。
    缺必需项或值非法时抛 ConfigurationError（消息不含敏感值）。
    """
    environ = os.environ if env is None else env
    file_values = _parse_env_file(env_file) if env_file is not None else {}

    kwargs: dict[str, str] = {}
    for field_name, env_name in ENV_VAR_NAMES.items():
        raw = environ.get(env_name)
        if raw is None:
            raw = file_values.get(env_name)
        if raw is not None:
            kwargs[field_name] = raw

    try:
        return Settings.model_validate(kwargs)
    except ValidationError as exc:
        missing = [
            ENV_VAR_NAMES[f]
            for f in sorted(_REQUIRED_FIELDS)
            if f not in kwargs
        ]
        detail = f"; missing required env vars: {', '.join(missing)}" if missing else ""
        raise ConfigurationError(f"invalid configuration{detail}: {exc.error_count()} error(s)") from exc


def has_provider_credentials(
    env: Mapping[str, str] | None = None,
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> bool:
    """凭据是否存在（供 smoke 测试 skip 判断；不暴露凭据内容）。"""
    try:
        load_settings(env=env, env_file=env_file)
    except ConfigurationError:
        return False
    return True
