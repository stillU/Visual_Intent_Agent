"""MVP v0.3 Step 04：图片 A/B 盲评工具链（打包 / 收集 / 出报告）。

本模块只实现**离线工具链**，不发起任何真实 Provider 调用、不做图片主观评分、
不下 Gate A 结论：

- `package`：读取 Step 03 真实运行目录（`<run>/run.json` + `cases/*.jsonl`），提取
  `designated_for_l4=True` 的成对图片（`baseline_image` / `image_file`），复制重命名
  为随机化左右位置的匿名样本（`pair_NNN`），生成自包含 `review.html`、`scores_template.json`
  （盲评包）与仓库内 `unblinding_map.json` / `review_manifest.json`（解盲映射与清单）。
- `ingest`：校验用户导出的评分 JSON（结构、pair 覆盖、分数范围、preference 枚举、
  评审人 ID），复制到 `evaluation/reviews/<run_id>/scores/scores_user.json`；允许部分收集
  但**显式报告**缺失 pair（不静默）。
- `report`：合并评分与解盲映射，输出 `evaluation/reports/<run_id>_layer4.json`——
  逐案例逐轮逐维度双方原始分、分数分布（不只均值）、User Preference 解盲归位计数、
  missing_data / failed 显式列出、sample_id ↔ case/turn/system/sha256 全可追溯。

去标签口径（协议第 4 节 Layer 4 + 盲评规程）：

- 盲评 HTML 与盲评包文件名**不得**出现系统名（`baseline`/`system_b`/A/B 语义标签）、
  原始 `case_id`、轮次内部 ID（`t1`…）、Prompt 文本、artifact/run 路径；
- 允许展示：案例背景（该轮及此前轮用户文本原文，两系统相同，取自冻结 fixture）、
  场景描述（中文标签 + 原始 scenario 代码）、逐维度评审焦点（取自冻结标注
  `image_evaluation_dimensions[].focus`，展示前做去标签与轮次 ID 中性化）；
- 左右位置逐对随机化：随机种子由 `run_id + 显式 --seed` 派生并记录，同种子可重现；
- `unblinding_map.json` 只写仓库内 `evaluation/reviews/<run_id>/`，**绝不**进入盲评包目录，
  HTML 也不引用它。

产物位置决策（编排方已定；Step 01 冻结的凭据卫生测试会按 UTF-8 读取 `evaluation/`
树下所有文件，二进制图片会击穿该冻结测试，故二进制只放 gitignore 的 `outputs/` 下）：

```text
outputs/evaluation_reviews/<run_id>/blind/          # 图片副本 + review.html + scores_template.json
evaluation/reviews/<run_id>/unblinding_map.json     # 解盲映射（仓库内 UTF-8 记录）
evaluation/reviews/<run_id>/review_manifest.json    # 盲评清单（种子/pair/缺图/规程）
evaluation/reviews/<run_id>/scores/scores_user.json # 收集到的评分（ingest 后）
evaluation/runs/<run_id>/run_pointer.json           # 指向 outputs/ 真实 run 目录的指针
evaluation/reports/<run_id>_layer4.json             # L4 报告（聚合 + 分布 + 可追溯）
```

CLI：`uv run python -m evaluation.image_review {package,ingest,report} ...`
"""

from __future__ import annotations

import argparse
import html
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from evaluation.direct_baseline import digest, sha256_bytes, sha256_file
from evaluation.reporting import (
    CaseAnnotation,
    EvalCaseRecord,
    EvalRunRecord,
    EvalTurnRecord,
    load_annotations,
    read_case_jsonl,
)
from visual_intent_agent.config import PROJECT_ROOT

# ---------------------------------------------------------------------------
# 版本与枚举常量
# ---------------------------------------------------------------------------

#: 盲评工具链记录信封版本。
IMAGE_REVIEW_SCHEMA_VERSION = "image_review_v1"
#: 解盲映射版本。
UNBLINDING_MAP_SCHEMA_VERSION = "unblinding_map_v1"
#: 盲评清单版本。
REVIEW_MANIFEST_SCHEMA_VERSION = "review_manifest_v1"
#: 评分文件版本（HTML 导出与 scores_template 共用同一结构）。
L4_SCORES_SCHEMA_VERSION = "layer4_scores_v1"
#: L4 报告版本。
L4_REPORT_SCHEMA_VERSION = "layer4_report_v1"
#: Run 指针版本。
RUN_POINTER_SCHEMA_VERSION = "run_pointer_v1"

#: 参评系统标识（与 `evaluation.reporting.SystemId` 一致）。
SYSTEM_BASELINE = "baseline_a"
SYSTEM_TREATMENT = "system_b"
SYSTEM_IDS: tuple[str, str] = (SYSTEM_BASELINE, SYSTEM_TREATMENT)

#: 进入逐图打分的三个维度（User Preference 是成对偏好，单独处理）。
DIMENSIONS: tuple[str, str, str] = ("intent_alignment", "attribute_preservation", "edit_success")
#: 成对偏好枚举（左右为去标签方位，不是系统标签）。
PREFERENCE_CHOICES: tuple[str, str, str] = ("left", "right", "tie")
#: `not_applicable` 字面量（协议第 5 节三态之一）。
NOT_APPLICABLE = "not_applicable"

#: 盲评包默认目录名（位于 `--output-root` 下）。
BLIND_DIR_NAME = "blind"
#: 评分文件默认名。
SCORES_USER_FILENAME = "scores_user.json"
#: 评分导出/模板默认名。
SCORES_EXPORT_FILENAME = "layer4_scores.json"

#: 默认随机种子（显式值与派生值都写入清单，便于复现）。
DEFAULT_SEED = 20260101

#: Step 03 冻结输入默认路径（可在 CLI/函数内注入，测试用合成文件）。
DEFAULT_ANNOTATIONS_PATH = PROJECT_ROOT / "evaluation" / "annotations" / "core_v0_3.jsonl"
DEFAULT_DATASET_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "core_v0_3.jsonl"
#: 产物默认根（二进制在 outputs/ 下，UTF-8 记录在 evaluation/ 下）。
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "evaluation_reviews"
DEFAULT_REVIEWS_ROOT = PROJECT_ROOT / "evaluation" / "reviews"
DEFAULT_RUNS_ROOT = PROJECT_ROOT / "evaluation" / "runs"
DEFAULT_REPORTS_ROOT = PROJECT_ROOT / "evaluation" / "reports"

#: 场景中文标签（仅用于盲评页可读性；原始代码一并展示）。
SCENARIO_LABELS: dict[str, str] = {
    "single_turn_complete": "单轮完整需求",
    "missing_core_decision": "缺失核心决策",
    "missing_perceptual_decision": "缺失感知决策",
    "conflicting_requirements": "冲突需求",
    "explicit_delegation": "显式委托",
    "vague_delegation": "模糊委托",
    "single_field_modification": "单字段修改",
    "pin_unpin": "PIN 保护与解除",
    "consecutive_modifications": "连续修改",
    "vague_image_feedback": "模糊图片反馈",
}

#: 维度中文标签（盲评页展示用）。
DIMENSION_LABELS: dict[str, str] = {
    "intent_alignment": "意图一致程度（Intent Alignment）",
    "attribute_preservation": "属性保持（Attribute Preservation）",
    "edit_success": "修改落实（Edit Success）",
    "user_preference": "成对偏好（User Preference）",
}

#: 去标签替换：标注 focus 中的系统标签 → 中性表述（保持语义、去掉身份线索）。
_LABEL_SANITIZERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"baseline\s*a", re.IGNORECASE), "另一侧"),
    (re.compile(r"baseline", re.IGNORECASE), "另一侧"),
    (re.compile(r"system\s*b", re.IGNORECASE), "另一侧"),
    (re.compile(r"system_b", re.IGNORECASE), "另一侧"),
    (re.compile(r"(?<![A-Za-z0-9])A/B(?![A-Za-z0-9])"), "两侧"),
    (re.compile(r"(?<![A-Za-z0-9])A\s*优"), "一侧更优"),
    (re.compile(r"(?<![A-Za-z0-9])B\s*优"), "一侧更优"),
    # 兜底：任何残留的独立 A/B 语义标签（含与中文相邻的情况）。
    (re.compile(r"(?<![A-Za-z0-9])[AB](?![A-Za-z0-9])"), "某一侧"),
)
#: 轮次内部 ID（`t1` / `t12`）→ 中性「第 N 轮」；用 ASCII 数字边界，避免误伤中文相邻文本。
_TURN_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])t(\d+)(?![A-Za-z0-9])")


class ImageReviewError(ValueError):
    """盲评工具链的可预期错误（CLI 退出码 2，信息面向使用者）。"""


# ---------------------------------------------------------------------------
# 纯函数：种子派生、去标签、确定性落盘
# ---------------------------------------------------------------------------


def derive_seed(run_id: str, seed: int) -> int:
    """由 `run_id + 显式 seed` 派生随机种子（同输入必然同结果）。

    派生而非直接使用显式值，使不同 Run 之间即使显式 seed 相同也不会共享同一随机序列；
    两个值都写入清单与解盲映射，便于复算。
    """
    return int(digest(run_id, str(seed))[:16], 16)


