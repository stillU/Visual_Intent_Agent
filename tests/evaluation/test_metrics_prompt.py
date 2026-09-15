"""MVP v0.3 Step 03：L3 Prompt Semantics 指标的离线单元测试。

四指标逐项覆盖 命中 / 未命中 / not_applicable / missing_data 分支；A/B 同口径
（同一函数处理两侧输入）。全部纯函数、全离线。
"""

from __future__ import annotations

import pytest

from evaluation.metrics.prompt import (
    PromptRequestFace,
    PromptSequenceTurn,
    evaluate_intent_coverage,
    evaluate_model_compatibility,
    evaluate_preservation,
    evaluate_unauthorized_addition,
    keyword_hit,
)

GROUPS = [["橘", "orange"], ["猫", "cat"], ["中景", "medium shot"]]


class TestKeywordHit:
    def test_case_insensitive_substring(self) -> None:
        assert keyword_hit("一只橘猫", "橘") is True
        assert keyword_hit("an Orange Cat", "orange") is True
        assert keyword_hit("黑狗", "猫") is False


class TestIntentCoverage:
    def test_all_groups_hit(self) -> None:
        payload = evaluate_intent_coverage(GROUPS, "一只橘猫，中景构图")
        assert payload.status == "ok"
        assert payload.score == 1.0
        assert payload.counts == {"groups_total": 3, "groups_hit": 3}

    def test_partial_hit(self) -> None:
        payload = evaluate_intent_coverage(GROUPS, "一只猫")
        assert payload.score == pytest.approx(1 / 3)
        assert payload.details["missed_groups"] == [["橘", "orange"], ["中景", "medium shot"]]

    def test_no_prompt_is_missing_data(self) -> None:
        payload = evaluate_intent_coverage(GROUPS, None)
        assert payload.status == "missing_data"
        assert payload.score is None
        assert payload.counts["groups_total"] == 3

    def test_empty_groups_yield_none_score(self) -> None:
        payload = evaluate_intent_coverage([], "任意 prompt")
        assert payload.status == "ok"
        assert payload.score is None


class TestUnauthorizedAddition:
    def test_no_hits(self) -> None:
        payload = evaluate_unauthorized_addition(["狗", "dog"], "一只橘猫")
        assert payload.status == "ok"
        assert payload.counts["hits"] == 0
        assert payload.counts["guard_bypassed"] == 0

    def test_hits_counted(self) -> None:
        payload = evaluate_unauthorized_addition(["狗", "dog"], "一只狗和一只 Dog")
        assert payload.counts["hits"] == 2
        assert payload.details["hit_keywords"] == ["狗", "dog"]
        # B 侧防护零触发且文本命中 → 绕过证据。
        assert payload.counts["guard_bypassed"] == 1

    def test_guard_triggered_prevents_bypass_flag(self) -> None:
        payload = evaluate_unauthorized_addition(["狗"], "一只狗", guard_triggered=1)
        assert payload.counts["hits"] == 1
        assert payload.counts["guard_triggered"] == 1
        assert payload.counts["guard_bypassed"] == 0

    def test_no_prompt_is_missing_data_but_keeps_guard_count(self) -> None:
        payload = evaluate_unauthorized_addition(["狗"], None, guard_triggered=2)
        assert payload.status == "missing_data"
        assert payload.counts["guard_triggered"] == 2


