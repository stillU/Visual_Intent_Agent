"""Generation 模型测试（Step 08）：字段面、不可变、无 URL、时间戳、错误 code。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from visual_intent_agent.config import PROJECT_ROOT
from visual_intent_agent.generation import (
    GENERATION_ARTIFACT_NOT_FOUND,
    GENERATION_ERROR_CODES,
    GENERATION_INVALID_STATE,
    GENERATION_NO_VALID_CONFIRMATION,
    GENERATION_OUTPUT_WRITE_FAILED,
    GENERATION_SESSION_MISMATCH,
    GenerationArtifact,
    GenerationError,
    OutputRef,
)
from visual_intent_agent.generation.pipeline import _project_relative_path
from visual_intent_agent.prompt_engine import PromptParameters


def _ref(**overrides) -> OutputRef:
    payload = {
        "path": "outputs/generations/gen_1/image_1.png",
        "mime_type": "image/png",
        "byte_size": 70,
    }
    payload.update(overrides)
    return OutputRef(**payload)  # type: ignore[arg-type]


def _artifact(**overrides) -> GenerationArtifact:
    payload = {
        "generation_id": "gen_1",
        "session_id": "ses_1",
        "prompt_artifact_id": "pra_1",
        "target_model": "qwen-image-3.0",
    }
    payload.update(overrides)
    return GenerationArtifact(**payload)  # type: ignore[arg-type]


def test_output_ref_holds_only_stable_local_references() -> None:
    ref = _ref()
    assert ref.path == "outputs/generations/gen_1/image_1.png"
    assert ref.mime_type == "image/png"
    assert ref.byte_size == 70
    assert "url" not in OutputRef.model_fields


@pytest.mark.parametrize(
    "path",
    [
        "/abs/outputs/generations/gen_1/image_1.png",
        "https://oss.invalid/x.png?sig=1",
        "C:/outputs/image_1.png",
        "   ",
    ],
)
def test_output_ref_rejects_absolute_paths_and_urls(path: str) -> None:
    with pytest.raises(ValidationError):
        _ref(path=path)


def test_output_ref_rejects_negative_byte_size() -> None:
    with pytest.raises(ValidationError):
        _ref(byte_size=-1)


def test_project_relative_path_matches_the_frozen_output_ref_form() -> None:
    # 默认 output_dir（<项目根>/outputs/generations）下必须是冻结表给出的形态。
    absolute = PROJECT_ROOT / "outputs" / "generations" / "gen_x" / "image_1.png"
    assert _project_relative_path(absolute) == "outputs/generations/gen_x/image_1.png"
    # tmp_path 输出目录 → 相对项目根的路径（仍可重新定位，绝不是 URL）。
    outside = PROJECT_ROOT.parent / "somewhere" / "gen_x" / "image_1.png"
    relative = _project_relative_path(outside)
    assert not relative.startswith("/")
    assert "://" not in relative
    assert (PROJECT_ROOT / relative).resolve() == outside.resolve()


def test_output_ref_is_frozen_and_forbids_extra() -> None:
    ref = _ref()
    with pytest.raises(ValidationError):
        ref.byte_size = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _ref(url="https://oss.invalid/x.png")


def test_artifact_reuses_step_07_prompt_parameters() -> None:
    artifact = _artifact()
    assert isinstance(artifact.parameters, PromptParameters)
    assert artifact.parameters.size == "1024x1024"
    assert set(PromptParameters.model_fields) == {"size"}


def test_artifact_optional_fields_default_to_none_never_fabricated() -> None:
    artifact = _artifact()
    assert artifact.model_version is None
    assert artifact.seed is None
    assert artifact.provider_request_id is None
    assert artifact.output_refs == []
    assert artifact.schema_version == "v1"


def test_artifact_accepts_the_full_frozen_field_set() -> None:
    artifact = _artifact(
        model_version="qwen-image-3.0",
        parameters=PromptParameters(size="512x512"),
        seed=7,
        output_refs=[_ref()],
        provider_request_id="req_1",
    )
    assert artifact.model_version == "qwen-image-3.0"
    assert artifact.parameters.size == "512x512"
    assert artifact.seed == 7
    assert artifact.provider_request_id == "req_1"
    assert artifact.output_refs == [_ref()]


@pytest.mark.parametrize("field", ["generation_id", "session_id", "prompt_artifact_id", "target_model"])
def test_artifact_required_identifiers_must_not_be_blank(field: str) -> None:
    with pytest.raises(ValidationError):
        _artifact(**{field: "  "})


def test_artifact_seed_rejects_bool() -> None:
    with pytest.raises(ValidationError):
        _artifact(seed=True)


def test_artifact_is_frozen_and_forbids_extra() -> None:
    artifact = _artifact()
    with pytest.raises(ValidationError):
        artifact.seed = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _artifact(image_url="https://oss.invalid/x.png")


def test_artifact_timestamps_are_tz_aware_utc_and_round_trip() -> None:
    artifact = _artifact(created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc))
    payload = artifact.model_dump_json()
    assert "+00:00" in payload
    assert GenerationArtifact.model_validate_json(payload) == artifact


def test_artifact_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError):
        _artifact(created_at=datetime(2026, 1, 2, 3, 4, 5))


def test_artifact_never_carries_a_temporary_url() -> None:
    payload = _artifact(output_refs=[_ref()]).model_dump_json()
    assert "http" not in payload
    assert "Expires" not in payload
    assert "Signature" not in payload


def test_generation_error_codes_are_frozen_and_validated() -> None:
    assert GENERATION_ERROR_CODES == frozenset(
        {
            GENERATION_INVALID_STATE,
            GENERATION_NO_VALID_CONFIRMATION,
            GENERATION_ARTIFACT_NOT_FOUND,
            GENERATION_SESSION_MISMATCH,
            GENERATION_OUTPUT_WRITE_FAILED,
        }
    )
    error = GenerationError(GENERATION_INVALID_STATE, "boom")
    assert error.code == GENERATION_INVALID_STATE
    assert GENERATION_INVALID_STATE in str(error)
    with pytest.raises(ValueError):
        GenerationError("generation.unknown", "boom")
