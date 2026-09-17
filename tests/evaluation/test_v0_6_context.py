"""Step 02：隔离上下文装配与固定 revision ID（全离线）。"""

from __future__ import annotations

import inspect

import pytest
from v0_6_helpers import POSITIVE_VALUES, make_case, make_settings

from evaluation.v0_6 import context
from evaluation.v0_6.context import (
    ConfirmedContext,
    build_intent,
    revision_seed_for,
    seed_confirmed_session,
)
from evaluation.v0_6.records import CaseSpec
from visual_intent_agent.persistence import SQLiteRepository
from visual_intent_agent.prompt_engine.engine import select_delegated_value


def _case(case_id: str = "ctx-01") -> CaseSpec:
    return CaseSpec.model_validate(
        make_case(
            case_id,
            layer="L1_applicable",
            category="applicable_adoption",
            primary_path="lighting.character",
            confirmed_intent=dict(POSITIVE_VALUES["lighting.character"]),
            delegated=["lighting.character"],
            rule_under_test="lighting.character.soft_for_tight_framing",
        )
    )


def test_revision_seed_is_pure_single_hash_without_candidate():
    """映射只读 case_id+repetition；不接收 candidate、不循环挑 ID。"""
    parameters = set(inspect.signature(revision_seed_for).parameters)
    assert parameters == {"case_id", "repetition"}
    first = revision_seed_for(case_id="c1", repetition=1)
    assert first == revision_seed_for(case_id="c1", repetition=1)
    assert first != revision_seed_for(case_id="c1", repetition=2)
    assert first != revision_seed_for(case_id="c2", repetition=1)
    assert first.startswith(context.REVISION_SEED_FAMILY + "_")


def test_revision_seed_does_not_avoid_the_candidate(tmp_path):
    """纯哈希可能让回退值恰好等于候选值：那必须是可接受的有效样本，不做规避。"""
    case = _case("ctx-equal")
    revision_id = revision_seed_for(case_id=case.case_id, repetition=1)
    fallback = select_delegated_value("lighting.character", revision_id)
    # 无论是否相等都合法；这里断言映射本身不因候选值而改变。
    assert revision_id == revision_seed_for(case_id=case.case_id, repetition=1)
    assert fallback in {"soft", "dramatic", "natural"}


def test_bc_share_revision_and_each_binds_its_own_confirmation(tmp_path):
    case = _case("ctx-bc")
    contexts: dict[str, ConfirmedContext] = {}
    for arm in ("B", "C"):
        repo = SQLiteRepository(tmp_path / f"{arm}.db")
        try:
            contexts[arm] = seed_confirmed_session(
                repo=repo, case=case, repetition=1, arm=arm, settings=make_settings()
            )
            assert repo.is_confirmation_valid(contexts[arm].confirmation_id) is True
            assert contexts[arm].confirmation_valid is True
        finally:
            repo.close()
    assert contexts["B"].intent_revision_id == contexts["C"].intent_revision_id
    assert contexts["B"].execution_revision_id == contexts["C"].execution_revision_id
    assert contexts["B"].confirmation_id != contexts["C"].confirmation_id
    assert contexts["B"].summary_hash == contexts["C"].summary_hash
    assert contexts["B"].session_id != contexts["C"].session_id


def test_build_intent_marks_delegated_and_legal_pinned_existing_value():
    case = _case("ctx-intent")
    intent = build_intent(case)
    assert intent.lighting.character is None
    assert intent.resolutions["lighting.character"].resolution.value == "user_delegated"
    assert intent.pinned_paths == frozenset()

    # 合法 PIN = 现存显式值 + PIN：值必须存在、pin 生效、且**不**被标为 user_delegated。
    pinned_case = CaseSpec.model_validate(
        make_case(
            "ctx-pin",
            layer="L2_explicit_pin_control",
            category="pinned_control",
            primary_path="lighting.character",
            confirmed_intent={"lighting.character": "natural", "subject.description": "synthetic"},
            delegated=[],
            pinned=["lighting.character"],
            explicit=["lighting.character"],
        )
    )
    pinned_intent = build_intent(pinned_case)
    assert pinned_intent.pinned_paths == frozenset({"lighting.character"})
    assert pinned_intent.lighting.character == "natural"
    assert "lighting.character" not in pinned_intent.resolutions


def test_call_order_is_deterministic_and_covers_both_directions():
    orders = {
        context.call_order_for(case_id=f"ctx-{index}", repetition=1) for index in range(40)
    }
    assert orders == {"B_first", "C_first"}
    assert context.call_order_for(case_id="ctx-1", repetition=1) == context.call_order_for(
        case_id="ctx-1", repetition=1
    )