class TestPreservation:
    def test_single_turn_not_applicable(self) -> None:
        sequence = [PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫")]
        payload = evaluate_preservation(GROUPS, sequence)
        assert payload.status == "not_applicable"

    def test_multi_turn_full_preservation(self) -> None:
        sequence = [
            PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫，中景"),
            PromptSequenceTurn(turn_id="t2", prompt_text="一只橘猫，中景，暖光",
                               is_modification_turn=True),
        ]
        payload = evaluate_preservation(GROUPS, sequence)
        assert payload.status == "ok"
        assert payload.score == 1.0
        assert payload.counts["pairs_scored"] == 1
        assert payload.counts["groups_lost"] == 0

    def test_preservation_loss(self) -> None:
        sequence = [
            PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫，中景"),
            PromptSequenceTurn(turn_id="t2", prompt_text="一只橘猫，特写",
                               is_modification_turn=True),
        ]
        payload = evaluate_preservation(GROUPS, sequence)
        # t1 命中 橘/猫/中景 三组；t2 命中 橘/猫 两组 → 2/3
        assert payload.score == pytest.approx(2 / 3)
        assert payload.counts["groups_kept"] == 2
        assert payload.counts["groups_lost"] == 1

    def test_modification_turn_missing_prompt_counts_zero(self) -> None:
        sequence = [
            PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫，中景"),
            PromptSequenceTurn(turn_id="t2", prompt_text=None, is_modification_turn=True),
        ]
        payload = evaluate_preservation(GROUPS, sequence)
        assert payload.status == "ok"
        assert payload.score == 0.0
        assert payload.details["pair_details"][0]["current_prompt_missing"] is True

    def test_non_modification_turns_do_not_form_pairs(self) -> None:
        sequence = [
            PromptSequenceTurn(turn_id="t1", prompt_text="一只橘猫"),
            PromptSequenceTurn(turn_id="t2", prompt_text="一只橘猫，中景"),
        ]
        payload = evaluate_preservation(GROUPS, sequence)
        assert payload.status == "not_applicable"

    def test_pairs_without_previous_hits_are_skipped(self) -> None:
        sequence = [
            PromptSequenceTurn(turn_id="t1", prompt_text="完全没有关键词"),
            PromptSequenceTurn(turn_id="t2", prompt_text="一只橘猫",
                               is_modification_turn=True),
        ]
        payload = evaluate_preservation(GROUPS, sequence)
        assert payload.status == "not_applicable"
        assert payload.counts["pairs_skipped_no_prev_hits"] == 1


class TestModelCompatibility:
    def test_all_compatible(self) -> None:
        requests = [
            PromptRequestFace(turn_id="t1", size="1024x1024", model="qwen-image-3.0"),
            PromptRequestFace(turn_id="t2", size="1024x1024", model="qwen-image-3.0"),
        ]
        payload = evaluate_model_compatibility(
            requests, expected_size="1024x1024", expected_model="qwen-image-3.0"
        )
        assert payload.score == 1.0
        assert payload.counts == {"requests": 2, "compatible": 2, "incompatible": 0}

    def test_bad_size_format(self) -> None:
        requests = [PromptRequestFace(turn_id="t1", size="1024", model="qwen-image-3.0")]
        payload = evaluate_model_compatibility(
            requests, expected_size="1024x1024", expected_model="qwen-image-3.0"
        )
        assert payload.score == 0.0
        assert "size_format_invalid" in payload.details["violations"][0]["problems"]

    def test_size_not_frozen_value(self) -> None:
        requests = [PromptRequestFace(turn_id="t1", size="512x512", model="qwen-image-3.0")]
        payload = evaluate_model_compatibility(
            requests, expected_size="1024x1024", expected_model="qwen-image-3.0"
        )
        assert payload.score == 0.0
        assert "size_not_frozen_value" in payload.details["violations"][0]["problems"]

    def test_model_mismatch(self) -> None:
        requests = [PromptRequestFace(turn_id="t1", size="1024x1024", model="other-model")]
        payload = evaluate_model_compatibility(
            requests, expected_size="1024x1024", expected_model="qwen-image-3.0"
        )
        assert payload.score == 0.0
        assert "target_model_mismatch" in payload.details["violations"][0]["problems"]

    def test_extra_parameters(self) -> None:
        requests = [
            PromptRequestFace(
                turn_id="t1", size="1024x1024", model="qwen-image-3.0",
                extra_parameters_free=False,
            )
        ]
        payload = evaluate_model_compatibility(
            requests, expected_size="1024x1024", expected_model="qwen-image-3.0"
        )
        assert payload.score == 0.0

    def test_no_requests_is_missing_data(self) -> None:
        payload = evaluate_model_compatibility(
            [], expected_size="1024x1024", expected_model="qwen-image-3.0"
        )
        assert payload.status == "missing_data"
        assert payload.score is None