def sanitize_focus(text: str) -> str:
    """把标注 focus 中的系统标签与轮次内部 ID 中性化（盲评页展示前调用）。

    - `Baseline A` / `Baseline` / `System B` → 「另一侧」；
    - `t1` / `t12` → 「第 1 轮」/「第 12 轮」；
    - 折叠连续空白，去首尾空白。
    """
    sanitized = _TURN_ID_PATTERN.sub(lambda m: f"第{m.group(1)}轮", text)
    for pattern, replacement in _LABEL_SANITIZERS:
        sanitized = pattern.sub(replacement, sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    # 去掉替换后残留的中文之间的空格（纯排版，不改变语义）。
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", sanitized)


def display_path(path: Path) -> str:
    """路径记录：项目根内记相对路径，根外记绝对路径（与 Step 02/03 同口径）。"""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


def iso_utc(value: datetime) -> str:
    """tz-aware datetime → UTC ISO 字符串（拒绝 naive）。"""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ImageReviewError("created_at must be timezone-aware UTC; naive datetimes are rejected")
    return value.astimezone(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    """确定性 JSON 落盘（`sort_keys=True` + 固定缩进，同输入逐字节一致）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    """读入 UTF-8 JSON；失败时给出可读错误。"""
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ImageReviewError(f"文件不存在：{source}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageReviewError(f"无法解析 JSON {source}：{exc}") from exc


# ---------------------------------------------------------------------------
# 数据结构：图片引用 / L4 轮次单元 / 成对样本
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImageRefView:
    """一张进入 L4 盲评的图片引用（路径相对 run 根目录，sha256 可校验）。"""

    system: str
    path: str
    sha256: str | None
    ref_id: str = ""


@dataclass(frozen=True)
class L4TurnUnit:
    """一个 (案例, 轮次, L4 重复序次) 的成对候选单元。"""

    case_id: str
    turn_id: str
    turn_index: int
    turn_type: str
    scenario: str
    baseline_images: tuple[ImageRefView, ...]
    treatment_images: tuple[ImageRefView, ...]
    invalid_images: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class PairPlan:
    """一个匿名化成对样本的计划（左右位置已随机化）。"""

    sample_id: str
    case_id: str
    turn_id: str
    turn_index: int
    turn_type: str
    scenario: str
    left: ImageRefView
    right: ImageRefView


@dataclass(frozen=True)
class FailedEntry:
    """运行记录中显式失败的一条证据（案例级或轮次级）。"""

    level: str
    case_id: str
    system: str
    turn_id: str | None
    turn_index: int | None
    status: str
    error_code: str | None
    error_message: str | None
    stage: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "case_id": self.case_id,
            "system": self.system,
            "turn_id": self.turn_id,
            "turn_index": self.turn_index,
            "status": self.status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "stage": self.stage,
        }


@dataclass(frozen=True)
class PackageResult:
    """`package` 的返回（全部落盘路径 + 计数，便于 CLI 打印与测试断言）。"""

    run_id: str
    seed: int
    derived_seed: int
    pair_count: int
    missing_count: int
    blind_dir: Path
    reviews_dir: Path
    run_pointer_path: Path


@dataclass(frozen=True)
class IngestResult:
    """`ingest` 的返回（覆盖/缺失 pair 显式列表）。"""

    run_id: str
    scores_path: Path
    reviewer_id: str
    provided_samples: tuple[str, ...]
    missing_samples: tuple[str, ...]
    total_pairs: int


# ---------------------------------------------------------------------------
# 读取 Step 03 运行产物（只读；以 reporting 合同校验）
# ---------------------------------------------------------------------------


def load_run_record(run_dir: Path) -> EvalRunRecord:
    """读入并校验 `<run_dir>/run.json`（缺失/不完整时给出清晰错误）。"""
    directory = Path(run_dir)
    if not directory.is_dir():
        raise ImageReviewError(f"运行目录不存在：{directory}")
    run_json = directory / "run.json"
    if not run_json.is_file():
        raise ImageReviewError(
            f"缺少 {run_json}：A/B 运行可能尚未完成或目录不是 Step 03 运行产物"
        )
    payload = read_json(run_json)
    try:
        return EvalRunRecord.model_validate(payload)
    except ValueError as exc:
        raise ImageReviewError(f"{run_json} 不符合 EvalRunRecord 合同：{exc}") from exc


def _load_case_records(
    run_dir: Path, case_id: str
) -> tuple[list[EvalCaseRecord], list[EvalTurnRecord]]:
    """读入 `cases/<case_id>.jsonl` 并按合同校验 case/turn 记录。"""
    path = Path(run_dir) / "cases" / f"{case_id}.jsonl"
    if not path.is_file():
        return [], []
    cases: list[EvalCaseRecord] = []
    turns: list[EvalTurnRecord] = []
    for entry in read_case_jsonl(path):
        record_type = entry.get("record_type")
        record = entry.get("record")
        try:
            if record_type == "case":
                cases.append(EvalCaseRecord.model_validate(record))
            elif record_type == "turn":
                turns.append(EvalTurnRecord.model_validate(record))
        except ValueError as exc:
            raise ImageReviewError(f"{path} 记录不符合合同（{record_type}）：{exc}") from exc
    return cases, turns


def _l4_image_refs(
    turn: EvalTurnRecord | None, kind: str
) -> tuple[list[ImageRefView], list[dict[str, Any]]]:
    """取某轮 `designated_for_l4=True` 且 kind 匹配的图片引用。

    返回 `(可用引用, 无效引用说明)`：`path` 缺失或文件不存在 / sha256 不匹配的引用
    一律进入无效列表（显式 missing_data，不做静默丢弃、不成对硬凑）。
    """
    if turn is None:
        return [], []
    usable: list[ImageRefView] = []
    invalid: list[dict[str, Any]] = []
    for ref in turn.artifact_refs:
        if ref.kind != kind or not ref.designated_for_l4:
            continue
        if not ref.path:
            invalid.append({"ref_id": ref.ref_id, "path": None, "reason": "artifact_ref_without_path"})
            continue
        usable.append(ImageRefView(system=turn.system, path=ref.path, sha256=ref.sha256, ref_id=ref.ref_id))
    usable.sort(key=lambda item: item.path)
    return usable, invalid


def collect_l4_units(
    run_dir: Path, run: EvalRunRecord
) -> tuple[list[L4TurnUnit], list[FailedEntry], list[dict[str, Any]]]:
    """扫描整个 Run，收集 L4 成对候选单元与显式失败证据。

    - 只取 `repetition == run.l4_repetition` 的记录（冻结口径：仅第 1 次重复进 L4）；
    - 同一轮两系统各取 `designated_for_l4=True` 的图片；
    - 返回 `(units, failed_entries, case_level_missing)`，其中 case_level_missing 覆盖
      「整个案例没有任何 L4 图片」或「cases JSONL 缺失」的情况。
    """
    directory = Path(run_dir)
    units: list[L4TurnUnit] = []
    failed: list[FailedEntry] = []
    case_level_missing: list[dict[str, Any]] = []

    for case_id in run.case_ids:
        case_records, turn_records = _load_case_records(directory, case_id)
        if not case_records and not turn_records:
            case_level_missing.append(
                {
                    "case_id": case_id,
                    "turn_id": None,
                    "turn_index": None,
                    "missing_sides": list(SYSTEM_IDS),
                    "reason": "case_jsonl_missing",
                    "details": {"expected_path": f"cases/{case_id}.jsonl"},
                }
            )
            continue

        l4_cases = [c for c in case_records if c.repetition == run.l4_repetition]
        for case_record in l4_cases:
            if case_record.status == "failed":
                error = case_record.error
                failed.append(
                    FailedEntry(
                        level="case",
                        case_id=case_id,
                        system=case_record.system,
                        turn_id=None,
                        turn_index=None,
                        status=case_record.status,
                        error_code=error.code if error else None,
                        error_message=error.message if error else None,
                        stage=error.stage if error else None,
                    )
                )

        case_meta = next((c for c in l4_cases if c.system == SYSTEM_TREATMENT), None)
        if case_meta is None:
            case_meta = l4_cases[0] if l4_cases else (case_records[0] if case_records else None)
        scenario = case_meta.scenario if case_meta is not None else ""
        turn_type = case_meta.turn_type if case_meta is not None else "single"

        by_turn: dict[str, dict[str, EvalTurnRecord]] = {}
        for turn in turn_records:
            if turn.repetition != run.l4_repetition:
                continue
            if turn.status == "failed":
                error = turn.error
                failed.append(
                    FailedEntry(
                        level="turn",
                        case_id=case_id,
                        system=turn.system,
                        turn_id=turn.turn_id,
                        turn_index=turn.turn_index,
                        status=turn.status,
                        error_code=error.code if error else None,
                        error_message=error.message if error else None,
                        stage=error.stage if error else None,
                    )
                )
            by_turn.setdefault(turn.turn_id, {})[turn.system] = turn

        case_has_image = False
        ordered_turn_ids = sorted(
            by_turn,
            key=lambda tid: next(
                (t.turn_index for t in by_turn[tid].values()), 0
            ),
        )
        for turn_id in ordered_turn_ids:
            per_system = by_turn[turn_id]
            baseline_turn = per_system.get(SYSTEM_BASELINE)
            treatment_turn = per_system.get(SYSTEM_TREATMENT)
            baseline_images, baseline_invalid = _l4_image_refs(baseline_turn, "baseline_image")
            treatment_images, treatment_invalid = _l4_image_refs(treatment_turn, "image_file")
            invalid_images = tuple(
                {"system": SYSTEM_BASELINE, **item} for item in baseline_invalid
            ) + tuple({"system": SYSTEM_TREATMENT, **item} for item in treatment_invalid)
            if not baseline_images and not treatment_images and not invalid_images:
                continue
            case_has_image = True
            reference_turn = baseline_turn or treatment_turn
            units.append(
                L4TurnUnit(
                    case_id=case_id,
                    turn_id=turn_id,
                    turn_index=reference_turn.turn_index if reference_turn else 0,
                    turn_type=turn_type,
                    scenario=scenario,
                    baseline_images=tuple(baseline_images),
                    treatment_images=tuple(treatment_images),
                    invalid_images=invalid_images,
                )
            )

        if not case_has_image:
            case_level_missing.append(
                {
                    "case_id": case_id,
                    "turn_id": None,
                    "turn_index": None,
                    "missing_sides": list(SYSTEM_IDS),
                    "reason": "no_designated_l4_image_in_case",
                    "details": {"l4_repetition": run.l4_repetition},
                }
            )

    units.sort(key=lambda unit: (run.case_ids.index(unit.case_id), unit.turn_index, unit.turn_id))
    failed.sort(key=lambda item: (item.case_id, item.system, item.turn_index or 0, item.level))
    case_level_missing.sort(key=lambda item: str(item["case_id"]))
    return units, failed, case_level_missing


def plan_pairs(
    run_id: str,
    units: Sequence[L4TurnUnit],
    *,
    seed: int,
    case_level_missing: Sequence[dict[str, Any]] = (),
    run_case_ids: Sequence[str] = (),
) -> tuple[list[PairPlan], list[dict[str, Any]], int]:
    """把 L4 单元规划为匿名成对样本，并生成显式 missing_data 清单。

    - `pair_NNN` 为全局稳定编号：按案例顺序 + 轮次顺序编号，与随机种子无关；
    - 逐对左右随机化（同一派生种子的 `random.Random` 序列，同种子可重现）；
    - 缺图侧（某轮只有一侧有图、或某侧图片校验失败）记 missing_data，绝不硬凑成对。
    """
    derived = derive_seed(run_id, seed)
    rng = random.Random(derived)
    pairs: list[PairPlan] = []
    missing: list[dict[str, Any]] = list(case_level_missing)
    ordered_case_ids = list(run_case_ids)

    for unit in units:
        for invalid in unit.invalid_images:
            missing.append(
                {
                    "case_id": unit.case_id,
                    "turn_id": unit.turn_id,
                    "turn_index": unit.turn_index,
                    "missing_sides": [invalid.get("system")],
                    "reason": invalid.get("reason", "invalid_image"),
                    "details": {
                        "ref_id": invalid.get("ref_id"),
                        "path": invalid.get("path"),
                    },
                }
            )

        pair_count = min(len(unit.baseline_images), len(unit.treatment_images))
        for index in range(pair_count):
            sample_number = len(pairs) + 1
            baseline = unit.baseline_images[index]
            treatment = unit.treatment_images[index]
            if rng.random() < 0.5:
                left, right = baseline, treatment
            else:
                left, right = treatment, baseline
            pairs.append(
                PairPlan(
                    sample_id=f"pair_{sample_number:03d}",
                    case_id=unit.case_id,
                    turn_id=unit.turn_id,
                    turn_index=unit.turn_index,
                    turn_type=unit.turn_type,
                    scenario=unit.scenario,
                    left=left,
                    right=right,
                )
            )
        for extra in unit.baseline_images[pair_count:]:
            missing.append(_extra_image_missing(unit, extra, missing_system=SYSTEM_TREATMENT))
        for extra in unit.treatment_images[pair_count:]:
            missing.append(_extra_image_missing(unit, extra, missing_system=SYSTEM_BASELINE))

    missing.sort(
        key=lambda item: (
            ordered_case_ids.index(item["case_id"]) if item["case_id"] in ordered_case_ids else 0,
            item["turn_index"] if item["turn_index"] is not None else -1,
            ",".join(item["missing_sides"]),
            item["reason"],
        )
    )
    return pairs, missing, derived


def _extra_image_missing(
    unit: L4TurnUnit, image: ImageRefView, *, missing_system: str
) -> dict[str, Any]:
    """一侧多出、另一侧无对应图时的显式 missing_data 记录。"""
    return {
        "case_id": unit.case_id,
        "turn_id": unit.turn_id,
        "turn_index": unit.turn_index,
        "missing_sides": [missing_system],
        "reason": "counterpart_image_missing",
        "details": {"present_system": image.system, "present_path": image.path, "present_sha256": image.sha256},
    }


# ---------------------------------------------------------------------------
# 案例上下文（冻结 fixture 的用户文本；两系统相同，可安全展示）
# ---------------------------------------------------------------------------


def load_fixture_turns(dataset_path: Path) -> dict[str, list[dict[str, str]]]:
    """读入冻结 fixture，返回 `case_id -> [{turn_id, kind, user_text}]`。"""
    source = Path(dataset_path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ImageReviewError(f"无法读取数据集 {source}：{exc}") from exc
    turns_by_case: dict[str, list[dict[str, str]]] = {}
    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
            case_id = payload["case_id"]
            turns = payload.get("turns", [])
        except (json.JSONDecodeError, KeyError) as exc:
            raise ImageReviewError(f"{source}:{lineno}: 数据集行不合法：{exc}") from exc
        turns_by_case[case_id] = [
            {
                "turn_id": str(turn.get("turn_id", "")),
                "kind": str(turn.get("kind", "")),
                "user_text": str(turn.get("user_text", "")),
            }
            for turn in turns
        ]
    return turns_by_case


def case_background_lines(
    fixture_turns: Sequence[dict[str, str]], turn_index: int
) -> list[dict[str, str]]:
    """该轮及此前轮的用户文本原文（编号用 1 基轮序，不暴露内部 turn_id）。"""
    lines: list[dict[str, str]] = []
    for index, turn in enumerate(fixture_turns, start=1):
        if index > turn_index:
            break
        lines.append({"label": f"第{index}轮用户输入", "text": turn["user_text"]})
    return lines


#: 12 条意图路径的中文标签（盲评页只展示语义，不展示内部系统身份）。
PATH_LABELS: dict[str, str] = {
    "subject.description": "主体描述",
    "subject.count": "主体数量",
    "subject.pose_action": "主体姿态/动作",
    "style.primary": "主风格",
    "style.description": "风格细节",
    "environment.mode": "环境模式（室内/户外等）",
    "environment.location": "地点",
    "composition.framing": "构图景别",
    "lighting.character": "光线特征",
    "camera.angle": "相机角度",
    "camera.depth_of_field": "景深",
    "color.palette": "色调",
}


def case_review_constraints(
    annotation: CaseAnnotation | None, turn_id: str
) -> dict[str, list[str]]:
    """R1-B #6：盲评页要呈现的“必须保留内容”与“目标改动”。

    - `must_preserve`：该轮 `forbidden_change_paths` 对应的用户约束（中文路径标签）；
    - `target_changes`：该轮 `expected_deltas` 描述的目标改动（路径 + 关键词）。
    只展示语义标签，去除系统名与内部 ID，保持盲评中性。
    """
    if annotation is None:
        return {"must_preserve": [], "target_changes": []}
    turn = annotation.turn_annotation(turn_id)
    if turn is None:
        return {"must_preserve": [], "target_changes": []}
    must_preserve = [
        PATH_LABELS.get(path, path) for path in turn.forbidden_change_paths
    ]
    target_changes: list[str] = []
    for delta in turn.expected_deltas:
        label = PATH_LABELS.get(delta.path, delta.path)
        keywords: list[str] = []
        if delta.value_match is not None:
            if delta.value_match.keywords:
                keywords = list(delta.value_match.keywords)
            elif delta.value_match.value is not None:
                keywords = [str(delta.value_match.value)]
        detail = "/".join(keywords) if keywords else "（不携带固定值）"
        target_changes.append(f"{delta.operation} {label} → {detail}")
    return {"must_preserve": must_preserve, "target_changes": target_changes}


def dimension_focuses(annotation: CaseAnnotation | None, turn_id: str) -> dict[str, str]:
    """按标注给出各维度在该轮的评审焦点（`applies_to_turns` 匹配；可多条合并）。

    返回前统一做 `sanitize_focus` 去标签（去掉系统名与轮次内部 ID），保证盲评页
    只展示中性化后的焦点文本。
    """
    focuses: dict[str, list[str]] = {}
    if annotation is None:
        return {}
    for dim in annotation.image_evaluation_dimensions:
        if dim.applies_to_turns is not None and turn_id not in dim.applies_to_turns:
            continue
        focuses.setdefault(dim.dimension, []).append(dim.focus)
    return {key: sanitize_focus(" ".join(value)) for key, value in focuses.items()}


def dimension_applicability(annotation: CaseAnnotation | None, turn_id: str) -> dict[str, bool]:
    """各维度在该轮是否结构上适用（annotation 为唯一依据）。

    - `intent_alignment` 默认适用；
    - `attribute_preservation` / `edit_success` 仅在标注列出且轮次匹配时适用
      （单轮案例协议口径即 not_applicable）；
    - `user_preference` 恒适用（成对盲评的核心问题）。
    """
    applicable = {
        "intent_alignment": True,
        "attribute_preservation": False,
        "edit_success": False,
        "user_preference": True,
    }
    if annotation is None:
        return applicable
    listed = {dim.dimension for dim in annotation.image_evaluation_dimensions}
    for dimension in DIMENSIONS:
        if dimension not in listed:
            applicable[dimension] = False
        else:
            dims = [
                dim
                for dim in annotation.image_evaluation_dimensions
                if dim.dimension == dimension
            ]
            applicable[dimension] = any(
                dim.applies_to_turns is None or turn_id in dim.applies_to_turns for dim in dims
            )
    return applicable


# ---------------------------------------------------------------------------
# review.html 渲染（自包含，无外部资源）
# ---------------------------------------------------------------------------


def _select_options(include_not_applicable: bool) -> str:
    options = ['<option value="">未评分</option>']
    options.extend(f'<option value="{value}">{value}</option>' for value in range(1, 6))
    if include_not_applicable:
        options.append(f'<option value="{NOT_APPLICABLE}">不适用</option>')
    return "".join(options)


def _score_fieldset(
    sample_id: str,
    dimension: str,
    focus: str,
    applicable: bool,
) -> str:
    label = DIMENSION_LABELS[dimension]
    if dimension == "user_preference":
        name = f"pref_{sample_id}"
        preference_labels = {"left": "左侧更优", "right": "右侧更优", "tie": "平局"}
        radios = "".join(
            f'<label class="inline"><input type="radio" name="{name}" value="{value}">'
            f"{preference_labels[value]}</label>"
            for value in PREFERENCE_CHOICES
        )
        focus_html = (
            f'<p class="focus">评审焦点：{html.escape(focus)}</p>' if focus else ""
        )
        return (
            f'<fieldset class="dim" data-dim="{dimension}">'
            f"<legend>{label}</legend>{focus_html}"
            f'<div class="choices">{radios}</div>'
            f'<label class="reason">理由（可选）：<input type="text" data-reason="{dimension}"></label>'
            "</fieldset>"
        )

    if not applicable:
        return (
            f'<fieldset class="dim na" data-dim="{dimension}">'
            f"<legend>{label}</legend>"
            f'<p class="na-note">本样本该维度不适用（not_applicable）。</p>'
            f'<select data-side="left" disabled><option value="{NOT_APPLICABLE}" selected>不适用</option></select>'
            f'<select data-side="right" disabled><option value="{NOT_APPLICABLE}" selected>不适用</option></select>'
            "</fieldset>"
        )

    focus_html = f'<p class="focus">评审焦点：{html.escape(focus)}</p>' if focus else ""
    options = _select_options(include_not_applicable=True)
    return (
        f'<fieldset class="dim" data-dim="{dimension}">'
        f"<legend>{label}（1–5，或标记不适用）</legend>{focus_html}"
        f'<div class="scores">'
        f'<label class="inline">左侧图片：<select data-side="left">{options}</select></label>'
        f'<label class="inline">右侧图片：<select data-side="right">{options}</select></label>'
        f"</div>"
        f'<label class="reason">理由（可选）：<input type="text" data-reason="{dimension}"></label>'
        "</fieldset>"
    )


def _pair_section(
    pair: PairPlan,
    *,
    scenario: str,
    background: Sequence[dict[str, str]],
    focuses: dict[str, str],
    applicability: dict[str, bool],
    constraints: dict[str, list[str]] | None = None,
) -> str:
    scenario_label = SCENARIO_LABELS.get(scenario, "案例场景")
    scenario_text = f"{scenario_label}（{scenario}）" if scenario else scenario_label
    context_lines = "".join(
        f'<p class="context-line"><strong>{html.escape(line["label"])}：</strong>'
        f'{html.escape(line["text"])}</p>'
        for line in background
    )
    constraints = constraints or {"must_preserve": [], "target_changes": []}
    must_preserve_html = "".join(
        f"<li>{html.escape(item)}</li>" for item in constraints["must_preserve"]
    )
    target_change_html = "".join(
        f"<li>{html.escape(item)}</li>" for item in constraints["target_changes"]
    )
    constraints_html = ""
    if must_preserve_html or target_change_html:
        preserve_block = (
            f"<p><strong>必须保留（用户明确要求不变）：</strong>暂无</p>"
            if not must_preserve_html
            else f"<p><strong>必须保留（用户明确要求不变）：</strong></p><ul>{must_preserve_html}</ul>"
        )
        target_block = (
            "<p><strong>目标改动：</strong>本轮无显式修改</p>"
            if not target_change_html
            else f"<p><strong>目标改动：</strong></p><ul>{target_change_html}</ul>"
        )
        constraints_html = f'<div class="constraints">{preserve_block}{target_block}</div>'
    fieldsets = "".join(
        _score_fieldset(
            pair.sample_id, dimension, focuses.get(dimension, ""), applicability.get(dimension, False)
        )
        for dimension in (*DIMENSIONS, "user_preference")
    )
    return (
        f'<section class="pair" data-sample="{pair.sample_id}">'
        f'<h2>样本 {pair.sample_id}</h2>'
        f'<p class="scenario">场景：{html.escape(scenario_text)}</p>'
        f'<div class="context"><h3>案例背景（用户输入原文）</h3>{context_lines}</div>'
        f"{constraints_html}"
        f'<div class="images">'
        f'<figure><img src="{pair.sample_id}_left.png" alt="样本 {pair.sample_id} 左侧图片">'
        f"<figcaption>左侧图片</figcaption></figure>"
        f'<figure><img src="{pair.sample_id}_right.png" alt="样本 {pair.sample_id} 右侧图片">'
        f"<figcaption>右侧图片</figcaption></figure>"
        f"</div>"
        f'<div class="dims">{fieldsets}</div>'
        "</section>"
    )


_HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>图片成对盲评</title>
<style>
  :root { color-scheme: light; }
  body { font-family: "Segoe UI", "Noto Sans CJK SC", "PingFang SC", sans-serif;
         margin: 0; padding: 0 1.5rem 4rem; color: #1f2328; background: #f6f8fa; }
  header { position: sticky; top: 0; background: #ffffff; border-bottom: 1px solid #d0d7de;
           padding: 1rem 0; z-index: 10; }
  h1 { font-size: 1.35rem; margin: 0 0 .5rem; }
  .pair { background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px;
          margin: 1.25rem 0; padding: 1rem 1.25rem; }
  .pair h2 { font-size: 1.1rem; margin: 0 0 .25rem; }
  .scenario { color: #57606a; margin: .15rem 0 .75rem; }
  .context { background: #f6f8fa; border-radius: 6px; padding: .5rem .75rem; margin-bottom: .75rem; }
  .context h3 { font-size: .95rem; margin: .15rem 0 .35rem; }
  .context-line { margin: .2rem 0; }
  .constraints { background: #fff8e5; border: 1px solid #e5c07b; border-radius: 6px;
                 padding: .5rem .75rem; margin-bottom: .75rem; }
  .constraints ul { margin: .2rem 0 .4rem 1.2rem; }
  .constraints p { margin: .2rem 0; }
  .images { display: flex; flex-wrap: wrap; gap: 1rem; }
  figure { flex: 1 1 320px; margin: 0; text-align: center; }
  img { max-width: 100%; height: auto; border: 1px solid #d0d7de; border-radius: 6px; background: #fff; }
  figcaption { color: #57606a; margin-top: .35rem; }
  fieldset.dim { border: 1px solid #d8dee4; border-radius: 6px; margin: .75rem 0; padding: .5rem .75rem; }
  fieldset.dim legend { font-weight: 600; padding: 0 .35rem; }
  .focus { color: #57606a; margin: .25rem 0 .5rem; }
  .scores, .choices { display: flex; flex-wrap: wrap; gap: 1rem; margin-bottom: .4rem; }
  label.inline { display: inline-flex; align-items: center; gap: .35rem; }
  .reason { display: block; margin-top: .35rem; }
  .reason input { width: min(100%, 520px); }
  .na-note { color: #57606a; margin: .25rem 0; }
  footer { position: fixed; bottom: 0; left: 0; right: 0; background: #ffffff; border-top: 1px solid #d0d7de;
           padding: .75rem 1.5rem; display: flex; gap: 1rem; align-items: center; }
  button { font-size: 1rem; padding: .4rem 1rem; border-radius: 6px; border: 1px solid #1f883d;
           background: #1f883d; color: #fff; cursor: pointer; }
  #status { color: #57606a; }
</style>
</head>
<body>
<header>
  <h1>图片成对盲评</h1>
  <p>以下每个样本包含同一案例同一轮次的两张图片。左右顺序已逐样本随机化，不反映任何系统身份。
     请先阅读案例背景与每个维度的评审焦点，再分别给左右两张图片打分，最后给出成对偏好。
     全部完成后填写评审人 ID 并点击“导出评分 JSON”。</p>
  <label>评审人 ID：<input id="reviewer" type="text" placeholder="例如 reviewer-01"></label>
</header>
<main>
__SECTIONS__
</main>
<footer>
  <button id="export" type="button">导出评分 JSON</button>
  <span id="status"></span>
</footer>
<script>
function selectValue(select) {
  if (!select) { return null; }
  var raw = select.value;
  if (raw === "") { return null; }
  if (raw === "not_applicable") { return "not_applicable"; }
  return parseInt(raw, 10);
}
function collectScores() {
  var samples = [];
  var sections = document.querySelectorAll("section.pair");
  for (var i = 0; i < sections.length; i++) {
    var section = sections[i];
    var sample = {
      sample_id: section.getAttribute("data-sample"),
      dimensions: {},
      user_preference: { choice: null, reason: "" }
    };
    var fieldsets = section.querySelectorAll("fieldset.dim");
    for (var j = 0; j < fieldsets.length; j++) {
      var fieldset = fieldsets[j];
      var dimension = fieldset.getAttribute("data-dim");
      var reasonInput = fieldset.querySelector("input[data-reason]");
      var reason = reasonInput ? reasonInput.value : "";
      if (dimension === "user_preference") {
        var checked = fieldset.querySelector("input[type=radio]:checked");
        sample.user_preference.choice = checked ? checked.value : null;
        sample.user_preference.reason = reason;
      } else {
        sample.dimensions[dimension] = {
          left: selectValue(fieldset.querySelector('select[data-side="left"]')),
          right: selectValue(fieldset.querySelector('select[data-side="right"]')),
          reason: reason
        };
      }
    }
    samples.push(sample);
  }
  return {
    schema_version: "layer4_scores_v1",
    reviewer_id: document.getElementById("reviewer").value.trim(),
    completed_at: new Date().toISOString(),
    samples: samples
  };
}
document.getElementById("export").addEventListener("click", function () {
  var payload = collectScores();
  var status = document.getElementById("status");
  if (!payload.reviewer_id) {
    status.textContent = "请先填写评审人 ID。";
    return;
  }
  var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  var url = URL.createObjectURL(blob);
  var link = document.createElement("a");
  link.href = url;
  link.download = "layer4_scores.json";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
  status.textContent = "已导出 " + payload.samples.length + " 个样本的评分。";
});
</script>
</body>
</html>
"""


def render_review_html(sections: Sequence[str]) -> str:
    """把逐对样本区块嵌入自包含 HTML 模板（无外部 CDN/网络引用）。"""
    return _HTML_HEAD.replace("__SECTIONS__", "\n".join(sections))


def build_scores_template(sample_ids: Sequence[str]) -> dict[str, Any]:
    """scores_template.json 骨架：全部 pair 的全部维度预填 null，便于手工填写。

    刻意**不含** run_id / case_id / turn_id 等内部标识，避免手工填写时把身份线索
    带回盲评材料；run_id 由 `ingest --run-id`（或自动识别）确定。
    """
    samples: list[dict[str, Any]] = []
    for sample_id in sample_ids:
        dimensions = {
            dimension: {"left": None, "right": None, "reason": ""} for dimension in DIMENSIONS
        }
        samples.append(
            {
                "sample_id": sample_id,
                "dimensions": dimensions,
                "user_preference": {"choice": None, "reason": ""},
            }
        )
    return {
        "schema_version": L4_SCORES_SCHEMA_VERSION,
        "reviewer_id": "",
        "completed_at": "",
        "samples": samples,
    }


# ---------------------------------------------------------------------------
# package：生成盲评包 + 仓库内记录 + run 指针
# ---------------------------------------------------------------------------


def _verify_images(
    run_dir: Path, pairs: Sequence[PairPlan], missing: list[dict[str, Any]]
) -> list[PairPlan]:
    """复制前校验图片存在且 sha256 匹配；失效样本转为显式 missing_data。"""
    valid: list[PairPlan] = []
    for pair in pairs:
        left_ok, left_reason = _image_ok(run_dir, pair.left)
        right_ok, right_reason = _image_ok(run_dir, pair.right)
        if not left_ok or not right_ok:
            broken: list[str] = []
            details: dict[str, Any] = {}
            if not left_ok:
                broken.append(pair.left.system)
                details["left"] = {"system": pair.left.system, "path": pair.left.path, "reason": left_reason}
            if not right_ok:
                broken.append(pair.right.system)
                details["right"] = {"system": pair.right.system, "path": pair.right.path, "reason": right_reason}
            missing.append(
                {
                    "case_id": pair.case_id,
                    "turn_id": pair.turn_id,
                    "turn_index": pair.turn_index,
                    "missing_sides": sorted(broken),
                    "reason": "pair_image_unavailable",
                    "details": details,
                }
            )
            continue
        valid.append(pair)
    return valid


def _image_ok(run_dir: Path, image: ImageRefView) -> tuple[bool, str]:
    absolute = Path(run_dir) / image.path
    if not absolute.is_file():
        return False, "artifact_file_missing"
    if image.sha256 is not None:
        actual = sha256_file(absolute)
        if actual != image.sha256:
            return False, "sha256_mismatch"
    return True, ""


def _copy_pairs(run_dir: Path, blind_dir: Path, pairs: Sequence[PairPlan]) -> dict[str, dict[str, str]]:
    """把图片字节原样复制为匿名文件名，返回 sample_id -> 左右 sha256。"""
    blind_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, dict[str, str]] = {}
    for pair in pairs:
        entry: dict[str, str] = {}
        for side, image in (("left", pair.left), ("right", pair.right)):
            data = (Path(run_dir) / image.path).read_bytes()
            actual = sha256_bytes(data)
            if image.sha256 is not None and actual != image.sha256:
                raise ImageReviewError(
                    f"图片在复制期发生变化：{image.path}（期望 {image.sha256}，实际 {actual}）"
                )
            (blind_dir / f"{pair.sample_id}_{side}.png").write_bytes(data)
            entry[side] = actual
        digests[pair.sample_id] = entry
    return digests


def _pointer_payload(
    run_id: str,
    run_dir: Path,
    run: EvalRunRecord,
    digest_map: dict[str, dict[str, str]],
    created_at: datetime,
) -> dict[str, Any]:
    """run 指针：只存路径与哈希摘要，不复制任何二进制。"""
    inventory_lines: list[str] = []
    for sample_id in sorted(digest_map):
        for side in ("left", "right"):
            inventory_lines.append(f"{sample_id}_{side}:{digest_map[sample_id][side]}")
    run_json = Path(run_dir) / "run.json"
    return {
        "schema_version": RUN_POINTER_SCHEMA_VERSION,
        "run_id": run_id,
        "run_dir": display_path(run_dir),
        "run_dir_absolute": str(Path(run_dir).resolve()),
        "run_json_sha256": sha256_file(run_json) if run_json.is_file() else None,
        "l4_repetition": run.l4_repetition,
        "l4_image_count": 2 * len(digest_map),
        "l4_image_inventory_sha256": digest(*sorted(inventory_lines)),
        "created_at": iso_utc(created_at),
        "note": (
            "本文件只记录指向原始 A/B 运行目录的指针与图片清单摘要；二进制图片"
            "不复制进 evaluation/（Step 01 凭据卫生测试按 UTF-8 读取该树下全部文件）。"
        ),
    }


def package_run(
    run_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    annotations_path: Path = DEFAULT_ANNOTATIONS_PATH,
    dataset_path: Path = DEFAULT_DATASET_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    reviews_root: Path = DEFAULT_REVIEWS_ROOT,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    created_at: datetime | None = None,
) -> PackageResult:
    """生成盲评包与仓库内记录（唯一会写盘的 `package` 入口；全离线）。

    参数全部可注入，测试用 `tmp_path` 指向合成运行产物，绝不触碰真实
    `evaluation/` 与 `outputs/` 目录。
    """
    when = created_at or datetime.now(timezone.utc)
    directory = Path(run_dir)
    run = load_run_record(directory)

    try:
        annotations = load_annotations(Path(annotations_path))
    except ValueError as exc:
        raise ImageReviewError(f"无法读取标注 {annotations_path}：{exc}") from exc
    annotations_by_case: dict[str, CaseAnnotation] = {item.case_id: item for item in annotations}
    fixture_turns = load_fixture_turns(Path(dataset_path))

    units, failed, case_missing = collect_l4_units(directory, run)
    if not units and not case_missing:
        raise ImageReviewError(
            f"{directory} 中找不到任何 designated_for_l4=True 的成对图片；"
            "请确认 A/B 运行已完成且 l4_repetition 正确"
        )

    pairs, missing, derived = plan_pairs(
        run.run_id,
        units,
        seed=seed,
        case_level_missing=case_missing,
        run_case_ids=run.case_ids,
    )
    pairs = _verify_images(directory, pairs, missing)
    missing.sort(key=lambda item: (str(item["case_id"]), item["turn_index"] if item["turn_index"] is not None else -1, ",".join(item["missing_sides"]), item["reason"]))
    if not pairs:
        raise ImageReviewError(
            "所有候选成对图片均不可用：没有 designated_for_l4=True 的成对图片，"
            f"或图片缺失 / sha256 不匹配；共 {len(missing)} 条 missing_data，"
            "详见 review_manifest.json"
        )

    run_id = run.run_id
    blind_dir = Path(output_root) / run_id / BLIND_DIR_NAME
    reviews_dir = Path(reviews_root) / run_id
    scores_dir = reviews_dir / "scores"

    digest_map = _copy_pairs(directory, blind_dir, pairs)

    sample_ids = [pair.sample_id for pair in pairs]
    sections: list[str] = []
    applicability_by_pair: dict[str, dict[str, bool]] = {}
    for pair in pairs:
        annotation = annotations_by_case.get(pair.case_id)
        applicability = dimension_applicability(annotation, pair.turn_id)
        applicability_by_pair[f"{pair.case_id}|{pair.turn_id}"] = applicability
        # 兜底：标注 focus 若含 case_id，盲评页也必须去掉（只展示 <匿名> 语义）。
        focuses = {
            key: value.replace(pair.case_id, "本案例")
            for key, value in dimension_focuses(annotation, pair.turn_id).items()
        }
        sections.append(
            _pair_section(
                pair,
                scenario=pair.scenario,
                background=case_background_lines(
                    fixture_turns.get(pair.case_id, []), pair.turn_index
                ),
                focuses=focuses,
                applicability=applicability,
                constraints=case_review_constraints(annotation, pair.turn_id),
            )
        )
    (blind_dir / "review.html").write_text(render_review_html(sections), encoding="utf-8")
    write_json(blind_dir / "scores_template.json", build_scores_template(sample_ids))

    # 解盲映射：只写仓库内 reviews 目录，绝不进入 blind/，HTML 也不引用。
    map_pairs: dict[str, Any] = {}
    for pair in pairs:
        map_pairs[pair.sample_id] = {
            "case_id": pair.case_id,
            "turn_id": pair.turn_id,
            "turn_index": pair.turn_index,
            "turn_type": pair.turn_type,
            "scenario": pair.scenario,
            "left": {
                "system": pair.left.system,
                "path": pair.left.path,
                "sha256": digest_map[pair.sample_id]["left"],
            },
            "right": {
                "system": pair.right.system,
                "path": pair.right.path,
                "sha256": digest_map[pair.sample_id]["right"],
            },
        }
    write_json(
        reviews_dir / "unblinding_map.json",
        {
            "schema_version": UNBLINDING_MAP_SCHEMA_VERSION,
            "run_id": run_id,
            "seed": seed,
            "derived_seed": derived,
            "created_at": iso_utc(when),
            "pair_count": len(pairs),
            "pairs": map_pairs,
            "missing_data": missing,
        },
    )

    run_json = directory / "run.json"
    write_json(
        reviews_dir / "review_manifest.json",
        {
            "schema_version": REVIEW_MANIFEST_SCHEMA_VERSION,
            "image_review_schema_version": IMAGE_REVIEW_SCHEMA_VERSION,
            "run_id": run_id,
            "run_dir": display_path(directory),
            "run_json_sha256": sha256_file(run_json) if run_json.is_file() else None,
            "annotations_path": display_path(Path(annotations_path)),
            "annotations_sha256": sha256_file(Path(annotations_path))
            if Path(annotations_path).is_file()
            else None,
            "dataset_path": display_path(Path(dataset_path)),
            "dataset_sha256": sha256_file(Path(dataset_path))
            if Path(dataset_path).is_file()
            else None,
            "seed": seed,
            "derived_seed": derived,
            "l4_repetition": run.l4_repetition,
            "case_count": run.case_count,
            "pair_count": len(pairs),
            "missing_data_count": len(missing),
            "missing_data": missing,
            "failed": [entry.as_dict() for entry in failed],
            "sample_ids": sample_ids,
            "dimensions": list(DIMENSIONS),
            "dimension_applicability": applicability_by_pair,
            "blind_package_dir": display_path(blind_dir),
            "scores_dir": display_path(scores_dir),
            "created_at": iso_utc(when),
            "protocol": (
                "去标签（HTML 与文件名不含系统名/case_id/轮次内部 ID/Prompt 文本/artifact 路径）；"
                "左右位置逐对随机化（种子由 run_id+显式 seed 派生并记录）；"
                "四维度：Intent Alignment 1–5、Attribute Preservation 1–5 或 not_applicable、"
                "Edit Success 1–5 或 not_applicable、User Preference 左优/右优/平局；"
                "原始评分逐案例逐维度落盘（评审人 ID、时间戳、分数、理由）；聚合保留分布而非只报均值。"
            ),
        },
    )

    run_pointer_path = Path(runs_root) / run_id / "run_pointer.json"
    write_json(
        run_pointer_path,
        _pointer_payload(run_id, directory, run, digest_map, when),
    )

    return PackageResult(
        run_id=run_id,
        seed=seed,
        derived_seed=derived,
        pair_count=len(pairs),
        missing_count=len(missing),
        blind_dir=blind_dir,
        reviews_dir=reviews_dir,
        run_pointer_path=run_pointer_path,
    )


# ---------------------------------------------------------------------------
# ingest：校验并落盘用户评分
# ---------------------------------------------------------------------------


def resolve_run_id(run_id: str | None, reviews_root: Path) -> str:
    """确定 run_id：显式给出即用；否则要求 reviews 根下恰好一个带解盲映射的目录。"""
    if run_id:
        return run_id
    root = Path(reviews_root)
    candidates = sorted(
        path.name for path in root.iterdir() if (path / "unblinding_map.json").is_file()
    ) if root.is_dir() else []
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ImageReviewError(f"{root} 下找不到任何含 unblinding_map.json 的 run 目录；请显式 --run-id")
    raise ImageReviewError(
        f"{root} 下存在多个 run 目录 {candidates}；请显式 --run-id 指定"
    )


def load_unblinding_map(run_id: str, reviews_root: Path) -> dict[str, Any]:
    """读入解盲映射（缺失时给出清晰错误）。"""
    path = Path(reviews_root) / run_id / "unblinding_map.json"
    payload = read_json(path)
    if payload.get("schema_version") != UNBLINDING_MAP_SCHEMA_VERSION:
        raise ImageReviewError(
            f"{path} schema_version 期望 {UNBLINDING_MAP_SCHEMA_VERSION}，实际 {payload.get('schema_version')!r}"
        )
    if payload.get("run_id") != run_id:
        raise ImageReviewError(f"{path} run_id 与请求的 {run_id!r} 不一致")
    pairs = payload.get("pairs")
    if not isinstance(pairs, dict) or not pairs:
        raise ImageReviewError(f"{path} 不含任何 pair")
    return payload


def _validate_score_value(value: Any, *, where: str) -> None:
    if value is None:
        return
    if value == NOT_APPLICABLE:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= 5):
        raise ImageReviewError(f"{where} 必须是 1–5 的整数、{NOT_APPLICABLE!r} 或 null，实际 {value!r}")


