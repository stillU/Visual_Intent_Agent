"""ID 与时间戳工具（ARCHITECTURE.md 5.1 / 5.2）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from visual_intent_agent.domain import Id, new_id, utc_now

FROZEN_ID_PREFIXES = ("ses", "msg", "irev", "erev", "cnf", "qst", "pra", "gen", "fbk", "rlz")


def test_id_alias_is_str() -> None:
    assert Id is str


def test_new_id_shape_is_prefix_underscore_uuid4_hex() -> None:
    value = new_id("ses")
    prefix, separator, suffix = value.partition("_")
    assert separator == "_"
    assert prefix == "ses"
    assert len(suffix) == 32
    int(suffix, 16)  # 必须是合法 hex


def test_new_id_is_unique_across_frozen_prefixes() -> None:
    generated = [new_id(prefix) for prefix in FROZEN_ID_PREFIXES for _ in range(20)]
    assert len(set(generated)) == len(generated)
    for prefix in FROZEN_ID_PREFIXES:
        assert any(value.startswith(f"{prefix}_") for value in generated)


def test_utc_now_returns_timezone_aware_utc() -> None:
    before = utc_now()
    after = utc_now()
    assert before.tzinfo is not None
    assert before.utcoffset() == timedelta(0)
    assert after >= before


def test_utc_now_round_trips_through_isoformat() -> None:
    now = utc_now()
    assert datetime.fromisoformat(now.isoformat()) == now
