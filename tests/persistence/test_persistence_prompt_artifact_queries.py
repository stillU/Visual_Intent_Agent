"""`get_latest_prompt_artifact(session_id)` 只读 getter 测试（Rev.2 裁定 1 工单 C）。

覆盖：空会话 → None、单条、多条取最新（`created_at` 相同则 rowid 定序）、跨会话隔离、
会话不存在 → `persistence.session_not_found`，以及 PromptArtifact 外键约束仍生效。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from persistence_helpers import FIXED_TIME, seed_context

import visual_intent_agent.persistence.repository as repository_module
from visual_intent_agent.persistence import RepositoryError


def append_prompt(repo, prompt_artifact_id: str, payload: str = "{}") -> None:
    """用会话 0001 的有效 refs 追加一条 PromptArtifact。"""
    repo.append_prompt_artifact(
        prompt_artifact_id,
        "ses_0001",
        {"intent_revision_id": "irev_0001", "confirmation_id": "cnf_0001"},
        payload,
    )


def prompt_count(repo) -> int:
    return int(repo.connection.execute("SELECT COUNT(*) FROM prompt_artifacts").fetchone()[0])


def created_at_of(repo, prompt_artifact_id: str) -> str:
    row = repo.connection.execute(
        "SELECT created_at FROM prompt_artifacts WHERE prompt_artifact_id = ?",
        (prompt_artifact_id,),
    ).fetchone()
    return str(row["created_at"])


def test_latest_prompt_artifact_is_none_until_the_first_artifact(repo) -> None:
    seed_context(repo)
    assert repo.get_latest_prompt_artifact("ses_0001") is None


def test_latest_prompt_artifact_returns_the_single_envelope(repo) -> None:
    seed_context(repo)
    append_prompt(repo, "pra_0001", '{"prompt": "a lone astronaut"}')

    artifact = repo.get_latest_prompt_artifact("ses_0001")

    assert artifact is not None
    assert artifact.artifact_id == "pra_0001"
    assert artifact.session_id == "ses_0001"
    assert artifact.payload == '{"prompt": "a lone astronaut"}'
    assert artifact.refs == {"intent_revision_id": "irev_0001", "confirmation_id": "cnf_0001"}
    assert artifact.created_at.tzinfo is not None
    assert artifact == repo.get_prompt_artifact("pra_0001")


def test_latest_prompt_artifact_picks_the_newest_created_at(repo, monkeypatch) -> None:
    seed_context(repo)
    append_prompt(repo, "pra_0001", '{"prompt": "first"}')

    # 固定时钟推进：第二行严格晚于第一行（不依赖真实时钟精度与执行速度）。
    base = datetime.fromisoformat(created_at_of(repo, "pra_0001"))
    monkeypatch.setattr(repository_module, "utc_now", lambda: base + timedelta(seconds=5))
    append_prompt(repo, "pra_0002", '{"prompt": "second"}')

    latest = repo.get_latest_prompt_artifact("ses_0001")
    assert latest is not None
    assert latest.artifact_id == "pra_0002"
    assert latest.payload == '{"prompt": "second"}'
    # 历史不覆盖：两行都在。
    assert prompt_count(repo) == 2


def test_latest_prompt_artifact_uses_rowid_to_break_created_at_ties(repo, monkeypatch) -> None:
    seed_context(repo)
    monkeypatch.setattr(repository_module, "utc_now", lambda: FIXED_TIME)

    append_prompt(repo, "pra_0001", '{"prompt": "first"}')
    append_prompt(repo, "pra_0002", '{"prompt": "second"}')
    rows = repo.connection.execute(
        "SELECT prompt_artifact_id, created_at FROM prompt_artifacts ORDER BY rowid"
    ).fetchall()
    assert [row["prompt_artifact_id"] for row in rows] == ["pra_0001", "pra_0002"]
    assert rows[0]["created_at"] == rows[1]["created_at"]  # 人为构造的并列时间

    latest = repo.get_latest_prompt_artifact("ses_0001")
    assert latest is not None
    assert latest.artifact_id == "pra_0002"  # 同一 created_at → rowid DESC 定序


def test_latest_prompt_artifact_is_isolated_per_session(repo) -> None:
    seed_context(repo, "ses_0001")
    seed_context(repo, "ses_0002", suffix="_b")
    append_prompt(repo, "pra_0001", '{"prompt": "session one"}')
    repo.append_prompt_artifact(
        "pra_0002",
        "ses_0002",
        {"intent_revision_id": "irev_0001_b", "confirmation_id": "cnf_0001_b"},
        '{"prompt": "session two"}',
    )

    first = repo.get_latest_prompt_artifact("ses_0001")
    second = repo.get_latest_prompt_artifact("ses_0002")
    assert first is not None and first.artifact_id == "pra_0001"
    assert second is not None and second.artifact_id == "pra_0002"

    repo.create_session("ses_empty")
    assert repo.get_latest_prompt_artifact("ses_empty") is None


def test_latest_prompt_artifact_for_missing_session_is_rejected(repo) -> None:
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_latest_prompt_artifact("ses_missing")
    assert excinfo.value.code == "persistence.session_not_found"


@pytest.mark.parametrize("bad_session_id", ["", None])
def test_latest_prompt_artifact_requires_a_non_empty_session_id(repo, bad_session_id) -> None:
    with pytest.raises(RepositoryError) as excinfo:
        repo.get_latest_prompt_artifact(bad_session_id)
    assert excinfo.value.code == "persistence.invalid_field"


def test_prompt_artifact_foreign_key_constraints_still_apply(repo) -> None:
    seed_context(repo)
    append_prompt(repo, "pra_0001", "{}")
    before = prompt_count(repo)

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_bad_confirmation",
            "ses_0001",
            {"intent_revision_id": "irev_0001", "confirmation_id": "cnf_missing"},
            "{}",
        )
    assert excinfo.value.code == "persistence.foreign_key_violation"

    with pytest.raises(RepositoryError) as excinfo:
        repo.append_prompt_artifact(
            "pra_bad_intent",
            "ses_0001",
            {"intent_revision_id": "irev_missing", "confirmation_id": "cnf_0001"},
            "{}",
        )
    assert excinfo.value.code == "persistence.foreign_key_violation"

    # 外键拒绝的写入不得留下任何行；最新一条仍是成功写入的那条。
    assert prompt_count(repo) == before
    latest = repo.get_latest_prompt_artifact("ses_0001")
    assert latest is not None
    assert latest.artifact_id == "pra_0001"


def test_latest_prompt_artifact_is_a_read_only_query(repo) -> None:
    """只读 getter 不得创建/修改数据：两次调用结果逐字段一致且行数不变。"""
    seed_context(repo)
    append_prompt(repo, "pra_0001", '{"prompt": "stable"}')

    first = repo.get_latest_prompt_artifact("ses_0001")
    second = repo.get_latest_prompt_artifact("ses_0001")
    assert first == second
    assert prompt_count(repo) == 1
