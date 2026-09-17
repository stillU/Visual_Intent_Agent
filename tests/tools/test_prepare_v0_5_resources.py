"""v0.5 Step 01 数据集准备工具（``tools/prepare_v0_5_resources.py``）的离线测试。

覆盖：

- 锁定 sha256 / bytes / nonempty_rows 校验与"不改写期望值"；
- 路径逃逸拒绝与 tar 安全检查（绝对路径 / ``..`` / 链接 / 成员数 / 解包大小）；
- AppleDouble ``._*`` 与 ``.py`` 脚本跳过，只提取文本 / 许可；
- COCO-CN 真实字段 ``<image_id>#<序号>\\t<中文>`` 解析，只取人工撰写子集；
- 相同原文 / 同图 ID 分组的整组抽样与开发 / 保留不泄漏；
- 项目派生工程样例：三路径 × 十状态、原文不改、标注显式属于项目；
- 端到端离线：写全派生产物且不删除并发的 ``knowledge_review/`` 目录。

全部离线：网络读取一律被 ``offline=True`` 或注入的 opener 拦截。
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from tools.prepare_v0_5_resources import (
    PATH_CANDIDATES,
    PROJECT_COVERAGE_TAGS,
    Limits,
    ResourceError,
    Sample,
    UnsafeArchiveError,
    _coco_data_allowed,
    _safe_relative,
    assign_splits,
    build_coco_samples,
    build_project_derived_samples,
    count_nonempty_rows,
    discover_coco_subsets,
    group_samples,
    inspect_tar,
    load_lock,
    parse_coco_caption_file,
    prepare,
    safe_extract_coco,
    sample_groups,
    sha256_bytes,
    sha256_file,
    sort_key_for,
    verify_locked_file,
)

STAMP = "20260916"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(path: Path, data: bytes | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return path


def _make_tar(path: Path, members: dict[str, bytes], links: dict[str, str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            tar.addfile(info)
    return path


def _sample(dataset_id: str, original_id, text: str, *, image_id: str = "", note: str = "") -> Sample:
    return Sample(
        dataset_id=dataset_id,
        revision="r1",
        source_file=f"raw/{dataset_id}/f.txt",
        original_id=original_id,
        group_id="",
        language="zh" if dataset_id == "coco_cn" else "en",
        original_text=text,
        supported_scope=["x"],
        unsupported_constraints=[],
        license="MIT",
        source_note=note,
        image_id=image_id,
        _sort_key=sort_key_for(dataset_id, original_id),
    )


# ---------------------------------------------------------------------------
# hashing / lock verification
# ---------------------------------------------------------------------------


def test_sort_key_matches_documented_formula() -> None:
    import hashlib

    assert sort_key_for("coco_cn", "COCO_val2014_000000043734#0") == hashlib.sha256(
        b"20260916|coco_cn|COCO_val2014_000000043734#0"
    ).hexdigest()


def test_verify_locked_file_statuses(tmp_path: Path) -> None:
    path = _write(tmp_path / "f.txt", b"alpha\n\nbeta\n")
    good = {
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "nonempty_rows": count_nonempty_rows(path),
    }
    assert good["nonempty_rows"] == 2
    assert verify_locked_file(path, good)["status"] == "verified"
    assert verify_locked_file(path, {**good, "sha256": "0" * 64})["status"] == "hash_mismatch"
    assert verify_locked_file(path, {**good, "bytes": 1})["status"] == "bytes_mismatch"
    assert verify_locked_file(path, {**good, "nonempty_rows": 99})["status"] == "rows_mismatch"
    assert verify_locked_file(path, {**good, "sha256": None})["status"] == "missing_expected"


def test_safe_relative_rejects_unsafe_paths() -> None:
    for bad in ("/etc/passwd", "..\\evil", "a/../../b", "", "C:/x"):
        with pytest.raises(ResourceError):
            _safe_relative(bad, label="t")
    assert str(_safe_relative("examples/dataset/color_val.txt", label="t")) == (
        "examples/dataset/color_val.txt"
    )


def test_load_lock_rejects_wrong_schema(tmp_path: Path) -> None:
    path = _write(tmp_path / "lock.json", json.dumps({"schema": "nope", "datasets": [{"id": "x"}]}).encode())
    with pytest.raises(ResourceError):
        load_lock(path)


# ---------------------------------------------------------------------------
# COCO-CN field parsing
# ---------------------------------------------------------------------------


def test_coco_caption_parse_tab_and_whitespace_forms(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "imageid.human-written-caption.txt",
        "COCO_val2014_000000000001#0\t一只狗\n"
        "COCO_val2014_000000000002#0\t两只猫\n"
        "COCO_val2014_000000000003#0 三只鸟\n"
        "no-hash-line-ignored\n"
        "\n",
    )
    records, gaps = parse_coco_caption_file(path)
    assert gaps == []
    assert [r["image_id"] for r in records] == [
        "COCO_val2014_000000000001",
        "COCO_val2014_000000000002",
        "COCO_val2014_000000000003",
    ]
    assert [r["original_id"] for r in records][0] == "COCO_val2014_000000000001#0"
    assert records[0]["text"] == "一只狗"


def test_coco_data_allowed_skips_appledouble_and_script() -> None:
    assert _coco_data_allowed("d/imageid.human-written-caption.txt") is True
    assert _coco_data_allowed("d/LICENSE") is True
    assert _coco_data_allowed("d/README.md") is True
    assert _coco_data_allowed("d/._imageid.human-written-caption.txt") is False
    assert _coco_data_allowed("._top-level") is False
    assert _coco_data_allowed("d/verify_data.py") is False
    assert _coco_data_allowed("d/image.jpg") is False


def test_discover_coco_subsets_parses_only_used_subset(tmp_path: Path) -> None:
    root = tmp_path / "extracted"
    _write(
        root / "coco/imageid.human-written-caption.txt",
        "COCO_val2014_000000000001#0\t一只狗\nCOCO_val2014_000000000002#0\t两只猫\n",
    )
    _write(
        root / "coco/imageid.machine-translated-caption.bosonseg.txt",
        "COCO_val2014_000000000001#zhbDec#0 一:m 只:q 狗:n\n",
    )
    _write(root / "coco/._imageid.human-written-caption.txt", b"\x00\x01binary")
    report = discover_coco_subsets(root)
    used = next(e for e in report["subsets"] if e["used_for_samples"])
    assert used["present"] is True
    assert used["records"] == 2
    assert used["distinct_image_ids"] == 2
    machine = next(e for e in report["subsets"] if e["label"] == "machine_translated_caption_wordseg")
    assert machine["present"] is True
    assert machine["nonempty_rows"] == 1
    assert "records" not in machine


def test_build_coco_samples_uses_human_written_subset_only(tmp_path: Path) -> None:
    root = tmp_path / "extracted"
    _write(root / "imageid.human-written-caption.txt", "COCO_val2014_000000000009#0\t一名男子在跑步\n")
    _write(root / "imageid.machine-translated-caption.bosonseg.txt", "COCO_val2014_000000000009#zhbDec#0 男子:n\n")
    report = discover_coco_subsets(root)
    dataset = {"id": "coco_cn", "revision": "rev", "license": "MIT"}
    samples, gaps = build_coco_samples(root, dataset, tmp_path, report)
    assert gaps == []
    assert len(samples) == 1
    assert samples[0].original_text == "一名男子在跑步"
    assert samples[0].language == "zh"
    assert samples[0].image_id == "COCO_val2014_000000000009"
    assert samples[0].source_note == "subset=human_written_caption"


# ---------------------------------------------------------------------------
# tar safety
# ---------------------------------------------------------------------------


def test_inspect_tar_rejects_traversal_absolute_and_links(tmp_path: Path) -> None:
    limits = Limits()
    bad_paths = {
        "traversal": "../evil.txt",
        "absolute": "/tmp/evil.txt",
        "link": "link.txt",
    }
    for name, member in bad_paths.items():
        links = {"link.txt": "/etc/passwd"} if name == "link" else None
        archive = _make_tar(tmp_path / f"{name}.tar.gz", {member: b"x"}, links=links)
        with pytest.raises(UnsafeArchiveError):
            inspect_tar(archive, limits)


def test_inspect_tar_rejects_member_count(tmp_path: Path) -> None:
    archive = _make_tar(tmp_path / "many.tar.gz", {f"f{i}.txt": b"x" for i in range(5)})
    with pytest.raises(UnsafeArchiveError):
        inspect_tar(archive, Limits(max_tar_members=3))


def test_safe_extract_extracts_text_and_skips_metadata(tmp_path: Path) -> None:
    archive = _make_tar(
        tmp_path / "coco.tar.gz",
        {
            "coco/imageid.human-written-caption.txt": "COCO_val2014_000000000001#0\t一只狗\n".encode(),
            "coco/LICENSE": b"MIT\n",
            "coco/._imageid.human-written-caption.txt": b"\x00\x01",
            "coco/verify_data.py": b"print('must never run')\n",
            "coco/image.jpg": b"\xff\xd8\xff",
        },
    )
    dest = tmp_path / "out"
    result = safe_extract_coco(archive, dest, Limits())
    extracted = set(result["extracted"])
    assert "coco/imageid.human-written-caption.txt" in extracted
    assert "coco/LICENSE" in extracted
    assert not any(name.endswith(".py") for name in extracted)
    assert not any("._" in Path(name).name for name in extracted)
    assert not (dest / "coco" / "verify_data.py").exists()
    assert not (dest / "coco" / "image.jpg").exists()


# ---------------------------------------------------------------------------
# grouping / sampling / splitting
# ---------------------------------------------------------------------------


def test_group_samples_unions_same_image_and_same_text() -> None:
    records = [
        _sample("coco_cn", "A#0", "一只狗", image_id="A"),
        _sample("coco_cn", "A#1", "一只小狗", image_id="A"),
        _sample("coco_cn", "B#0", "一只狗", image_id="B"),  # same text as A#0
        _sample("coco_cn", "C#0", "一只猫", image_id="C"),
    ]
    groups = group_samples(records)
    by_id = {r.original_id: r.group_id for r in records}
    # A#0/A#1 share an image; A#0/B#0 share the exact text. The three therefore form
    # one parent group, and the repeated text is collapsed inside it.
    merged = next(g for g in groups if by_id["A#1"] == g["group_id"])
    assert merged["group_id"] != ""
    assert len(merged["members"]) == 2
    assert len(merged["duplicates_removed"]) == 1
    assert merged["duplicates_removed"][0] in {"A#0", "B#0"}
    assert merged["group_id"] in {by_id["A#0"], by_id["B#0"]}
    assert by_id["C#0"] not in ("", merged["group_id"])


def test_sample_groups_is_deterministic_and_respects_quota() -> None:
    records = [_sample("coco_cn", f"COCO_val2014_{i:012d}#0", f"描述{i}") for i in range(25)]
    first, gaps = sample_groups(list(records), "coco_cn", stamp=STAMP)
    assert len(first) == 20
    assert gaps == []
    shuffled = list(reversed(records))
    second, _ = sample_groups(shuffled, "coco_cn", stamp=STAMP)
    assert [r.original_id for r in first] == [r.original_id for r in second]


def test_assign_splits_keeps_groups_together_and_is_about_70_30() -> None:
    records = []
    oid = 0
    for image in range(10):
        for caption in range(2):
            oid += 1
            records.append(
                _sample("coco_cn", f"img{image}#{caption}", f"描述{oid}", image_id=f"img{image}")
            )
    records, manifest = assign_splits(records)
    by_group: dict[str, set[str]] = {}
    for record in records:
        by_group.setdefault(record.group_id, set()).add(record.split)
    assert all(len(splits) == 1 for splits in by_group.values())
    assert manifest["development"] == 14
    assert manifest["holdout"] == 6


# ---------------------------------------------------------------------------
# project-derived engineering samples
# ---------------------------------------------------------------------------


def _has_key(obj: object, name: str) -> bool:
    if isinstance(obj, dict):
        return name in obj or any(_has_key(value, name) for value in obj.values())
    if isinstance(obj, list):
        return any(_has_key(value, name) for value in obj)
    return False


def test_project_derived_samples_shape_and_origins() -> None:
    pool = [
        _sample("geneval", i, f"text {i}", note="tag=single_object")
        for i in range(1, 40)
    ]
    for record in pool:
        record.split = "development"
    derived = build_project_derived_samples(pool)
    assert len(derived) == len(PATH_CANDIDATES) * len(PROJECT_COVERAGE_TAGS) == 30
    assert {d["knowledge_path"] for d in derived} == set(PATH_CANDIDATES)
    assert {d["coverage_tag"] for d in derived} == set(PROJECT_COVERAGE_TAGS)
    for item in derived:
        assert item["staging_only"] is True
        assert item["runtime_schema_change"] is False
        annotations = item["project_added_annotations"]
        assert annotations["added_by"] == "project"
        assert annotations["benchmark_metadata_reused_as_user_expression"] is False
        assert "expected_disposition" in annotations
        assert not _has_key(annotations, "reviewer")
        assert not _has_key(annotations, "reviewed_at")
        parent = next(
            r for r in pool
            if r.dataset_id == item["source_sample"]["dataset_id"]
            and r.original_id == item["source_sample"]["original_id"]
        )
        assert item["original_text"] == parent.original_text
        assert item["source_sample"]["split"] == "development"


def test_project_annotation_pinned_sets_pin_path() -> None:
    from tools.prepare_v0_5_resources import _project_annotation

    pinned = _project_annotation("lighting.character", "pinned")
    assert pinned["pinned_paths"] == ["lighting.character"]
    delegated = _project_annotation("lighting.character", "delegated")
    assert delegated["resolutions"] == {"lighting.character": "user_delegated"}
    assert delegated["confirmed_facets"] == {}


def test_project_derived_records_are_marked_historical_not_current_acceptance() -> None:
    """Step 01 派生样例必须带明确历史/计划阶段，不得无据声称当前生产知识为零。"""
    pool = [
        _sample("geneval", i, f"text {i}", note="tag=single_object")
        for i in range(1, 40)
    ]
    for record in pool:
        record.split = "development"
    derived = build_project_derived_samples(pool)

    for item in derived:
        annotations = item["project_added_annotations"]
        assert annotations["material_stage"] == "historical_pre_review"
        assert annotations["current_release_input"] is False
        assert "v0.5-approved-1" in annotations["knowledge_state_current"]
        assert "9 draft / 0 approved" in annotations["knowledge_state_basis"]
        assert "Step 01" in annotations["stage_note"]
        assert "historical" in item["assertions_status"].lower()
        assert "not counted as executed coverage" in item["assertions_status"]

    adoption = next(
        d for d in derived if d["coverage_tag"] == "successful_adoption"
    )
    disposition = adoption["project_added_annotations"]["expected_disposition"]
    # 历史记录可以陈述当时的 BLOCKED 状态，但必须说明已被真实审核取代，不能当作当前状态。
    assert "v0.5-approved-1" in disposition
    assert "v0_5_acceptance_cases.json" in disposition

    legacy = next(d for d in derived if d["coverage_tag"] == "unapproved_knowledge")
    legacy_disposition = legacy["project_added_annotations"]["expected_disposition"]
    assert "5 approved" in legacy_disposition
    assert "not a claim about the current corpus" in legacy_disposition


# ---------------------------------------------------------------------------
# end to end (fully offline)
# ---------------------------------------------------------------------------


def _build_synthetic_staging(tmp_path: Path) -> tuple[Path, Path, Path]:
    staging = tmp_path / "staging"
    # a concurrent Step 02 directory that must survive untouched
    decoy = staging / "knowledge_review" / "keep.md"
    _write(decoy, b"another agent's work\n")

    geneval = b'{"prompt": "a photo of a dog", "tag": "single_object"}\n{"prompt": "two cats", "tag": "counting"}\n'
    color = b"a red bench\n"
    shape = b"a round table\n"
    geneval_path = _write(staging / "raw" / "geneval" / "prompts" / "evaluation_metadata.jsonl", geneval)
    color_path = _write(
        staging / "raw" / "t2i_compbench_original" / "examples" / "dataset" / "color_val.txt", color
    )
    shape_path = _write(
        staging / "raw" / "t2i_compbench_original" / "examples" / "dataset" / "shape_val.txt", shape
    )

    archive = _make_tar(
        staging / "raw" / "coco_cn" / "coco.tar.gz",
        {
            "coco/imageid.human-written-caption.txt": (
                "COCO_val2014_000000000001#0\t一只狗在草地上\n"
                "COCO_val2014_000000000002#0\t两只猫在沙发上\n"
            ).encode(),
            "coco/._imageid.human-written-caption.txt": b"\x00\x01",
        },
    )

    lock = {
        "schema": "resource-handoff.v1",
        "verified_on": "2026-09-16",
        "datasets": [
            {
                "id": "geneval",
                "revision": "rev-g",
                "license": "MIT",
                "files": [
                    {
                        "path": "prompts/evaluation_metadata.jsonl",
                        "url": "file:///never/used",
                        "bytes": len(geneval),
                        "nonempty_rows": 2,
                        "sha256": sha256_bytes(geneval),
                    }
                ],
            },
            {
                "id": "t2i_compbench_original",
                "revision": "rev-c",
                "license": "MIT",
                "files": [
                    {
                        "path": "examples/dataset/color_val.txt",
                        "url": "file:///never/used",
                        "bytes": len(color),
                        "nonempty_rows": 1,
                        "sha256": sha256_bytes(color),
                    },
                    {
                        "path": "examples/dataset/shape_val.txt",
                        "url": "file:///never/used",
                        "bytes": len(shape),
                        "nonempty_rows": 1,
                        "sha256": sha256_bytes(shape),
                    },
                ],
            },
            {
                "id": "coco_cn",
                "revision": "rev-cc",
                "license": "MIT (archive terms provisional)",
                "files": [
                    {
                        "path": "coco.tar.gz",
                        "url": "file:///never/used",
                        "status": "metadata_verified_archive_not_downloaded",
                        "bytes": None,
                        "nonempty_rows": None,
                        "sha256": None,
                    }
                ],
            },
        ],
    }
    lock_path = tmp_path / "sources.lock.json"
    _write(lock_path, json.dumps(lock).encode())
    return staging, lock_path, archive


def test_end_to_end_offline_writes_all_artifacts_and_preserves_concurrent_dir(tmp_path: Path) -> None:
    staging, lock_path, archive = _build_synthetic_staging(tmp_path)
    record = prepare(
        lock_path,
        staging,
        Limits(),
        coco_archive=archive,
        offline=True,
    )
    totals = record["totals"]
    assert totals["samples"] == 6  # 2 GenEval + 2 CompBench + 2 COCO-CN (all synthetic)
    assert totals["development"] + totals["holdout"] == 6
    assert totals["sources_locked_verified"] == 2
    assert totals["sources_archive_extracted_unlocked"] == 1
    assert totals["project_derived_samples"] == 30
    derived = staging / "derived"
    for name in (
        "download_record.json",
        "source_samples.jsonl",
        "split_manifest.json",
        "coco_cn_field_report.json",
        "project_derived_samples.jsonl",
    ):
        assert (derived / name).is_file(), name
    assert (staging / "README.md").is_file()
    # concurrent Step 02 directory untouched
    assert (staging / "knowledge_review" / "keep.md").read_bytes() == b"another agent's work\n"
    rows = [json.loads(line) for line in (derived / "source_samples.jsonl").read_text().splitlines()]
    assert len(rows) == totals["samples"]
    assert {r["split"] for r in rows} <= {"development", "holdout"}
    coco_rows = [r for r in rows if r["dataset_id"] == "coco_cn"]
    assert len(coco_rows) == 2
    assert all(r["language"] == "zh" for r in coco_rows)
    # no image or script bytes leaked into the extracted tree
    extracted = list((staging / "extracted").rglob("*"))
    assert all(p.suffix != ".py" for p in extracted if p.is_file())
