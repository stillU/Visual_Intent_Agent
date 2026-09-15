"""MVP v0.3 Step 04：图片 A/B 盲评工具链离线测试（全离线，不触网、不写真实目录）。

覆盖：

- `package`：pair 数、缺图侧 missing_data、匿名化强断言（盲评目录文件名与文本均不含
  系统名/case_id/轮次内部 ID/Prompt 片段/artifact 路径/解盲线索）、同种子逐字节可重现、
  不同种子左右分布非退化、非 L4 重复被排除、解盲映射不在盲评目录内；
- `ingest`：合法评分落盘、未知 pair/越界分数/缺评审人 ID 拒绝、部分评分显式报缺；
- `report`：分布计数、偏好解盲归位（左为 A 与左为 B 各验证）、missing_data 留在分母侧
  显式列出、逐轮保留、failed 显式列出、追溯字段（sample_id↔case/turn/system/sha256）齐全、
  维度结构不适用（not_applicable）与 failed 不静默。

全部合成产物写入 pytest `tmp_path`，绝不触碰仓库内 `evaluation/` 与 `outputs/`。
"""

from __future__ import annotations

import ast
import json
import re
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import image_review
from evaluation.image_review import (
    DIMENSIONS,
    ImageReviewError,
    build_layer4_report_payload,
    ingest_scores,
    package_run,
    report_run,
    resolve_run_id,
    validate_scores_payload,
)
from evaluation.reporting import (
    ArtifactRef,
    EvalCaseRecord,
    EvalFailureRecord,
    EvalProviderSnapshot,
    EvalRunRecord,
    EvalTurnRecord,
    make_eval_record_id,
    write_case_jsonl,
    write_json,
)

FIXED_EPOCH = datetime(2026, 2, 1, tzinfo=timezone.utc)
RUN_ID = "erun_synth0000000001"
L4_REPETITION = 1
REPETITIONS = 2


# ---------------------------------------------------------------------------
# 合成 PNG / 合成 Step 03 运行目录
# ---------------------------------------------------------------------------


def make_png(color: tuple[int, int, int], *, width: int = 4, height: int = 4) -> bytes:
    """构造一张最小合法 PNG（确定性字节；不需要 Pillow）。"""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _case_spec(
    case_id: str,
    turn_type: str,
    scenario: str,
    turns: list[tuple[str, str, str]],
    *,
    l4_images: dict[str, dict[str, int]],
    non_l4_images: dict[str, dict[str, int]] | None = None,
    failed_turns: dict[str, str] | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "turn_type": turn_type,
        "scenario": scenario,
        "turns": turns,
        "l4_images": l4_images,
        "non_l4_images": non_l4_images or {},
        "failed_turns": failed_turns or {},
    }


def _default_specs() -> list[dict]:
    return [
        _case_spec(
            "case-alpha",
            "single",
            "single_turn_complete",
            [("t1", "user_message", "画一只橘猫，室内客厅，中景，柔和暖光。")],
            l4_images={"baseline_a": {"t1": 1}, "system_b": {"t1": 1}},
            non_l4_images={"baseline_a": {"t1": 1}, "system_b": {"t1": 1}},
        ),
        _case_spec(
            "case-beta",
            "multi",
            "consecutive_modifications",
            [
                ("t1", "user_message", "画一只柴犬，室内 loft，全景构图。"),
                ("t2", "image_feedback", "把光线换成戏剧性侧光。"),
            ],
            l4_images={"baseline_a": {"t1": 1, "t2": 1}, "system_b": {"t2": 1}},
        ),
        _case_spec(
            "case-gamma",
            "multi",
            "vague_delegation",
            [("t1", "user_message", "随便来一张好看的图。")],
            l4_images={},
        ),
        _case_spec(
            "case-delta",
            "multi",
            "single_field_modification",
            [
                ("t1", "user_message", "画一只橘猫坐姿，室内书房，中景，台灯光。"),
                ("t2", "image_feedback", "构图换成特写。"),
            ],
            l4_images={"baseline_a": {"t1": 1, "t2": 1}, "system_b": {"t1": 1, "t2": 1}},
        ),
        _case_spec(
            "case-epsilon",
            "single",
            "explicit_delegation",
            [("t1", "user_message", "画一只白色波斯猫，复古客厅。")],
            l4_images={"baseline_a": {"t1": 1}, "system_b": {"t1": 1}},
        ),
        _case_spec(
            "case-zeta",
            "single",
            "conflicting_requirements",
            [("t1", "user_message", "画一只黑猫，户外海边，柔和光线。")],
            l4_images={"baseline_a": {"t1": 1}, "system_b": {"t1": 1}},
        ),
        _case_spec(
            "case-eta",
            "single",
            "vague_image_feedback",
            [("t1", "user_message", "画一只金毛犬趴姿，小木屋客厅。")],
            l4_images={"baseline_a": {"t1": 1}},
            failed_turns={"system_b": "t1"},
        ),
    ]


def _annotation_dims(case_id: str, turn_ids: list[str]) -> list[dict]:
    dims: list[dict] = [
        {"dimension": "intent_alignment", "focus": f"{case_id} 终图应呈现主要主体与场景。"},
        {"dimension": "user_preference", "focus": "与 Baseline A 终图成对偏好比较"},
    ]
    if case_id == "case-delta":
        dims = [
            {"dimension": "intent_alignment", "focus": "终图应呈现：橘猫、书房、柔和台灯光。"},
            {
                "dimension": "attribute_preservation",
                "focus": "t1→t2 除构图（视角变近）外，猫的外观、书房环境、风格、光线应保持",
                "applies_to_turns": ["t1", "t2"],
            },
            {
                "dimension": "edit_success",
                "focus": "t2 后构图应从“中景”变为“特写”",
                "applies_to_turns": ["t2"],
            },
            {"dimension": "user_preference", "focus": "与 Baseline A 终图成对偏好比较"},
        ]
    return dims