def validate_scores_payload(payload: Any, *, valid_sample_ids: set[str]) -> dict[str, Any]:
    """校验用户评分 JSON，返回规范化后的结构（不落盘；纯函数便于测试）。

    拒绝项：schema_version 不符、评审人 ID 为空、未知 pair、重复 pair、
    分数越界/类型错误、preference 非枚举值。允许部分收集（缺 pair 不报错）。
    """
    if not isinstance(payload, dict):
        raise ImageReviewError("评分文件顶层必须是 JSON 对象")
    if payload.get("schema_version") != L4_SCORES_SCHEMA_VERSION:
        raise ImageReviewError(
            f"schema_version 期望 {L4_SCORES_SCHEMA_VERSION}，实际 {payload.get('schema_version')!r}"
        )
    reviewer_id = payload.get("reviewer_id")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ImageReviewError("reviewer_id 不能为空")
    samples = payload.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ImageReviewError("samples 必须是非空数组")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, sample in enumerate(samples):
        where = f"samples[{index}]"
        if not isinstance(sample, dict):
            raise ImageReviewError(f"{where} 必须是对象")
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ImageReviewError(f"{where}.sample_id 不能为空")
        if sample_id not in valid_sample_ids:
            raise ImageReviewError(f"{where}.sample_id {sample_id!r} 不是本次盲评包中的样本")
        if sample_id in seen:
            raise ImageReviewError(f"{where}.sample_id {sample_id!r} 重复出现")
        seen.add(sample_id)

        dimensions = sample.get("dimensions")
        if dimensions is None:
            dimensions = {}
        if not isinstance(dimensions, dict):
            raise ImageReviewError(f"{where}.dimensions 必须是对象")
        unknown = sorted(set(dimensions) - set(DIMENSIONS))
        if unknown:
            raise ImageReviewError(f"{where}.dimensions 含未知维度 {unknown}")
        clean_dimensions: dict[str, Any] = {}
        for dimension in DIMENSIONS:
            entry = dimensions.get(dimension)
            if entry is None:
                clean_dimensions[dimension] = {"left": None, "right": None, "reason": ""}
                continue
            if not isinstance(entry, dict):
                raise ImageReviewError(f"{where}.dimensions.{dimension} 必须是对象")
            for side in ("left", "right"):
                _validate_score_value(entry.get(side), where=f"{where}.dimensions.{dimension}.{side}")
            reason = entry.get("reason", "")
            if not isinstance(reason, str):
                raise ImageReviewError(f"{where}.dimensions.{dimension}.reason 必须是字符串")
            clean_dimensions[dimension] = {
                "left": entry.get("left"),
                "right": entry.get("right"),
                "reason": reason,
            }

        preference = sample.get("user_preference")
        if preference is None:
            preference = {"choice": None, "reason": ""}
        if not isinstance(preference, dict):
            raise ImageReviewError(f"{where}.user_preference 必须是对象")
        choice = preference.get("choice")
        if choice is not None and choice not in PREFERENCE_CHOICES:
            raise ImageReviewError(
                f"{where}.user_preference.choice 必须是 {list(PREFERENCE_CHOICES)} 之一或 null，实际 {choice!r}"
            )
        pref_reason = preference.get("reason", "")
        if not isinstance(pref_reason, str):
            raise ImageReviewError(f"{where}.user_preference.reason 必须是字符串")

        normalized.append(
            {
                "sample_id": sample_id,
                "dimensions": clean_dimensions,
                "user_preference": {"choice": choice, "reason": pref_reason},
            }
        )

    completed_at = payload.get("completed_at")
    if completed_at is not None and not isinstance(completed_at, str):
        raise ImageReviewError("completed_at 必须是字符串")
    return {
        "schema_version": L4_SCORES_SCHEMA_VERSION,
        "reviewer_id": reviewer_id.strip(),
        "completed_at": completed_at or "",
        "samples": normalized,
        "provided_sample_ids": [item["sample_id"] for item in normalized],
    }


