"""Step 04 persistence 集成测试工厂与场景装配工具。

放在唯一命名的模块（而非 `conftest`）中，避免 pytest 以多个目录参数运行时的
`conftest` basename 缓存冲突（上游 `tests/validation` 与 `tests/policy` 都使用
`from conftest import ...`；见 step_04_handoff 的「已知限制」）。

- 所有测试使用 `tmp_path` 下的临时 SQLite（绝不写 `data/`）；
- 工厂只使用字面量 ID 与固定时间戳，保证可复现。
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import deque
from datetime import datetime, timezone

from visual_intent_agent.domain import (
    DeltaOperation,
    EvidenceRef,
    ExecutionRevision,
    IntentDelta,
    IntentRevision,
    SubjectFacet,
    VisualIntent,
)
from visual_intent_agent.persistence import (
    ALLOWED_TRANSITIONS,
    ConfirmationRecord,
    SQLiteRepository,
    WorkflowState,
)

#: 固定 tz-aware UTC 时间戳（避免任何测试依赖真实时钟）。
FIXED_TIME = datetime(2024, 5, 1, 8, 30, 0, tzinfo=timezone.utc)
#: 冻结的默认图像模型（仅作为 ExecutionRevision 的字面量值）。
DEFAULT_MODEL = "qwen-image-3.0"


# ---------------------------------------------------------------------------
# 模型工厂
# ---------------------------------------------------------------------------


def make_intent(description: str | None = "a lone astronaut") -> VisualIntent:
    return VisualIntent(subject=SubjectFacet(description=description))


def make_delta(
    *,
    path: str = "subject.description",
    value: str | int = "a lone astronaut",
    message_id: str = "msg_evidence",
    operation: DeltaOperation = DeltaOperation.SET,
) -> IntentDelta:
    return IntentDelta(
        operation=operation,
        path=path,
        value=value,
        evidence_refs=[EvidenceRef(message_id=message_id)],
    )


def make_intent_revision(
    session_id: str,
    revision_id: str,
    parent: str | None = None,
    *,
    description: str | None = "a lone astronaut",
    deltas: list[IntentDelta] | None = None,
) -> IntentRevision:
    return IntentRevision(
        intent_revision_id=revision_id,
        session_id=session_id,
        parent_revision_id=parent,
        intent=make_intent(description),
        applied_deltas=list(deltas or []),
        created_at=FIXED_TIME,
    )


def make_execution_revision(
    session_id: str,
    revision_id: str,
    parent: str | None = None,
    *,
    output_size: str = "1024x1024",
) -> ExecutionRevision:
    return ExecutionRevision(
        execution_revision_id=revision_id,
        session_id=session_id,
        parent_revision_id=parent,
        target_model=DEFAULT_MODEL,
        output_size=output_size,
        created_at=FIXED_TIME,
    )


def make_summary_hash(text: str = "step-04-summary") -> str:
    """确定性的 sha256 十六进制摘要（Step 06 冻结算法的形态）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_confirmation(
    session_id: str,
    confirmation_id: str,
    intent_revision_id: str,
    execution_revision_id: str,
    *,
    summary_hash: str | None = None,
) -> ConfirmationRecord:
    return ConfirmationRecord(
        confirmation_id=confirmation_id,
        session_id=session_id,
        intent_revision_id=intent_revision_id,
        execution_revision_id=execution_revision_id,
        summary_hash=summary_hash if summary_hash is not None else make_summary_hash(),
        confirmed_at=FIXED_TIME,
    )


# ---------------------------------------------------------------------------
# 场景装配
# ---------------------------------------------------------------------------


def seed_session(repo: SQLiteRepository, session_id: str = "ses_0001") -> str:
    """建立会话（初始状态 UNDERSTANDING）。"""
    repo.create_session(session_id)
    return session_id


def seed_context(
    repo: SQLiteRepository,
    session_id: str = "ses_0001",
    *,
    confirm: bool = True,
    suffix: str = "",
) -> tuple[IntentRevision, ExecutionRevision, ConfirmationRecord | None]:
    """session + 第一条 intent revision + 第一条 execution revision（+ 有效确认）。

    `suffix` 用于在同一测试内建立第二个会话，避免固定字面量 ID 冲突。
    """
    seed_session(repo, session_id)
    intent_revision = make_intent_revision(session_id, f"irev_0001{suffix}")
    execution_revision = make_execution_revision(session_id, f"erev_0001{suffix}")
    repo.append_intent_revision(intent_revision)
    repo.append_execution_revision(execution_revision)
    confirmation = None
    if confirm:
        confirmation = make_confirmation(
            session_id,
            f"cnf_0001{suffix}",
            intent_revision.intent_revision_id,
            execution_revision.execution_revision_id,
        )
        repo.save_confirmation(confirmation)
    return intent_revision, execution_revision, confirmation


def seed_prompt_and_generation(
    repo: SQLiteRepository,
    session_id: str = "ses_0001",
    *,
    prompt_artifact_id: str = "pra_0001",
    generation_id: str = "gen_0001",
):
    """在 `seed_context` 之上建立 PromptArtifact 与 GenerationArtifact。"""
    intent_revision, execution_revision, confirmation = seed_context(repo, session_id, confirm=True)
    assert confirmation is not None
    repo.append_prompt_artifact(
        prompt_artifact_id,
        session_id,
        {
            "intent_revision_id": intent_revision.intent_revision_id,
            "confirmation_id": confirmation.confirmation_id,
        },
        '{"prompt": "a lone astronaut"}',
    )
    repo.append_generation_artifact(
        generation_id,
        session_id,
        {"prompt_artifact_id": prompt_artifact_id},
        '{"output_refs": []}',
    )
    return intent_revision, execution_revision, confirmation


def reach_state(
    repo: SQLiteRepository,
    session_id: str,
    target: WorkflowState,
    *,
    start: WorkflowState = WorkflowState.UNDERSTANDING,
) -> None:
    """通过合法迁移把会话推进到 `target`（BFS 最短路径，确定性）。"""
    if target == start:
        return
    queue: deque[tuple[WorkflowState, list[WorkflowState]]] = deque([(start, [])])
    seen = {start}
    ordered = sorted(
        ALLOWED_TRANSITIONS, key=lambda pair: (pair[0].value, pair[1].value)
    )
    while queue:
        state, path = queue.popleft()
        for source, destination in ordered:
            if source != state or destination in seen:
                continue
            new_path = [*path, destination]
            if destination == target:
                for step in new_path:
                    repo.transition_state(session_id, step)
                return
            seen.add(destination)
            queue.append((destination, new_path))
    raise AssertionError(f"no legal transition path reaches {target}")


# ---------------------------------------------------------------------------
# 故障注入（事务回滚测试）：只改测试用临时库的 schema，不触碰生产代码
# ---------------------------------------------------------------------------


def install_abort_trigger(db_path, *, table: str, column: str, name: str) -> None:
    """在 `UPDATE OF <column> ON <table>` 上安装 RAISE(ABORT) 触发器。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            f"CREATE TRIGGER {name} BEFORE UPDATE OF {column} ON {table} "
            f"BEGIN SELECT RAISE(ABORT, 'injected failure on {table}.{column}'); END;"
        )
        conn.commit()
    finally:
        conn.close()


def drop_trigger(db_path, name: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"DROP TRIGGER {name}")
        conn.commit()
    finally:
        conn.close()