def _write_annotations(path: Path, specs: list[dict]) -> Path:
    rows = []
    for spec in specs:
        rows.append(
            {
                "case_id": spec["case_id"],
                "dataset_version": "synthetic_v1",
                "scenario": spec["scenario"],
                "annotation_basis": "synthetic",
                "turn_annotations": [
                    {"turn_id": turn_id} for turn_id, _, _ in spec["turns"]
                ],
                "image_evaluation_dimensions": _annotation_dims(
                    spec["case_id"], [turn_id for turn_id, _, _ in spec["turns"]]
                ),
                "prompt_expectations": None,
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def _write_dataset(path: Path, specs: list[dict]) -> Path:
    rows = []
    for spec in specs:
        rows.append(
            {
                "case_id": spec["case_id"],
                "dataset_version": "synthetic_v1",
                "scenario": spec["scenario"],
                "turn_type": spec["turn_type"],
                "turns": [
                    {"turn_id": turn_id, "kind": kind, "user_text": text}
                    for turn_id, kind, text in spec["turns"]
                ],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def _provider_snapshot() -> EvalProviderSnapshot:
    return EvalProviderSnapshot(
        llm_model="fake-llm",
        image_model="fake-image",
        llm_timeout_seconds=60.0,
        image_timeout_seconds=120.0,
        http_max_retries=2,
        image_size="1024x1024",
        images_per_generation=1,
        base_url_env="VIA_PROVIDER_BASE_URL",
        api_key_env="VIA_PROVIDER_API_KEY",
    )


def _image_rel_path(case_id: str, system: str, turn_index: int, repetition: int, index: int) -> str:
    if system == "baseline_a":
        return (
            f"baseline_a/rep{repetition}/cases/{case_id}/images/"
            f"turn_{turn_index:02d}_image_{index}.png"
        )
    return (
        f"system_b/rep{repetition}/cases/{case_id}/images/"
        f"gen_{case_id}_turn{turn_index}_{index}/image_{index}.png"
    )


def _image_color(case_id: str, system: str, turn_index: int, repetition: int, index: int) -> tuple[int, int, int]:
    seed = f"{case_id}|{system}|{turn_index}|{repetition}|{index}"
    raw = zlib.crc32(seed.encode("utf-8"))
    return (raw % 256, (raw // 256) % 256, (raw // 65536) % 256)


def _build_images(
    run_dir: Path,
    spec: dict,
    repetition: int,
    *,
    designated: bool,
) -> dict[str, dict[str, list[ArtifactRef]]]:
    """落盘某次重复的图片并返回 `system -> turn_id -> [ArtifactRef]`。"""
    turn_index_by_id = {
        turn_id: index for index, (turn_id, _, _) in enumerate(spec["turns"], start=1)
    }
    image_spec = spec["l4_images"] if designated else spec["non_l4_images"]
    refs_by_system: dict[str, dict[str, list[ArtifactRef]]] = {"baseline_a": {}, "system_b": {}}
    for system, turns in image_spec.items():
        for turn_id, count in turns.items():
            turn_index = turn_index_by_id[turn_id]
            for index in range(1, count + 1):
                rel = _image_rel_path(spec["case_id"], system, turn_index, repetition, index)
                data = make_png(_image_color(spec["case_id"], system, turn_index, repetition, index))
                target = run_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                kind = "baseline_image" if system == "baseline_a" else "image_file"
                refs_by_system[system].setdefault(turn_id, []).append(
                    ArtifactRef(
                        artifact_ref_id=make_eval_record_id(
                            "eart", RUN_ID, spec["case_id"], system, str(repetition), turn_id, str(index)
                        ),
                        kind=kind,
                        ref_id=f"img_{system}_{turn_id}_{index}",
                        path=rel,
                        sha256=image_review.sha256_bytes(data),
                        designated_for_l4=designated,
                    )
                )
    return refs_by_system


def build_offline_env(tmp_path: Path, specs: list[dict] | None = None) -> SimpleNamespace:
    """构造一个完整可用的合成 Step 03 运行目录 + 标注/fixture + 注入式产物根。"""
    specs = specs if specs is not None else _default_specs()
    run_dir = tmp_path / "runs_src" / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)

    all_case_records: list[EvalCaseRecord] = []
    all_turn_records: list[EvalTurnRecord] = []
    for spec in specs:
        per_rep_cases: list[EvalCaseRecord] = []
        per_rep_turns: list[EvalTurnRecord] = []
        for repetition in (1, REPETITIONS):
            designated = repetition == L4_REPETITION
            image_refs = _build_images(run_dir, spec, repetition, designated=designated)
            turn_records: list[EvalTurnRecord] = []
            for turn_index, (turn_id, kind, user_text) in enumerate(spec["turns"], start=1):
                for system in ("baseline_a", "system_b"):
                    failed = spec["failed_turns"].get(system) == turn_id
                    error = (
                        EvalFailureRecord(
                            code="provider.timeout",
                            message="合成失败：Provider 超时",
                            stage="image",
                        )
                        if failed
                        else None
                    )
                    turn_records.append(
                        EvalTurnRecord(
                            record_id=make_eval_record_id(
                                "eturn", RUN_ID, spec["case_id"], system, str(repetition), turn_id
                            ),
                            run_id=RUN_ID,
                            case_id=spec["case_id"],
                            system=system,
                            repetition=repetition,
                            turn_id=turn_id,
                            turn_index=turn_index,
                            turn_kind=kind,
                            input_text=user_text,
                            input_source="user_text",
                            status="failed" if failed else "completed",
                            error=error,
                            prompt_text=f"PROMPT-SECRET-{spec['case_id']}-{turn_id}-{system}",
                            prompt_sha256="0" * 64,
                            artifact_refs=list(image_refs[system].get(turn_id, [])),
                            created_at=FIXED_EPOCH,
                        )
                    )
            for system in ("baseline_a", "system_b"):
                has_failure = any(
                    spec["failed_turns"].get(system) == turn_id for turn_id, _, _ in spec["turns"]
                )
                per_rep_cases.append(
                    EvalCaseRecord(
                        record_id=make_eval_record_id(
                            "ecase", RUN_ID, spec["case_id"], system, str(repetition)
                        ),
                        run_id=RUN_ID,
                        case_id=spec["case_id"],
                        system=system,
                        repetition=repetition,
                        scenario=spec["scenario"],
                        turn_type=spec["turn_type"],
                        status="failed" if has_failure else "completed",
                        l1_failed=False if system == "system_b" else None,
                        turn_record_ids=[
                            record.record_id for record in turn_records if record.system == system
                        ],
                        artifact_refs=[],
                        error=(
                            EvalFailureRecord(
                                code="provider.timeout",
                                message="合成失败：Provider 超时",
                                stage="image",
                            )
                            if has_failure
                            else None
                        ),
                        created_at=FIXED_EPOCH,
                    )
                )
            per_rep_turns.extend(turn_records)
        write_case_jsonl(
            run_dir / "cases" / f"{spec['case_id']}.jsonl",
            per_rep_cases,
            per_rep_turns,
            [],
        )
        all_case_records.extend(per_rep_cases)
        all_turn_records.extend(per_rep_turns)

    run = EvalRunRecord(
        run_id=RUN_ID,
        harness_version="evaluation_harness_v1",
        id_scheme_version="eval_ids_sha256_v1",
        code_version="synthetic",
        run_nonce="synth",
        protocol_version="v0_3",
        dataset_version="synthetic_v1",
        config_version="synthetic_config_v1",
        manifest_version="synthetic_manifest_v1",
        dataset_sha256="1" * 64,
        annotations_sha256="2" * 64,
        config_sha256="3" * 64,
        protocol_sha256="4" * 64,
        manifest_sha256="5" * 64,
        provider=_provider_snapshot(),
        repetitions=REPETITIONS,
        l4_repetition=L4_REPETITION,
        systems=["baseline_a", "system_b"],
        case_ids=[spec["case_id"] for spec in specs],
        case_count=len(specs),
        created_at=FIXED_EPOCH,
    )
    write_json(run_dir / "run.json", run)

    annotations_path = _write_annotations(tmp_path / "ann.jsonl", specs)
    dataset_path = _write_dataset(tmp_path / "data.jsonl", specs)
    return SimpleNamespace(
        specs=specs,
        run_dir=run_dir,
        annotations_path=annotations_path,
        dataset_path=dataset_path,
        output_root=tmp_path / "outputs",
        reviews_root=tmp_path / "reviews",
        runs_root=tmp_path / "runs_pointer",
        reports_root=tmp_path / "reports",
        tmp_path=tmp_path,
    )


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    return build_offline_env(tmp_path)


def _package(env: SimpleNamespace, *, seed: int = 20260101, output_root: Path | None = None):
    return package_run(
        env.run_dir,
        seed=seed,
        annotations_path=env.annotations_path,
        dataset_path=env.dataset_path,
        output_root=output_root or env.output_root,
        reviews_root=env.reviews_root,
        runs_root=env.runs_root,
        created_at=FIXED_EPOCH,
    )


def _read_map(env: SimpleNamespace, run_id: str = RUN_ID) -> dict:
    return json.loads((env.reviews_root / run_id / "unblinding_map.json").read_text(encoding="utf-8"))


def _read_manifest(env: SimpleNamespace, run_id: str = RUN_ID) -> dict:
    return json.loads((env.reviews_root / run_id / "review_manifest.json").read_text(encoding="utf-8"))


def _sample_for(mapping: dict, case_id: str, turn_id: str) -> str:
    for sample_id, entry in mapping["pairs"].items():
        if entry["case_id"] == case_id and entry["turn_id"] == turn_id:
            return sample_id
    raise AssertionError(f"no sample for {case_id}/{turn_id}")


FORBIDDEN_SUBSTRINGS = (
    "baseline",
    "Baseline",
    "system_b",
    "System B",
    "system",
    "unblinding",
    "review_manifest",
    "turn_01",
    "gen_",
    "outputs/",
    "evaluation/",
)
FORBIDDEN_REGEXES = (
    # 独立 A/B 语义标签（含与中文相邻的情况）。
    r"(?<![A-Za-z0-9])[AB](?![A-Za-z0-9])",
    # 轮次内部 ID（t1 / t2 …）。
    r"(?<![A-Za-z0-9])t\d+(?![A-Za-z0-9])",
)


def _blind_files(blind_dir: Path) -> list[Path]:
    return sorted(path for path in blind_dir.rglob("*") if path.is_file())


# ---------------------------------------------------------------------------
# package
# ---------------------------------------------------------------------------


class TestPackage:
    def test_pair_count_and_missing_data(self, env: SimpleNamespace) -> None:
        result = _package(env)
        assert result.run_id == RUN_ID
        # alpha 1 + beta(t2) 1 + delta 2 + epsilon 1 + zeta 1 = 6；eta 只有一侧有图 → missing。
        assert result.pair_count == 6
        mapping = _read_map(env)
        assert len(mapping["pairs"]) == 6
        missing = mapping["missing_data"]
        assert any(
            item["case_id"] == "case-beta"
            and item["turn_id"] == "t1"
            and item["missing_sides"] == ["system_b"]
            for item in missing
        )
        assert any(
            item["case_id"] == "case-eta"
            and item["turn_id"] == "t1"
            and item["missing_sides"] == ["system_b"]
            for item in missing
        )
        assert any(
            item["case_id"] == "case-gamma"
            and item["turn_id"] is None
            and item["reason"] == "no_designated_l4_image_in_case"
            for item in missing
        )
        assert result.missing_count == len(missing)

    def test_non_l4_repetition_is_excluded(self, env: SimpleNamespace) -> None:
        result = _package(env)
        mapping = _read_map(env)
        blind_shas = {
            entry[side]["sha256"]
            for entry in mapping["pairs"].values()
            for side in ("left", "right")
        }
        # rep2（非 L4 重复）的 case-alpha 图片不应出现在盲评包中。
        rep2_data = make_png(_image_color("case-alpha", "baseline_a", 1, 2, 1))
        assert image_review.sha256_bytes(rep2_data) not in blind_shas
        # 盲评包图片文件数 = 2 * pair 数。
        images = [p for p in result.blind_dir.glob("*.png")]
        assert len(images) == 2 * result.pair_count

    def test_blind_tree_is_fully_anonymized(self, env: SimpleNamespace) -> None:
        result = _package(env)
        blind_dir = result.blind_dir
        # 解盲映射绝不在盲评目录内。
        assert not (blind_dir / "unblinding_map.json").exists()
        assert not (blind_dir / "review_manifest.json").exists()

        forbidden = list(FORBIDDEN_SUBSTRINGS)
        for spec in env.specs:
            forbidden.append(spec["case_id"])
        forbidden.append(str(env.run_dir))

        for path in _blind_files(blind_dir):
            relative = str(path.relative_to(blind_dir))
            for token in forbidden:
                assert token not in relative, f"文件名泄露 {token!r}: {relative}"
            if path.suffix.lower() in {".html", ".json", ".txt", ".md"}:
                text = path.read_text(encoding="utf-8")
                for token in forbidden:
                    assert token not in text, f"文件内容泄露 {token!r}: {relative}"
                for pattern in FORBIDDEN_REGEXES:
                    assert not re.search(pattern, text), (
                        f"文件内容命中禁用模式 {pattern!r}: {relative}"
                    )
            else:
                # 二进制图片无法按文本扫描：要求文件名严格符合匿名命名，且不含身份线索。
                assert re.fullmatch(r"pair_\d{3,}_(left|right)\.png", relative), relative

    def test_prompt_text_and_labels_never_reach_blind_package(self, env: SimpleNamespace) -> None:
        result = _package(env)
        html_text = (result.blind_dir / "review.html").read_text(encoding="utf-8")
        assert "PROMPT-SECRET" not in html_text
        # 标注 focus 中的 "Baseline A" 已被中性化，改以「另一侧」呈现。
        assert "Baseline" not in html_text
        assert "另一侧" in html_text
        assert "unblinding" not in html_text
        # 轮次内部 ID 已中性化为「第 N 轮」。
        assert "第1轮用户输入" in html_text
        assert "t1" not in html_text

    def test_same_seed_is_byte_identical(self, env: SimpleNamespace) -> None:
        _package(env)
        first = {path.relative_to(env.tmp_path): path.read_bytes() for path in _blind_files(env.output_root)}
        first.update(
            {
                path.relative_to(env.tmp_path): path.read_bytes()
                for path in env.reviews_root.rglob("*.json")
            }
        )
        first.update(
            {
                path.relative_to(env.tmp_path): path.read_bytes()
                for path in env.runs_root.rglob("*.json")
            }
        )
        _package(env)
        second = {path.relative_to(env.tmp_path): path.read_bytes() for path in _blind_files(env.output_root)}
        second.update(
            {
                path.relative_to(env.tmp_path): path.read_bytes()
                for path in env.reviews_root.rglob("*.json")
            }
        )
        second.update(
            {
                path.relative_to(env.tmp_path): path.read_bytes()
                for path in env.runs_root.rglob("*.json")
            }
        )
        assert set(first) == set(second)
        for key in first:
            assert first[key] == second[key], f"同种子两次 package 结果不一致：{key}"

    def test_seed_changes_left_right_assignment_but_not_numbering(self, env: SimpleNamespace) -> None:
        result_a = _package(env, seed=1)
        map_a = _read_map(env)
        result_b = _package(env, seed=2)
        map_b = _read_map(env)
        assert result_a.derived_seed != result_b.derived_seed
        assert set(map_a["pairs"]) == set(map_b["pairs"])  # 编号稳定，与种子无关

        # 非退化：至少存在一个种子，使左右两侧都出现过两个系统。
        found_non_degenerate = False
        for seed in range(0, 8):
            _package(env, seed=seed)
            mapping = _read_map(env)
            orientations = {entry["left"]["system"] for entry in mapping["pairs"].values()}
            if orientations == {"baseline_a", "system_b"}:
                found_non_degenerate = True
                break
        assert found_non_degenerate, "8 个种子内未出现左右分布非退化的样本集"

    def test_unblinding_map_is_traceable(self, env: SimpleNamespace) -> None:
        _package(env)
        mapping = _read_map(env)
        for sample_id, entry in mapping["pairs"].items():
            for side in ("left", "right"):
                assert entry[side]["system"] in ("baseline_a", "system_b")
                absolute = env.run_dir / entry[side]["path"]
                assert absolute.is_file()
                assert image_review.sha256_file(absolute) == entry[side]["sha256"]
            # 左右必为不同系统，且 sha 不同。
            assert entry["left"]["system"] != entry["right"]["system"]
            assert entry["left"]["sha256"] != entry["right"]["sha256"]
        # 解盲映射只在仓库内目录出现。
        assert (env.reviews_root / RUN_ID / "unblinding_map.json").is_file()

    def test_manifest_records_seed_and_missing(self, env: SimpleNamespace) -> None:
        result = _package(env, seed=42)
        manifest = _read_manifest(env)
        assert manifest["seed"] == 42
        assert manifest["derived_seed"] == result.derived_seed
        assert manifest["l4_repetition"] == L4_REPETITION
        assert manifest["pair_count"] == 6
        assert manifest["missing_data_count"] == len(manifest["missing_data"]) > 0
        assert manifest["failed"], "合成失败轮必须写入清单"
        assert "case-delta|t2" in manifest["dimension_applicability"]

    def test_run_pointer_points_to_outputs_without_binaries(self, env: SimpleNamespace) -> None:
        result = _package(env)
        pointer = json.loads(result.run_pointer_path.read_text(encoding="utf-8"))
        assert pointer["run_json_sha256"] == image_review.sha256_file(env.run_dir / "run.json")
        assert pointer["l4_image_count"] == 12
        assert pointer["l4_image_inventory_sha256"]
        # 指针目录内不得出现二进制图片。
        assert not list(result.run_pointer_path.parent.glob("*.png"))

    def test_missing_run_dir_raises_clear_error(self, tmp_path: Path) -> None:
        with pytest.raises(ImageReviewError, match="运行目录不存在"):
            package_run(tmp_path / "nope", created_at=FIXED_EPOCH)
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(ImageReviewError, match="run.json"):
            package_run(empty, created_at=FIXED_EPOCH)

    def test_no_designated_images_raises_clear_error(self, tmp_path: Path) -> None:
        env = build_offline_env(tmp_path)
        # 去掉所有 L4 图片引用：把 cases JSONL 的 designated_for_l4 改成 False。
        for path in (env.run_dir / "cases").glob("*.jsonl"):
            lines = []
            for raw in path.read_text(encoding="utf-8").splitlines():
                entry = json.loads(raw)
                for ref in entry["record"].get("artifact_refs", []):
                    ref["designated_for_l4"] = False
                lines.append(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(ImageReviewError, match="designated_for_l4"):
            _package(env)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def _valid_scores_payload(env: SimpleNamespace, *, sample_ids: list[str] | None = None) -> dict:
    mapping = _read_map(env)
    selected = sample_ids if sample_ids is not None else sorted(mapping["pairs"])
    samples = []
    for sample_id in selected:
        samples.append(
            {
                "sample_id": sample_id,
                "dimensions": {
                    dimension: {"left": 4, "right": 2, "reason": "合成理由"}
                    for dimension in DIMENSIONS
                },
                "user_preference": {"choice": "left", "reason": "左侧更自然"},
            }
        )
    return {
        "schema_version": "layer4_scores_v1",
        "reviewer_id": "reviewer-01",
        "completed_at": "2026-02-02T00:00:00+00:00",
        "samples": samples,
    }


class TestIngest:
    def test_valid_scores_are_stored(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        scores_path = tmp_path / "export.json"
        scores_path.write_text(
            json.dumps(_valid_scores_payload(env), ensure_ascii=False), encoding="utf-8"
        )
        result = ingest_scores(scores_path, run_id=RUN_ID, reviews_root=env.reviews_root)
        assert result.reviewer_id == "reviewer-01"
        assert len(result.provided_samples) == 6
        assert result.missing_samples == ()
        stored = json.loads(result.scores_path.read_text(encoding="utf-8"))
        assert stored["reviewer_id"] == "reviewer-01"
        assert len(stored["samples"]) == 6
        report = json.loads(
            (env.reviews_root / RUN_ID / "scores" / "ingest_report.json").read_text(encoding="utf-8")
        )
        assert report["missing_count"] == 0

    def test_partial_scores_report_missing_explicitly(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        mapping = _read_map(env)
        selected = sorted(mapping["pairs"])[:2]
        scores_path = tmp_path / "partial.json"
        scores_path.write_text(
            json.dumps(_valid_scores_payload(env, sample_ids=selected), ensure_ascii=False),
            encoding="utf-8",
        )
        result = ingest_scores(scores_path, run_id=RUN_ID, reviews_root=env.reviews_root)
        assert len(result.provided_samples) == 2
        assert set(result.missing_samples) == set(mapping["pairs"]) - set(selected)

    def test_unknown_pair_is_rejected(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        payload = _valid_scores_payload(env)
        payload["samples"].append(
            {
                "sample_id": "pair_999",
                "dimensions": {},
                "user_preference": {"choice": "tie", "reason": ""},
            }
        )
        with pytest.raises(ImageReviewError, match="不是本次盲评包中的样本"):
            validate_scores_payload(payload, valid_sample_ids=set(_read_map(env)["pairs"]))

    def test_out_of_range_score_is_rejected(self, env: SimpleNamespace) -> None:
        _package(env)
        payload = _valid_scores_payload(env)
        payload["samples"][0]["dimensions"]["intent_alignment"]["left"] = 9
        with pytest.raises(ImageReviewError, match="1–5"):
            validate_scores_payload(payload, valid_sample_ids=set(_read_map(env)["pairs"]))

    def test_bad_preference_is_rejected(self, env: SimpleNamespace) -> None:
        _package(env)
        payload = _valid_scores_payload(env)
        payload["samples"][0]["user_preference"]["choice"] = "middle"
        with pytest.raises(ImageReviewError, match="user_preference.choice"):
            validate_scores_payload(payload, valid_sample_ids=set(_read_map(env)["pairs"]))

    def test_empty_reviewer_id_is_rejected(self, env: SimpleNamespace) -> None:
        _package(env)
        payload = _valid_scores_payload(env)
        payload["reviewer_id"] = "   "
        with pytest.raises(ImageReviewError, match="reviewer_id"):
            validate_scores_payload(payload, valid_sample_ids=set(_read_map(env)["pairs"]))

    def test_bad_schema_version_is_rejected(self, env: SimpleNamespace) -> None:
        _package(env)
        payload = _valid_scores_payload(env)
        payload["schema_version"] = "something_else"
        with pytest.raises(ImageReviewError, match="schema_version"):
            validate_scores_payload(payload, valid_sample_ids=set(_read_map(env)["pairs"]))

    def test_resolve_run_id_autodetects_single_run(self, env: SimpleNamespace) -> None:
        _package(env)
        assert resolve_run_id(None, env.reviews_root) == RUN_ID
        with pytest.raises(ImageReviewError, match="找不到任何"):
            resolve_run_id(None, env.reviews_root / "missing")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


class TestReport:
    def _ingest_all(self, env: SimpleNamespace, tmp_path: Path) -> None:
        scores_path = tmp_path / "export.json"
        scores_path.write_text(
            json.dumps(_valid_scores_payload(env), ensure_ascii=False), encoding="utf-8"
        )
        ingest_scores(scores_path, run_id=RUN_ID, reviews_root=env.reviews_root)

    def test_distribution_and_preference_unblinding(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        self._ingest_all(env, tmp_path)
        report_path, payload = report_run(
            run_id=RUN_ID, reviews_root=env.reviews_root, reports_root=env.reports_root,
            created_at=FIXED_EPOCH,
        )
        assert report_path.is_file()
        mapping = _read_map(env)
        overall = payload["aggregates"]["overall"]

        # 左右固定为 4 / 2：baseline 的分布取决于它落在左还是右。
        baseline_left = [
            sample_id
            for sample_id, entry in mapping["pairs"].items()
            if entry["left"]["system"] == "baseline_a"
        ]
        intent_base = overall["intent_alignment"]["per_system"]["baseline_a"]
        assert intent_base["n_scored"] == 6
        assert intent_base["distribution"]["4"] == len(baseline_left)
        assert intent_base["distribution"]["2"] == 6 - len(baseline_left)
        assert intent_base["mean"] == pytest.approx(
            (4 * len(baseline_left) + 2 * (6 - len(baseline_left))) / 6
        )

        # 偏好均为“左侧更优”：解盲后应分别归位到两个真实系统。
        preference = overall["user_preference"]
        assert preference["baseline_a_wins"] == len(baseline_left)
        assert preference["system_b_wins"] == 6 - len(baseline_left)
        assert preference["ties"] == 0
        assert preference["n_scored"] == 6
        assert len(baseline_left) not in (0, 6), "测试需要左右分布非退化的种子"

    def test_raw_scores_are_traceable_and_per_case_per_turn(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        self._ingest_all(env, tmp_path)
        _, payload = report_run(
            run_id=RUN_ID, reviews_root=env.reviews_root, reports_root=env.reports_root,
            created_at=FIXED_EPOCH,
        )
        mapping = _read_map(env)
        for pair in payload["pairs"]:
            entry = mapping["pairs"][pair["sample_id"]]
            assert pair["case_id"] == entry["case_id"]
            assert pair["turn_id"] == entry["turn_id"]
            assert pair["left"]["sha256"] == entry["left"]["sha256"]
            assert pair["right"]["sha256"] == entry["right"]["sha256"]
            for dimension in DIMENSIONS:
                for system in ("baseline_a", "system_b"):
                    slot = pair["ratings"][dimension][system]
                    assert slot["trace"]["sample_id"] == pair["sample_id"]
                    assert slot["trace"]["case_id"] == pair["case_id"]
                    assert slot["trace"]["turn_id"] == pair["turn_id"]
                    assert slot["trace"]["sha256"] in (
                        entry["left"]["sha256"],
                        entry["right"]["sha256"],
                    )

        # 多轮逐轮保留：case-delta 有两个轮次、两条样本。
        delta_turns = [pair for pair in payload["pairs"] if pair["case_id"] == "case-delta"]
        assert {pair["turn_id"] for pair in delta_turns} == {"t1", "t2"}
        delta_case = next(item for item in payload["by_case"] if item["case_id"] == "case-delta")
        assert {turn["turn_id"] for turn in delta_case["turns"]} == {"t1", "t2"}

    def test_structural_not_applicable_and_failed_are_explicit(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        self._ingest_all(env, tmp_path)
        _, payload = report_run(
            run_id=RUN_ID, reviews_root=env.reviews_root, reports_root=env.reports_root,
            created_at=FIXED_EPOCH,
        )
        mapping = _read_map(env)
        delta_t1 = _sample_for(mapping, "case-delta", "t1")
        delta_t2 = _sample_for(mapping, "case-delta", "t2")
        pairs = {pair["sample_id"]: pair for pair in payload["pairs"]}
        # edit_success 只适用于 t2；t1 结构上不适用。
        assert (
            pairs[delta_t1]["ratings"]["edit_success"]["baseline_a"]["status"]
            == "not_applicable_structural"
        )
        assert pairs[delta_t2]["ratings"]["edit_success"]["baseline_a"]["status"] == "scored"
        # 单轮案例的属性保持结构上不适用。
        alpha = _sample_for(mapping, "case-alpha", "t1")
        assert (
            pairs[alpha]["ratings"]["attribute_preservation"]["system_b"]["status"]
            == "not_applicable_structural"
        )
        # failed 显式列出（case-eta 的 system_b 轮失败）。
        assert any(
            item["case_id"] == "case-eta" and item["system"] == "system_b"
            for item in payload["failed"]
        )

    def test_partial_scores_keep_missing_in_denominator(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        mapping = _read_map(env)
        selected = sorted(mapping["pairs"])[:1]
        scores_path = tmp_path / "partial.json"
        scores_path.write_text(
            json.dumps(_valid_scores_payload(env, sample_ids=selected), ensure_ascii=False),
            encoding="utf-8",
        )
        ingest_scores(scores_path, run_id=RUN_ID, reviews_root=env.reviews_root)
        _, payload = report_run(
            run_id=RUN_ID, reviews_root=env.reviews_root, reports_root=env.reports_root,
            created_at=FIXED_EPOCH,
        )
        intent_base = payload["aggregates"]["overall"]["intent_alignment"]["per_system"]["baseline_a"]
        assert intent_base["n_scored"] == 1
        assert intent_base["n_missing"] == 5
        assert intent_base["n_slots"] == 6  # missing 保留在分母侧
        assert payload["missing_data"]["pairs_without_any_score"]
        assert any(
            item["dimension"] == "intent_alignment" and item["reason"] == "no_score_provided"
            for item in payload["missing_data"]["scores"]
        )
        preference = payload["aggregates"]["overall"]["user_preference"]
        assert preference["n_missing"] == 5
        assert preference["n_slots"] == 6

    def test_report_is_deterministic(self, env: SimpleNamespace, tmp_path: Path) -> None:
        _package(env)
        self._ingest_all(env, tmp_path)
        first_path, _ = report_run(
            run_id=RUN_ID, reviews_root=env.reviews_root, reports_root=env.reports_root,
            created_at=FIXED_EPOCH,
        )
        first = first_path.read_bytes()
        second_path, _ = report_run(
            run_id=RUN_ID, reviews_root=env.reviews_root, reports_root=env.reports_root,
            created_at=FIXED_EPOCH,
        )
        assert first == second_path.read_bytes()

    def test_report_without_scores_raises_clear_error(self, env: SimpleNamespace) -> None:
        _package(env)
        with pytest.raises(ImageReviewError, match="不存在"):
            report_run(run_id=RUN_ID, reviews_root=env.reviews_root, reports_root=env.reports_root)


# ---------------------------------------------------------------------------
# 纯函数与卫生
# ---------------------------------------------------------------------------


class TestPureFunctions:
    def test_sanitize_focus_removes_labels_and_turn_ids(self) -> None:
        text = image_review.sanitize_focus(
            "与 Baseline A 终图成对偏好比较（注意：Baseline 无 PIN 保护）；t1→t2 全部保持"
        )
        assert "Baseline" not in text
        assert "t1" not in text and "t2" not in text
        assert "另一侧" in text
        assert "第1轮→第2轮" in text

    def test_derive_seed_is_stable_and_run_scoped(self) -> None:
        assert image_review.derive_seed(RUN_ID, 1) == image_review.derive_seed(RUN_ID, 1)
        assert image_review.derive_seed(RUN_ID, 1) != image_review.derive_seed(RUN_ID, 2)
        assert image_review.derive_seed(RUN_ID, 1) != image_review.derive_seed("other", 1)

    def test_build_layer4_report_payload_requires_no_filesystem(self, env: SimpleNamespace) -> None:
        _package(env)
        mapping = _read_map(env)
        manifest = _read_manifest(env)
        payload = build_layer4_report_payload(
            run_id=RUN_ID,
            scores={
                "reviewer_id": "r1",
                "completed_at": "2026-02-02T00:00:00+00:00",
                "samples": [],
            },
            unblinding=mapping,
            manifest=manifest,
            created_at=FIXED_EPOCH,
        )
        assert payload["pair_count"] == 6
        # 未提供任何评分时全部 applicable 槽位记 missing，不从分母剔除。
        intent_base = payload["aggregates"]["overall"]["intent_alignment"]["per_system"]["baseline_a"]
        assert intent_base["n_missing"] == 6
        assert intent_base["n_slots"] == 6
        assert payload["missing_data"]["pairs_without_any_score"] == sorted(mapping["pairs"])

    def test_scores_template_is_all_null(self, env: SimpleNamespace) -> None:
        result = _package(env)
        template = json.loads(
            (result.blind_dir / "scores_template.json").read_text(encoding="utf-8")
        )
        assert template["schema_version"] == "layer4_scores_v1"
        assert template["reviewer_id"] == ""
        assert len(template["samples"]) == 6
        for sample in template["samples"]:
            for dimension in DIMENSIONS:
                assert sample["dimensions"][dimension] == {"left": None, "right": None, "reason": ""}
            assert sample["user_preference"] == {"choice": None, "reason": ""}


class TestHygiene:
    def test_no_real_network_imports_at_module_top_level(self) -> None:
        forbidden = ("httpx", "socket", "requests", "openai_llm", "openai_image")
        for path in (Path(image_review.__file__), Path(__file__)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders: list[str] = []
            for node in tree.body:
                if isinstance(node, ast.Import):
                    offenders.extend(alias.name for alias in node.names if alias.name.split(".")[0] in forbidden)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".")[0] in forbidden:
                        offenders.append(node.module)
            assert not offenders, f"{path.name} 顶层导入了网络/真实 adapter 库：{offenders}"

    def test_offline_env_never_touches_repository_dirs(self, env: SimpleNamespace) -> None:
        # 所有产物根都在 tmp_path 下；真实仓库目录未被写入。
        for root in (env.output_root, env.reviews_root, env.runs_root, env.reports_root):
            assert str(root).startswith(str(env.tmp_path))
        assert not (image_review.PROJECT_ROOT / "evaluation" / "reviews" / RUN_ID).exists()
        assert not (image_review.PROJECT_ROOT / "outputs" / "evaluation_reviews" / RUN_ID).exists()


class TestRealAnnotationIntegration:
    """用合成运行布局 + **真实冻结标注/fixture** 验证去标签与焦点读取（只读冻结物）。"""

    def _real_specs(self) -> list[dict]:
        return [
            _case_spec(
                "s07-single-field-001",
                "multi",
                "single_field_modification",
                [
                    ("t1", "user_message", "画一只橘猫坐姿，室内书房，中景，台灯光。"),
                    ("t2", "image_feedback", "构图换成特写。"),
                    ("t3", "accept", "可以了。"),
                ],
                l4_images={"baseline_a": {"t1": 1, "t2": 1}, "system_b": {"t1": 1, "t2": 1}},
            )
        ]

    def test_focuses_and_background_come_from_frozen_files(self, tmp_path: Path) -> None:
        env = build_offline_env(tmp_path, self._real_specs())
        result = package_run(
            env.run_dir,
            seed=3,
            annotations_path=image_review.DEFAULT_ANNOTATIONS_PATH,
            dataset_path=image_review.DEFAULT_DATASET_PATH,
            output_root=env.output_root,
            reviews_root=env.reviews_root,
            runs_root=env.runs_root,
            created_at=FIXED_EPOCH,
        )
        html_text = (result.blind_dir / "review.html").read_text(encoding="utf-8")
        # 真实标注 focus 里的 "Baseline A" 与 "t1→t2" 已被中性化。
        assert "Baseline" not in html_text
        assert "第1轮→第2轮" in html_text
        assert "第2轮后构图" in html_text
        # 案例背景来自冻结 fixture 的用户文本（该轮及此前轮）。
        assert "一只橘猫坐在木质书桌上" in html_text
        assert "只把构图改成特写" in html_text
        # 内部轮次 ID 不出现。
        assert not re.search(r"(?<![A-Za-z0-9])t\d+(?![A-Za-z0-9])", html_text)

        manifest = json.loads(
            (env.reviews_root / RUN_ID / "review_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["dimension_applicability"]["s07-single-field-001|t2"]["edit_success"] is True
        assert (
            manifest["dimension_applicability"]["s07-single-field-001|t1"]["edit_success"] is False
        )


class TestHtmlArtifact:
    def test_review_html_is_self_contained(self, env: SimpleNamespace) -> None:
        result = _package(env)
        html_text = (result.blind_dir / "review.html").read_text(encoding="utf-8")
        assert "http://" not in html_text
        assert "https://" not in html_text
        assert "//cdn" not in html_text
        # 图片以相对路径引用，且引用的文件确实存在。
        sources = re.findall(r'<img src="([^"]+)"', html_text)
        assert sources
        for source in sources:
            assert not source.startswith("/")
            assert (result.blind_dir / source).is_file()
        # 盲评包里不存在解盲映射，HTML 也不引用。
        assert "unblinding_map" not in html_text
        assert not (result.blind_dir / "unblinding_map.json").exists()


class TestCli:
    def test_package_ingest_report_roundtrip(self, env: SimpleNamespace, tmp_path: Path, capsys) -> None:
        code = image_review.main(
            [
                "package",
                "--run-dir",
                str(env.run_dir),
                "--annotations",
                str(env.annotations_path),
                "--dataset",
                str(env.dataset_path),
                "--output-root",
                str(env.output_root),
                "--reviews-root",
                str(env.reviews_root),
                "--runs-root",
                str(env.runs_root),
                "--seed",
                "7",
            ]
        )
        assert code == 0
        assert RUN_ID in capsys.readouterr().out

        scores_path = tmp_path / "cli_scores.json"
        scores_path.write_text(json.dumps(_valid_scores_payload(env), ensure_ascii=False), encoding="utf-8")
        code = image_review.main(
            [
                "ingest",
                "--scores",
                str(scores_path),
                "--run-id",
                RUN_ID,
                "--reviews-root",
                str(env.reviews_root),
            ]
        )
        assert code == 0
        assert "评分覆盖: 6/6" in capsys.readouterr().out

        code = image_review.main(
            [
                "report",
                "--run-id",
                RUN_ID,
                "--reviews-root",
                str(env.reviews_root),
                "--reports-root",
                str(env.reports_root),
            ]
        )
        assert code == 0
        captured = capsys.readouterr().out
        assert "user_preference" in captured
        assert (env.reports_root / f"{RUN_ID}_layer4.json").is_file()

    def test_cli_errors_return_exit_code_two(self, env: SimpleNamespace, tmp_path: Path, capsys) -> None:
        assert image_review.main(["package", "--run-dir", str(tmp_path / "missing")]) == 2
        assert "错误" in capsys.readouterr().err

        # 已 package 但未 ingest 时 report 必须失败（不静默产出空报告）。
        _package(env)
        assert (
            image_review.main(
                [
                    "report",
                    "--run-id",
                    RUN_ID,
                    "--reviews-root",
                    str(env.reviews_root),
                    "--reports-root",
                    str(env.reports_root),
                ]
            )
            == 2
        )
        assert "错误" in capsys.readouterr().err

