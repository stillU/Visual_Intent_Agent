"""公开面测试：再导出、协议一致性、依赖边界、命名空间与无凭据。"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from persistence_helpers import seed_context

import visual_intent_agent.persistence as persistence
from visual_intent_agent.persistence import (
    ALLOWED_TRANSITIONS,
    ConfirmationRecord,
    InvalidStateTransitionError,
    Repository,
    RepositoryError,
    SessionSnapshot,
    SQLiteRepository,
    StoredArtifact,
    WorkflowState,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "visual_intent_agent" / "persistence"

EXPECTED_PUBLIC_NAMES = {
    "Repository",
    "SQLiteRepository",
    "RepositoryError",
    "WorkflowState",
    "ALLOWED_TRANSITIONS",
    "InvalidStateTransitionError",
    "ConfirmationRecord",
    "SessionSnapshot",
    "StoredArtifact",
}

#: 任务书 9 个方法 + 架构冻结的 pending question / Artifact 信封方法 + 只读补充方法。
EXPECTED_REPOSITORY_METHODS = {
    "create_session",
    "append_message",
    "append_intent_revision",
    "append_execution_revision",
    "save_confirmation",
    "is_confirmation_valid",
    "get_current_session_snapshot",
    "get_intent_revision",
    "get_execution_revision",
    "get_confirmation",
    "transition_state",
    "save_pending_question",
    "clear_pending_question",
    "append_prompt_artifact",
    "get_prompt_artifact",
    "append_generation_artifact",
    "get_generation_artifact",
    "list_generation_artifacts",
    "append_feedback_result",
    "append_realization_state",
    "get_current_realization_state",
    # v0.4 Step 03：append-only knowledge_bundles 信封
    "append_knowledge_bundle",
    "get_knowledge_bundle",
    "list_knowledge_bundles",
}

BANNED_THIRD_PARTY = {
    "httpx",
    "requests",
    "urllib3",
    "fastapi",
    "openai",
    "PIL",
    "numpy",
    "yaml",
    "dotenv",
}


def test_package_reexports_the_frozen_public_surface() -> None:
    assert set(persistence.__all__) == EXPECTED_PUBLIC_NAMES
    for name in EXPECTED_PUBLIC_NAMES:
        assert hasattr(persistence, name), name


def test_public_names_are_the_expected_objects() -> None:
    assert persistence.Repository is Repository
    assert persistence.SQLiteRepository is SQLiteRepository
    assert persistence.RepositoryError is RepositoryError
    assert persistence.WorkflowState is WorkflowState
    assert persistence.ALLOWED_TRANSITIONS is ALLOWED_TRANSITIONS
    assert persistence.InvalidStateTransitionError is InvalidStateTransitionError
    assert persistence.ConfirmationRecord is ConfirmationRecord
    assert persistence.SessionSnapshot is SessionSnapshot
    assert persistence.StoredArtifact is StoredArtifact


def test_repository_protocol_declares_every_frozen_method() -> None:
    declared = {
        name
        for name in dir(Repository)
        if not name.startswith("_") and callable(getattr(Repository, name, None))
    }
    assert declared >= EXPECTED_REPOSITORY_METHODS


def test_sqlite_repository_satisfies_the_protocol(repo) -> None:
    assert isinstance(repo, Repository)


def test_signatures_match_the_frozen_architecture_table() -> None:
    import inspect

    def parameters(method) -> list[str]:
        return list(inspect.signature(method).parameters)

    assert parameters(SQLiteRepository.create_session) == ["self", "session_id"]
    assert parameters(SQLiteRepository.append_message) == [
        "self",
        "session_id",
        "message_id",
        "role",
        "content",
        "created_at",
    ]
    assert parameters(SQLiteRepository.append_intent_revision) == ["self", "revision"]
    assert parameters(SQLiteRepository.append_execution_revision) == ["self", "revision"]
    assert parameters(SQLiteRepository.save_confirmation) == ["self", "record"]
    assert parameters(SQLiteRepository.is_confirmation_valid) == ["self", "confirmation_id"]
    assert parameters(SQLiteRepository.get_current_session_snapshot) == ["self", "session_id"]
    assert parameters(SQLiteRepository.get_intent_revision) == ["self", "intent_revision_id"]
    assert parameters(SQLiteRepository.transition_state) == ["self", "session_id", "to_state"]
    assert parameters(SQLiteRepository.save_pending_question) == [
        "self",
        "session_id",
        "question_id",
        "payload",
    ]
    assert parameters(SQLiteRepository.clear_pending_question) == ["self", "session_id"]
    assert parameters(SQLiteRepository.append_prompt_artifact) == [
        "self",
        "prompt_artifact_id",
        "session_id",
        "refs",
        "payload",
    ]
    assert parameters(SQLiteRepository.append_realization_state) == [
        "self",
        "realization_id",
        "session_id",
        "refs",
        "payload",
    ]
    assert parameters(SQLiteRepository.append_knowledge_bundle) == [
        "self",
        "bundle_id",
        "session_id",
        "refs",
        "payload",
    ]
    assert parameters(SQLiteRepository.get_knowledge_bundle) == ["self", "bundle_id"]
    assert parameters(SQLiteRepository.list_knowledge_bundles) == ["self", "session_id"]


def test_protocol_and_implementation_share_the_same_parameter_names() -> None:
    import inspect

    for name in sorted(EXPECTED_REPOSITORY_METHODS):
        protocol_method = getattr(Repository, name)
        implementation_method = getattr(SQLiteRepository, name)
        assert list(inspect.signature(protocol_method).parameters) == list(
            inspect.signature(implementation_method).parameters
        ), name


def test_persistence_package_only_loads_domain_and_itself() -> None:
    code = (
        "import sys\n"
        "import visual_intent_agent.persistence\n"
        "banned = {'httpx', 'requests', 'urllib3', 'fastapi', 'openai', 'PIL', 'numpy', 'yaml', 'dotenv'}\n"
        "third_party = sorted({m.split('.')[0] for m in sys.modules} & banned)\n"
        "internal = sorted(m for m in sys.modules if m.startswith('visual_intent_agent'))\n"
        "print(repr(third_party))\n"
        "print(repr(internal))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    third_party_line, internal_line = result.stdout.strip().splitlines()
    assert third_party_line == "[]"
    loaded = eval(internal_line)  # noqa: S307 - 子进程输出，内容受控
    assert "visual_intent_agent.persistence" in loaded
    assert "visual_intent_agent.domain" in loaded
    for forbidden in (
        "visual_intent_agent.config",
        "visual_intent_agent.providers",
        "visual_intent_agent.intent_engine",
        "visual_intent_agent.validation",
        "visual_intent_agent.policy",
        "visual_intent_agent.workflow",
        "visual_intent_agent.prompt_engine",
        "visual_intent_agent.generation",
        "visual_intent_agent.feedback",
    ):
        assert forbidden not in loaded, forbidden


@pytest.mark.parametrize(
    "later_step_name",
    [
        "IntentEngine",
        "Interpreter",
        "PromptEngine",
        "PromptArtifact",
        "WorkflowService",
        "GenerationPipeline",
        "FeedbackEngine",
        "QuestionBuilder",
    ],
)
def test_package_does_not_pre_implement_later_steps(later_step_name: str) -> None:
    assert later_step_name not in persistence.__all__
    assert not hasattr(persistence, later_step_name)


def test_persistence_modules_have_no_network_llm_or_image_dependency() -> None:
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in ("httpx", "requests", "urllib3", "openai", "fastapi", "chat/completions"):
            assert token not in source, f"{path.name}: {token}"


def test_persistence_does_not_use_randomness() -> None:
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in ("random", "uuid4", "time.time"):
            assert token not in source, f"{path.name}: {token}"


def test_error_codes_use_the_persistence_namespace() -> None:
    codes = set()
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        codes.update(re.findall(r'RepositoryError\(\s*"([^"]+)"', source))
    assert codes, "expected RepositoryError codes in the persistence package"
    for code in codes:
        assert code.startswith("persistence."), code
    assert "persistence.invalid_state_transition" not in codes  # 由 InvalidStateTransitionError 承载


def test_no_module_in_the_step_04_package_contains_an_api_key() -> None:
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "sk-" not in text, path
        assert "api_key" not in text.lower(), path
    schema = (PACKAGE_DIR / "schema.sql").read_text(encoding="utf-8")
    assert "sk-" not in schema
    assert "api_key" not in schema.lower()


def test_repository_does_not_accept_settings() -> None:
    import inspect

    parameters = inspect.signature(SQLiteRepository.__init__).parameters
    assert list(parameters) == ["self", "db_path"]
    source = (PACKAGE_DIR / "repository.py").read_text(encoding="utf-8")
    assert "visual_intent_agent.config" not in source
    assert "load_settings" not in source


def test_repository_error_carries_a_code_attribute() -> None:
    error = RepositoryError("persistence.test_code", "boom")
    assert error.code == "persistence.test_code"
    assert "persistence.test_code" in str(error)


def test_end_to_end_public_surface_usage(repo) -> None:
    intent_revision, _, confirmation = seed_context(repo)
    assert confirmation is not None
    assert repo.is_confirmation_valid(confirmation.confirmation_id) is True
    assert repo.get_intent_revision(intent_revision.intent_revision_id) == intent_revision
    assert isinstance(repo.get_current_session_snapshot("ses_0001"), SessionSnapshot)
