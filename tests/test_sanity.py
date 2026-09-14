"""骨架 sanity 测试：验证依赖版本与配置模块契约。全程离线，不用真实凭据。"""

import pytest
import pydantic

from visual_intent_agent.config import (
    ConfigurationError,
    Settings,
    has_provider_credentials,
    load_settings,
)


def test_pydantic_is_v2() -> None:
    assert pydantic.VERSION.startswith("2.")


def test_settings_load_from_env_only(tmp_path) -> None:
    settings = load_settings(
        env={
            "VIA_PROVIDER_BASE_URL": "https://example.invalid/v1",
            "VIA_PROVIDER_API_KEY": "test-placeholder-key",
        },
        env_file=tmp_path / "absent.env",
    )
    assert settings.provider_base_url == "https://example.invalid/v1"
    assert settings.provider_api_key.get_secret_value() == "test-placeholder-key"
    # 默认值
    assert settings.llm_model == "qwen3.8-max"
    assert settings.image_model == "qwen-image-3.0"
    assert settings.http_max_retries == 2


def test_settings_env_file_fallback(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VIA_PROVIDER_BASE_URL=https://example.invalid/v1\n"
        "VIA_PROVIDER_API_KEY=test-file-key\n"
        "VIA_LLM_MODEL=kimi-k3\n",
        encoding="utf-8",
    )
    settings = load_settings(env={}, env_file=env_file)
    assert settings.llm_model == "kimi-k3"
    # 环境变量优先于 .env
    override = load_settings(env={"VIA_LLM_MODEL": "deepseek-v4.1-flash"}, env_file=env_file)
    assert override.llm_model == "deepseek-v4.1-flash"


def test_settings_missing_credentials_raise(tmp_path) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(env={}, env_file=tmp_path / "absent.env")
    assert not has_provider_credentials(env={}, env_file=tmp_path / "absent.env")


def test_settings_are_frozen() -> None:
    settings = Settings(
        provider_base_url="https://example.invalid/v1",
        provider_api_key="test-placeholder-key",
    )
    with pytest.raises(Exception):
        settings.llm_model = "other"  # type: ignore[misc]