def ingest_scores(
    scores_path: Path,
    *,
    run_id: str | None = None,
    reviews_root: Path = DEFAULT_REVIEWS_ROOT,
    created_at: datetime | None = None,
) -> IngestResult:
    """校验用户导出的评分 JSON 并落盘到 `reviews/<run_id>/scores/scores_user.json`。"""
    when = created_at or datetime.now(timezone.utc)
    resolved_run_id = resolve_run_id(run_id, Path(reviews_root))
    unblinding = load_unblinding_map(resolved_run_id, Path(reviews_root))
    valid_ids = set(unblinding["pairs"].keys())

    payload = read_json(Path(scores_path))
    normalized = validate_scores_payload(payload, valid_sample_ids=valid_ids)

    provided = normalized.pop("provided_sample_ids")
    missing = sorted(valid_ids - set(provided))
    scores_dir = Path(reviews_root) / resolved_run_id / "scores"
    scores_dir.mkdir(parents=True, exist_ok=True)
    write_json(scores_dir / SCORES_USER_FILENAME, normalized)
    write_json(
        scores_dir / "ingest_report.json",
        {
            "schema_version": IMAGE_REVIEW_SCHEMA_VERSION,
            "run_id": resolved_run_id,
            "source_scores_file": display_path(Path(scores_path)),
            "ingested_at": iso_utc(when),
            "reviewer_id": normalized["reviewer_id"],
            "total_pairs": len(valid_ids),
            "provided_count": len(provided),
            "provided_sample_ids": provided,
            "missing_count": len(missing),
            "missing_sample_ids": missing,
        },
    )
    return IngestResult(
        run_id=resolved_run_id,
        scores_path=scores_dir / SCORES_USER_FILENAME,
        reviewer_id=normalized["reviewer_id"],
        provided_samples=tuple(provided),
        missing_samples=tuple(missing),
        total_pairs=len(valid_ids),
    )


