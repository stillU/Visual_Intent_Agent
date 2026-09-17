#!/usr/bin/env python3
"""v0.6 Step 01：B/C 预注册候选集与冻结清单的离线校验工具（只读、无网络）。

校验对象（全部为 Step 01 交付物）：

- `evaluation/v0_6/protocol.md`        —— 协议（角色 protocol）
- `evaluation/v0_6/run_config.json`    —— 配置（角色 config）
- `evaluation/v0_6/cases.jsonl`        —— 候选集（角色 dataset）
- `evaluation/v0_6/annotations.jsonl`  —— 标注（角色 annotations）
- `evaluation/v0_6/frozen_manifest.json` —— 冻结清单（sha256 sidecar）

校验内容：

1. 结构与分层：30 个独立上下文 = 18 适用 / 6 明确值+PIN 负对照 / 6 无命中回退，
   三路径分配符合任务书；case_id 唯一且 cases/annotations 一一对应。
2. 样本合规：全部项目原创、`derived_from_dataset=false`、无外部数据集 ID，
   `user_text` / `case_id` **不逐字出现**在开发池中。开发池按目录实际扫描
   `tests/**`、`evaluation/fixtures/**`、`evaluation/annotations/**`（排除保留池
   `evaluation/v0_6/`，跳过缓存与非 UTF-8 文件）。
3. 授权状态：`real_run_authorized=false`，预算与阈值仍为待确认，凭据不落盘。
4. 冻结清单：四角色解析与 sha256 全量一致，知识语料 `knowledge_base/v0.5/` 字节哈希一致。
5. 检索可达性（只读冻结语料，不触网、不调用 Provider）：逐 case 用
   `LocalKnowledgeEngine` 复算检索结果，与 `annotations.jsonl` 的预注册期望逐项比对。
6. seed 合规：revision seed 规则必须是 `case_id + repetition` 的固定哈希、与候选值/
   知识结果无关；`fallback_constraint` 固定为 `none`，不得预注册“B 回退必须与 C 候选
   不同”的差异强制约束，适用层必须允许两侧数值（及 Prompt）相同。

用法（离线）：
    uv run python tools/validate_v0_6_cases.py
    uv run python tools/validate_v0_6_cases.py --root /path/to/repo
退出码：0 = RESULT: OK；1 = RESULT: FAIL。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

#: 允许 `uv run python tools/validate_v0_6_cases.py` 直接执行（此时 sys.path[0] 是
#: tools/ 而非仓库根）；只读地把仓库根加入导入路径，不改变任何冻结物。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 本工具只用只读的冻结检索实现复算期望；不执行任何图片生成或 Provider 调用。
from evaluation.manifest import load_manifest, sha256_file
from visual_intent_agent.domain import Resolution, ResolutionRecord, VisualIntent
from visual_intent_agent.knowledge import (
    LocalKnowledgeEngine,
    RetrievalOutcome,
    compute_corpus_fingerprint,
    load_corpus,
)
from visual_intent_agent.knowledge.retrieval import RetrievalRequest
from visual_intent_agent.prompt_engine.engine import DELEGATED_CANDIDATES

ROOT_DEFAULT = Path(__file__).resolve().parents[1]

V06_DIR = "evaluation/v0_6"
PROTOCOL_REL = f"{V06_DIR}/protocol.md"
CONFIG_REL = f"{V06_DIR}/run_config.json"
CASES_REL = f"{V06_DIR}/cases.jsonl"
ANNOTATIONS_REL = f"{V06_DIR}/annotations.jsonl"
MANIFEST_REL = f"{V06_DIR}/frozen_manifest.json"

PROTOCOL_VERSION = "bc_protocol_v0_6"
CONFIG_VERSION = "bc_v0_6"
DATASET_VERSION = "bc_cases_v0_6"

KNOWLEDGE_DIR = "knowledge_base/v0.5"
KNOWLEDGE_FILES = (
    "knowledge_base/v0.5/manifest.json",
    "knowledge_base/v0.5/camera.depth_of_field.jsonl",
    "knowledge_base/v0.5/composition.framing.jsonl",
    "knowledge_base/v0.5/lighting.character.jsonl",
)

KNOWLEDGE_PATHS = ("camera.depth_of_field", "composition.framing", "lighting.character")
PATHS = KNOWLEDGE_PATHS

LAYER_COUNTS = {
    "L1_applicable": 18,
    "L2_explicit_pin_control": 6,
    "L3_no_hit_fallback": 6,
}
CATEGORY_COUNTS = {
    "applicable_adoption": 18,
    "explicit_value_control": 3,
    "pinned_control": 3,
    "no_hit_fallback": 6,
}
# 五条获批规则在 L1 的最小覆盖次数（合计 18）。
RULE_MIN_L1 = {
    "camera.depth_of_field.shallow_for_close_up": 3,
    "camera.depth_of_field.deep_for_wide_shot": 3,
    "composition.framing.medium_shot_for_standing_pose": 6,
    "lighting.character.soft_for_tight_framing": 3,
    "lighting.character.dramatic_for_angled_framing": 3,
}

#: 开发/回归池目录（实际扫描，非固定文件清单）：本候选集必须与其逐字分离。
DEV_POOL_DIRS = ("tests", "evaluation/fixtures", "evaluation/annotations")

#: 只在校验请求里使用的合成 revision id；与 Step 02 的冻结 seed 映射无关。
_VALIDATION_REVISION_ID = "irev_v06_validate"
_CREDENTIAL_KEY = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|access[_-]?token)")


class ValidationError(RuntimeError):
    """校验失败（不应被吞掉）。"""


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{path}:{lineno} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValidationError(f"{path}:{lineno} must be a JSON object")
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 结构校验
# ---------------------------------------------------------------------------


def check_cases_structure(cases: list[dict], config: dict) -> list[str]:
    problems: list[str] = []
    if len(cases) != 30:
        problems.append(f"expected 30 candidate contexts, found {len(cases)}")

    ids = [c.get("case_id") for c in cases]
    if len(set(ids)) != len(ids):
        problems.append("case_id values are not unique")
    if any(not isinstance(cid, str) or not cid for cid in ids):
        problems.append("every case needs a non-empty string case_id")

    layer_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    per_path_layer: dict[tuple[str, str], int] = {}
    per_path_category: dict[tuple[str, str], int] = {}
    rule_l1: dict[str, int] = {}
    for case in cases:
        layer = case.get("layer")
        category = case.get("category")
        path = case.get("primary_path")
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        category_counts[category] = category_counts.get(category, 0) + 1
        per_path_layer[(path, layer)] = per_path_layer.get((path, layer), 0) + 1
        per_path_category[(path, category)] = per_path_category.get((path, category), 0) + 1

        if case.get("dataset_version") != DATASET_VERSION:
            problems.append(f"{case.get('case_id')}: dataset_version != {DATASET_VERSION}")
        if path not in PATHS:
            problems.append(f"{case.get('case_id')}: primary_path {path!r} outside the three paths")
        if not isinstance(case.get("user_text"), str) or not case["user_text"].strip():
            problems.append(f"{case.get('case_id')}: empty user_text")
        if case.get("derived_from_dataset") is not False:
            problems.append(f"{case.get('case_id')}: derived_from_dataset must be false")
        if case.get("parent_case_id") is not None:
            problems.append(f"{case.get('case_id')}: parent_case_id must be null")
        if not case.get("group_id"):
            problems.append(f"{case.get('case_id')}: missing group_id")
        source = case.get("source") or {}
        if source.get("source_type") != "project_original":
            problems.append(f"{case.get('case_id')}: source_type must be project_original")
        if source.get("external_ids"):
            problems.append(f"{case.get('case_id')}: external_ids must be empty")
        if source.get("license") != "internal-project-original":
            problems.append(f"{case.get('case_id')}: license must be internal-project-original")

        values = case.get("confirmed_intent") or {}
        for key in values:
            facet, _, field = key.partition(".")
            if not facet or not field:
                problems.append(f"{case.get('case_id')}: malformed intent path {key!r}")
        delegated = set(case.get("delegated_paths") or ())
        pinned = set(case.get("pinned_paths") or ())
        explicit = set(case.get("explicit_paths") or ())
        if len(delegated) + len(pinned) + len(explicit) == 0:
            problems.append(f"{case.get('case_id')}: no delegated/pinned/explicit path declared")
        for name, group in (("delegated", delegated), ("pinned", pinned), ("explicit", explicit)):
            if group - set(PATHS):
                problems.append(f"{case.get('case_id')}: {name}_paths outside the three paths")
        # PIN 合法性（domain/validation）：被 PIN 的路径必须已有值；
        # 有值路径不会是 delegated，故 pinned ⊆ explicit（值来源）是合法形状。
        for pinned_path in pinned:
            if values.get(pinned_path) is None:
                problems.append(
                    f"{case.get('case_id')}: pinned path {pinned_path!r} must already have a value "
                    "(pin_requires_existing_value)"
                )
            if pinned_path in delegated:
                problems.append(
                    f"{case.get('case_id')}: pinned path {pinned_path!r} cannot be delegated "
                    "(a delegated path has no value to pin)"
                )
        if explicit & delegated:
            problems.append(f"{case.get('case_id')}: a path cannot be both explicit and delegated")

        if category == "applicable_adoption":
            rule = case.get("rule_under_test")
            if not rule:
                problems.append(f"{case.get('case_id')}: applicable case needs rule_under_test")
            else:
                rule_l1[rule] = rule_l1.get(rule, 0) + 1
            if path not in delegated or pinned or explicit:
                problems.append(f"{case.get('case_id')}: applicable case must be delegated-only")
            if values.get(path) is not None:
                problems.append(f"{case.get('case_id')}: applicable case must have no value on its path")
        elif category == "explicit_value_control":
            if path not in explicit:
                problems.append(f"{case.get('case_id')}: explicit control must mark the path explicit")
            if values.get(path) not in DELEGATED_CANDIDATES.get(path, ()):
                problems.append(
                    f"{case.get('case_id')}: explicit value {values.get(path)!r} is not an authorized candidate"
                )
        elif category == "pinned_control":
            # PIN 合法性：`validation.pin_requires_existing_value` 要求 PIN 时路径已有值，
            # 因此可合法达成（并可由合法 delta 序列 SET → PIN 构造）的 PIN 控制必须是
            # “已有确认值 + 被 PIN”，而不是 delegated+pinned 空值。
            if path not in pinned or path not in explicit:
                problems.append(
                    f"{case.get('case_id')}: pinned control must carry an existing explicit value "
                    "and be pinned (pin_requires_existing_value)"
                )
            if delegated:
                problems.append(
                    f"{case.get('case_id')}: pinned control must not be delegated "
                    "(a delegated path has no value to pin)"
                )
            if values.get(path) not in DELEGATED_CANDIDATES.get(path, ()):
                problems.append(
                    f"{case.get('case_id')}: pinned control value {values.get(path)!r} "
                    "is not an authorized candidate"
                )
        elif category == "no_hit_fallback":
            if path not in delegated or pinned or explicit:
                problems.append(f"{case.get('case_id')}: no-hit case must be delegated-only")
            if values.get(path) is not None:
                problems.append(f"{case.get('case_id')}: no-hit case must have no value on its path")
        else:
            problems.append(f"{case.get('case_id')}: unknown category {category!r}")

    for layer, expected in LAYER_COUNTS.items():
        if layer_counts.get(layer, 0) != expected:
            problems.append(f"layer {layer}: expected {expected}, found {layer_counts.get(layer, 0)}")
    for category, expected in CATEGORY_COUNTS.items():
        if category_counts.get(category, 0) != expected:
            problems.append(
                f"category {category}: expected {expected}, found {category_counts.get(category, 0)}"
            )
    for path in PATHS:
        if per_path_layer.get((path, "L1_applicable"), 0) != 6:
            problems.append(f"path {path}: expected 6 applicable cases")
        if per_path_layer.get((path, "L2_explicit_pin_control"), 0) != 2:
            problems.append(f"path {path}: expected 2 explicit/PIN controls")
        if per_path_layer.get((path, "L3_no_hit_fallback"), 0) != 2:
            problems.append(f"path {path}: expected 2 no-hit cases")
        if per_path_category.get((path, "explicit_value_control"), 0) != 1:
            problems.append(f"path {path}: expected exactly 1 explicit-value control")
        if per_path_category.get((path, "pinned_control"), 0) != 1:
            problems.append(f"path {path}: expected exactly 1 pinned control")
    for rule, minimum in RULE_MIN_L1.items():
        if rule_l1.get(rule, 0) != minimum:
            problems.append(f"rule {rule}: expected {minimum} applicable cases, found {rule_l1.get(rule, 0)}")
    unknown_rules = sorted(set(rule_l1) - set(RULE_MIN_L1))
    if unknown_rules:
        problems.append(f"unexpected rule_under_test values: {unknown_rules}")

    if config.get("dataset", {}).get("context_count") != 30:
        problems.append("run_config.dataset.context_count must be 30")
    return problems


def check_annotations_structure(cases: list[dict], annotations: list[dict]) -> list[str]:
    problems: list[str] = []
    case_ids = [c["case_id"] for c in cases]
    ann_ids = [a.get("case_id") for a in annotations]
    if ann_ids != case_ids:
        problems.append("annotations must match cases one-to-one in the same order")
    if len(set(ann_ids)) != len(ann_ids):
        problems.append("annotation case_id values are not unique")
    cases_by_id = {c["case_id"]: c for c in cases}
    for ann in annotations:
        cid = ann.get("case_id")
        case = cases_by_id.get(cid)
        if case is None:
            continue
        if ann.get("dataset_version") != DATASET_VERSION:
            problems.append(f"{cid}: annotation dataset_version mismatch")
        if ann.get("layer") != case["layer"] or ann.get("primary_path") != case["primary_path"]:
            problems.append(f"{cid}: annotation layer/path must mirror the case")
        retrieval = ann.get("expected_retrieval") or {}
        if not isinstance(retrieval.get("queried"), bool):
            problems.append(f"{cid}: expected_retrieval.queried must be a boolean")
        if not retrieval.get("outcome"):
            problems.append(f"{cid}: expected_retrieval.outcome is required")
        constraints = ann.get("expected_constraints") or {}
        if constraints.get("immutable_history_violations") != 0:
            problems.append(f"{cid}: immutable_history_violations must be pre-registered as 0")
        if constraints.get("unauthorized_path_changes") != 0:
            problems.append(f"{cid}: unauthorized_path_changes must be pre-registered as 0")
        if ann.get("annotation_status") != "pre_registered_pending_independent_review":
            problems.append(f"{cid}: annotation_status must disclose pending independent review")
        if ann.get("independent_reviewer") is not None:
            problems.append(f"{cid}: independent_reviewer must stay null until a human reviews")
        # 合规：不得预注册“B 回退必须与 C 候选不同”之类的差异强制约束。
        if ann.get("fallback_constraint") != "none":
            problems.append(
                f"{cid}: fallback_constraint must be 'none' (no forced value separation)"
            )
        relation = ann.get("expected_bc_relation")
        if case["layer"] == "L1_applicable":
            if relation != "c_may_adopt_in_authorized_path_only_bc_values_may_coincide":
                problems.append(
                    f"{cid}: applicable layer must allow B/C value coincidence (got {relation!r})"
                )
            delta = ann.get("expected_prompt_delta") or {}
            if delta.get("prompt_may_be_identical_when_candidate_equals_fallback") is not True:
                problems.append(
                    f"{cid}: applicable prompt delta must record that identical prompts are allowed"
                )
            if delta.get("other_paths_identical") is not True:
                problems.append(f"{cid}: non-authorized paths must stay identical")
        elif relation != "b_and_c_identical_on_target_path":
            problems.append(f"{cid}: non-applicable layer must expect B == C on the target path")
        # top_score_positive 语义：只有真正被评分的 adopted 才为 true；
        # 条件过滤发生在评分前（no_hit）或未检索，因而必须是 null。
        outcome = retrieval.get("outcome")
        top_score = retrieval.get("top_score_positive")
        if outcome == "adopted":
            if top_score is not True:
                problems.append(f"{cid}: adopted retrieval must record top_score_positive=true")
        elif top_score is not None:
            problems.append(
                f"{cid}: {outcome!r} has no scored unit; top_score_positive must be null"
            )
        # L2 控制必须让越权可见：显式值 ≠ 被满足规则的知识候选。
        if case["layer"] == "L2_explicit_pin_control":
            over = ann.get("overreach_check") or {}
            if over.get("differs") is not True:
                problems.append(
                    f"{cid}: explicit/PIN control must differ from the satisfied rule candidate "
                    "so any overreach is visible"
                )
            expected_value = (case.get("confirmed_intent") or {}).get(case["primary_path"])
            if over.get("explicit_value") != expected_value:
                problems.append(f"{cid}: overreach_check.explicit_value must match the confirmed value")
            if over.get("rule_candidate_value") == expected_value:
                problems.append(
                    f"{cid}: explicit value equals the rule candidate, overreach would be invisible"
                )
    return problems


#: 保留池所在目录：不参与开发池扫描（它是被检查对象本身）。
RESERVED_POOL_DIR = "evaluation/v0_6"


def _development_pool_texts(root: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """实际目录扫描开发池：`tests/**`、`evaluation/fixtures/**`、`evaluation/annotations/**`。

    - 排除 `evaluation/v0_6/`（保留池自身）；
    - 跳过 `__pycache__` 等缓存目录与非 UTF-8 二进制文件（不静默跳过文本文件）。
    """
    texts: list[tuple[str, str]] = []
    problems: list[str] = []
    roots = DEV_POOL_DIRS
    for rel_root in roots:
        base = root / rel_root
        if not base.is_dir():
            problems.append(f"development-pool directory is missing: {rel_root}/")
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if rel.startswith(RESERVED_POOL_DIR + "/"):
                continue
            if any(part in {"__pycache__", ".pytest_cache", ".mypy_cache"} for part in path.parts):
                continue
            try:
                texts.append((rel, path.read_text(encoding="utf-8")))
            except UnicodeDecodeError:
                continue
    return texts, problems


def check_dev_pool_separation(cases: list[dict], root: Path) -> list[str]:
    problems: list[str] = []
    texts, scan_problems = _development_pool_texts(root)
    problems.extend(scan_problems)
    for case in cases:
        cid = case["case_id"]
        for rel, text in texts:
            if cid in text:
                problems.append(f"{cid}: case_id appears verbatim in development pool {rel}")
            if case["user_text"] in text:
                problems.append(f"{cid}: user_text appears verbatim in development pool {rel}")
    return problems


def check_run_config(config: dict, root: Path) -> list[str]:
    problems: list[str] = []
    if config.get("real_run_authorized") is not False:
        problems.append("real_run_authorized must be false in the preparation phase")
    if config.get("config_version") != CONFIG_VERSION:
        problems.append(f"config_version must be {CONFIG_VERSION}")
    if config.get("protocol_version") != PROTOCOL_VERSION:
        problems.append(f"protocol_version must be {PROTOCOL_VERSION}")
    if config.get("dataset_version") != DATASET_VERSION:
        problems.append(f"dataset_version must be {DATASET_VERSION}")
    arms = config.get("arms") or {}
    if (arms.get("B") or {}).get("knowledge_engine") is not None:
        problems.append("arm B must disable the knowledge engine")
    if (arms.get("C") or {}).get("corpus_version") != "v0.5-approved-1":
        problems.append("arm C must load v0.5-approved-1")
    budget = config.get("budget") or {}
    if budget.get("status") != "draft_pending_user_confirmation":
        problems.append("budget.status must remain draft_pending_user_confirmation")
    for field in ("max_total_amount", "max_requests", "max_attempts"):
        if budget.get(field) is not None:
            problems.append(f"budget.{field} must stay null until G1 confirmation")
    if (config.get("thresholds") or {}).get("status") != "pending_user_confirmation_before_results":
        problems.append("thresholds must remain pending_user_confirmation_before_results")
    providers = config.get("providers") or {}
    if (providers.get("image") or {}).get("model_identity_verified") is not False:
        problems.append("image model identity must stay explicitly unverified")
    if (providers.get("llm") or {}).get("model_identity_verified") is not False:
        problems.append("llm model identity must stay explicitly unverified")
    if (config.get("repetitions") or {}).get("base_image_requests") != 120:
        problems.append("repetitions.base_image_requests must be 120 (30 x 2 x 2)")
    seed_control = config.get("revision_seed_control") or {}
    if seed_control.get("candidate_agnostic") is not True:
        problems.append("revision_seed_control must be candidate-agnostic")
    if seed_control.get("reads_expected_candidate_or_knowledge_result") is not False:
        problems.append("revision seed mapping must not read expected candidate / knowledge results")
    if sorted(seed_control.get("map_inputs") or []) != ["case_id", "repetition"]:
        problems.append("revision seed map inputs must be exactly case_id + repetition")
    if seed_control.get("post_hoc_id_search_for_c_win_forbidden") is not True:
        problems.append("searching revision ids to make C win must stay forbidden")
    if seed_control.get("value_separation_required") is not False:
        problems.append("value separation between B fallback and C candidate must not be required")
    if seed_control.get("b_c_value_coincidence_allowed") is not True:
        problems.append("B/C value coincidence must be explicitly allowed")
    if "offline_feasibility_verified" in seed_control:
        problems.append("no candidate-aware seed-separation feasibility claim is allowed")
    for path, value in _walk_dict(config):
        if _CREDENTIAL_KEY.search(str(path)) and isinstance(value, str) and value.strip():
            problems.append(f"credential-like key {path!r} must not carry a literal value")
    protocol_text = (root / PROTOCOL_REL).read_text(encoding="utf-8")
    if PROTOCOL_VERSION not in protocol_text:
        problems.append(f"{PROTOCOL_REL} must declare {PROTOCOL_VERSION}")
    if "real_run_authorized" not in protocol_text:
        problems.append(f"{PROTOCOL_REL} must state the real_run_authorized boundary")
    for banned in ("让 C 必赢",):
        if banned in protocol_text:
            problems.append(f"{PROTOCOL_REL} must not describe searching ids to force a C win ({banned})")
    return problems


def _walk_dict(node: Any, prefix: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield child, value
            yield from _walk_dict(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_dict(value, f"{prefix}[{index}]")


def check_manifest(root: Path, config: dict) -> list[str]:
    problems: list[str] = []
    manifest_path = root / MANIFEST_REL
    if not manifest_path.is_file():
        return [f"missing frozen manifest {MANIFEST_REL}"]
    manifest = load_manifest(manifest_path, base_dir=root)
    if manifest.manifest_version != "frozen_manifest_v0_6":
        problems.append("manifest_version must be frozen_manifest_v0_6")
    for attr, expected in (
        ("protocol_version", PROTOCOL_VERSION),
        ("dataset_version", DATASET_VERSION),
        ("config_version", CONFIG_VERSION),
    ):
        if getattr(manifest, attr) != expected:
            problems.append(f"manifest {attr} must be {expected}")
    if manifest.algorithm != "sha256":
        problems.append("manifest algorithm must be sha256")
    expected_roles = {
        "protocol": PROTOCOL_REL,
        "config": CONFIG_REL,
        "dataset": CASES_REL,
        "annotations": ANNOTATIONS_REL,
    }
    for role, rel in expected_roles.items():
        if manifest.roles.get(role) != rel:
            problems.append(f"manifest role {role} must resolve to {rel}")
    try:
        manifest.verify()
    except Exception as exc:  # ManifestError subclass of ValueError
        problems.append(f"frozen manifest verification failed: {exc}")
        return problems
    for rel in KNOWLEDGE_FILES:
        if rel not in manifest.files:
            problems.append(f"frozen manifest must include knowledge file {rel}")
        elif manifest.files[rel] != sha256_file(root / rel):
            problems.append(f"knowledge hash mismatch for {rel}")
    for role, rel in expected_roles.items():
        if rel not in manifest.files:
            problems.append(f"manifest role {role} -> {rel} is not frozen in 'files'")
    return problems


# ---------------------------------------------------------------------------
# 检索可达性（只读冻结语料，不触网）
# ---------------------------------------------------------------------------


def build_intent(values: dict[str, object], delegated, pinned, explicit) -> VisualIntent:
    buckets: dict[str, dict[str, object]] = {
        "subject": {}, "composition": {}, "environment": {}, "style": {},
        "lighting": {}, "camera": {}, "color": {},
    }
    for path, value in values.items():
        facet, _, field = path.partition(".")
        if facet not in buckets:
            raise ValidationError(f"unknown facet in intent path {path!r}")
        buckets[facet][field] = value
    resolutions: dict[str, ResolutionRecord] = {}
    for path in delegated:
        resolutions[path] = ResolutionRecord(resolution=Resolution.USER_DELEGATED)
    for path in pinned:
        resolutions.setdefault(path, ResolutionRecord(resolution=Resolution.USER_DELEGATED))
    for path in explicit:
        resolutions[path] = ResolutionRecord(resolution=Resolution.USER_SPECIFIED)
    return VisualIntent(
        **{name: model(**buckets[name]) for name, model in _FACET_MODELS.items()},
        resolutions=resolutions,
        pinned_paths=frozenset(pinned),
    )


def _facet_models():
    from visual_intent_agent.domain import (
        CameraFacet, ColorFacet, CompositionFacet, EnvironmentFacet,
        LightingFacet, StyleFacet, SubjectFacet,
    )

    return {
        "subject": SubjectFacet, "composition": CompositionFacet,
        "environment": EnvironmentFacet, "style": StyleFacet,
        "lighting": LightingFacet, "camera": CameraFacet, "color": ColorFacet,
    }


_FACET_MODELS = None


def pending_paths_for(case: dict) -> tuple[str, ...]:
    values = case["confirmed_intent"]
    pinned = set(case.get("pinned_paths") or ())
    return tuple(
        path for path in case.get("delegated_paths") or ()
        if path not in pinned and values.get(path) is None
    )


def check_retrieval(cases: list[dict], annotations: list[dict], config: dict, root: Path) -> list[str]:
    global _FACET_MODELS
    _FACET_MODELS = _facet_models()
    problems: list[str] = []
    corpus_dir = root / (config["knowledge_snapshot"]["corpus_dir"])
    corpus = load_corpus(corpus_dir)
    fingerprint = compute_corpus_fingerprint(corpus.manifest)
    if fingerprint != config["knowledge_snapshot"]["corpus_fingerprint"]:
        problems.append(
            "knowledge corpus fingerprint mismatch: "
            f"config={config['knowledge_snapshot']['corpus_fingerprint']} actual={fingerprint}"
        )
    engine = LocalKnowledgeEngine(corpus=corpus)
    target_model = config["providers"]["image"]["model_default"]
    ann_by_id = {a["case_id"]: a for a in annotations}

    for case in cases:
        cid = case["case_id"]
        ann = ann_by_id.get(cid)
        if ann is None:
            continue
        expected = ann["expected_retrieval"]
        pending = pending_paths_for(case)
        if not pending:
            if expected["queried"]:
                problems.append(f"{cid}: annotation expects a query but no path is pending")
            continue
        if not expected["queried"]:
            problems.append(f"{cid}: annotation expects no query but pending={pending}")
            continue
        intent = build_intent(
            case["confirmed_intent"],
            case.get("delegated_paths") or (),
            case.get("pinned_paths") or (),
            case.get("explicit_paths") or (),
        )
        request = RetrievalRequest(
            session_id=f"validate-{cid}",
            intent_revision_id=_VALIDATION_REVISION_ID,
            execution_revision_id="erev_validate",
            confirmation_id="cnf_validate",
            intent=intent,
            target_model=target_model,
            pending_paths=pending,
        )
        bundle = engine.retrieve(request)
        path = case["primary_path"]
        result = bundle.result_for(path)
        recommendation = bundle.recommendation_for(path)
        if result is None:
            problems.append(f"{cid}: no retrieval result for {path}")
            continue
        actual_outcome = result.outcome.value
        if actual_outcome != expected["outcome"]:
            problems.append(
                f"{cid}: retrieval outcome {actual_outcome!r} != annotation {expected['outcome']!r}"
            )
        if (result.reason_code or None) != (expected["reason_code"] or None):
            problems.append(
                f"{cid}: retrieval reason {result.reason_code!r} != annotation {expected['reason_code']!r}"
            )
        if expected["outcome"] == "adopted":
            if recommendation is None:
                problems.append(f"{cid}: annotation expects adoption but no recommendation was produced")
                continue
            if recommendation.knowledge_id != expected["knowledge_id"]:
                problems.append(
                    f"{cid}: adopted {recommendation.knowledge_id!r} != annotation {expected['knowledge_id']!r}"
                )
            if recommendation.candidate_value != expected["candidate_value"]:
                problems.append(
                    f"{cid}: adopted value {recommendation.candidate_value!r} != annotation {expected['candidate_value']!r}"
                )
            if not recommendation.score > 0:
                problems.append(f"{cid}: adopted candidate must have a positive lexical score")
        else:
            if recommendation is not None:
                problems.append(f"{cid}: no-hit case must not produce a recommendation")
            reasons = {item.reason_code for item in result.rejections}
            if result.outcome is RetrievalOutcome.NO_HIT and "conditions_not_satisfied" not in reasons:
                problems.append(
                    f"{cid}: documented no-hit mechanism requires a conditions_not_satisfied rejection; "
                    f"actual reasons={sorted(reasons)}"
                )
    return problems


def check_seed_policy_is_candidate_agnostic(cases: list[dict], config: dict) -> list[str]:
    """合规：seed 规则必须是固定哈希，不得读取候选值/知识结果，也不得要求数值分离。"""
    problems: list[str] = []
    seed_control = config.get("revision_seed_control") or {}
    if seed_control.get("candidate_agnostic") is not True:
        problems.append("revision_seed_control.candidate_agnostic must be true")
    if seed_control.get("reads_expected_candidate_or_knowledge_result") is not False:
        problems.append(
            "revision seed mapping must not read expected_candidate or knowledge results"
        )
    if seed_control.get("value_separation_required") is not False:
        problems.append("revision seed policy must not require B/C value separation")
    banned = ("must_differ", "必须与候选不同", "必须产生差异")
    for case in cases:
        blob = json.dumps(case, ensure_ascii=False)
        for token in banned:
            if token in blob:
                problems.append(f"{case['case_id']}: case text still enforces value separation ({token})")
    return problems


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def validate(root: Path) -> dict:
    cases = load_jsonl(root / CASES_REL)
    annotations = load_jsonl(root / ANNOTATIONS_REL)
    config = load_json(root / CONFIG_REL)
    problems: list[str] = []
    problems += check_cases_structure(cases, config)
    problems += check_annotations_structure(cases, annotations)
    problems += check_dev_pool_separation(cases, root)
    problems += check_run_config(config, root)
    problems += check_manifest(root, config)
    problems += check_retrieval(cases, annotations, config, root)
    problems += check_seed_policy_is_candidate_agnostic(cases, config)
    return {
        "ok": not problems,
        "problems": problems,
        "cases": len(cases),
        "annotations": len(annotations),
        "layers": {
            layer: sum(1 for c in cases if c.get("layer") == layer)
            for layer in LAYER_COUNTS
        },
        "real_run_authorized": config.get("real_run_authorized"),
        "corpus_version": config.get("knowledge_snapshot", {}).get("corpus_version"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT_DEFAULT)
    args = parser.parse_args(argv)
    report = validate(args.root.resolve())
    print(f"cases={report['cases']} annotations={report['annotations']} layers={report['layers']}")
    print(f"real_run_authorized={report['real_run_authorized']} corpus={report['corpus_version']}")
    if not report["ok"]:
        print(f"RESULT: FAIL ({len(report['problems'])} problem(s))")
        for item in report["problems"]:
            print(f"  - {item}")
        return 1
    print("RESULT: OK — 30 contexts (18/6/6), candidate-agnostic seed policy, "
          "manifest hashes verified, retrieval expectations match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
