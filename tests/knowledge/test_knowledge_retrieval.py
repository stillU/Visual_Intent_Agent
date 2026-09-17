"""v0.4 Step 02 正式测试：确定性本地检索（`keyword.v1` / `lexical.v1`）。

覆盖任务书 `02_retrieval_bundle.md` 与 ADR-005 第 5/6/9 节的验收点：

- 规范化：NFKC、英文小写、`close-up`/`close_up`、中文二元组与单字、混合边界；
- QueryBuilder：只接受 `user_delegated` + 无值 + 未 PIN 的知识路径、空查询；
- 条件：多条件 AND、空条件恒满足、缺字段不满足、`subject.count` 严格十进制、
  其余路径逐字精确比较；
- 过滤：approved-only、目标模型（精确或 `any`）、路径、conditions；
- 评分：`|q∩u| / |q|`、零分不命中、每路径 Top-3、行序无关的稳定排序；
- 并列：最高分同 candidate 采用、不同 candidate `ambiguous` 回退（含第 4 个并列）；
- 至少两个不同确认场景给出不同推荐；
- 语料指纹与版本可回溯、引擎缓存不热更新；
- `KnowledgeError` 转诊断 Bundle，`TypeError`/`AssertionError` 绝不吞掉；
- `KnowledgeEngine` 是 `runtime_checkable` Protocol，真实实现与 Fake 同一合同；
- 生产语料 0 approved → 只回退；测试夹具里的 approved **不是**生产审核证据。

本文件自包含：只复用 `knowledge_helpers`（不修改它），全部离线、无网络、无 Provider。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from knowledge_helpers import (
    approved_payload,
    build_intent,
    build_request,
    load_test_corpus,
    unit_payload,
    write_corpus,
)
from pydantic import ValidationError

from visual_intent_agent.knowledge import (
    ELIGIBILITY_SNAPSHOT_VERSION,
    KNOWLEDGE_HASH_MISMATCH,
    KNOWLEDGE_MANIFEST_MISSING,
    BundleStatus,
    KnowledgeBundle,
    KnowledgeCondition,
    KnowledgeEngine,
    KnowledgeManifest,
    KnowledgeUnit,
    LocalKnowledgeEngine,
    QueryBuilder,
    RetrievalOutcome,
    ReviewStatus,
    build_query_text,
    compute_corpus_fingerprint,
    evaluate_conditions,
    load_corpus,
    normalize_text,
    read_confirmed_intent_value,
    score_tokens,
    tokenize,
    unit_retrieval_tokens,
)
from visual_intent_agent.knowledge import retrieval as retrieval_module

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_CORPUS_DIR = PROJECT_ROOT / "knowledge_base" / "v0.4"

# 测试请求的目标模型：`qwen-image-3.0` 是 PromptEngine 已知模型之一。
TARGET_MODEL = "qwen-image-3.0"


# ---------------------------------------------------------------------------
# 内部小工具（全部使用测试夹具 approved，非生产审核证据）
# ---------------------------------------------------------------------------


def _corpus(tmp_path: Path, units: list[dict], name: str = "corpus"):
    return load_test_corpus(tmp_path / name, units)


def _engine(corpus) -> LocalKnowledgeEngine:
    return LocalKnowledgeEngine(corpus=corpus)


def _request(description: str, *, path: str = "lighting.character", model: str = TARGET_MODEL,
             suffix: str = "1"):
    intent = build_intent({"subject.description": description}, delegated=(path,))
    return build_request(intent, target_model=model, pending_paths=(path,), suffix=suffix)


def _retrieve_lighting(corpus, description: str, **kwargs):
    return _engine(corpus).retrieve(_request(description, **kwargs))


def _condition(path: str, operator: str, values: tuple[str, ...]) -> KnowledgeCondition:
    return KnowledgeCondition.model_validate(
        {"path": path, "operator": operator, "values": list(values)}
    )


def _reasons(result) -> set[tuple[str, str]]:
    return {(item.knowledge_id, item.reason_code.value) for item in result.rejections}


# ---------------------------------------------------------------------------
# 1. 规范化：NFKC / 英文 / close-up 与 close_up / 中文二元组与单字 / 混合边界
# ---------------------------------------------------------------------------


def test_normalize_text_applies_nfkc_and_rejects_non_strings():
    assert normalize_text("ＳＯＦＴ") == "SOFT"  # 全角 → 半角
    assert normalize_text("①") == "1"  # 兼容字符折叠
    assert normalize_text("ﬁ") == "fi"  # 连字展开
    assert normalize_text("柔光") == "柔光"
    with pytest.raises(TypeError):
        normalize_text(123)  # type: ignore[arg-type]


def test_tokenize_english_lowercases_and_keeps_alphanumeric_words():
    assert tokenize("Soft LIGHT 3") == ("soft", "light", "3")
    assert tokenize("SOFT") == tokenize("soft") == ("soft",)
    assert tokenize("top3") == ("top3",)  # 字母数字同词
    assert tokenize("soft soft SOFT") == ("soft",)  # 去重保序


def test_tokenize_maps_close_up_and_close_hyphen_to_the_same_tokens():
    # 连字符与下划线都是分隔符，因此任务书的 close-up / close_up 归一到同一 token 集。
    assert tokenize("close-up") == tokenize("close_up") == ("close", "up")


def test_tokenize_chinese_bigrams_and_single_char():
    assert tokenize("柔光") == ("柔光",)
    assert tokenize("柔和的光") == ("柔和", "和的", "的光")
    assert tokenize("光") == ("光",)  # 单字输入保留
    assert tokenize("柔光柔光") == ("柔光", "光柔")  # 二元组去重
    # 单字嵌在连续中文串里不作为独立 token（这是 bigram 规则的明确边界）。
    assert "光" not in tokenize("柔光")


def test_tokenize_mixed_cjk_latin_boundary():
    assert tokenize("soft柔光") == ("soft", "柔光")
    assert tokenize("柔光soft") == ("柔光", "soft")
    assert tokenize("close-up 柔光") == ("close", "up", "柔光")


def test_unit_retrieval_tokens_reads_content_keywords_and_aliases():
    unit = KnowledgeUnit.model_validate(
        approved_payload(
            knowledge_id="lighting.character.tokens",
            content="柔光主体",
            keywords=("fill",),
            aliases=("别名",),
        )
    )
    tokens = unit_retrieval_tokens(unit)
    assert "柔光" in tokens and "光主" in tokens and "主体" in tokens
    assert "fill" in tokens
    assert "别名" in tokens


# ---------------------------------------------------------------------------
# 2. 评分公式与零分
# ---------------------------------------------------------------------------


def test_score_tokens_formula_empty_query_and_set_semantics():
    assert score_tokens(("a", "b"), ("a", "b", "c")) == 1.0
    assert score_tokens(("a", "b"), ("a",)) == 0.5
    assert score_tokens(("a", "b", "c", "d"), ("a",)) == 0.25
    assert score_tokens((), ("a",)) == 0.0  # 空查询得 0
    assert score_tokens(("a",), ()) == 0.0
    assert score_tokens(("a", "a", "b"), ("a", "b")) == 1.0  # 分子分母都按集合


# ---------------------------------------------------------------------------
# 3. QueryBuilder：user_delegated 无值未 PIN、空查询、拒绝非法输入
# ---------------------------------------------------------------------------


def test_query_builder_accepts_delegated_empty_unpinned_path():
    intent = build_intent({}, delegated=("lighting.character",))
    queries = QueryBuilder().build(
        intent=intent, target_model=TARGET_MODEL, pending_paths=("lighting.character",)
    )
    assert len(queries) == 1
    assert queries[0].path == "lighting.character"
    assert queries[0].target_model == TARGET_MODEL
    # 已确认上下文为空 → 查询为空，下游必须明确无命中而不是猜测。
    assert queries[0].text == "" and queries[0].tokens == ()
    assert build_query_text(intent) == ""


def test_query_builder_rejects_value_carrying_and_pinned_pending_paths():
    with_value = build_intent({"lighting.character": "soft"}, delegated=("lighting.character",))
    with pytest.raises(ValueError):
        QueryBuilder().build(
            intent=with_value, target_model=TARGET_MODEL, pending_paths=("lighting.character",)
        )
    pinned = build_intent({}, delegated=("lighting.character",), pinned=("lighting.character",))
    with pytest.raises(ValueError):
        QueryBuilder().build(
            intent=pinned, target_model=TARGET_MODEL, pending_paths=("lighting.character",)
        )


def test_query_builder_rejects_non_delegated_and_non_whitelisted_paths():
    not_delegated = build_intent({})
    with pytest.raises(ValueError):
        QueryBuilder().build(
            intent=not_delegated,
            target_model=TARGET_MODEL,
            pending_paths=("lighting.character",),
        )
    delegated = build_intent({}, delegated=("lighting.character",))
    with pytest.raises(ValueError):
        QueryBuilder().build(
            intent=delegated, target_model=TARGET_MODEL, pending_paths=("not.a.path",)
        )
    with pytest.raises(TypeError):
        QueryBuilder().build(
            intent=delegated, target_model=TARGET_MODEL, pending_paths="lighting.character"
        )
    with pytest.raises(ValueError):
        QueryBuilder().build(intent=delegated, target_model="   ", pending_paths=())


def test_query_builder_builds_one_query_per_known_path_and_ignores_non_knowledge_paths():
    delegated = ("lighting.character", "composition.framing", "camera.depth_of_field")
    intent = build_intent({}, delegated=delegated)
    queries = QueryBuilder().build(
        intent=intent, target_model=TARGET_MODEL, pending_paths=delegated + delegated
    )
    assert [query.path for query in queries] == sorted(delegated)  # 去重 + 字典序
    # 委托了非知识路径时本版不检索（知识库只覆盖三条路径），绝不越界猜测。
    other = build_intent({}, delegated=("subject.description",))
    assert (
        QueryBuilder().build(
            intent=other, target_model=TARGET_MODEL, pending_paths=("subject.description",)
        )
        == ()
    )


def test_engine_empty_query_is_an_explicit_empty_query_outcome(tmp_path):
    corpus = _corpus(
        tmp_path, [approved_payload(knowledge_id="lighting.character.unit", content="柔光")]
    )
    bundle = _engine(corpus).retrieve(_request(""))
    result = bundle.result_for("lighting.character")
    assert result is not None
    assert result.outcome is RetrievalOutcome.EMPTY_QUERY
    assert result.hits == () and bundle.recommendations == ()
    assert bundle.status is BundleStatus.OK and bundle.is_fallback is True


# ---------------------------------------------------------------------------
# 4. 条件：AND / 空 / 缺失 / subject.count 严格十进制 / 字符串精确
# ---------------------------------------------------------------------------


def test_conditions_empty_and_and_missing_path_semantics():
    intent = build_intent({"environment.mode": "studio", "camera.angle": "low_angle"})
    assert evaluate_conditions((), intent) is True  # 空条件恒满足
    both = [
        _condition("environment.mode", "equals", ("studio",)),
        _condition("camera.angle", "equals", ("low_angle",)),
    ]
    assert evaluate_conditions(both, intent) is True
    # 多条件 AND：任一不满足即整体不满足。
    assert evaluate_conditions(both + [_condition("style.primary", "in", ("cinematic",))], intent) is False
    # 缺字段（无值且无解析记录）→ 不满足，missing ≠ user_delegated。
    assert evaluate_conditions([_condition("style.primary", "equals", ("cinematic",))], intent) is False


def test_condition_subject_count_is_strict_decimal():
    intent = build_intent({"subject.count": 2})
    assert evaluate_conditions([_condition("subject.count", "equals", ("2",))], intent) is True
    assert evaluate_conditions([_condition("subject.count", "in", ("1", "2"))], intent) is True
    # 前导零 / 正号 / 小数 / 其他值都不得宽松通过（这些字面量能通过合同构造）。
    for bad in ("02", "+2", "2.0", "3"):
        assert evaluate_conditions([_condition("subject.count", "equals", (bad,))], intent) is False
    # 空串在合同层就被拒绝（Step 01 `KnowledgeCondition.values` 合同）。
    with pytest.raises(ValidationError):
        _condition("subject.count", "equals", ("",))
    # 前导/尾随空白由 Step 01 合同在构造时 strip；这不是检索层的宽松匹配。
    assert _condition("subject.count", "equals", (" 2 ",)).values == ("2",)
    # 枚举里混入一个非严格十进制值 → 整条条件不满足，不做部分匹配。
    assert evaluate_conditions([_condition("subject.count", "in", ("1", "02"))], intent) is False
    # 缺失 subject.count → 不满足。
    assert evaluate_conditions([_condition("subject.count", "equals", ("2",))], build_intent({})) is False


def test_condition_string_paths_are_exact_with_no_folding_or_substring():
    intent = build_intent({"environment.mode": "studio"})
    assert evaluate_conditions([_condition("environment.mode", "equals", ("studio",))], intent) is True
    assert evaluate_conditions([_condition("environment.mode", "in", ("outdoor", "studio"))], intent) is True
    for bad in ("Studio", "STUDIO", "studi"):
        assert evaluate_conditions([_condition("environment.mode", "equals", (bad,))], intent) is False
    # Step 01 合同在构造时 strip 首尾空白；`studio ` 因此就是字面量 `studio`。
    assert _condition("environment.mode", "equals", (" studio ",)).values == ("studio",)


def test_read_confirmed_intent_value_is_whitelist_only():
    intent = build_intent({"lighting.character": "soft"})
    assert read_confirmed_intent_value(intent, "lighting.character") == "soft"
    assert read_confirmed_intent_value(intent, "composition.framing") is None
    with pytest.raises(ValueError):
        read_confirmed_intent_value(intent, "not.a.path")


# ---------------------------------------------------------------------------
# 5. 过滤：approved / model / path / conditions（先过滤后排序）
# ---------------------------------------------------------------------------


def test_retrieve_consumes_approved_units_only(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.approved", content="柔光", candidate_value="soft"
            ),
            unit_payload(
                knowledge_id="lighting.character.draft",
                content="柔光",
                candidate_value="dramatic",
                review_status="draft",
            ),
        ],
    )
    bundle = _retrieve_lighting(corpus, "柔光")
    result = bundle.result_for("lighting.character")
    assert result.outcome is RetrievalOutcome.ADOPTED
    assert [hit.knowledge_id for hit in result.hits] == ["lighting.character.approved"]
    assert result.rejections == ()  # draft 根本不进入候选，也不出现在拒绝里
    assert bundle.recommendation_for("lighting.character").candidate_value == "soft"


def test_retrieve_filters_exact_model_and_generic_any(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.exact",
                content="柔光",
                candidate_value="soft",
                target_models=("qwen-image-3.0",),
            ),
            approved_payload(
                knowledge_id="lighting.character.generic",
                content="硬光",
                candidate_value="dramatic",
                target_models=("any",),
            ),
            approved_payload(
                knowledge_id="lighting.character.other",
                content="自然光",
                candidate_value="natural",
                target_models=("other-model",),
            ),
        ],
    )
    engine = _engine(corpus)
    result = engine.retrieve(_request("柔光")).result_for("lighting.character")
    assert [hit.knowledge_id for hit in result.hits] == ["lighting.character.exact"]
    assert _reasons(result) == {
        ("lighting.character.generic", "zero_score"),
        ("lighting.character.other", "model_mismatch"),
    }
    # `any` 对任意目标模型都生效（证明不是固定模型表）。
    other = engine.retrieve(_request("硬光", model="brand-new-model")).result_for("lighting.character")
    assert [hit.knowledge_id for hit in other.hits] == ["lighting.character.generic"]
    assert other.hits[0].candidate_value == "dramatic"


def test_retrieve_filters_conditions_with_and_missing_field(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.all",
                content="柔光",
                candidate_value="soft",
                conditions=[
                    {"path": "environment.mode", "operator": "in", "values": ["studio"]},
                    {"path": "camera.angle", "operator": "equals", "values": ["low_angle"]},
                ],
            ),
            approved_payload(
                knowledge_id="lighting.character.mode",
                content="柔光",
                candidate_value="dramatic",
                conditions=[
                    {"path": "environment.mode", "operator": "equals", "values": ["studio"]}
                ],
            ),
        ],
    )
    intent = build_intent({"subject.description": "柔光", "environment.mode": "studio"},
                          delegated=("lighting.character",))
    request = build_request(intent, target_model=TARGET_MODEL, pending_paths=("lighting.character",))
    result = _engine(corpus).retrieve(request).result_for("lighting.character")
    # camera.angle 缺失 → 双条件单元不满足；单条件单元命中。
    assert result.outcome is RetrievalOutcome.ADOPTED
    assert [hit.knowledge_id for hit in result.hits] == ["lighting.character.mode"]
    assert _reasons(result) == {("lighting.character.all", "conditions_not_satisfied")}
    # 条件值不匹配时两个单元都不满足 → 明确无命中，不宽松通过。
    mismatch = build_intent(
        {"subject.description": "柔光", "environment.mode": "outdoor"},
        delegated=("lighting.character",),
    )
    no_hit = _engine(corpus).retrieve(
        build_request(mismatch, target_model=TARGET_MODEL, pending_paths=("lighting.character",))
    ).result_for("lighting.character")
    assert no_hit.outcome is RetrievalOutcome.NO_HIT
    assert {reason for _, reason in _reasons(no_hit)} == {"conditions_not_satisfied"}


def test_retrieve_path_filter_keeps_cross_path_units_out(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.unit", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="composition.framing.unit",
                applicable_path="composition.framing",
                content="柔光",
                candidate_value="close_up",
            ),
        ],
    )
    intent = build_intent(
        {"subject.description": "柔光"}, delegated=("lighting.character", "composition.framing")
    )
    request = build_request(
        intent,
        target_model=TARGET_MODEL,
        pending_paths=("lighting.character", "composition.framing"),
    )
    bundle = _engine(corpus).retrieve(request)
    lighting = bundle.result_for("lighting.character")
    framing = bundle.result_for("composition.framing")
    assert [hit.knowledge_id for hit in lighting.hits] == ["lighting.character.unit"]
    assert [hit.knowledge_id for hit in framing.hits] == ["composition.framing.unit"]
    assert all(hit.applicable_path == "lighting.character" for hit in lighting.hits)
    assert all(hit.applicable_path == "composition.framing" for hit in framing.hits)
    assert bundle.recommendation_for("lighting.character").candidate_value == "soft"
    assert bundle.recommendation_for("composition.framing").candidate_value == "close_up"


# ---------------------------------------------------------------------------
# 6. 评分 / 零分 / Top-3 / 稳定排序（行序无关）
# ---------------------------------------------------------------------------


def test_retrieve_scores_are_exact_and_zero_score_never_hits(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.scored",
                content="soft lighting 柔光",
                candidate_value="soft",
            )
        ],
    )
    engine = _engine(corpus)
    full = engine.retrieve(_request("soft lighting")).result_for("lighting.character")
    assert full.hits[0].score == 1.0
    assert full.hits[0].matched_tokens == ("lighting", "soft")
    partial = engine.retrieve(_request("soft unknownword")).result_for("lighting.character")
    assert partial.hits[0].score == 0.5
    zero = engine.retrieve(_request("no-such-token-xyz")).result_for("lighting.character")
    assert zero.outcome is RetrievalOutcome.NO_HIT and zero.hits == ()
    assert _reasons(zero) == {("lighting.character.scored", "zero_score")}


def test_retrieve_keeps_top3_and_rejects_below_top_k(tmp_path):
    contents = {
        "lighting.character.a": "alpha beta gamma delta",
        "lighting.character.b": "alpha beta gamma",
        "lighting.character.c": "alpha beta",
        "lighting.character.d": "alpha",
    }
    units = [
        approved_payload(knowledge_id=key, content=value, candidate_value="soft")
        for key, value in contents.items()
    ]
    result = _retrieve_lighting(_corpus(tmp_path, units), "alpha beta gamma delta").result_for(
        "lighting.character"
    )
    assert [hit.knowledge_id for hit in result.hits] == [
        "lighting.character.a",
        "lighting.character.b",
        "lighting.character.c",
    ]
    assert [hit.rank for hit in result.hits] == [1, 2, 3]
    assert [hit.score for hit in result.hits] == [1.0, 0.75, 0.5]
    assert _reasons(result) == {("lighting.character.d", "below_top_k")}


def test_retrieval_selection_is_line_order_independent(tmp_path):
    content_by_id = {
        "lighting.character.a": "alpha beta gamma delta",
        "lighting.character.b": "alpha beta gamma",
        "lighting.character.c": "alpha beta",
        "lighting.character.d": "alpha",
    }

    def units(order):
        return [
            approved_payload(
                knowledge_id=key, content=content_by_id[key], candidate_value="soft"
            )
            for key in order
        ]

    forward = _retrieve_lighting(
        _corpus(tmp_path, units(sorted(content_by_id)), name="forward"), "alpha beta gamma delta"
    ).result_for("lighting.character")
    reversed_ = _retrieve_lighting(
        _corpus(tmp_path, units(sorted(content_by_id, reverse=True)), name="reversed"),
        "alpha beta gamma delta",
    ).result_for("lighting.character")

    def signature(result):
        return [
            (hit.knowledge_id, hit.version, hit.score, hit.matched_tokens) for hit in result.hits
        ]

    assert signature(forward) == signature(reversed_)


def test_retrieve_stable_sort_breaks_same_score_tie_by_knowledge_id(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.beta", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="lighting.character.alpha", content="柔光", candidate_value="soft"
            ),
        ],
    )
    result = _retrieve_lighting(corpus, "柔光").result_for("lighting.character")
    assert [hit.knowledge_id for hit in result.hits] == [
        "lighting.character.alpha",
        "lighting.character.beta",
    ]  # 同分同 candidate：稳定排序首位被采用
    assert _reasons(result) == {("lighting.character.beta", "tie_not_adopted")}


# ---------------------------------------------------------------------------
# 7. 并列：同 candidate 采用 / 不同 candidate ambiguous（含第 4 个并列）
# ---------------------------------------------------------------------------


def test_top_score_tie_with_same_candidate_is_adopted(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.a", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="lighting.character.b", content="柔光", candidate_value="soft"
            ),
        ],
    )
    bundle = _retrieve_lighting(corpus, "柔光")
    result = bundle.result_for("lighting.character")
    assert result.outcome is RetrievalOutcome.ADOPTED
    assert len(result.hits) == 2  # 保留全部 Top-3 诊断
    assert bundle.recommendation_for("lighting.character").knowledge_id == "lighting.character.a"


def test_top_score_tie_with_different_candidates_is_ambiguous(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.a", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="lighting.character.b", content="柔光", candidate_value="dramatic"
            ),
        ],
    )
    bundle = _retrieve_lighting(corpus, "柔光")
    result = bundle.result_for("lighting.character")
    assert result.outcome is RetrievalOutcome.AMBIGUOUS
    assert bundle.recommendations == ()  # 绝不强行采用
    assert bundle.status is BundleStatus.OK and bundle.is_fallback is True
    assert _reasons(result) == {
        ("lighting.character.a", "tie_ambiguous"),
        ("lighting.character.b", "tie_ambiguous"),
    }


def test_ambiguous_considers_ties_beyond_the_retained_top3(tmp_path):
    # 前 3 个同分同 candidate、第 4 个同分不同 candidate：必须仍判 ambiguous，
    # 不能只看保留的 Top-3 而错误采用。
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.a", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="lighting.character.b", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="lighting.character.c", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="lighting.character.d", content="柔光", candidate_value="dramatic"
            ),
        ],
    )
    result = _retrieve_lighting(corpus, "柔光").result_for("lighting.character")
    assert result.outcome is RetrievalOutcome.AMBIGUOUS
    assert ("lighting.character.d", "tie_ambiguous") in _reasons(result)


# ---------------------------------------------------------------------------
# 8. 至少两个不同确认场景 → 不同合法推荐
# ---------------------------------------------------------------------------


def test_two_confirmed_scenarios_produce_different_recommendations(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.soft", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="lighting.character.dramatic", content="硬光", candidate_value="dramatic"
            ),
        ],
    )
    soft = _retrieve_lighting(corpus, "柔光").recommendation_for("lighting.character")
    dramatic = _retrieve_lighting(corpus, "硬光").recommendation_for("lighting.character")
    assert soft.candidate_value == "soft" and soft.knowledge_id == "lighting.character.soft"
    assert (
        dramatic.candidate_value == "dramatic"
        and dramatic.knowledge_id == "lighting.character.dramatic"
    )
    assert soft.candidate_value != dramatic.candidate_value  # 不是固定返回同一条知识


# ---------------------------------------------------------------------------
# 9. 语料指纹 / 版本记录 / 缓存不热更新
# ---------------------------------------------------------------------------


def test_bundle_records_corpus_versions_fingerprint_and_hit_snapshot(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.unit", content="柔光", candidate_value="soft"
            )
        ],
    )
    bundle = _retrieve_lighting(corpus, "柔光")
    manifest = corpus.manifest
    assert bundle.schema_version == manifest.schema_version
    assert bundle.corpus_version == manifest.corpus_version
    assert bundle.tokenizer_version == manifest.tokenizer_version
    assert bundle.retrieval_version == manifest.retrieval_version
    assert bundle.corpus_fingerprint == compute_corpus_fingerprint(manifest)
    hit = bundle.result_for("lighting.character").hits[0]
    unit = corpus.all_units[0]
    assert hit.knowledge_id == unit.knowledge_id
    assert hit.content_hash == unit.content_hash
    assert hit.source == unit.source
    assert hit.content == unit.content
    assert hit.eligibility_snapshot is not None
    assert hit.eligibility_snapshot.review_status is ReviewStatus.APPROVED


def test_hit_snapshot_is_copied_verbatim_from_the_authoritative_unit(tmp_path):
    """F2：快照来自已校验的 KnowledgeUnit，不从 query 猜、不从正文解析。"""
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.snapshot",
                content="柔光",
                candidate_value="soft",
                conditions=[
                    {"path": "environment.mode", "operator": "equals", "values": ["studio"]}
                ],
                target_models=("qwen-image-3.0",),
            )
        ],
    )
    intent = build_intent(
        {"subject.description": "柔光", "environment.mode": "studio"},
        delegated=("lighting.character",),
    )
    request = build_request(
        intent, target_model=TARGET_MODEL, pending_paths=("lighting.character",)
    )
    hit = _engine(corpus).retrieve(request).result_for("lighting.character").hits[0]
    unit = corpus.all_units[0]
    snapshot = hit.eligibility_snapshot
    assert snapshot is not None
    assert snapshot.snapshot_version == ELIGIBILITY_SNAPSHOT_VERSION
    assert snapshot.conditions == unit.conditions
    assert snapshot.target_models == unit.target_models == ("qwen-image-3.0",)
    assert snapshot.review_status is unit.review_status is ReviewStatus.APPROVED
    assert snapshot.reviewer == unit.reviewer
    assert snapshot.reviewed_at == unit.reviewed_at


def test_corpus_fingerprint_is_file_order_stable_and_content_sensitive():
    def manifest(corpus_version: str, sha_first: str) -> KnowledgeManifest:
        return KnowledgeManifest.model_validate(
            {
                "schema_version": "knowledge.v1",
                "corpus_version": corpus_version,
                "tokenizer_version": "keyword.v1",
                "retrieval_version": "lexical.v1",
                "files": [
                    {"path": "a.jsonl", "sha256": sha_first, "unit_count": 1},
                    {"path": "b.jsonl", "sha256": "b" * 64, "unit_count": 2},
                ],
            }
        )

    base = manifest("v0.4-test", "a" * 64)
    reordered = KnowledgeManifest.model_validate(
        {**base.model_dump(), "files": list(reversed(base.model_dump()["files"]))}
    )
    assert compute_corpus_fingerprint(base) == compute_corpus_fingerprint(reordered)
    assert compute_corpus_fingerprint(base) != compute_corpus_fingerprint(
        manifest("v0.4-test", "c" * 64)
    )
    assert compute_corpus_fingerprint(base) != compute_corpus_fingerprint(
        manifest("v0.4-test-2", "a" * 64)
    )


def test_engine_caches_the_corpus_and_does_not_hot_reload(tmp_path):
    directory = tmp_path / "corpus"
    load_test_corpus(
        directory,
        [
            approved_payload(
                knowledge_id="lighting.character.unit", content="柔光", candidate_value="soft"
            )
        ],
    )
    engine = LocalKnowledgeEngine(corpus_dir=directory)
    first = engine.retrieve(_request("柔光"))
    assert first.recommendation_for("lighting.character").candidate_value == "soft"

    # 显式改写权威语料（同 ID，换成另一个受授权候选）：旧引擎不得热更新。
    write_corpus(
        directory,
        [
            approved_payload(
                knowledge_id="lighting.character.unit", content="柔光", candidate_value="dramatic"
            )
        ],
    )
    cached = engine.retrieve(_request("柔光"))
    assert cached.corpus_fingerprint == first.corpus_fingerprint
    assert cached.recommendation_for("lighting.character").candidate_value == "soft"
    # 新引擎（显式重建）才能看到新语料，且指纹可观测地变化。
    rebuilt = LocalKnowledgeEngine(corpus_dir=directory).retrieve(_request("柔光"))
    assert rebuilt.corpus_fingerprint != first.corpus_fingerprint
    assert rebuilt.recommendation_for("lighting.character").candidate_value == "dramatic"


# ---------------------------------------------------------------------------
# 10. 生产语料 0 approved → 只回退；测试 approved 不冒充生产
# ---------------------------------------------------------------------------


def test_production_corpus_has_zero_approved_and_only_falls_back():
    corpus = load_corpus(PRODUCTION_CORPUS_DIR)
    assert corpus.build_units() == ()
    assert {unit.review_status for unit in corpus.all_units} == {ReviewStatus.DRAFT}
    bundle = _engine(corpus).retrieve(_request("柔光"))
    assert bundle.status is BundleStatus.NO_APPROVED_UNITS
    assert bundle.recommendations == () and bundle.path_results == ()
    assert bundle.is_fallback is True
    assert bundle.corpus_version == "v0.4-draft-1"
    assert bundle.corpus_fingerprint is not None


def test_test_approved_fixture_does_not_masquerade_as_production(tmp_path):
    fixture = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.unit", content="柔光", candidate_value="soft"
            )
        ],
    )
    # 夹具 approved 能驱动离线链路，但这只是工程数据……
    assert _engine(fixture).retrieve(_request("柔光")).recommendations
    # ……生产语料仍是 0 approved，同一请求必须回退。
    production = load_corpus(PRODUCTION_CORPUS_DIR)
    assert production.build_units() == ()
    assert _engine(production).retrieve(_request("柔光")).recommendations == ()


# ---------------------------------------------------------------------------
# 11. 错误边界：KnowledgeError → 诊断；TypeError/AssertionError 不吞
# ---------------------------------------------------------------------------


def test_missing_corpus_becomes_an_observable_diagnostic_bundle(tmp_path):
    engine = LocalKnowledgeEngine(corpus_dir=tmp_path / "absent")
    bundle = engine.retrieve(_request("柔光"))
    assert bundle.status is BundleStatus.CORPUS_ERROR
    assert bundle.reason_code == KNOWLEDGE_MANIFEST_MISSING
    assert bundle.corpus_version is None and bundle.corpus_fingerprint is None
    assert bundle.recommendations == () and bundle.is_fallback is True
    assert len(bundle.queries) == 1  # 即使语料不可用也保留查询诊断


def test_tampered_corpus_becomes_an_observable_diagnostic_bundle(tmp_path):
    directory = write_corpus(
        tmp_path / "tampered",
        [approved_payload(knowledge_id="lighting.character.unit", content="柔光")],
        sha256="0" * 64,
    )
    bundle = LocalKnowledgeEngine(corpus_dir=directory).retrieve(_request("柔光"))
    assert bundle.status is BundleStatus.CORPUS_ERROR
    assert bundle.reason_code == KNOWLEDGE_HASH_MISMATCH
    assert bundle.recommendations == ()


def test_non_knowledge_errors_are_not_swallowed(tmp_path, monkeypatch):
    engine = LocalKnowledgeEngine(corpus_dir=tmp_path / "corpus")
    with pytest.raises(TypeError):
        engine.retrieve("not-a-request")  # type: ignore[arg-type]

    def boom(_directory):
        raise AssertionError("programming error must not be swallowed")

    monkeypatch.setattr(retrieval_module, "load_corpus", boom)
    with pytest.raises(AssertionError):
        engine.retrieve(_request("柔光"))

    def boom_type(_directory):
        raise TypeError("programming error must not be swallowed")

    monkeypatch.setattr(retrieval_module, "load_corpus", boom_type)
    with pytest.raises(TypeError):
        engine.retrieve(_request("柔光"))


def test_engine_constructor_requires_exactly_one_corpus_source(tmp_path):
    with pytest.raises(ValueError):
        LocalKnowledgeEngine()
    with pytest.raises(ValueError):
        LocalKnowledgeEngine(corpus=_corpus(tmp_path, [approved_payload()]), corpus_dir=tmp_path)
    with pytest.raises(TypeError):
        LocalKnowledgeEngine(corpus="not-a-corpus")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 12. runtime_checkable KnowledgeEngine Protocol 与 Bundle 身份绑定
# ---------------------------------------------------------------------------


def test_knowledge_engine_protocol_is_runtime_checkable(tmp_path):
    corpus = _corpus(
        tmp_path,
        [approved_payload(knowledge_id="lighting.character.unit", content="柔光")],
    )
    assert getattr(KnowledgeEngine, "_is_runtime_protocol", False) is True
    assert isinstance(LocalKnowledgeEngine(corpus=corpus), KnowledgeEngine)

    class FakeEngine:
        def retrieve(self, request) -> KnowledgeBundle:
            raise NotImplementedError

    class NotAnEngine:
        pass

    assert isinstance(FakeEngine(), KnowledgeEngine)
    assert not isinstance(NotAnEngine(), KnowledgeEngine)


def test_bundle_binds_identity_queries_and_is_append_only(tmp_path):
    corpus = _corpus(
        tmp_path,
        [
            approved_payload(
                knowledge_id="lighting.character.unit", content="柔光", candidate_value="soft"
            ),
            approved_payload(
                knowledge_id="composition.framing.unit",
                applicable_path="composition.framing",
                content="柔光",
                candidate_value="close_up",
            ),
        ],
    )
    intent = build_intent(
        {"subject.description": "柔光"}, delegated=("lighting.character", "composition.framing")
    )
    request = build_request(
        intent,
        target_model=TARGET_MODEL,
        pending_paths=("lighting.character", "composition.framing"),
        suffix="9",
    )
    engine = _engine(corpus)
    bundle = engine.retrieve(request)
    assert bundle.bundle_id.startswith("kbu_")
    assert bundle.session_id == request.session_id
    assert bundle.intent_revision_id == request.intent_revision_id
    assert bundle.execution_revision_id == request.execution_revision_id
    assert bundle.confirmation_id == request.confirmation_id
    assert bundle.target_model == TARGET_MODEL
    assert [query.path for query in bundle.queries] == [
        "composition.framing",
        "lighting.character",
    ]
    assert bundle.created_at.tzinfo is not None
    assert {rec.path for rec in bundle.recommendations} == {
        "lighting.character",
        "composition.framing",
    }
    again = engine.retrieve(request)
    assert again.bundle_id != bundle.bundle_id  # append-only：不复用主键
    assert again.corpus_fingerprint == bundle.corpus_fingerprint