# ---------------------------------------------------------------------------
# report：合并评分与解盲映射，输出 L4 报告
# ---------------------------------------------------------------------------


def _empty_side_aggregate() -> dict[str, Any]:
    return {
        "n_slots": 0,
        "n_scored": 0,
        "n_not_applicable": 0,
        "n_missing": 0,
        "n_structurally_not_applicable": 0,
        "distribution": {str(value): 0 for value in range(1, 6)},
        "mean": None,
        "scores": [],
    }


def _new_dimension_aggregate() -> dict[str, Any]:
    return {"per_system": {SYSTEM_BASELINE: _empty_side_aggregate(), SYSTEM_TREATMENT: _empty_side_aggregate()}}


def _new_report_aggregate() -> dict[str, Any]:
    aggregate: dict[str, Any] = {dimension: _new_dimension_aggregate() for dimension in DIMENSIONS}
    aggregate["user_preference"] = {
        "n_slots": 0,
        "n_scored": 0,
        "n_missing": 0,
        "baseline_a_wins": 0,
        "system_b_wins": 0,
        "ties": 0,
        "distribution": {choice: 0 for choice in PREFERENCE_CHOICES},
    }
    return aggregate


def _side_slot_status(
    *, structurally_applicable: bool, raw_value: Any
) -> tuple[str, int | None]:
    """把一个「某系统某维度」的原始值归类为状态 + 数值。"""
    if not structurally_applicable:
        return "not_applicable_structural", None
    if raw_value is None:
        return "missing", None
    if raw_value == NOT_APPLICABLE:
        return "not_applicable", None
    return "scored", int(raw_value)


