"""Step 01 加载器测试：JSONL 权威源、清单/哈希/版本校验、重复 ID、approved-only。

对应任务书验收：draft 排除、重复 ID/篡改哈希拒绝、非法路径与非法候选拒绝。
"""

from __future__ import annotations

import hashlib

import pytest
from knowledge_helpers import (
    approved_payload,
    jsonl_text,
    unit_payload,
    write_corpus,
    write_jsonl,
    write_manifest,
)

from visual_intent_agent.knowledge import (
    KNOWLEDGE_DUPLICATE_ID,
    KNOWLEDGE_EMPTY_CORPUS,
    KNOWLEDGE_FILE_MISSING,
    KNOWLEDGE_FILE_UNLISTED,
    KNOWLEDGE_HASH_MISMATCH,
    KNOWLEDGE_INVALID_UNIT,
    KNOWLEDGE_MANIFEST_INVALID,
    KNOWLEDGE_MANIFEST_MISSING,
    KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED,
    KNOWLEDGE_UNIT_COUNT_MISMATCH,
    KNOWLEDGE_VERSION_MISMATCH,
    ReviewStatus,
    load_corpus,
)


def test_loads_a_well_formed_corpus_and_filters_drafts(tmp_path):
    corpus_dir = write_corpus(
        tmp_path / "corpus",
        [
            approved_payload(knowledge_id="lighting.character.one"),
            approved_payload(
                knowledge_id="composition.framing.one",
                applicable_path="composition.framing",
                candidate_value="wide_shot",
            ),
            unit_payload(knowledge_id="camera.depth_of_field.one", applicable_path="camera.depth_of_field", candidate_value="deep"),
        ],
        corpus_version="v0.4-test-1",
    )
    corpus = load_corpus(corpus_dir)
    assert corpus.corpus_version == "v0.4-test-1"
    assert corpus.all_unit_count == 3
    built = corpus.build_units()
    assert [unit.knowledge_id for unit in built] == [
        "lighting.character.one",
        "composition.framing.one",
    ]
    assert all(unit.review_status is ReviewStatus.APPROVED for unit in built)
    assert all(
        unit.review_status is not ReviewStatus.APPROVED
        for unit in corpus.all_units
        if unit.knowledge_id == "camera.depth_of_field.one"
    )


def test_missing_manifest_is_rejected(tmp_path):
    (tmp_path / "units.jsonl").write_text(jsonl_text([unit_payload()]), encoding="utf-8")
    with pytest.raises(Exception) as excinfo:
        load_corpus(tmp_path)
    assert excinfo.value.code == KNOWLEDGE_MANIFEST_MISSING


def test_missing_corpus_directory_is_rejected(tmp_path):
    with pytest.raises(Exception) as excinfo:
        load_corpus(tmp_path / "nope")
    assert excinfo.value.code == KNOWLEDGE_MANIFEST_MISSING


def test_invalid_manifest_json_is_rejected(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception) as excinfo:
        load_corpus(tmp_path)
    assert excinfo.value.code == KNOWLEDGE_MANIFEST_INVALID


def test_manifest_traversal_path_is_rejected(tmp_path):
    write_manifest(
        tmp_path,
        [{"path": "../outside.jsonl", "sha256": "0" * 64, "unit_count": 1}],
    )
    with pytest.raises(Exception) as excinfo:
        load_corpus(tmp_path)
    assert excinfo.value.code == KNOWLEDGE_MANIFEST_INVALID


def test_unsupported_schema_version_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload()], schema_version="knowledge.v2")
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_SCHEMA_VERSION_UNSUPPORTED


def test_tokenizer_version_mismatch_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload()], tokenizer_version="keyword.v2")
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_VERSION_MISMATCH
    assert "tokenizer_version" in str(excinfo.value)


def test_retrieval_version_mismatch_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload()], retrieval_version="lexical.v9")
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_VERSION_MISMATCH
    assert "retrieval_version" in str(excinfo.value)


def test_listed_file_missing_is_rejected(tmp_path):
    write_manifest(tmp_path, [{"path": "missing.jsonl", "sha256": "0" * 64, "unit_count": 1}])
    with pytest.raises(Exception) as excinfo:
        load_corpus(tmp_path)
    assert excinfo.value.code == KNOWLEDGE_FILE_MISSING


