"""Step 01 合同测试：Schema 严格性、路径/候选白名单、来源完整性、审核字段。

对应任务书验收：Schema 与候选校验、来源完整性、伪造审核字段拒绝。
"""

from __future__ import annotations

import pytest
from knowledge_helpers import approved_payload, content_hash, source_payload, unit_payload
from pydantic import ValidationError

import visual_intent_agent.prompt_engine.engine as engine_module
from visual_intent_agent.domain import INTENT_PATHS
from visual_intent_agent.knowledge import (
    GENERIC_TARGET_MODEL,
    KNOWLEDGE_PATHS,
    MAX_CONDITION_VALUES,
    MAX_CONDITIONS_PER_UNIT,
    MAX_CONTENT_CHARS,
    MAX_ERROR_MESSAGE_CHARS,
    MAX_METADATA_CHARS,
    MAX_TERM_CHARS,
    ConditionOperator,
    KnowledgeCondition,
    KnowledgeError,
    KnowledgeManifest,
    KnowledgeSource,
    KnowledgeUnit,
    ReviewStatus,
    SourceType,
    candidate_values_for,
    compute_content_hash,
    delegated_candidates,
)


def make_unit(**overrides):
    return KnowledgeUnit.model_validate(unit_payload(**overrides))


# ---------------------------------------------------------------------------
# 基础严格性
# ---------------------------------------------------------------------------


def test_a_well_formed_draft_unit_validates():
    unit = make_unit()
    assert unit.review_status is ReviewStatus.DRAFT
    assert unit.reviewer is None and unit.reviewed_at is None
    assert unit.content_hash == compute_content_hash(unit.content)