def _add_dimension_slot(
    side: dict[str, Any],
    *,
    status: str,
    value: int | None,
    trace: dict[str, Any],
) -> None:
    side["n_slots"] += 1
    if status == "scored":
        side["n_scored"] += 1
        side["distribution"][str(value)] = side["distribution"].get(str(value), 0) + 1
        side["scores"].append({"value": value, **trace})
    elif status == "not_applicable":
        side["n_not_applicable"] += 1
    elif status == "missing":
        side["n_missing"] += 1
    else:
        side["n_structurally_not_applicable"] += 1


def _finalize_side(side: dict[str, Any]) -> None:
    values = [entry["value"] for entry in side["scores"]]
    side["mean"] = (sum(values) / len(values)) if values else None


def build_layer4_report_payload(
    *,
    run_id: str,
    scores: dict[str, Any],
    unblinding: dict[str, Any],
    manifest: dict[str, Any],
    created_at: datetime,
) -> dict[str, Any]:
    """合并评分 + 解盲映射，构造 L4 报告载荷（纯函数，便于测试）。"""
    scores_by_sample = {item["sample_id"]: item for item in scores["samples"]}
    pairs_map: dict[str, Any] = unblinding["pairs"]

    report_pairs: list[dict[str, Any]] = []
    missing_scores: list[dict[str, Any]] = []
    overall = _new_report_aggregate()
    by_turn_type: dict[str, dict[str, Any]] = {"single": _new_report_aggregate(), "multi": _new_report_aggregate()}
    by_case: dict[str, dict[str, Any]] = {}

    for sample_id in sorted(pairs_map):
        mapping = pairs_map[sample_id]
        case_id = mapping["case_id"]
        turn_id = mapping["turn_id"]
        turn_type = mapping.get("turn_type", "single")
        scenario = mapping.get("scenario", "")
        annotation_applicability = _annotation_applicability_for_report(manifest, case_id, turn_id)
        sample_scores = scores_by_sample.get(sample_id)

        ratings: dict[str, Any] = {}
        for dimension in DIMENSIONS:
            applicable = annotation_applicability.get(dimension, False)
            entry: dict[str, Any] = {}
            reason = ""
            if sample_scores is not None:
                dim_scores = sample_scores["dimensions"].get(dimension, {})
                reason = dim_scores.get("reason", "")
            for side_name in ("left", "right"):
                system = mapping[side_name]["system"]
                raw_value = sample_scores["dimensions"][dimension].get(side_name) if sample_scores else None
                status, value = _side_slot_status(
                    structurally_applicable=applicable, raw_value=raw_value
                )
                trace = {
                    "sample_id": sample_id,
                    "case_id": case_id,
                    "turn_id": turn_id,
                    "turn_type": turn_type,
                    "system": system,
                    "dimension": dimension,
                    "sha256": mapping[side_name]["sha256"],
                }
                entry[system] = {
                    "status": status,
                    "value": value,
                    "reason": reason,
                    "reviewer_id": scores["reviewer_id"] if sample_scores else None,
                    "recorded_at": scores.get("completed_at") or None,
                    "trace": trace,
                }
                _add_dimension_slot(
                    overall[dimension]["per_system"][system],
                    status=status,
                    value=value,
                    trace=trace,
                )
                if turn_type in by_turn_type:
                    _add_dimension_slot(
                        by_turn_type[turn_type][dimension]["per_system"][system],
                        status=status,
                        value=value,
                        trace=trace,
                    )
                if status == "missing":
                    missing_scores.append(
                        {
                            "sample_id": sample_id,
                            "case_id": case_id,
                            "turn_id": turn_id,
                            "dimension": dimension,
                            "system": system,
                            "reason": "no_score_provided",
                        }
                    )
            ratings[dimension] = entry

        # User Preference（成对偏好；解盲后归位到真实系统）。
        preference_choice: Any = None
        preference_reason = ""
        if sample_scores is not None:
            preference_choice = sample_scores["user_preference"].get("choice")
            preference_reason = sample_scores["user_preference"].get("reason", "")
        winner: str | None = None
        if preference_choice in ("left", "right"):
            winner = mapping[preference_choice]["system"]
        for aggregate in (overall, by_turn_type.get(turn_type)):
            if aggregate is None:
                continue
            bucket = aggregate["user_preference"]
            bucket["n_slots"] += 1
            if preference_choice is None:
                bucket["n_missing"] += 1
                if aggregate is overall:
                    missing_scores.append(
                        {
                            "sample_id": sample_id,
                            "case_id": case_id,
                            "turn_id": turn_id,
                            "dimension": "user_preference",
                            "system": None,
                            "reason": "no_preference_provided",
                        }
                    )
            else:
                bucket["n_scored"] += 1
                bucket["distribution"][preference_choice] = (
                    bucket["distribution"].get(preference_choice, 0) + 1
                )
                if winner == SYSTEM_BASELINE:
                    bucket["baseline_a_wins"] += 1
                elif winner == SYSTEM_TREATMENT:
                    bucket["system_b_wins"] += 1
                else:
                    bucket["ties"] += 1
        preference_payload = {
            "choice": preference_choice,
            "winner_system": winner,
            "reason": preference_reason,
            "reviewer_id": scores["reviewer_id"] if sample_scores else None,
            "recorded_at": scores.get("completed_at") or None,
        }

        report_pairs.append(
            {
                "sample_id": sample_id,
                "case_id": case_id,
                "turn_id": turn_id,
                "turn_index": mapping.get("turn_index"),
                "turn_type": turn_type,
                "scenario": scenario,
                "left": dict(mapping["left"]),
                "right": dict(mapping["right"]),
                "ratings": ratings,
                "user_preference": preference_payload,
                "has_scores": sample_scores is not None,
            }
        )

        case_entry = by_case.setdefault(
            case_id,
            {"case_id": case_id, "turn_type": turn_type, "scenario": scenario, "turns": []},
        )
        case_entry["turns"].append(
            {
                "turn_id": turn_id,
                "turn_index": mapping.get("turn_index"),
                "sample_id": sample_id,
                "ratings": ratings,
                "user_preference": preference_payload,
            }
        )

    for dimension in DIMENSIONS:
        for scope in (overall, *by_turn_type.values()):
            for system in (SYSTEM_BASELINE, SYSTEM_TREATMENT):
                _finalize_side(scope[dimension]["per_system"][system])

    pairs_without_any_score = [
        pair["sample_id"] for pair in report_pairs if not pair["has_scores"]
    ]
    traceability = {
        "unblinding_map_sha256": manifest.get("unblinding_map_sha256"),
        "review_manifest_sha256": manifest.get("review_manifest_sha256"),
        "scores_payload": {
            "reviewer_id": scores["reviewer_id"],
            "completed_at": scores.get("completed_at") or None,
            "sample_count": len(scores["samples"]),
        },
        "run_id": run_id,
    }

    return {
        "schema_version": L4_REPORT_SCHEMA_VERSION,
        "layer": "L4",
        "run_id": run_id,
        "generated_at": iso_utc(created_at),
        "dimensions": list(DIMENSIONS),
        "seed": manifest.get("seed"),
        "derived_seed": manifest.get("derived_seed"),
        "reviewer_ids": sorted({scores["reviewer_id"]}) if scores["reviewer_id"] else [],
        "pair_count": len(report_pairs),
        "pairs": report_pairs,
        "by_case": [by_case[case_id] for case_id in sorted(by_case)],
        "aggregates": {"overall": overall, "by_turn_type": by_turn_type},
        "missing_data": {
            "images": manifest.get("missing_data", []),
            "scores": sorted(
                missing_scores,
                key=lambda item: (
                    item["sample_id"],
                    item["dimension"],
                    item.get("system") or "",
                ),
            ),
            "pairs_without_any_score": pairs_without_any_score,
            "note": "missing_data 不从分母剔除：各维度 per_system 的 n_slots 含 missing 槽位。",
        },
        "failed": manifest.get("failed", []),
        "traceability": traceability,
        "notes": (
            "本报告只做聚合与保留分布，不做任何样本筛选；分数解盲后按真实系统归位；"
            "不代替 Gate A 结论（下结论属 Step 05/07）。"
        ),
    }


