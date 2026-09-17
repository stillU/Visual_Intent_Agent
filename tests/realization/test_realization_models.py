"""Step 07 Realization 模型测试：冻结字段面、默认值、状态语义、append-only 历史。

`realization/` 是多 Step 拥有的包（Step 07 建 models.py，Step 09 加 carry.py），
因此 `visual_intent_agent.realization.__init__` **保持空白**（ARCHITECTURE.md 第 1 节），
测试与下游一律从 `visual_intent_agent.realization.models` 导入。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import visual_intent_agent.realization as realization_package
from visual_intent_agent.domain import (
    ExecutionRevision,
    IntentRevision,
    VisualIntent,
    new_id,
    utc_now,
)
from visual_intent_agent.persistence import SQLiteRepository
from visual_intent_agent.realization.models import (
    REALIZATION_CARRY_PRESERVE_UNTIL_INVALIDATED,
    REALIZATION_SOURCE_USER_DELEGATED,
    REALIZATION_STATUS_ACTIVE,
    REALIZATION_STATUS_INVALIDATED,
    RealizationState,
    RealizationValue,
)


def _value(**overrides) -> RealizationValue:
    fields = {
        "path": "lighting.character",
        "value": "soft",
        "source": "user_delegated",
        "first_prompt_artifact_id": "pra_1",
    }
    fields.update(overrides)
    return RealizationValue(**fields)


def _state(values=None, **overrides) -> RealizationState:
    fields = {
        "realization_id": "rlz_1",
        "session_id": "ses_1",
        "based_on_intent_revision_id": "irev_1",
        "values": list(values) if values is not None else [_value()],
    }
    fields.update(overrides)
    return RealizationState(**fields)


def test_realization_value_has_exactly_the_eleven_frozen_fields():
    assert list(RealizationValue.model_fields) == [
        "path",
        "value",
        "source",
        "first_prompt_artifact_id",
        "carry_policy",
        "status",
        "invalidated_reason",
        "invalidated_at",
        "knowledge_bundle_id",
        "knowledge_unit_id",
        "knowledge_unit_version",
    ]


def test_realization_value_defaults_are_frozen_values():
    value = _value()
    assert value.source == REALIZATION_SOURCE_USER_DELEGATED == "user_delegated"
    assert value.carry_policy == REALIZATION_CARRY_PRESERVE_UNTIL_INVALIDATED
    assert value.carry_policy == "preserve_until_invalidated"
    assert value.status == REALIZATION_STATUS_ACTIVE == "active"
    assert value.invalidated_reason is None
    assert value.invalidated_at is None
    assert value.knowledge_bundle_id is None
    assert value.knowledge_unit_id is None
    assert value.knowledge_unit_version is None


def test_realization_value_is_frozen_and_rejects_unknown_fields():
    value = _value()
    assert value.model_config["frozen"] is True
    with pytest.raises(ValidationError):
        RealizationValue(
            path="lighting.character",
            value="soft",
            source="user_delegated",
            first_prompt_artifact_id="pra_1",
            seed=7,
        )


@pytest.mark.parametrize("source", ["model_choice", "system_default", "user_specified", ""])
def test_only_user_delegated_is_a_legal_source(source):
    with pytest.raises(ValidationError):
        _value(source=source)


def test_realization_value_knowledge_provenance_is_optional_and_accepts_all_three():
    value = _value(
        knowledge_bundle_id="kbu_0123456789abcdef0123456789abcdef",
        knowledge_unit_id="lighting-soft-v1",
        knowledge_unit_version="1.0.0",
    )
    assert value.knowledge_bundle_id == "kbu_0123456789abcdef0123456789abcdef"
    assert value.knowledge_unit_id == "lighting-soft-v1"
    assert value.knowledge_unit_version == "1.0.0"
    # 知识追溯不改变授权来源。
    assert value.source == REALIZATION_SOURCE_USER_DELEGATED


@pytest.mark.parametrize(
    "override",
    [
        {"knowledge_bundle_id": "kbu_x"},
        {"knowledge_unit_id": "unit"},
        {"knowledge_unit_version": "1"},
        {"knowledge_bundle_id": "kbu_x", "knowledge_unit_id": "unit"},
        {"knowledge_bundle_id": "kbu_x", "knowledge_unit_version": "1"},
        {"knowledge_unit_id": "unit", "knowledge_unit_version": "1"},
    ],
)
def test_realization_value_rejects_partial_knowledge_provenance(override):
    with pytest.raises(ValidationError):
        _value(**override)


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_realization_value_rejects_blank_knowledge_provenance(blank):
    with pytest.raises(ValidationError):
        _value(
            knowledge_bundle_id=blank,
            knowledge_unit_id="unit",
            knowledge_unit_version="1",
        )
    with pytest.raises(ValidationError):
        _value(knowledge_bundle_id=blank)


def test_knowledge_provenance_is_not_a_new_authorization_source():
    with pytest.raises(ValidationError):
        _value(
            source="knowledge",
            knowledge_bundle_id="kbu_x",
            knowledge_unit_id="unit",
            knowledge_unit_version="1",
        )


def test_knowledge_provenance_survives_model_copy_and_json_round_trip():
    value = _value(
        knowledge_bundle_id="kbu_x",
        knowledge_unit_id="lighting-soft",
        knowledge_unit_version="1.0.0",
    )
    copied = value.model_copy()
    assert copied == value
    assert copied.knowledge_bundle_id == "kbu_x"
    assert copied.knowledge_unit_id == "lighting-soft"
    assert copied.knowledge_unit_version == "1.0.0"

    invalidated = value.model_copy(
        update={
            "status": "invalidated",
            "invalidated_reason": "user override",
            "invalidated_at": utc_now(),
        }
    )
    assert invalidated.knowledge_bundle_id == "kbu_x"
    assert invalidated.knowledge_unit_id == "lighting-soft"
    assert invalidated.knowledge_unit_version == "1.0.0"

    restored = RealizationValue.model_validate_json(value.model_dump_json())
    assert restored == value
    assert restored.knowledge_unit_id == "lighting-soft"


def test_legacy_realization_value_payload_without_knowledge_fields_is_readable():
    legacy_json = (
        '{"path": "lighting.character", "value": "soft", "source": "user_delegated",'
        ' "first_prompt_artifact_id": "pra_1"}'
    )
    value = RealizationValue.model_validate_json(legacy_json)
    assert value.knowledge_bundle_id is None
    assert value.knowledge_unit_id is None
    assert value.knowledge_unit_version is None
    assert value.carry_policy == REALIZATION_CARRY_PRESERVE_UNTIL_INVALIDATED
    assert value.status == REALIZATION_STATUS_ACTIVE

    full_legacy_json = (
        '{"path": "lighting.character", "value": "soft", "source": "user_delegated",'
        ' "first_prompt_artifact_id": "pra_1",'
        ' "carry_policy": "preserve_until_invalidated", "status": "active",'
        ' "invalidated_reason": null, "invalidated_at": null}'
    )
    full = RealizationValue.model_validate_json(full_legacy_json)
    assert full.knowledge_bundle_id is None
    assert full.knowledge_unit_id is None
    assert full.knowledge_unit_version is None
    assert full.model_dump()["knowledge_unit_version"] is None


def test_legacy_realization_state_payload_without_knowledge_fields_is_readable():
    legacy_json = (
        '{"schema_version": "v1", "realization_id": "rlz_1", "session_id": "ses_1",'
        ' "based_on_intent_revision_id": "irev_1", "values": ['
        '{"path": "lighting.character", "value": "soft", "source": "user_delegated",'
        ' "first_prompt_artifact_id": "pra_1",'
        ' "carry_policy": "preserve_until_invalidated", "status": "active",'
        ' "invalidated_reason": null, "invalidated_at": null}],'
        ' "created_at": "2026-01-01T00:00:00+00:00"}'
    )
    state = RealizationState.model_validate_json(legacy_json)
    assert state.values[0].knowledge_bundle_id is None
    assert state.values[0].knowledge_unit_version is None
    assert RealizationState.model_validate_json(state.model_dump_json()) == state


@pytest.mark.parametrize("status", ["pending", "expired", "ACTIVE", ""])
def test_only_active_or_invalidated_are_legal_statuses(status):
    with pytest.raises(ValidationError):
        _value(status=status)


def test_only_preserve_until_invalidated_is_a_legal_carry_policy():
    with pytest.raises(ValidationError):
        _value(carry_policy="always_carry")


def test_an_invalidated_value_carries_a_reason_and_a_utc_timestamp():
    value = _value(
        status="invalidated",
        invalidated_reason="user set lighting.character explicitly",
        invalidated_at=utc_now(),
    )
    assert value.status == REALIZATION_STATUS_INVALIDATED
    assert value.invalidated_reason is not None
    assert value.invalidated_at is not None
    assert "+00:00" in value.model_dump_json()


def test_invalidated_at_rejects_naive_datetimes_and_normalizes_offsets():
    with pytest.raises(ValidationError):
        _value(status="invalidated", invalidated_at=datetime(2026, 1, 1, 12, 0, 0))
    tokyo = timezone(timedelta(hours=9))
    value = _value(
        status="invalidated", invalidated_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=tokyo)
    )
    assert value.invalidated_at == datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)


def test_realization_state_has_exactly_the_six_frozen_fields():
    assert list(RealizationState.model_fields) == [
        "schema_version",
        "realization_id",
        "session_id",
        "based_on_intent_revision_id",
        "values",
        "created_at",
    ]


def test_realization_state_round_trips_and_defaults_to_empty_values():
    state = _state(values=[])
    assert state.values == []
    assert state.schema_version == "v1"
    restored = RealizationState.model_validate_json(state.model_dump_json())
    assert restored == state

    full = _state()
    assert RealizationState.model_validate_json(full.model_dump_json()) == full


def test_realization_state_rejects_naive_created_at():
    with pytest.raises(ValidationError):
        _state(created_at=datetime(2026, 1, 1, 12, 0, 0))


def test_active_helpers_ignore_invalidated_values():
    active = _value()
    invalidated = _value(
        path="color.palette",
        value="warm",
        status="invalidated",
        invalidated_reason="replaced",
        invalidated_at=utc_now(),
    )
    state = _state(values=[active, invalidated])

    assert state.active_values() == [active]
    assert state.active_value_for("lighting.character") == active
    assert state.active_value_for("color.palette") is None
    assert state.active_value_for("unknown.path") is None


def test_active_value_for_takes_the_first_active_duplicate_deterministically():
    first = _value(value="soft")
    second = _value(value="dramatic")
    state = _state(values=[first, second])
    assert state.active_value_for("lighting.character") == first


def test_realization_states_are_append_only_and_history_is_not_overwritten(tmp_path):
    repo = SQLiteRepository(tmp_path / "realization.db")
    session_id = new_id("ses")
    repo.create_session(session_id)
    repo.append_execution_revision(
        ExecutionRevision(
            execution_revision_id=new_id("erev"),
            session_id=session_id,
            parent_revision_id=None,
            target_model="qwen-image-3.0",
        )
    )
    revision = IntentRevision(
        intent_revision_id=new_id("irev"),
        session_id=session_id,
        parent_revision_id=None,
        intent=VisualIntent(),
    )
    repo.append_intent_revision(revision)

    first = _state(
        values=[_value()],
        realization_id=new_id("rlz"),
        session_id=session_id,
        based_on_intent_revision_id=revision.intent_revision_id,
    )
    repo.append_realization_state(
        first.realization_id,
        session_id,
        {"based_on_intent_revision_id": revision.intent_revision_id},
        first.model_dump_json(),
    )
    second = _state(
        values=[
            _value(
                status="invalidated",
                invalidated_reason="user overrode lighting",
                invalidated_at=utc_now(),
            ),
            _value(path="color.palette", value="warm"),
        ],
        realization_id=new_id("rlz"),
        session_id=session_id,
        based_on_intent_revision_id=revision.intent_revision_id,
    )
    repo.append_realization_state(
        second.realization_id,
        session_id,
        {"based_on_intent_revision_id": revision.intent_revision_id},
        second.model_dump_json(),
    )

    current = repo.get_current_realization_state(session_id)
    assert RealizationState.model_validate_json(current.payload) == second
    assert current.artifact_id == second.realization_id
    count = repo.connection.execute(
        "SELECT COUNT(*) FROM realization_states WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    assert count == 2  # 新 state 不覆盖旧 state
    repo.close()


def test_realization_package_init_stays_blank():
    init_file = realization_package.__file__
    assert init_file is not None
    source = open(init_file, encoding="utf-8").read()
    assert source.strip() == ""
    assert not hasattr(realization_package, "RealizationState")
    assert not hasattr(realization_package, "RealizationValue")