def test_extra_fields_are_forbidden():
    payload = unit_payload()
    payload["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        KnowledgeUnit.model_validate(payload)


def test_models_are_frozen():
    unit = make_unit()
    with pytest.raises(ValidationError):
        unit.candidate_value = "dramatic"  # type: ignore[misc]


def test_content_hash_must_match_content():
    payload = unit_payload()
    payload["content_hash"] = content_hash("another content")
    with pytest.raises(ValidationError, match="content_hash"):
        KnowledgeUnit.model_validate(payload)


def test_content_must_be_non_empty():
    payload = unit_payload(content="   ")
    with pytest.raises(ValidationError, match="content"):
        KnowledgeUnit.model_validate(payload)


def test_content_length_is_bounded():
    ok = make_unit(content="字" * MAX_CONTENT_CHARS)
    assert len(ok.content) == MAX_CONTENT_CHARS
    payload = unit_payload(content="字" * (MAX_CONTENT_CHARS + 1))
    with pytest.raises(ValidationError, match="at most"):
        KnowledgeUnit.model_validate(payload)


def test_knowledge_id_and_version_shapes():
    with pytest.raises(ValidationError):
        make_unit(knowledge_id="Upper Case")
    with pytest.raises(ValidationError):
        make_unit(version="has space")


# ---------------------------------------------------------------------------
# 路径与候选白名单
# ---------------------------------------------------------------------------


def test_knowledge_paths_are_exactly_the_frozen_three():
    assert KNOWLEDGE_PATHS == frozenset(
        {"lighting.character", "composition.framing", "camera.depth_of_field"}
    )
    assert KNOWLEDGE_PATHS <= INTENT_PATHS


@pytest.mark.parametrize(
    "path",
    ["color.palette", "style.primary", "subject.description", "camera.angle", "not.a.path"],
)
def test_paths_outside_the_whitelist_are_rejected(path):
    with pytest.raises(ValidationError, match="whitelist"):
        make_unit(applicable_path=path)


@pytest.mark.parametrize(
    "path,value",
    [
        ("lighting.character", "harsh"),
        ("composition.framing", "extreme_wide"),
        ("camera.depth_of_field", "medium"),
    ],
)
def test_unauthorized_candidates_are_rejected(path, value):
    with pytest.raises(ValidationError, match="authorized candidate"):
        make_unit(applicable_path=path, candidate_value=value)


def test_all_authorized_candidates_are_accepted():
    for path, values in delegated_candidates().items():
        if path not in KNOWLEDGE_PATHS:
            continue
        for value in values:
            unit = make_unit(applicable_path=path, candidate_value=value)
            assert unit.candidate_value == value


def test_candidate_table_is_reused_from_prompt_engine(monkeypatch):
    """证明候选校验读的就是 prompt_engine 的映射，而不是本地第二份表。"""
    assert delegated_candidates() is engine_module.DELEGATED_CANDIDATES
    monkeypatch.setattr(
        engine_module, "DELEGATED_CANDIDATES", {"lighting.character": ("only_option",)}
    )
    assert candidate_values_for("lighting.character") == ("only_option",)
    accepted = make_unit(candidate_value="only_option")
    assert accepted.candidate_value == "only_option"
    with pytest.raises(ValidationError, match="authorized candidate"):
        make_unit(candidate_value="soft")


# ---------------------------------------------------------------------------
# 来源完整性
# ---------------------------------------------------------------------------


def test_source_is_required():
    payload = unit_payload()
    payload.pop("source")
    with pytest.raises(ValidationError):
        KnowledgeUnit.model_validate(payload)


def test_source_requires_an_entry_point():
    with pytest.raises(ValidationError, match="url or a repository_path"):
        KnowledgeSource.model_validate(source_payload(url=None, repository_path=None))


def test_source_requires_license_or_original_declaration():
    with pytest.raises(ValidationError, match="license or an original-work declaration"):
        KnowledgeSource.model_validate(source_payload(license=None, original_declaration=None))


def test_project_original_source_requires_a_declaration():
    with pytest.raises(ValidationError, match="original_declaration"):
        KnowledgeSource.model_validate(
            source_payload(source_type="project_original", original_declaration=None)
        )


def test_official_source_may_use_a_url_and_a_license():
    source = KnowledgeSource.model_validate(
        source_payload(
            source_type="official_documentation",
            url="https://example.invalid/docs",
            repository_path=None,
            license="Apache-2.0",
            original_declaration=None,
        )
    )
    assert source.source_type is SourceType.OFFICIAL_DOCUMENTATION
    assert source.url == "https://example.invalid/docs"


@pytest.mark.parametrize("bad_url", ["file:///etc/passwd", "not-a-url", "ftp://x/y"])
def test_source_url_must_be_absolute_http(bad_url):
    with pytest.raises(ValidationError, match="http"):
        KnowledgeSource.model_validate(source_payload(url=bad_url, repository_path=None))


@pytest.mark.parametrize("bad_path", ["../../etc/passwd", "/etc/passwd", "a\\b.jsonl", ".."])
def test_source_repository_path_must_stay_relative(bad_path):
    with pytest.raises(ValidationError, match="repository-relative"):
        KnowledgeSource.model_validate(source_payload(url=None, repository_path=bad_path))


def test_source_title_and_locator_must_be_non_blank():
    with pytest.raises(ValidationError, match="title"):
        KnowledgeSource.model_validate(source_payload(title="  "))
    with pytest.raises(ValidationError, match="locator"):
        KnowledgeSource.model_validate(source_payload(locator=""))


def test_source_must_record_a_revision_or_a_date():
    with pytest.raises(ValidationError, match="source_revision / source_date"):
        KnowledgeSource.model_validate(source_payload(source_revision=None, source_date=None))


def test_source_date_alone_is_acceptable():
    source = KnowledgeSource.model_validate(
        source_payload(source_revision=None, source_date="2026-09-16")
    )
    assert source.source_revision is None
    assert source.source_date is not None


def test_terms_are_length_bounded():
    with pytest.raises(ValidationError, match=f"at most {MAX_TERM_CHARS}"):
        make_unit(keywords=("长" * (MAX_TERM_CHARS + 1),))
    with pytest.raises(ValidationError, match=f"at most {MAX_TERM_CHARS}"):
        make_unit(aliases=("x" * (MAX_TERM_CHARS + 1),))


def test_metadata_is_length_bounded():
    with pytest.raises(ValidationError, match=f"at most {MAX_METADATA_CHARS}"):
        KnowledgeSource.model_validate(source_payload(title="t" * (MAX_METADATA_CHARS + 1)))


def test_control_characters_are_rejected_in_terms_and_metadata():
    with pytest.raises(ValidationError, match="control"):
        make_unit(keywords=("bad\x00term",))
    with pytest.raises(ValidationError, match="control"):
        KnowledgeSource.model_validate(source_payload(title="bad\x01title"))
    with pytest.raises(ValidationError, match="control"):
        KnowledgeCondition.model_validate(
            {"path": "environment.mode", "operator": "equals", "values": ["a\x00b"]}
        )
    with pytest.raises(ValidationError, match="control"):
        make_unit(knowledge_id="lighting.character.bad\x07")


def test_content_rejects_nul_but_allows_line_breaks():
    unit = make_unit(content="line1\nline2\tend")
    assert "\n" in unit.content
    with pytest.raises(ValidationError, match="control"):
        make_unit(content="bad\x00content")


def test_condition_value_and_condition_counts_are_bounded():
    too_many_values = {
        "path": "environment.mode",
        "operator": "in",
        "values": [f"v{i}" for i in range(MAX_CONDITION_VALUES + 1)],
    }
    with pytest.raises(ValidationError, match=f"at most {MAX_CONDITION_VALUES}"):
        KnowledgeCondition.model_validate(too_many_values)
    too_many_conditions = [
        {"path": "environment.mode", "operator": "equals", "values": ["studio"]}
        for _ in range(MAX_CONDITIONS_PER_UNIT + 1)
    ]
    with pytest.raises(ValidationError, match=f"at most {MAX_CONDITIONS_PER_UNIT}"):
        make_unit(conditions=too_many_conditions)


def test_empty_conditions_are_allowed_and_multiple_conditions_preserved():
    assert make_unit(conditions=[]).conditions == ()
    unit = make_unit(
        conditions=[
            {"path": "environment.mode", "operator": "equals", "values": ["studio"]},
            {"path": "composition.framing", "operator": "in", "values": ["close_up", "medium_shot"]},
        ]
    )
    assert [condition.path for condition in unit.conditions] == [
        "environment.mode",
        "composition.framing",
    ]


def test_content_hash_binds_content_only():
    """content_hash 只覆盖正文；授权字段由清单文件 sha256 与语料版本绑定。"""
    unit = make_unit(candidate_value="soft")
    assert unit.content_hash == compute_content_hash(unit.content)


def test_knowledge_error_message_is_truncated():
    error = KnowledgeError("knowledge.test", "x" * 5000)
    assert len(str(error)) <= MAX_ERROR_MESSAGE_CHARS + len("[knowledge.test] ")
    assert str(error).endswith("...")


# ---------------------------------------------------------------------------
# conditions：封闭算子，无表达式
# ---------------------------------------------------------------------------


def test_condition_paths_must_be_confirmed_intent_paths():
    with pytest.raises(ValidationError, match="intent path"):
        KnowledgeCondition.model_validate(
            {"path": "workflow.state", "operator": "equals", "values": ["x"]}
        )


def test_condition_operator_is_a_closed_enum():
    with pytest.raises(ValidationError):
        KnowledgeCondition.model_validate(
            {"path": "environment.mode", "operator": "regex", "values": ["out.*"]}
        )


def test_equals_requires_exactly_one_value():
    with pytest.raises(ValidationError, match="exactly one"):
        KnowledgeCondition.model_validate(
            {"path": "environment.mode", "operator": "equals", "values": ["a", "b"]}
        )


def test_in_requires_a_non_empty_value_set():
    with pytest.raises(ValidationError, match="at least one"):
        KnowledgeCondition.model_validate(
            {"path": "environment.mode", "operator": "in", "values": []}
        )


def test_regex_like_value_is_plain_data_not_a_pattern():
    """值里出现正则元字符也只是字面量；模型没有正则算子。"""
    unit = make_unit(
        conditions=[{"path": "environment.mode", "operator": "equals", "values": [".*"]}]
    )
    assert unit.conditions[0].operator is ConditionOperator.EQUALS
    assert unit.conditions[0].values == (".*",)


# ---------------------------------------------------------------------------
# target_models / keywords
# ---------------------------------------------------------------------------


def test_target_models_reject_wildcards():
    with pytest.raises(ValidationError, match="wildcard"):
        make_unit(target_models=("qwen-image-*",))


def test_target_models_accept_exact_ids_and_generic_marker():
    exact = make_unit(target_models=("qwen-image-3.0",))
    assert exact.target_models == ("qwen-image-3.0",)
    generic = make_unit(target_models=(GENERIC_TARGET_MODEL,))
    assert generic.target_models == (GENERIC_TARGET_MODEL,)


def test_keywords_must_be_non_empty_and_unique():
    with pytest.raises(ValidationError, match="at least one"):
        make_unit(keywords=())
    with pytest.raises(ValidationError, match="duplicate"):
        make_unit(keywords=("Light", "light"))


# ---------------------------------------------------------------------------
# 审核字段：不可伪造
# ---------------------------------------------------------------------------


def test_approved_requires_both_review_fields():
    with pytest.raises(ValidationError, match="reviewer and reviewed_at"):
        make_unit(review_status="approved", reviewer=None, reviewed_at=None)
    with pytest.raises(ValidationError, match="reviewer and reviewed_at"):
        make_unit(
            review_status="approved",
            reviewer="someone",
            reviewed_at=None,
        )


def test_approved_rejects_naive_reviewed_at():
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_unit(
            review_status="approved",
            reviewer="someone",
            reviewed_at="2026-09-16T00:00:00",
        )


def test_draft_and_rejected_must_not_carry_review_fields():
    with pytest.raises(ValidationError, match="forged review fields"):
        make_unit(reviewer="someone")
    with pytest.raises(ValidationError, match="forged review fields"):
        make_unit(review_status="rejected", reviewed_at="2026-09-16T00:00:00+00:00")


def test_approved_test_fixture_is_self_consistent():
    unit = KnowledgeUnit.model_validate(approved_payload())
    assert unit.review_status is ReviewStatus.APPROVED
    assert unit.reviewer is not None and unit.reviewed_at is not None


# ---------------------------------------------------------------------------
# 清单合同
# ---------------------------------------------------------------------------


def test_manifest_requires_at_least_one_jsonl_file():
    with pytest.raises(ValidationError, match="at least one JSONL"):
        KnowledgeManifest.model_validate(
            {
                "schema_version": "knowledge.v1",
                "corpus_version": "v1",
                "tokenizer_version": "keyword.v1",
                "retrieval_version": "lexical.v1",
                "files": [],
            }
        )


def test_manifest_file_entries_must_be_jsonl_with_valid_hash():
    base = {
        "schema_version": "knowledge.v1",
        "corpus_version": "v1",
        "tokenizer_version": "keyword.v1",
        "retrieval_version": "lexical.v1",
    }
    with pytest.raises(ValidationError, match=".jsonl"):
        KnowledgeManifest.model_validate(
            {**base, "files": [{"path": "units.json", "sha256": "0" * 64, "unit_count": 0}]}
        )
    with pytest.raises(ValidationError, match="sha256"):
        KnowledgeManifest.model_validate(
            {**base, "files": [{"path": "units.jsonl", "sha256": "NOTHEX", "unit_count": 0}]}
        )
    with pytest.raises(ValidationError, match="corpus-relative"):
        KnowledgeManifest.model_validate(
            {**base, "files": [{"path": "../units.jsonl", "sha256": "0" * 64, "unit_count": 0}]}
        )