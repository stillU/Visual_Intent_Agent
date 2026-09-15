"""回归测试（MVP v0.3 Step 06 · P3 / 更改书 001 · R1-A）：执行上下文接线。

失败证据（`evaluation/reports/failure_catalog.jsonl#FC-P3-execution-context-never-passed`）：

- s02-missing-core-002 t3 与 s09-multiturn-001 t3/t4 系统一次都没报 execution conflict；
- 根因：`assess()` 的四处调用点全部使用默认 `execution_context=None`，执行上下文
  （产品自己在 `create_session` 落盘的 `ExecutionRevision`，`1024x1024`）从未传入。

**R1-A 语义纠正**：宽幅景别（`全景` / `wide_shot` / `远景构图`）与方图**不自动构成**
冲突，`execution_conflict.framing_aspect_mismatch` 规则停用；因此本文件改为：

1. 断言景别单独出现时**不产生**该 execution conflict（不再固化错误语义）；
2. 用 spy 证明执行上下文**确实**从会话接进 `assess`（接线不因停用规则而回退），
   不再依赖某条冲突规则的结果来间接证明。
"""

from __future__ import annotations

import pytest

import visual_intent_agent.intent_engine.engine as intent_engine_module
from workflow_helpers import (
    empty_response,
    make_provider,
    make_repo,
    make_service,
    response,
    set_entry,
)
from visual_intent_agent.workflow import DEFAULT_OUTPUT_SIZE

FRAMING_ASPECT = "policy.execution_conflict.framing_aspect_mismatch"
EXECUTION_CONFLICT_PREFIX = "policy.execution_conflict"


def _conflict_codes(outcome) -> list[str]:
    return [issue.code for issue in outcome.resolution.conflicts]


@pytest.mark.parametrize("framing", ["全景", "wide_shot", "远景构图"])
def test_framing_alone_never_constitutes_an_aspect_conflict(tmp_path, framing: str) -> None:
    """R1-A：远景/宽幅景别不等于宽高比要求，单独出现时不得被阻断。"""
    repo = make_repo(tmp_path)
    llm = make_provider(
        [
            response(set_entry("composition.framing", framing, fragment=framing)),
            empty_response(),
        ]
    )
    service = make_service(repo, llm)
    snapshot = service.create_session()

    outcome = service.submit_message(snapshot.session_id, f"构图改成{framing}")

    assert FRAMING_ASPECT not in _conflict_codes(outcome)
    # 该规则停用后，不存在任何 execution conflict 被凭空报出。
    assert not [
        code for code in _conflict_codes(outcome) if code.startswith(EXECUTION_CONFLICT_PREFIX)
    ]


def test_the_session_execution_revision_carries_the_frozen_square_output_size(tmp_path) -> None:
    """执行上下文来自产品自建的 ExecutionRevision（不是评测注入）。"""
    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([]))
    snapshot = service.create_session()
    revision = repo.get_execution_revision(snapshot.current_execution_revision_id)
    assert revision.output_size == DEFAULT_OUTPUT_SIZE == "1024x1024"


def test_execution_context_is_wired_from_the_session_into_assess(tmp_path, monkeypatch) -> None:
    """接线证据（不依赖任何冲突规则）：`assess` 收到的执行上下文来自会话。"""
    captured: list = []
    real_assess = intent_engine_module.assess

    def spy(intent, execution_context=None):
        captured.append(execution_context)
        return real_assess(intent, execution_context)

    monkeypatch.setattr(intent_engine_module, "assess", spy)

    repo = make_repo(tmp_path)
    service = make_service(repo, make_provider([empty_response()]))
    snapshot = service.create_session()

    service.submit_message(snapshot.session_id, "make it wider")

    assert captured, "IntentEngine.resolve must call assess"
    assert all(context is not None for context in captured)
    assert {context.execution_revision_id for context in captured} == {
        snapshot.current_execution_revision_id
    }
    assert {context.output_size for context in captured} == {DEFAULT_OUTPUT_SIZE}


def test_non_wide_framing_never_reports_the_execution_conflict(tmp_path) -> None:
    """只接执行上下文，不改变景别判定语义。"""
    repo = make_repo(tmp_path)
    llm = make_provider([response(set_entry("composition.framing", "中景", fragment="中景"))])
    service = make_service(repo, llm)
    snapshot = service.create_session()
    outcome = service.submit_message(snapshot.session_id, "中景构图")
    assert FRAMING_ASPECT not in _conflict_codes(outcome)
