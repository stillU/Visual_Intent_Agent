"""Issue 与 EvidenceRef 合同测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from visual_intent_agent.domain import (
    EvidenceRef,
    IntentDelta,
    Issue,
    Resolution,
    ResolutionRecord,
    Severity,
)


def test_severity_has_exactly_three_values() -> None:
    assert {member.value for member in Severity} == {"error", "warning", "info"}
    assert Severity("error") is Severity.ERROR


def test_evidence_ref_associates_an_exact_message() -> None:
    ref = EvidenceRef(
        message_id="msg_0123456789abcdef0123456789abcdef",
        fragment="there should be two foxes",
        pending_question_id="qst_0123456789abcdef0123456789abcdef",
    )
    assert ref.message_id == "msg_0123456789abcdef0123456789abcdef"
    assert ref.fragment == "there should be two foxes"
    assert ref.pending_question_id == "qst_0123456789abcdef0123456789abcdef"

    delta = IntentDelta(
        operation="SET",
        path="subject.count",
        value=2,
        evidence_refs=[ref],
    )
    record = ResolutionRecord(resolution=Resolution.USER_SPECIFIED, evidence_refs=[ref])
    assert delta.evidence_refs[0].message_id == ref.message_id
    assert record.evidence_refs[0].pending_question_id == ref.pending_question_id


def test_evidence_ref_requires_message_id() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef()
    with pytest.raises(ValidationError):
        EvidenceRef(fragment="no message behind it")


def test_evidence_ref_optional_fields_default_to_none() -> None:
    ref = EvidenceRef(message_id="msg_1")
    assert ref.fragment is None
    assert ref.pending_question_id is None


def test_evidence_ref_round_trips() -> None:
    ref = EvidenceRef(message_id="msg_1", fragment="keep it", pending_question_id="qst_2")
    restored = EvidenceRef.model_validate_json(ref.model_dump_json())
    assert restored == ref


def test_evidence_ref_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate({"message_id": "msg_1", "confidence": 0.9})


def test_evidence_ref_is_frozen() -> None:
    ref = EvidenceRef(message_id="msg_1")
    with pytest.raises(ValidationError):
        ref.message_id = "msg_2"  # type: ignore[misc]


def test_issue_defaults_to_error_severity() -> None:
    issue = Issue(code="validation.unknown_path", message="path is not whitelisted")
    assert issue.severity is Severity.ERROR
    assert issue.path is None


def test_issue_round_trips_with_path_and_severity() -> None:
    issue = Issue(
        code="policy.missing_core_field",
        message="primary subject is still missing",
        path="subject.description",
        severity="warning",
    )
    restored = Issue.model_validate_json(issue.model_dump_json())
    assert restored == issue
    assert restored.severity is Severity.WARNING
    assert restored.path == "subject.description"


def test_issue_rejects_illegal_severity_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Issue(code="x.y", message="m", severity="fatal")
    with pytest.raises(ValidationError):
        Issue.model_validate({"code": "x.y", "message": "m", "details": "extra"})


def test_issue_requires_code_and_message() -> None:
    with pytest.raises(ValidationError):
        Issue(code="x.y")
    with pytest.raises(ValidationError):
        Issue(message="m")
