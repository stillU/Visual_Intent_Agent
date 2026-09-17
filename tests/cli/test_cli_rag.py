"""v0.4 Step 04：CLI 本地 RAG 开关测试（全离线、Fake、tmp_path）。

覆盖任务书 `04_cli_acceptance.md` 的 CLI 面：参数默认/组合、非法远程路径、损坏语料、
确认前安全提示、RAG 关闭旧流程、`--demo --rag` 演示标识、采用/回退展示。

不覆盖：真实 Provider、真实图片批次、正式评测；也不声称知识库已人工审核或 RAG 有收益。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from visual_intent_agent.cli import (
    DEFAULT_KNOWLEDGE_DIR,
    KnowledgeActivationError,
    KnowledgeReport,
    KnowledgeSourceInfo,
    build_app,
    build_knowledge_engine,
    main,
    parse_args,
    render_knowledge_report,
    resolve_knowledge_dir,
)
from visual_intent_agent.knowledge import KnowledgeBundle, KnowledgeError
from visual_intent_agent.realization.models import RealizationValue

from cli_helpers import ScriptedConsole, make_settings

#: `--demo` 剧本（与 `_DemoLLM` 的确定性映射一致；最后一步委托光线）。
DEMO_TURNS: tuple[str, ...] = (
    "一只猫",
    "photorealistic",
    "studio",
    "木桌",
    "medium_shot",
    "sitting",
    "you decide",
    "y",
    "accept",
)

CREDENTIAL_ENV = {
    "VIA_PROVIDER_BASE_URL": "https://provider.invalid/v1",
    "VIA_PROVIDER_API_KEY": "unit-test-key",
}


# ---------------------------------------------------------------------------
# 参数默认 / 组合
# ---------------------------------------------------------------------------


def test_rag_is_off_by_default():
    args = parse_args([])
    assert args.rag is False
    assert args.knowledge_dir is None
    assert args.demo is False


def test_rag_and_knowledge_dir_combination_is_parsed():
    args = parse_args(["--rag", "--knowledge-dir", "/tmp/local-kb"])
    assert args.rag is True
    assert args.knowledge_dir == "/tmp/local-kb"

    demo = parse_args(["--demo", "--rag"])
    assert demo.demo is True
    assert demo.rag is True
    assert demo.knowledge_dir is None


def test_knowledge_dir_defaults_to_local_production_dir():
    resolved = resolve_knowledge_dir(None)
    assert resolved == DEFAULT_KNOWLEDGE_DIR
    assert resolved.is_absolute()


# ---------------------------------------------------------------------------
# 非法远程路径：拒绝且不伪装启用
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "remote",
    [
        "https://example.com/knowledge",
        "http://example.com/knowledge",
        "ftp://host/kb",
        "s3://bucket/kb",
        "gs://bucket/kb",
    ],
)
def test_remote_knowledge_dir_is_rejected(remote):
    with pytest.raises(KnowledgeActivationError) as info:
        resolve_knowledge_dir(remote)
    assert info.value.code == "cli.knowledge_dir_remote"


def test_main_rejects_remote_knowledge_dir_even_without_rag():
    console = ScriptedConsole([])
    code = main(
        ["--knowledge-dir", "https://example.com/kb"],
        console=console,
        env={},
        env_file=None,
    )
    assert code == 2
    assert "cli.knowledge_dir_remote" in console.text
    assert "已确认并生成" not in console.text


def test_knowledge_dir_without_rag_is_not_read(tmp_path):
    """只给 --knowledge-dir、没开 --rag：不读取目录，也不因目录不存在而报错。"""
    console = ScriptedConsole([])
    code = main(
        ["--knowledge-dir", str(tmp_path / "does-not-exist")],
        console=console,
        env={},
        env_file=None,
    )
    # 走到真实模式配置缺失（退出码 2）；关键是不读取知识、也不伪装 RAG。
    assert code == 2
    assert "--rag 未开启" in console.text
    assert "配置错误" in console.text


# ---------------------------------------------------------------------------
# 损坏语料 / 不存在的目录：安全可见、非零退出
# ---------------------------------------------------------------------------


def test_missing_knowledge_dir_is_a_visible_error(tmp_path):
    with pytest.raises(KnowledgeActivationError) as info:
        build_knowledge_engine(knowledge_dir=str(tmp_path / "nope"), demo=False)
    assert info.value.code == "cli.knowledge_dir_invalid"


def test_corrupt_manifest_raises_knowledge_error(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "manifest.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(KnowledgeError) as info:
        build_knowledge_engine(knowledge_dir=str(corpus_dir), demo=False)
    assert info.value.code.startswith("knowledge.")


def test_main_rag_with_corrupt_corpus_exits_2_without_pretending(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "manifest.json").write_text("{ broken", encoding="utf-8")
    console = ScriptedConsole([])
    code = main(
        [
            "--rag",
            "--knowledge-dir",
            str(corpus_dir),
            "--db",
            str(tmp_path / "rag.db"),
        ],
        console=console,
        env=CREDENTIAL_ENV,
        env_file=None,
    )
    assert code == 2
    assert "知识/RAG 错误" in console.text
    assert "未生效" in console.text
    assert "已确认并生成" not in console.text


def test_demo_rag_rejects_explicit_knowledge_dir(tmp_path):
    """演示 RAG 不能拿外部目录冒充演示 approved 数据。"""
    console = ScriptedConsole([])
    code = main(
        ["--demo", "--rag", "--knowledge-dir", str(tmp_path)],
        console=console,
        env={},
        env_file=None,
    )
    assert code == 2
    assert "cli.demo_knowledge_conflict" in console.text


# ---------------------------------------------------------------------------
# RAG 关闭：旧流程逐字兼容，不展示知识块
# ---------------------------------------------------------------------------


def test_demo_without_rag_keeps_old_flow(tmp_path):
    console = ScriptedConsole(list(DEMO_TURNS))
    code = main(
        [
            "--demo",
            "--db",
            str(tmp_path / "demo.db"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        console=console,
        env={},
        env_file=None,
    )
    assert code == 0
    assert "已确认并生成" in console.text
    assert "会话已完成" in console.text
    assert "RAG 已开启" not in console.text
    assert "知识使用情况" not in console.text
    assert "知识说明" not in console.text


def test_build_app_default_is_rag_disabled(tmp_path):
    settings = make_settings(
        db_path=tmp_path / "compat.db", output_dir=tmp_path / "out"
    )
    app = build_app(settings)
    try:
        assert app.rag_enabled is False
        assert app.knowledge_source is None
        assert app.knowledge_report("missing-session") is None
    finally:
        app.close()


# ---------------------------------------------------------------------------
# --demo --rag：明确演示标识 + 采用/来源展示
# ---------------------------------------------------------------------------


def test_demo_rag_labels_demo_corpus_and_shows_adoption(tmp_path):
    console = ScriptedConsole(list(DEMO_TURNS))
    code = main(
        [
            "--demo",
            "--rag",
            "--db",
            str(tmp_path / "demo-rag.db"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        console=console,
        env={},
        env_file=None,
    )
    assert code == 0
    # 演示/测试标识（绝不冒充生产审核或真实效果）。
    assert "演示模式 RAG" in console.text
    assert "内置演示/测试 approved 夹具" in console.text
    assert "不是生产人工审核结果" in console.text
    assert "不代表真实检索或图片效果" in console.text
    # 确认前安全提示。
    assert "知识仅辅助明确委托项" in console.text
    # 生成后采用/来源/路径展示；知识值不得说成用户确认过。
    assert "本次采用 (adopted)" in console.text
    assert "demo.lighting.character.soft" in console.text
    assert "lighting.character" in console.text
    assert "Bundle / 来源 ID" in console.text
    assert "kbu_" in console.text
    assert "不代表用户确认过任何知识取值" in console.text


def test_real_rag_with_draft_only_production_corpus_falls_back_transparently(tmp_path):
    """生产 9 条全 draft：加载成功但 0 approved，界面如实说明会透明回退。"""
    console = ScriptedConsole(["exit"])
    code = main(
        [
            "--rag",
            "--knowledge-dir",
            str(DEFAULT_KNOWLEDGE_DIR),
            "--db",
            str(tmp_path / "real-rag.db"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
        console=console,
        env=CREDENTIAL_ENV,
        env_file=None,
    )
    assert code == 0
    assert "RAG 已开启" in console.text
    assert "approved 0 条" in console.text
    assert "透明回退" in console.text


# ---------------------------------------------------------------------------
# 回退展示（纯只读渲染，不依赖检索结果）
# ---------------------------------------------------------------------------


def test_render_knowledge_report_shows_fallback_and_never_claims_confirmation():
    source = KnowledgeSourceInfo(
        label="本地审核语料目录 /tmp/kb",
        path="/tmp/kb",
        corpus_version="v0.4-test",
        all_unit_count=3,
        approved_unit_count=3,
        is_demo=False,
    )
    report = KnowledgeReport(
        prompt_artifact_id="pra_demo",
        source=source,
        fallback_notes=("camera.depth_of_field: no_hit（no_hit）",),
        note="本次编译未记录任何知识 Bundle：没有采用知识。",
    )
    console = ScriptedConsole([])
    render_knowledge_report(console, report)
    assert "回退 (fallback)" in console.text
    assert "camera.depth_of_field: no_hit" in console.text
    assert "不代表用户确认过任何知识取值" in console.text


# ---------------------------------------------------------------------------
# v0.4 F2：检索器推荐 vs 编译器裁定；被拒建议绝不显示成 adopted
# ---------------------------------------------------------------------------


def _source_info() -> KnowledgeSourceInfo:
    return KnowledgeSourceInfo(
        label="本地审核语料目录 /tmp/kb",
        path="/tmp/kb",
        corpus_version="v0.4-test",
        all_unit_count=1,
        approved_unit_count=1,
        is_demo=False,
    )


def _bundle_payload(*, decision: str | None) -> KnowledgeBundle:
    """最小合法 Bundle：一条检索推荐，可选持久化编译器裁定。"""
    content_hash = hashlib.sha256("正文".encode("utf-8")).hexdigest()
    payload = {
        "bundle_id": "kbu_" + "a" * 32,
        "session_id": "ses_x",
        "intent_revision_id": "irev_x",
        "execution_revision_id": "erev_x",
        "confirmation_id": "cnf_x",
        "target_model": "qwen-image-3.0",
        "status": "ok",
        "schema_version": "knowledge.v1",
        "corpus_version": "v0.4-test",
        "tokenizer_version": "keyword.v1",
        "retrieval_version": "lexical.v1",
        "corpus_fingerprint": "b" * 64,
        "queries": [
            {
                "path": "lighting.character",
                "target_model": "qwen-image-3.0",
                "text": "cat",
                "tokens": ["cat"],
            }
        ],
        "path_results": [
            {
                "path": "lighting.character",
                "outcome": "adopted",
                "reason_code": "unique_top_candidate",
                "reason": "fixture",
                "hits": [
                    {
                        "knowledge_id": "lighting.character.fixture",
                        "version": "1",
                        "content_hash": content_hash,
                        "applicable_path": "lighting.character",
                        "candidate_value": "soft",
                        "source": {
                            "source_type": "project_original",
                            "title": "fixture",
                            "repository_path": "units.jsonl",
                            "locator": "fixture",
                            "source_revision": "v0.4-test",
                            "original_declaration": "fixture only",
                        },
                        "content": "正文",
                        "score": 0.5,
                        "rank": 1,
                        "matched_tokens": ["cat"],
                        "eligibility_snapshot": {
                            "snapshot_version": "eligibility.v1",
                            "conditions": [],
                            "target_models": ["any"],
                            "review_status": "approved",
                            "reviewer": "fixture-reviewer",
                            "reviewed_at": "2026-09-16T00:00:00+00:00",
                        },
                    }
                ],
            }
        ],
        "recommendations": [
            {
                "path": "lighting.character",
                "knowledge_id": "lighting.character.fixture",
                "version": "1",
                "content_hash": content_hash,
                "candidate_value": "soft",
                "score": 0.5,
                "reason_code": "unique_top_candidate",
                "reason": "fixture",
            }
        ],
    }
    if decision == "adopted":
        payload["adoption_decisions"] = [
            {
                "path": "lighting.character",
                "knowledge_id": "lighting.character.fixture",
                "version": "1",
                "candidate_value": "soft",
                "outcome": "adopted",
                "reason": "ok",
            }
        ]
    elif decision == "rejected":
        payload["adoption_decisions"] = [
            {
                "path": "lighting.character",
                "knowledge_id": "lighting.character.fixture",
                "version": "1",
                "candidate_value": "soft",
                "outcome": "rejected",
                "reason_code": "conditions_not_satisfied",
                "reason": "conditions are not satisfied",
            }
        ]
    return KnowledgeBundle.model_validate(payload)


def test_render_knowledge_report_never_shows_a_rejected_recommendation_as_adopted():
    report = KnowledgeReport(
        prompt_artifact_id="pra_x",
        source=_source_info(),
        bundles=(_bundle_payload(decision="rejected"),),
        fallback_notes=(
            "lighting.character: 编译复核拒绝推荐 'soft'（conditions_not_satisfied）",
        ),
    )
    console = ScriptedConsole([])
    render_knowledge_report(console, report)

    assert "本次采用 (adopted): （无）" in console.text
    assert "编译器未采用 (rejected: conditions_not_satisfied)" in console.text
    assert "编译器已采用" not in console.text
    assert "回退 (fallback)" in console.text


def test_render_knowledge_report_marks_the_adopted_decision():
    value = RealizationValue(
        path="lighting.character",
        value="soft",
        source="user_delegated",
        first_prompt_artifact_id="pra_x",
        knowledge_bundle_id="kbu_" + "a" * 32,
        knowledge_unit_id="lighting.character.fixture",
        knowledge_unit_version="1",
    )
    report = KnowledgeReport(
        prompt_artifact_id="pra_x",
        source=_source_info(),
        bundles=(_bundle_payload(decision="adopted"),),
        adopted=(value,),
    )
    console = ScriptedConsole([])
    render_knowledge_report(console, report)

    assert "本次采用 (adopted):" in console.text
    assert "编译器已采用 (adopted)" in console.text
    assert "编译器未采用" not in console.text


def test_render_knowledge_report_flags_legacy_bundle_without_decisions():
    report = KnowledgeReport(
        prompt_artifact_id="pra_x",
        source=_source_info(),
        bundles=(_bundle_payload(decision=None),),
    )
    console = ScriptedConsole([])
    render_knowledge_report(console, report)

    assert "编译器未记录裁定（旧 Bundle，不作为已采用）" in console.text
    assert "编译器已采用" not in console.text