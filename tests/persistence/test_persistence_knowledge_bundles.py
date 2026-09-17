"""knowledge_bundles 存储测试（v0.4 Step 03 持久化范围）。

覆盖：append/get/list 信封往返、refs 精确三键、重复 ID、跨会话/缺失引用、
非空校验、会话隔离与顺序、append-only（无 UPDATE 路径、旧快照不被改写）。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from persistence_helpers import seed_context

from visual_intent_agent.domain import new_id
from visual_intent_agent.knowledge import (
    ELIGIBILITY_SNAPSHOT_VERSION,
    AdoptionOutcome,
    KnowledgeAdoptionDecision,
    KnowledgeBundle,
    KnowledgeEligibilitySnapshot,
    KnowledgeQuery,
    KnowledgeRecommendation,
    KnowledgeSource,
    KnowledgeUnitHit,
    PathRetrievalResult,
    RetrievalOutcome,
)
from visual_intent_agent.persistence import RepositoryError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "visual_intent_agent" / "persistence"

_CONTENT = "持久化夹具正文"


def _source() -> KnowledgeSource:
    return KnowledgeSource(
        source_type="project_original",
        title="persistence fixture",
        repository_path="units.jsonl",
        locator="fixture",
        source_revision="v0.4-fixture",
        original_declaration="fixture-only original declaration (not production evidence)",
    )


def _ok_bundle_with_decision() -> KnowledgeBundle:
    """构造一个含推荐 + 消费端 adopted 裁定的合法 Bundle（纯内存夹具）。"""
    content_hash = hashlib.sha256(_CONTENT.encode("utf-8")).hexdigest()
    hit = KnowledgeUnitHit(
        knowledge_id="lighting.character.fixture",
        version="1",
        content_hash=content_hash,
        applicable_path="lighting.character",
        candidate_value="soft",
        source=_source(),
        content=_CONTENT,
        score=0.5,
        rank=1,
        matched_tokens=("cat",),
        eligibility_snapshot=KnowledgeEligibilitySnapshot(
            snapshot_version=ELIGIBILITY_SNAPSHOT_VERSION,
            target_models=("any",),
            review_status="approved",
            reviewer="fixture-reviewer",
            reviewed_at="2026-09-16T00:00:00+00:00",
        ),
    )
    recommendation = KnowledgeRecommendation(
        path="lighting.character",
        knowledge_id=hit.knowledge_id,
        version=hit.version,
        content_hash=hit.content_hash,
        candidate_value=hit.candidate_value,
        score=hit.score,
        reason_code="unique_top_candidate",
        reason="fixture",
    )
    return KnowledgeBundle(
        bundle_id=new_id("kbu"),
        session_id="ses_0001",
        intent_revision_id="irev_0001",
        execution_revision_id="erev_0001",
        confirmation_id="cnf_0001",
        target_model="qwen-image-3.0",
        status="ok",
        schema_version="knowledge.v1",
        corpus_version="v0.4-fixture",
        tokenizer_version="keyword.v1",
        retrieval_version="lexical.v1",
        corpus_fingerprint="a" * 64,
        queries=(
            KnowledgeQuery(
                path="lighting.character",
                target_model="qwen-image-3.0",
                text="a cat",
                tokens=("a", "cat"),
            ),
        ),
        path_results=(
            PathRetrievalResult(
                path="lighting.character",
                outcome=RetrievalOutcome.ADOPTED,
                reason_code="unique_top_candidate",
                reason="fixture",
                hits=(hit,),
            ),
        ),
        recommendations=(recommendation,),
        adoption_decisions=(
            KnowledgeAdoptionDecision(
                path="lighting.character",
                knowledge_id=hit.knowledge_id,
                version=hit.version,
                candidate_value=hit.candidate_value,
                outcome=AdoptionOutcome.ADOPTED,
                reason="fixture",
            ),
        ),
    )


def bundle_refs(suffix: str = "") -> dict[str, str]:
    return {
        "intent_revision_id": f"irev_0001{suffix}",
        "execution_revision_id": f"erev_0001{suffix}",
        "confirmation_id": f"cnf_0001{suffix}",
    }


def test_append_get_round_trip_keeps_exact_refs_and_payload(repo) -> None:
    seed_context(repo)
    refs = bundle_refs()
    payload = '{"status": "ok", "recommendations": []}'

    repo.append_knowledge_bundle("kbu_0001", "ses_0001", refs, payload)

    bundle = repo.get_knowledge_bundle("kbu_0001")
    assert bundle.artifact_id == "kbu_0001"
    assert bundle.session_id == "ses_0001"
    assert bundle.refs == refs
    assert set(bundle.refs) == {
        "intent_revision_id",
        "execution_revision_id",
        "confirmation_id",
    }
    assert bundle.payload == payload
    assert bundle.created_at.tzinfo is not None


def test_list_knowledge_bundles_is_ordered_and_session_scoped(repo) -> None:
    seed_context(repo, "ses_0001")
    seed_context(repo, "ses_0002", suffix="_b")
    assert repo.list_knowledge_bundles("ses_0001") == []

    repo.append_knowledge_bundle("kbu_0001", "ses_0001", bundle_refs(), '{"n": 1}')
    repo.append_knowledge_bundle("kbu_0002", "ses_0001", bundle_refs(), '{"n": 2}')
    repo.append_knowledge_bundle("kbu_0003", "ses_0002", bundle_refs("_b"), '{"n": 3}')

    first_session = repo.list_knowledge_bundles("ses_0001")
    assert [bundle.artifact_id for bundle in first_session] == ["kbu_0001", "kbu_0002"]
    assert [bundle.payload for bundle in first_session] == ['{"n": 1}', '{"n": 2}']
    assert [bundle.artifact_id for bundle in repo.list_knowledge_bundles("ses_0002")] == ["kbu_0003"]

    repo.create_session("ses_empty")
    assert repo.list_knowledge_bundles("ses_empty") == []


def test_duplicate_bundle_id_is_rejected_without_extra_rows(repo) -> None:
    seed_context(repo)
    repo.append_knowledge_bundle("kbu_0001", "ses_0001", bundle_refs(), "{}")

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_0001", "ses_0001", bundle_refs(), '{"other": true}')

    assert excinfo.value.code == "persistence.duplicate_id"
    assert repo.get_knowledge_bundle("kbu_0001").payload == "{}"
    assert len(repo.list_knowledge_bundles("ses_0001")) == 1


@pytest.mark.parametrize(
    "ref_key",
    ["intent_revision_id", "execution_revision_id", "confirmation_id"],
)
def test_missing_referenced_row_is_rejected(repo, ref_key: str) -> None:
    seed_context(repo)
    refs = bundle_refs()
    refs[ref_key] = "missing_row"

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_missing_ref", "ses_0001", refs, "{}")

    assert excinfo.value.code == "persistence.foreign_key_violation"
    with pytest.raises(RepositoryError):
        repo.get_knowledge_bundle("kbu_missing_ref")


@pytest.mark.parametrize(
    "ref_key",
    ["intent_revision_id", "execution_revision_id", "confirmation_id"],
)
def test_cross_session_reference_is_rejected(repo, ref_key: str) -> None:
    seed_context(repo, "ses_0001")
    seed_context(repo, "ses_0002", suffix="_b")
    refs = bundle_refs()
    refs[ref_key] = f"{refs[ref_key]}_b"

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_cross", "ses_0001", refs, "{}")

    assert excinfo.value.code == "persistence.ref_session_mismatch"


def test_refs_must_be_exactly_the_three_frozen_keys(repo) -> None:
    seed_context(repo)

    missing = bundle_refs()
    del missing["confirmation_id"]
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_missing_key", "ses_0001", missing, "{}")
    assert excinfo.value.code == "persistence.invalid_refs"

    unknown = bundle_refs()
    unknown["bundle_id"] = "kbu_0001"
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_unknown_key", "ses_0001", unknown, "{}")
    assert excinfo.value.code == "persistence.invalid_refs"


def test_empty_or_non_string_ref_value_is_rejected(repo) -> None:
    seed_context(repo)
    refs = bundle_refs()
    refs["execution_revision_id"] = ""

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_bad_ref", "ses_0001", refs, "{}")
    assert excinfo.value.code == "persistence.invalid_field"


def test_non_string_payload_is_rejected(repo) -> None:
    seed_context(repo)
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_bad_payload", "ses_0001", bundle_refs(), {"a": 1})
    assert excinfo.value.code == "persistence.invalid_field"


def test_missing_session_and_missing_bundle_are_rejected(repo) -> None:
    seed_context(repo)
    with pytest.raises(RepositoryError) as excinfo:
        repo.append_knowledge_bundle("kbu_0001", "ses_missing", bundle_refs(), "{}")
    assert excinfo.value.code == "persistence.session_not_found"

    with pytest.raises(RepositoryError) as excinfo:
        repo.get_knowledge_bundle("kbu_missing")
    assert excinfo.value.code == "persistence.artifact_not_found"

    with pytest.raises(RepositoryError) as excinfo:
        repo.list_knowledge_bundles("ses_missing")
    assert excinfo.value.code == "persistence.session_not_found"


def test_knowledge_bundles_have_no_update_path_and_snapshots_are_immutable(repo) -> None:
    source = (PACKAGE_DIR / "repository.py").read_text(encoding="utf-8")
    updated_tables = set(re.findall(r"UPDATE\s+([a-z_]+)", source, flags=re.IGNORECASE))
    assert "knowledge_bundles" not in updated_tables

    seed_context(repo)
    repo.append_knowledge_bundle("kbu_0001", "ses_0001", bundle_refs(), '{"n": 1}')
    before = repo.get_knowledge_bundle("kbu_0001")
    repo.append_knowledge_bundle("kbu_0002", "ses_0001", bundle_refs(), '{"n": 2}')

    after = repo.get_knowledge_bundle("kbu_0001")
    assert after == before
    assert after.payload == '{"n": 1}'
    assert len(repo.list_knowledge_bundles("ses_0001")) == 2


# ---------------------------------------------------------------------------
# v0.4 F2：消费端裁定随 payload 持久化；旧 payload 缺字段仍可读
# ---------------------------------------------------------------------------


def test_adoption_decision_round_trips_through_the_envelope(repo) -> None:
    seed_context(repo)
    bundle = _ok_bundle_with_decision()
    repo.append_knowledge_bundle(bundle.bundle_id, "ses_0001", bundle_refs(), bundle.model_dump_json())

    stored = repo.get_knowledge_bundle(bundle.bundle_id)
    restored = KnowledgeBundle.model_validate_json(stored.payload)

    assert restored == bundle
    decision = restored.decision_for("lighting.character")
    assert decision is not None
    assert decision.outcome is AdoptionOutcome.ADOPTED
    assert decision.reason_code is None
    # 检索器推荐与编译器裁定可区分地共存。
    assert restored.recommendation_for("lighting.character") is not None


def test_legacy_payload_without_adoption_decisions_is_still_readable(repo) -> None:
    seed_context(repo)
    bundle = _ok_bundle_with_decision()
    legacy_payload = json.loads(bundle.model_dump_json())
    del legacy_payload["adoption_decisions"]
    repo.append_knowledge_bundle(
        bundle.bundle_id, "ses_0001", bundle_refs(), json.dumps(legacy_payload)
    )

    restored = KnowledgeBundle.model_validate_json(
        repo.get_knowledge_bundle(bundle.bundle_id).payload
    )
    assert restored.adoption_decisions == ()
    assert restored.decision_for("lighting.character") is None
    assert restored.recommendation_for("lighting.character") is not None