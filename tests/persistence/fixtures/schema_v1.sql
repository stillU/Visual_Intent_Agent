-- Step 04 SQLite schema v1（ARCHITECTURE.md 4「Step 04」）。
--
-- 恰好 9 张表：
--   sessions / messages / intent_revisions / execution_revisions / confirmations
--   + realization_states / prompt_artifacts / generation_artifacts / feedback_results
--   （信封模式：refs 外键 + payload JSON；本步骤不建 knowledge_bundles）
--
-- 迁移：由 repository.py 通过 `PRAGMA user_version` 做最小迁移；本文件只含 v1 DDL，
-- 全部 `IF NOT EXISTS`，因此中途失败后可安全重跑。
-- `PRAGMA foreign_keys` 是连接级设置（且不能在事务内生效），由 repository.py 在每次
-- 建立连接时显式打开，不写在本文件里。
--
-- 不可覆盖约定：intent_revisions / execution_revisions / confirmations 以及四张
-- Artifact 表**只有 INSERT 路径**，没有任何 UPDATE；`sessions` 只保存"当前指针"，
-- 由 Step 04 在同一事务内推进。业务模型整份 JSON 存入 *_json / payload 列，
-- 保证读回与写入逐值一致。

-- ---------------------------------------------------------------------------
-- 1. sessions：会话 + 当前指针（唯一允许 UPDATE 的表）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    session_id                    TEXT PRIMARY KEY,
    workflow_state                TEXT NOT NULL,
    current_intent_revision_id    TEXT REFERENCES intent_revisions(intent_revision_id),
    current_execution_revision_id TEXT REFERENCES execution_revisions(execution_revision_id),
    latest_confirmation_id        TEXT REFERENCES confirmations(confirmation_id),
    pending_question_id           TEXT,
    pending_question_payload      TEXT,
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. messages：会话消息（append-only，读取顺序 = rowid 插入顺序）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

-- ---------------------------------------------------------------------------
-- 3. intent_revisions：不可覆盖的 Intent 快照（parent 链）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intent_revisions (
    intent_revision_id TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    parent_revision_id TEXT REFERENCES intent_revisions(intent_revision_id),
    schema_version     TEXT NOT NULL,
    revision_json      TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intent_revisions_session ON intent_revisions(session_id);
CREATE INDEX IF NOT EXISTS idx_intent_revisions_parent ON intent_revisions(parent_revision_id);

-- ---------------------------------------------------------------------------
-- 4. execution_revisions：不可覆盖的 ExecutionContext 快照（parent 链）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS execution_revisions (
    execution_revision_id TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES sessions(session_id),
    parent_revision_id    TEXT REFERENCES execution_revisions(execution_revision_id),
    schema_version        TEXT NOT NULL,
    revision_json        TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_revisions_session ON execution_revisions(session_id);
CREATE INDEX IF NOT EXISTS idx_execution_revisions_parent ON execution_revisions(parent_revision_id);

-- ---------------------------------------------------------------------------
-- 5. confirmations：Confirmation 的严格 revision 绑定（append-only）
--    binding_hash = sha256(intent_revision_id \x1f execution_revision_id \x1f summary_hash)
--    用于 is_confirmation_valid 的"hash 匹配"完整性校验（防篡改/串接）。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS confirmations (
    confirmation_id       TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL REFERENCES sessions(session_id),
    intent_revision_id    TEXT NOT NULL REFERENCES intent_revisions(intent_revision_id),
    execution_revision_id TEXT NOT NULL REFERENCES execution_revisions(execution_revision_id),
    summary_hash          TEXT NOT NULL,
    binding_hash          TEXT NOT NULL,
    schema_version        TEXT NOT NULL,
    confirmed_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_confirmations_session ON confirmations(session_id);

-- ---------------------------------------------------------------------------
-- 6. prompt_artifacts（信封）：必填 refs = intent_revision_id, confirmation_id
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prompt_artifacts (
    prompt_artifact_id TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    intent_revision_id TEXT NOT NULL REFERENCES intent_revisions(intent_revision_id),
    confirmation_id    TEXT NOT NULL REFERENCES confirmations(confirmation_id),
    refs_json          TEXT NOT NULL,
    payload            TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_artifacts_session ON prompt_artifacts(session_id);

-- ---------------------------------------------------------------------------
-- 7. generation_artifacts（信封）：必填 refs = prompt_artifact_id
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS generation_artifacts (
    generation_id      TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL REFERENCES sessions(session_id),
    prompt_artifact_id TEXT NOT NULL REFERENCES prompt_artifacts(prompt_artifact_id),
    refs_json          TEXT NOT NULL,
    payload            TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generation_artifacts_session ON generation_artifacts(session_id);

-- ---------------------------------------------------------------------------
-- 8. feedback_results（信封）：必填 refs = generation_id
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback_results (
    feedback_id   TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    generation_id TEXT NOT NULL REFERENCES generation_artifacts(generation_id),
    refs_json     TEXT NOT NULL,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_results_session ON feedback_results(session_id);

-- ---------------------------------------------------------------------------
-- 9. realization_states（信封）：必填 refs = based_on_intent_revision_id
--    失效产生新 state，历史不覆盖。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS realization_states (
    realization_id              TEXT PRIMARY KEY,
    session_id                  TEXT NOT NULL REFERENCES sessions(session_id),
    based_on_intent_revision_id TEXT NOT NULL REFERENCES intent_revisions(intent_revision_id),
    refs_json                   TEXT NOT NULL,
    payload                     TEXT NOT NULL,
    created_at                  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_realization_states_session ON realization_states(session_id);