def _annotation_applicability_for_report(
    manifest: dict[str, Any], case_id: str, turn_id: str
) -> dict[str, bool]:
    """从清单携带的逐 pair 适用性读取维度适用性（清单缺失时回退为默认）。"""
    applicability = manifest.get("dimension_applicability", {})
    key = f"{case_id}|{turn_id}"
    stored = applicability.get(key)
    if stored is not None:
        return stored
    return {"intent_alignment": True, "attribute_preservation": False, "edit_success": False}


def load_scores_file(run_id: str, reviews_root: Path) -> dict[str, Any]:
    """读取已 ingest 的评分文件（`scores/scores_user.json`）。"""
    path = Path(reviews_root) / run_id / "scores" / SCORES_USER_FILENAME
    payload = read_json(path)
    if payload.get("schema_version") != L4_SCORES_SCHEMA_VERSION:
        raise ImageReviewError(f"{path} schema_version 不受支持")
    if not isinstance(payload.get("samples"), list):
        raise ImageReviewError(f"{path} 不含 samples 数组；请先运行 ingest")
    return payload


def report_run(
    *,
    run_id: str,
    reviews_root: Path = DEFAULT_REVIEWS_ROOT,
    reports_root: Path = DEFAULT_REPORTS_ROOT,
    created_at: datetime | None = None,
) -> tuple[Path, dict[str, Any]]:
    """生成 `evaluation/reports/<run_id>_layer4.json` 并返回 (路径, 载荷)。"""
    when = created_at or datetime.now(timezone.utc)
    unblinding = load_unblinding_map(run_id, Path(reviews_root))
    manifest_path = Path(reviews_root) / run_id / "review_manifest.json"
    manifest = read_json(manifest_path)
    manifest["unblinding_map_sha256"] = sha256_file(
        Path(reviews_root) / run_id / "unblinding_map.json"
    )
    manifest["review_manifest_sha256"] = sha256_file(manifest_path)
    scores = load_scores_file(run_id, Path(reviews_root))
    # 落盘后再校验一次：即使评分文件被手工改动，报告也只消费合法值（越界/未知 pair 直接拒绝）。
    scores = validate_scores_payload(
        scores, valid_sample_ids=set(unblinding["pairs"].keys())
    )
    scores.pop("provided_sample_ids", None)

    payload = build_layer4_report_payload(
        run_id=run_id,
        scores=scores,
        unblinding=unblinding,
        manifest=manifest,
        created_at=when,
    )
    report_path = Path(reports_root) / f"{run_id}_layer4.json"
    write_json(report_path, payload)
    return report_path, payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    """构造 `python -m evaluation.image_review` 的 argparse 解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.image_review",
        description="MVP v0.3 Step 04：图片 A/B 盲评工具链（package / ingest / report，全离线）。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    package = subparsers.add_parser("package", help="从 Step 03 运行目录生成盲评包与解盲映射")
    package.add_argument("--run-dir", required=True, type=Path, help="Step 03 运行目录（含 run.json）")
    package.add_argument("--seed", type=int, default=DEFAULT_SEED, help="显式随机种子（与 run_id 派生）")
    package.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS_PATH, help="冻结标注 JSONL")
    package.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH, help="冻结 fixture JSONL")
    package.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="盲评包根目录（二进制，gitignore）")
    package.add_argument("--reviews-root", type=Path, default=DEFAULT_REVIEWS_ROOT, help="仓库内记录根目录")
    package.add_argument("--runs-root", type=Path, default=DEFAULT_RUNS_ROOT, help="run 指针根目录")

    ingest = subparsers.add_parser("ingest", help="校验并落盘用户导出的评分 JSON")
    ingest.add_argument("--scores", required=True, type=Path, help="用户导出的评分 JSON")
    ingest.add_argument("--run-id", default=None, help="运行 ID（缺省时从 reviews 根自动识别唯一目录）")
    ingest.add_argument("--reviews-root", type=Path, default=DEFAULT_REVIEWS_ROOT, help="仓库内记录根目录")

    report = subparsers.add_parser("report", help="合并评分与解盲映射，输出 L4 报告")
    report.add_argument("--run-id", default=None, help="运行 ID（缺省时自动识别）")
    report.add_argument("--reviews-root", type=Path, default=DEFAULT_REVIEWS_ROOT, help="仓库内记录根目录")
    report.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT, help="报告根目录")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口；可预期错误打印到 stderr 并返回退出码 2。"""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "package":
            result = package_run(
                args.run_dir,
                seed=args.seed,
                annotations_path=args.annotations,
                dataset_path=args.dataset,
                output_root=args.output_root,
                reviews_root=args.reviews_root,
                runs_root=args.runs_root,
            )
            print(f"run_id: {result.run_id}")
            print(f"盲评包: {display_path(result.blind_dir)}")
            print(f"解盲映射与清单: {display_path(result.reviews_dir)}")
            print(f"run 指针: {display_path(result.run_pointer_path)}")
            print(f"pair 数: {result.pair_count}；missing_data: {result.missing_count}")
            print(f"种子: 显式 {result.seed} → 派生 {result.derived_seed}")
            if result.missing_count:
                print("注意：存在 missing_data（缺图侧不成对硬凑），详见 review_manifest.json。")
            return 0

        if args.command == "ingest":
            result = ingest_scores(
                args.scores, run_id=args.run_id, reviews_root=args.reviews_root
            )
            print(f"run_id: {result.run_id}")
            print(f"评审人: {result.reviewer_id}")
            print(
                f"评分覆盖: {len(result.provided_samples)}/{result.total_pairs} 个 pair；"
                f"已写入 {display_path(result.scores_path)}"
            )
            if result.missing_samples:
                print(f"缺失 pair（{len(result.missing_samples)}）：{', '.join(result.missing_samples)}")
            return 0

        resolved = resolve_run_id(args.run_id, args.reviews_root)
        report_path, payload = report_run(
            run_id=resolved, reviews_root=args.reviews_root, reports_root=args.reports_root
        )
        overall = payload["aggregates"]["overall"]
        print(f"run_id: {payload['run_id']}")
        print(f"报告: {display_path(report_path)}")
        print(f"pair 数: {payload['pair_count']}；missing 图片条目: {len(payload['missing_data']['images'])}")
        for dimension in DIMENSIONS:
            parts = []
            for system in (SYSTEM_BASELINE, SYSTEM_TREATMENT):
                side = overall[dimension]["per_system"][system]
                parts.append(f"{system}: scored={side['n_scored']} na={side['n_not_applicable']} missing={side['n_missing']}")
            print(f"  {dimension}: " + "；".join(parts))
        preference = overall["user_preference"]
        print(
            "  user_preference: "
            f"baseline_a_wins={preference['baseline_a_wins']} "
            f"system_b_wins={preference['system_b_wins']} ties={preference['ties']} "
            f"missing={preference['n_missing']}"
        )
        return 0
    except ImageReviewError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - 由 CLI 测试覆盖 main()
    raise SystemExit(main())


