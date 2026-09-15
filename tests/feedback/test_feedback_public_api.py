"""Step 09 公开面与依赖纪律：`feedback` / `realization.carry` / `workflow.review`。

不修改 Step 06 既有文件是本步的硬约束：`ReviewService` / `FeedbackOutcome` 只从
`visual_intent_agent.workflow.review` 暴露（`workflow/__init__.py` 保持原样）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import visual_intent_agent.feedback as feedback
import visual_intent_agent.realization as realization_package
import visual_intent_agent.workflow as workflow_package
from visual_intent_agent.feedback import (
    FEEDBACK_PROMPT_VERSION,
    FeedbackEngine,
    FeedbackResult,
)
from visual_intent_agent.realization.carry import (
    CARRY_INVALIDATION_REASONS,
    CarryEvaluation,
    build_carry_state,
    evaluate_carry,
    owner_policy_for_path,
)
from visual_intent_agent.workflow.review import (
    REVIEW_ERROR_CODES,
    FeedbackOutcome,
    ReviewError,
    ReviewService,
)

PACKAGE = Path(feedback.__file__).resolve().parent
PROJECT = PACKAGE.parent


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_feedback_public_names_are_declared():
    for name in feedback.__all__:
        assert hasattr(feedback, name), name
        assert getattr(feedback, name) is not None
    assert len(feedback.__all__) == len(set(feedback.__all__))
    for required in (
        "FeedbackEngine",
        "FeedbackDecision",
        "FeedbackRequest",
        "FeedbackResult",
        "FeedbackError",
        "FeedbackParseError",
        "clarify_target_path",
        "is_unparseable",
    ):
        assert required in feedback.__all__


def test_feedback_prompt_version_is_recorded():
    # Step 06 · P1/P5：边界约束修订 → feedback.v2；
    # Step 06 patch 002（P6 超时缓解）：保语义精简 → feedback.v3
    # （修订理由见 fix_traceability.md patch 002 章节）；
    # 更改书 002 工包 B：补 COUNTING/LOCATIVE 语义边界 → feedback.v4。
    assert FEEDBACK_PROMPT_VERSION == "feedback.v4"
    assert "FEEDBACK_PROMPT_VERSION" in feedback.__all__
    assert "FEEDBACK_OUTPUT_SCHEMA" in feedback.__all__


def test_feedback_result_keeps_exactly_twelve_fields():
    assert len(FeedbackResult.model_fields) == 12


def test_feedback_engine_exposes_the_frozen_analyze_signature():
    parameters = list(inspect.signature(FeedbackEngine.analyze).parameters)
    assert parameters == ["self", "request"]
    constructor = list(inspect.signature(FeedbackEngine.__init__).parameters)
    assert constructor == ["self", "llm"]


def test_feedback_layer_never_imports_httpx_or_concrete_llm_adapters():
    forbidden = {
        "httpx",
        "visual_intent_agent.providers.openai_llm",
        "visual_intent_agent.providers.fake_llm",
        "visual_intent_agent.providers.openai_image",
        "visual_intent_agent.providers.fake_image",
        "visual_intent_agent.intent_engine",
        "visual_intent_agent.workflow",
        "visual_intent_agent.workflow.review",
    }
    for name in ("models.py", "engine.py", "__init__.py"):
        imported = _imported_modules(PACKAGE / name)
        assert not (imported & forbidden), (name, imported & forbidden)


def test_feedback_models_never_import_the_engine_and_vice_versa_is_one_way():
    # engine → models 单向；models 不 import engine（避免循环）
    assert "visual_intent_agent.feedback.engine" not in _imported_modules(
        PACKAGE / "models.py"
    )


def test_realization_carry_public_names_are_available():
    assert callable(evaluate_carry)
    assert callable(build_carry_state)
    assert callable(owner_policy_for_path)
    assert list(CarryEvaluation.model_fields) == ["carried_values", "invalidated_values"]
    assert CARRY_INVALIDATION_REASONS == {
        "user_changed_delegated_path",
        "decision_dependency_changed",
        "unpinned_delegated_path",
    }


def test_realization_package_init_stays_blank():
    init_file = Path(realization_package.__file__)
    assert init_file.read_text(encoding="utf-8").strip() == ""
    assert not hasattr(realization_package, "CarryEvaluation")


def test_review_service_is_not_reexported_from_the_step_06_workflow_init():
    init_file = Path(workflow_package.__file__)
    assert "ReviewService" not in init_file.read_text(encoding="utf-8")
    assert not hasattr(workflow_package, "ReviewService")


def test_review_public_surface_and_error_codes():
    assert callable(ReviewService.submit_feedback)
    parameters = list(inspect.signature(ReviewService.submit_feedback).parameters)
    assert parameters == ["self", "session_id", "generation_id", "text"]
    assert REVIEW_ERROR_CODES == {
        "workflow.generation_not_found",
        "workflow.generation_mismatch",
    }
    for name in FeedbackOutcome.model_fields:
        assert name in {
            "session_id",
            "message_id",
            "generation_id",
            "feedback",
            "snapshot",
            "resolution",
            "pending_question",
            "confirmation_summary",
            "carry",
            "realization_state_id",
            "recoverable_failure",
            "failure_codes",
        }
    with pytest.raises(ValueError):
        ReviewError("workflow.unknown_code", "nope")


def test_review_module_respects_the_step_06_workflow_import_boundary():
    """Step 06 冻结测试禁止 `workflow/` import prompt_engine / generation / 图像 adapter。

    `review.py` 因此不 import 这两个 Step 07/08 包，改为把 Repository payload 交给
    `FeedbackRequest.model_validate` 校验；持久化只经 `Repository`。
    """
    path = PROJECT / "workflow" / "review.py"
    imported = _imported_modules(path)
    assert "visual_intent_agent.validation" in imported
    assert "visual_intent_agent.policy" in imported
    assert "visual_intent_agent.realization.carry" in imported
    assert "visual_intent_agent.feedback" in imported
    forbidden = {
        "visual_intent_agent.prompt_engine",
        "visual_intent_agent.generation",
        "visual_intent_agent.providers.image",
        "visual_intent_agent.providers.openai_image",
        "visual_intent_agent.providers.fake_image",
        "sqlite3",
        "httpx",
        "os",
        "openai",
        "fastapi",
    }
    assert not (imported & forbidden), imported & forbidden
    source = path.read_text(encoding="utf-8")
    assert "get_secret_value" not in source
    assert "os.environ" not in source
    assert "Authorization" not in source
    assert "s" + "k" + "-" not in source
    # 不复制 Step 07/08 的字段面：请求由 FeedbackRequest 从 payload 校验而来
    assert "FeedbackRequest.model_validate" in source