def test_tampered_file_hash_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload()])
    with (corpus_dir / "units.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(jsonl_text([unit_payload(knowledge_id="lighting.character.extra")]))
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_HASH_MISMATCH


def test_tampered_unit_content_hash_is_rejected(tmp_path):
    """文件哈希同步更新，但单元自己的 content_hash 与正文不符 → 仍拒绝。"""
    payload = unit_payload()
    payload["content_hash"] = hashlib.sha256(b"something else").hexdigest()
    corpus_dir = write_corpus(tmp_path / "corpus", [payload])
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_INVALID_UNIT
    assert "content_hash" in str(excinfo.value)


def test_duplicate_knowledge_id_is_rejected(tmp_path):
    first = unit_payload(knowledge_id="lighting.character.dup", content="第一次出现")
    second = unit_payload(knowledge_id="lighting.character.dup", content="第二次出现")
    corpus_dir = write_corpus(tmp_path / "corpus", [first, second], unit_count=2)
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_DUPLICATE_ID


def test_duplicate_id_across_files_is_rejected(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    first_text = jsonl_text([unit_payload(knowledge_id="lighting.character.dup")])
    second_text = jsonl_text(
        [
            unit_payload(
                knowledge_id="lighting.character.dup",
                applicable_path="composition.framing",
                candidate_value="close_up",
                source={"source_type": "project_original", "title": "t", "url": None, "repository_path": "b.jsonl", "locator": "l", "source_revision": "v1", "source_date": None, "license": "internal", "original_declaration": "own"},
            )
        ]
    )
    (corpus_dir / "a.jsonl").write_text(first_text, encoding="utf-8")
    (corpus_dir / "b.jsonl").write_text(second_text, encoding="utf-8")
    write_manifest(
        corpus_dir,
        [
            {"path": "a.jsonl", "sha256": hashlib.sha256(first_text.encode()).hexdigest(), "unit_count": 1},
            {"path": "b.jsonl", "sha256": hashlib.sha256(second_text.encode()).hexdigest(), "unit_count": 1},
        ],
    )
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_DUPLICATE_ID


def test_unlisted_jsonl_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload()])
    (corpus_dir / "smuggled.jsonl").write_text(jsonl_text([unit_payload()]), encoding="utf-8")
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_FILE_UNLISTED


def test_malformed_jsonl_line_is_rejected(tmp_path):
    text = "{not json}\n"
    corpus_dir = write_jsonl(tmp_path / "corpus", "units.jsonl", text)
    write_manifest(
        corpus_dir,
        [{"path": "units.jsonl", "sha256": hashlib.sha256(text.encode()).hexdigest(), "unit_count": 1}],
    )
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_INVALID_UNIT


def test_non_object_jsonl_line_is_rejected(tmp_path):
    text = "[]\n"
    corpus_dir = write_jsonl(tmp_path / "corpus", "units.jsonl", text)
    write_manifest(
        corpus_dir,
        [{"path": "units.jsonl", "sha256": hashlib.sha256(text.encode()).hexdigest(), "unit_count": 1}],
    )
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_INVALID_UNIT


def test_illegal_path_in_corpus_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload(applicable_path="color.palette")])
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_INVALID_UNIT


def test_illegal_candidate_in_corpus_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload(candidate_value="harsh")])
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_INVALID_UNIT
    assert "authorized candidate" in str(excinfo.value)


def test_forged_approval_in_corpus_is_rejected(tmp_path):
    payload = approved_payload()
    payload["reviewer"] = None
    corpus_dir = write_corpus(tmp_path / "corpus", [payload])
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_INVALID_UNIT
    assert "reviewer and reviewed_at" in str(excinfo.value)


def test_unit_count_mismatch_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload()], unit_count=5)
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_UNIT_COUNT_MISMATCH


def test_empty_corpus_is_rejected(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [], unit_count=0)
    with pytest.raises(Exception) as excinfo:
        load_corpus(corpus_dir)
    assert excinfo.value.code == KNOWLEDGE_EMPTY_CORPUS


def test_blank_lines_are_ignored(tmp_path):
    text = "\n" + jsonl_text([unit_payload()]) + "\n\n"
    corpus_dir = write_jsonl(tmp_path / "corpus", "units.jsonl", text)
    write_manifest(
        corpus_dir,
        [{"path": "units.jsonl", "sha256": hashlib.sha256(text.encode()).hexdigest(), "unit_count": 1}],
    )
    corpus = load_corpus(corpus_dir)
    assert corpus.all_unit_count == 1


def test_loading_is_read_only(tmp_path):
    corpus_dir = write_corpus(tmp_path / "corpus", [unit_payload()])
    before = sorted(p.name for p in corpus_dir.iterdir())
    load_corpus(corpus_dir)
    after = sorted(p.name for p in corpus_dir.iterdir())
    assert before == after


def test_error_carries_a_namespaced_code(tmp_path):
    with pytest.raises(Exception) as excinfo:
        load_corpus(tmp_path)
    assert excinfo.value.code.startswith("knowledge.")
    assert str(excinfo.value).startswith("[knowledge.")