__all__ = [
    "IMAGE_REVIEW_SCHEMA_VERSION",
    "UNBLINDING_MAP_SCHEMA_VERSION",
    "REVIEW_MANIFEST_SCHEMA_VERSION",
    "L4_SCORES_SCHEMA_VERSION",
    "L4_REPORT_SCHEMA_VERSION",
    "RUN_POINTER_SCHEMA_VERSION",
    "SYSTEM_IDS",
    "SYSTEM_BASELINE",
    "SYSTEM_TREATMENT",
    "DIMENSIONS",
    "PREFERENCE_CHOICES",
    "NOT_APPLICABLE",
    "SCENARIO_LABELS",
    "DIMENSION_LABELS",
    "DEFAULT_SEED",
    "DEFAULT_ANNOTATIONS_PATH",
    "DEFAULT_DATASET_PATH",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_REVIEWS_ROOT",
    "DEFAULT_RUNS_ROOT",
    "DEFAULT_REPORTS_ROOT",
    "ImageReviewError",
    "ImageRefView",
    "L4TurnUnit",
    "PairPlan",
    "FailedEntry",
    "PackageResult",
    "IngestResult",
    "derive_seed",
    "sanitize_focus",
    "display_path",
    "iso_utc",
    "write_json",
    "read_json",
    "load_run_record",
    "collect_l4_units",
    "plan_pairs",
    "load_fixture_turns",
    "case_background_lines",
    "case_review_constraints",
    "PATH_LABELS",
    "dimension_focuses",
    "dimension_applicability",
    "render_review_html",
    "build_scores_template",
    "package_run",
    "validate_scores_payload",
    "ingest_scores",
    "resolve_run_id",
    "load_unblinding_map",
    "build_layer4_report_payload",
    "load_scores_file",
    "report_run",
    "build_arg_parser",
    "main",
]
