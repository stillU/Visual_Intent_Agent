"""Step 06 公开面与依赖边界测试（应用层用例即对外接口）。"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import visual_intent_agent.workflow as workflow_package
from visual_intent_agent.workflow import (
    DEFAULT_OUTPUT_SIZE,
    MAX_INTERPRETATION_ATTEMPTS,
    ConfirmationSummary,
    PendingQuestion,
    QuestionBuilder,
    SubmitMessageOutcome,
    WorkflowError,
    WorkflowService,
    build_confirmation_summary,
    compute_summary_hash,
    derive_change_summary,
    render_question_text,
)

WORKFLOW_DIR = Path(workflow_package.__file__).parent

#: workflow 包禁止 import 的模块前缀（Step 07/08/09 与 Web/DB 细节）。
FORBIDDEN_IMPORT_PREFIXES = (
    "visual_intent_agent.prompt_engine",
    "visual_intent_agent.generation",
    "visual_intent_agent.providers.image",
    "visual_intent_agent.providers.openai_image",
    "visual_intent_agent.providers.fake_image",
    "openai",
    "httpx",
    "sqlite3",
    "fastapi",
    "os",
)


def _workflow_modules() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.py"))


def _imported_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_expected_public_names_are_exported():
    assert workflow_package.__all__ == [
        "WorkflowService",
        "SubmitMessageOutcome",
        "PendingQuestion",
        "QuestionBuilder",
        "render_question_text",
        "ConfirmationSummary",
        "WorkflowError",
        "compute_summary_hash",
        "build_confirmation_summary",
        "derive_change_summary",
        "WORKFLOW_ERROR_CODES",
        "WORKFLOW_INVALID_STATE",
        "WORKFLOW_STALE_REVISION",
        "WORKFLOW_SUMMARY_HASH_MISMATCH",
        "DEFAULT_OUTPUT_SIZE",
        "MAX_INTERPRETATION_ATTEMPTS",
    ]
    for name in workflow_package.__all__:
        assert hasattr(workflow_package, name)


def test_frozen_use_case_signatures():
    assert list(inspect.signature(WorkflowService.create_session).parameters) == ["self"]
    assert list(inspect.signature(WorkflowService.submit_message).parameters) == [
        "self",
        "session_id",
        "text",
    ]
    assert list(inspect.signature(WorkflowService.get_session).parameters) == [
        "self",
        "session_id",
    ]
    assert list(
        inspect.signature(WorkflowService.confirm_current_intent).parameters
    ) == [
        "self",
        "session_id",
        "intent_revision_id",
        "execution_revision_id",
        "summary_hash",
    ]
    assert list(inspect.signature(WorkflowService.__init__).parameters) == [
        "self",
        "repo",
        "intent_engine",
        "question_builder",
        "settings",
    ]
    assert list(inspect.signature(QuestionBuilder.__init__).parameters) == ["self", "llm"]
    assert list(inspect.signature(QuestionBuilder.build).parameters) == [
        "self",
        "spec",
        "session_id",
    ]


def test_workflow_never_imports_later_steps_or_web_db_layers():
    for path in _workflow_modules():
        for name in _imported_names(path):
            assert not name.startswith(FORBIDDEN_IMPORT_PREFIXES), (
                f"{path.name} imports forbidden module {name!r}"
            )


def test_workflow_does_not_read_environment_or_secrets():
    # 逐字量拼接（避免本测试文件自身出现疑似 key 前缀的字面量）
    secret_marker = "s" + "k" + "-"
    for path in _workflow_modules():
        source = path.read_text(encoding="utf-8")
        assert "get_secret_value" not in source
        assert "os.environ" not in source
        assert "Authorization" not in source
        assert secret_marker not in source


def test_workflow_error_codes_use_the_reserved_namespace():
    from visual_intent_agent.workflow import WORKFLOW_ERROR_CODES

    for code in WORKFLOW_ERROR_CODES:
        assert code.startswith("workflow.")
    assert len(WORKFLOW_ERROR_CODES) == 3


def test_confirmation_summary_and_hash_are_reachable_from_the_package():
    assert ConfirmationSummary is workflow_package.ConfirmationSummary
    assert callable(compute_summary_hash)
    assert callable(build_confirmation_summary)
    assert callable(derive_change_summary)
    assert callable(render_question_text)
    assert PendingQuestion is workflow_package.PendingQuestion
    assert WorkflowService is workflow_package.WorkflowService
    assert SubmitMessageOutcome is workflow_package.SubmitMessageOutcome
    assert WorkflowError is workflow_package.WorkflowError


@pytest.mark.parametrize("name", ["questions.py", "confirmation.py", "service.py", "__init__.py"])
def test_workflow_module_files_exist(name: str):
    assert (WORKFLOW_DIR / name).is_file()


def test_no_generating_reference_anywhere_in_the_workflow_package():
    """Hard gate：workflow 包没有到 GENERATING 的迁移路径（Step 08 才拥有）。"""
    for path in _workflow_modules():
        assert "WorkflowState.GENERATING" not in path.read_text(encoding="utf-8")
