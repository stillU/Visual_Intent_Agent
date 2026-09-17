"""Step 04 公开面：SQLite Repository、状态机与存储记录信封。

    from visual_intent_agent.persistence import (
        Repository, SQLiteRepository, RepositoryError,
        WorkflowState, ALLOWED_TRANSITIONS, InvalidStateTransitionError,
        ConfirmationRecord, SessionSnapshot, StoredArtifact,
    )

本包只做确定性持久化：不理解自然语言、不调用 LLM、不生成 Prompt/图片、
不建 Web API、不使用 Event Sourcing。它回答唯一核心问题：

    会话当前处于哪个状态，哪些 revision / confirmation / artifact 是可审计的事实？

最小迁移机制通过 `PRAGMA user_version`（当前 v2：v1 的 9 张表 + v0.4 Step 03 的
append-only `knowledge_bundles`，见 `schema.sql`）；底层迁移函数
`apply_migrations` 与 `LATEST_SCHEMA_USER_VERSION` 从
`visual_intent_agent.persistence.repository` 导入（用于迁移测试与诊断）。
旧 v1 数据库由 `SQLiteRepository` 首次打开时幂等补建 `knowledge_bundles`，
不删表、不改写旧 payload/refs。
"""

from __future__ import annotations

from .records import ConfirmationRecord, SessionSnapshot, StoredArtifact
from .repository import Repository, RepositoryError, SQLiteRepository
from .state_machine import ALLOWED_TRANSITIONS, InvalidStateTransitionError, WorkflowState

__all__ = [
    "Repository",
    "SQLiteRepository",
    "RepositoryError",
    "WorkflowState",
    "ALLOWED_TRANSITIONS",
    "InvalidStateTransitionError",
    "ConfirmationRecord",
    "SessionSnapshot",
    "StoredArtifact",
]
