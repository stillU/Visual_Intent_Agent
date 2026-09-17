"""Step 07 模型合同测试：SourceBinding / PromptParameters / PromptArtifact / 错误 / 请求。

冻结字段面见 `docs/ARCHITECTURE.md` 第 4 节「Step 07 — prompt_engine」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import utc_now
from visual_intent_agent.prompt_engine import (
    PROMPT_ERROR_CODES,
    PROMPT_MISSING_SOURCE_BINDING,
    PROMPT_NO_VALID_CONFIRMATION,
    PROMPT_UNAUTHORIZED_ADDITION,
    PROMPT_UNSUPPORTED_REQUIREMENT,
    PromptArtifact,
    PromptCompilationError,
    PromptCompileRequest,
    PromptParameters,
    SourceBinding,
)


def _binding(**overrides):
    values = {
        "clause_id": "clause_00",
        "text": "subject: a cat",
        "source_kind": "intent",
        "intent_path": "subject.description",
    }
    values.update(overrides)
    return SourceBinding(**values)


def _artifact(**overrides):
    fields = {
        "prompt_artifact_id": "pra_1",
        "session_id": "ses_1",
        "based_on_intent_revision_id": "irev_1",
        "based_on_confirmation_id": "cnf_1",
        "target_model": "qwen-image-3.0",
        "prompt": "subject: a cat",
    }
    fields.update(overrides)
    return PromptArtifact(**fields)


def test_source_binding_has_exactly_the_frozen_fields():
    assert list(SourceBinding.model_fields) == [
        "clause_id",
        "text",
        "source_kind",
        "intent_path",
        "rule_id",
        "realization_id",
    ]


def test_source_binding_defaults_are_none_and_frozen():
    binding = _binding()
    assert binding.rule_id is None
    assert binding.realization_id is None
    assert binding.model_config["frozen"] is True
    assert binding.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        SourceBinding(
            clause_id="c", text="t", source_kind="intent", intent_path="subject.description", bogus=1
        )
    with pytest.raises(ValidationError):
        _binding(source_kind="model_best_practice")


def test_source_kinds_are_exactly_the_four_frozen_values():
    for kind in ("intent", "delegation", "realization", "runtime"):
        assert _binding(source_kind=kind).source_kind == kind
    # 知识引用是诊断/追溯，不是新的授权来源类别。
    with pytest.raises(ValidationError):
        _binding(source_kind="knowledge")


def test_prompt_parameters_expose_size_as_the_only_parameter_face():
    assert list(PromptParameters.model_fields) == ["size"]
    assert PromptParameters().size == "1024x1024"
    assert PromptParameters(size="1024x1536").size == "1024x1536"
    with pytest.raises(ValidationError):
        PromptParameters(size="1024x1024", seed=7)


def test_prompt_artifact_has_exactly_the_frozen_fields():
    assert list(PromptArtifact.model_fields) == [
        "schema_version",
        "prompt_artifact_id",
        "session_id",
        "based_on_intent_revision_id",
        "based_on_confirmation_id",
        "target_model",
        "prompt",
        "parameters",
        "source_bindings",
        "realization_refs",
        "created_at",
        "knowledge_bundle_refs",
    ]


def test_prompt_artifact_round_trips_and_keeps_traceable_ids():
    artifact = PromptArtifact(
        prompt_artifact_id="pra_1",
        session_id="ses_1",
        based_on_intent_revision_id="irev_1",
        based_on_confirmation_id="cnf_1",
        target_model="qwen-image-3.0",
        prompt="subject: a cat",
        parameters=PromptParameters(size="1024x1536"),
        source_bindings=[_binding()],
        realization_refs=["rlz_1"],
    )
    restored = PromptArtifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact
    assert restored.parameters.size == "1024x1536"
    assert restored.source_bindings[0].intent_path == "subject.description"
    assert restored.realization_refs == ["rlz_1"]


def test_prompt_artifact_knowledge_bundle_refs_default_to_empty_list():
    artifact = _artifact()
    assert artifact.knowledge_bundle_refs == []
    assert artifact.model_config["extra"] == "forbid"
    with pytest.raises(ValidationError):
        _artifact(negative_prompt="low quality")


def test_prompt_artifact_knowledge_bundle_refs_are_deduped_and_order_stable():
    refs = ["kbu_b", "kbu_a", "kbu_b", "kbu_c", "kbu_a"]
    artifact = _artifact(knowledge_bundle_refs=list(refs))
    assert artifact.knowledge_bundle_refs == ["kbu_b", "kbu_a", "kbu_c"]
    restored = PromptArtifact.model_validate_json(artifact.model_dump_json())
    assert restored == artifact
    assert restored.knowledge_bundle_refs == ["kbu_b", "kbu_a", "kbu_c"]


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_prompt_artifact_knowledge_bundle_refs_reject_blank_entries(bad):
    with pytest.raises(ValidationError):
        _artifact(knowledge_bundle_refs=[bad])
    with pytest.raises(ValidationError):
        _artifact(knowledge_bundle_refs=["kbu_x", bad])


def test_prompt_artifact_knowledge_bundle_refs_defaults_are_not_shared():
    first = _artifact(prompt_artifact_id="pra_1", prompt="subject: a cat")
    second = _artifact(prompt_artifact_id="pra_2", prompt="subject: a dog")
    first.knowledge_bundle_refs.append("kbu_x")
    assert first.knowledge_bundle_refs == ["kbu_x"]
    assert second.knowledge_bundle_refs == []


def test_legacy_prompt_artifact_payload_without_knowledge_bundle_refs_is_readable():
    legacy_json = (
        '{"schema_version": "v1", "prompt_artifact_id": "pra_1", "session_id": "ses_1",'
        ' "based_on_intent_revision_id": "irev_1",'
        ' "based_on_confirmation_id": "cnf_1",'
        ' "target_model": "qwen-image-3.0", "prompt": "subject: a cat",'
        ' "parameters": {"size": "1024x1024"}, "source_bindings": [],'
        ' "realization_refs": ["rlz_1"], "created_at": "2026-01-01T00:00:00+00:00"}'
    )
    artifact = PromptArtifact.model_validate_json(legacy_json)
    assert artifact.knowledge_bundle_refs == []
    assert artifact.realization_refs == ["rlz_1"]
    assert PromptArtifact.model_validate_json(artifact.model_dump_json()) == artifact


def test_prompt_artifact_created_at_is_tz_aware_utc_and_serialized_with_offset():
    artifact = PromptArtifact(
        prompt_artifact_id="pra_1",
        session_id="ses_1",
        based_on_intent_revision_id="irev_1",
        based_on_confirmation_id="cnf_1",
        target_model="qwen-image-3.0",
        prompt="subject: a cat",
    )
    assert artifact.created_at.tzinfo is not None
    assert artifact.model_dump_json().count("+00:00") == 1
    with pytest.raises(ValidationError):
        PromptArtifact(
            prompt_artifact_id="pra_1",
            session_id="ses_1",
            based_on_intent_revision_id="irev_1",
            based_on_confirmation_id="cnf_1",
            target_model="qwen-image-3.0",
            prompt="subject: a cat",
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )


def test_prompt_artifact_normalizes_non_utc_timestamps_to_utc():
    tokyo = timezone(timedelta(hours=9))
    artifact = PromptArtifact(
        prompt_artifact_id="pra_1",
        session_id="ses_1",
        based_on_intent_revision_id="irev_1",
        based_on_confirmation_id="cnf_1",
        target_model="qwen-image-3.0",
        prompt="subject: a cat",
        created_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=tokyo),
    )
    assert artifact.created_at == datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)


def test_prompt_artifact_is_frozen_and_rejects_unknown_fields():
    artifact = PromptArtifact(
        prompt_artifact_id="pra_1",
        session_id="ses_1",
        based_on_intent_revision_id="irev_1",
        based_on_confirmation_id="cnf_1",
        target_model="qwen-image-3.0",
        prompt="subject: a cat",
    )
    assert artifact.model_config["frozen"] is True
    with pytest.raises(ValidationError):
        PromptArtifact(
            prompt_artifact_id="pra_1",
            session_id="ses_1",
            based_on_intent_revision_id="irev_1",
            based_on_confirmation_id="cnf_1",
            target_model="qwen-image-3.0",
            prompt="subject: a cat",
            negative_prompt="low quality",
        )


def test_prompt_compile_request_carries_only_session_and_confirmation():
    assert list(PromptCompileRequest.model_fields) == ["session_id", "confirmation_id"]
    request = PromptCompileRequest(session_id="ses_1", confirmation_id="cnf_1")
    assert request.session_id == "ses_1" and request.confirmation_id == "cnf_1"
    with pytest.raises(ValidationError):
        PromptCompileRequest(session_id="ses_1", confirmation_id="cnf_1", prompt="subject: cat")


def test_prompt_error_codes_are_exactly_the_four_frozen_values():
    assert PROMPT_ERROR_CODES == {
        PROMPT_NO_VALID_CONFIRMATION,
        PROMPT_UNAUTHORIZED_ADDITION,
        PROMPT_MISSING_SOURCE_BINDING,
        PROMPT_UNSUPPORTED_REQUIREMENT,
    }
    assert PROMPT_NO_VALID_CONFIRMATION == "prompt.no_valid_confirmation"
    assert PROMPT_UNAUTHORIZED_ADDITION == "prompt.unauthorized_addition"
    assert PROMPT_MISSING_SOURCE_BINDING == "prompt.missing_source_binding"
    assert PROMPT_UNSUPPORTED_REQUIREMENT == "prompt.unsupported_requirement"


def test_prompt_compilation_error_requires_a_known_code():
    error = PromptCompilationError(PROMPT_NO_VALID_CONFIRMATION, "no confirmation")
    assert error.code == PROMPT_NO_VALID_CONFIRMATION
    assert PROMPT_NO_VALID_CONFIRMATION in str(error)
    with pytest.raises(ValueError):
        PromptCompilationError("prompt.invented_code", "not frozen")
    assert isinstance(error, Exception)


def test_prompt_artifact_defaults_are_empty_lists_not_shared():
    first = PromptArtifact(
        prompt_artifact_id="pra_1",
        session_id="ses_1",
        based_on_intent_revision_id="irev_1",
        based_on_confirmation_id="cnf_1",
        target_model="qwen-image-3.0",
        prompt="subject: a cat",
    )
    second = PromptArtifact(
        prompt_artifact_id="pra_2",
        session_id="ses_1",
        based_on_intent_revision_id="irev_1",
        based_on_confirmation_id="cnf_1",
        target_model="qwen-image-3.0",
        prompt="subject: a dog",
    )
    first.source_bindings.append(_binding())  # frozen 只阻止赋值，列表内容仍可改
    assert second.source_bindings == []
    assert first.created_at <= utc_now()
