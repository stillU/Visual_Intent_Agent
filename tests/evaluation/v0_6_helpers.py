"""MVP v0.6 Step 02 测试共享工具（唯一命名模块；无 conftest、不跨测试目录 import）。

自包含、全离线：

- `make_settings()`：伪造 `Settings`（不读 .env）；
- `make_case()` / `run_fixture()`：合成迷你 cases/annotations/run_config（写入 tmp_path），
  Runner 全部路径可注入，绝不触碰 Step 01 冻结物；
- `real_v05_dir()` / `v05_fingerprint()`：只读引用真实冻结语料快照（C 臂）；
- `fake_image_factory()`：确定性 `FakeImageProvider`；也可注入抛错脚本来模拟失败；
- `fixed_clock()` / `fixed_monotonic()`：确定性时间源；
- `read_prompt_text()`：从配对 DB 读回最终 Prompt 文本（可追溯性断言）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from pydantic import SecretStr

from evaluation.v0_6.context import OUTPUT_SIZE
from evaluation.v0_6.records import AnnotationSpec, CaseSpec
from evaluation.v0_6.paired_runner import PairedRunner
from visual_intent_agent.config import PROJECT_ROOT, Settings
from visual_intent_agent.knowledge import compute_corpus_fingerprint, load_corpus
from visual_intent_agent.persistence import SQLiteRepository
from visual_intent_agent.prompt_engine import PromptArtifact
from visual_intent_agent.providers.fake_image import FakeImageProvider

FAKE_BASE_URL = "https://provider.invalid/v1"
FAKE_SECRET = "unit-test-placeholder-not-a-real-key"
FAKE_IMAGE_MODEL = "qwen-image-3.0"
REAL_V05_DIR = PROJECT_ROOT / "knowledge_base" / "v0.5"
FIXED_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: 冻结正例条件（与 knowledge_base/v0.5 的 approved 单元条件一致）。
POSITIVE_VALUES: dict[str, dict[str, str]] = {
    "camera.depth_of_field": {
        "composition.framing": "close_up",
        "style.primary": "cinematic",
        "subject.description": "synthetic test subject",
    },
    "composition.framing": {
        "subject.pose_action": "standing",
        "environment.mode": "studio",
        "subject.description": "synthetic test subject",
    },
    "lighting.character": {
        "composition.framing": "close_up",
        "environment.mode": "studio",
        "subject.description": "synthetic test subject",
    },
}

#: 与正例只差一条条件 → 冻结语料 conditions_not_satisfied → no_hit。
NO_HIT_VALUES: dict[str, str] = {
    "composition.framing": "wide_shot",
    "environment.mode": "indoor",
    "subject.description": "synthetic test subject",
}

EXPECTED_CANDIDATES: dict[str, str] = {
    "camera.depth_of_field": "shallow",
    "composition.framing": "medium_shot",
    "lighting.character": "soft",
}


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "provider_base_url": FAKE_BASE_URL,
        "provider_api_key": SecretStr(FAKE_SECRET),
        "image_model": FAKE_IMAGE_MODEL,
        "llm_timeout_seconds": 60.0,
        "image_timeout_seconds": 120.0,
        "http_max_retries": 2,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def fixed_clock() -> Callable[[], datetime]:
    return lambda: FIXED_EPOCH


def fixed_monotonic() -> Callable[[], float]:
    state = {"t": 1000.0}

    def tick() -> float:
        state["t"] += 0.001
        return state["t"]

    return tick


def fake_image_factory(**kwargs: Any) -> Callable[[], FakeImageProvider]:
    return lambda: FakeImageProvider(**kwargs)


def real_v05_dir() -> Path:
    return REAL_V05_DIR


def v05_fingerprint() -> str:
    return compute_corpus_fingerprint(load_corpus(REAL_V05_DIR).manifest)


def v05_manifest_sha256() -> str:
    return hashlib.sha256((REAL_V05_DIR / "manifest.json").read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# 合成 case / annotation
# ---------------------------------------------------------------------------


def make_case(
    case_id: str,
    *,
    layer: str,
    category: str,
    primary_path: str,
    confirmed_intent: dict[str, str],
    delegated: Iterable[str] = (),
    pinned: Iterable[str] = (),
    explicit: Iterable[str] = (),
    rule_under_test: str | None = None,
    repetitions: int = 1,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "dataset_version": "bc_cases_v0_6_synthetic",
        "layer": layer,
        "category": category,
        "primary_path": primary_path,
        "user_text": f"synthetic user text for {case_id}",
        "confirmed_intent": confirmed_intent,
        "delegated_paths": list(delegated),
        "pinned_paths": list(pinned),
        "explicit_paths": list(explicit),
        "rule_under_test": rule_under_test,
        "pair": {"arms": ["B", "C"], "repetitions": repetitions},
        "group_id": f"{case_id}#g1",
        "parent_case_id": None,
        "derived_from_dataset": False,
        "source": {
            "source_type": "project_original",
            "license": "internal-project-original",
            "external_ids": [],
            "source_revision": "synthetic-test",
            "declaration": "synthetic test fixture; not a frozen Step 01 sample",
        },
        "notes": "",
    }


def make_annotation(
    case: dict[str, Any],
    *,
    retrieval_outcome: str,
    queried: bool,
    reason_code: str | None,
    candidate_value: str | None = None,
    knowledge_id: str | None = None,
    adoption_outcome: str | None = None,
    adoption_reason: str | None = None,
    bc_relation: str | None = None,
    overreach_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    primary = case["primary_path"]
    if bc_relation is None:
        bc_relation = (
            "c_differs_only_in_authorized_path_after_seed_control"
            if adoption_outcome == "adopted"
            else "b_and_c_identical_on_target_path"
        )
    return {
        "case_id": case["case_id"],
        "dataset_version": case["dataset_version"],
        "layer": case["layer"],
        "primary_path": primary,
        "rule_under_test": case["rule_under_test"],
        "expected_retrieval": {
            "queried": queried,
            "outcome": retrieval_outcome,
            "reason_code": reason_code,
            "knowledge_id": knowledge_id,
            "candidate_value": candidate_value,
            "top_score_positive": None,
            "mechanism": "synthetic test expectation",
        },
        "expected_adoption": {
            "outcome": adoption_outcome or "not_adopted_fallback",
            "reason_code": adoption_reason,
            "adoption_rejection_reason": adoption_reason,
        },
        "expected_constraints": {
            "explicit_value_preserved": None,
            "pin_preserved": None,
            "confirmation_binding_preserved": True,
            "immutable_history_violations": 0,
            "unauthorized_path_changes": 0,
        },
        "expected_bc_relation": bc_relation,
        "expected_prompt_delta": {
            "path": primary,
            "b_value_source": "deterministic_fallback",
            "c_value_source": "knowledge_unit",
            "other_paths_identical": True,
        },
        "fallback_constraint": "none",
        "stratification": {
            "layer": case["layer"],
            "category": case["category"],
            "primary_path": primary,
            "reported_separately": True,
            "independent_context_unit": case["case_id"],
        },
        "metric_roles": ["completion_rate"],
        "annotation_basis": "synthetic test annotation",
        "annotation_status": "pre_registered_pending_independent_review",
        "annotator": "synthetic-test",
        "independent_reviewer": None,
        "independent_reviewed_at": None,
        "overreach_check": overreach_check,
    }


def default_synthetic_cases(*, repetitions: int = 1) -> tuple[list[dict], list[dict]]:
    """1 个 L1 适用（lighting）+ 1 个明确值 + 1 个 PIN + 1 个无命中。"""
    cases: list[dict] = []
    annotations: list[dict] = []

    l1 = make_case(
        "syn-lit-01",
        layer="L1_applicable",
        category="applicable_adoption",
        primary_path="lighting.character",
        confirmed_intent=dict(POSITIVE_VALUES["lighting.character"]),
        delegated=["lighting.character"],
        rule_under_test="lighting.character.soft_for_tight_framing",
        repetitions=repetitions,
    )
    cases.append(l1)
    annotations.append(
        make_annotation(
            l1,
            queried=True,
            retrieval_outcome="adopted",
            reason_code="unique_top_candidate",
            knowledge_id="lighting.character.soft_for_tight_framing",
            candidate_value="soft",
            adoption_outcome="adopted",
        )
    )

    explicit = make_case(
        "syn-explicit-01",
        layer="L2_explicit_pin_control",
        category="explicit_value_control",
        primary_path="camera.depth_of_field",
        confirmed_intent={"camera.depth_of_field": "deep", **POSITIVE_VALUES["camera.depth_of_field"]},
        explicit=["camera.depth_of_field"],
        repetitions=repetitions,
    )
    cases.append(explicit)
    annotations.append(
        make_annotation(
            explicit,
            queried=False,
            retrieval_outcome="not_queried",
            reason_code="explicit_value_not_queried",
            adoption_outcome="path_not_queried",
            adoption_reason="explicit_value_preserved",
            overreach_check={
                "satisfied_rule_id": "camera.depth_of_field.shallow_for_close_up",
                "rule_candidate_value": "shallow",
                "explicit_value": "deep",
                "differs": True,
            },
        )
    )

    pinned_values = dict(POSITIVE_VALUES["composition.framing"])
    pinned_values["composition.framing"] = "close_up"  # 现存值（与知识候选 medium_shot 不同）
    pinned = make_case(
        "syn-pinned-01",
        layer="L2_explicit_pin_control",
        category="pinned_control",
        primary_path="composition.framing",
        confirmed_intent=pinned_values,
        delegated=[],
        pinned=["composition.framing"],
        explicit=["composition.framing"],
        repetitions=repetitions,
    )
    cases.append(pinned)
    annotations.append(
        make_annotation(
            pinned,
            queried=False,
            retrieval_outcome="not_queried",
            reason_code="pinned_existing_value_not_queried",
            adoption_outcome="path_not_queried",
            adoption_reason="path_pinned",
            overreach_check={
                "satisfied_rule_id": "composition.framing.medium_shot_for_standing_pose",
                "rule_candidate_value": "medium_shot",
                "explicit_value": "close_up",
                "differs": True,
            },
        )
    )

    no_hit = make_case(
        "syn-nohit-01",
        layer="L3_no_hit_fallback",
        category="no_hit_fallback",
        primary_path="camera.depth_of_field",
        confirmed_intent=dict(NO_HIT_VALUES),
        delegated=["camera.depth_of_field"],
        repetitions=repetitions,
    )
    cases.append(no_hit)
    annotations.append(
        make_annotation(
            no_hit,
            queried=True,
            retrieval_outcome="no_hit",
            reason_code="no_hit",
            adoption_outcome="not_adopted_fallback",
            adoption_reason="no_adopted_result",
        )
    )
    return cases, annotations


# ---------------------------------------------------------------------------
# Fixture 落盘
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fixture:
    root: Path
    cases_path: Path
    annotations_path: Path
    config_path: Path
    protocol_path: Path
    manifest_path: Path
    cases: list[dict]
    annotations: list[dict]
    config: dict


def write_fixture(
    root: Path,
    *,
    cases: list[dict],
    annotations: list[dict],
    repetitions: int = 1,
    real_run_authorized: bool = False,
    budget_overrides: dict[str, Any] | None = None,
    providers_overrides: dict[str, Any] | None = None,
    providers_llm_overrides: dict[str, Any] | None = None,
    thresholds_status: str = "pending_user_confirmation_before_results",
    thresholds_overrides: dict[str, Any] | None = None,
) -> Fixture:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    cases_path = root / "cases.jsonl"
    annotations_path = root / "annotations.jsonl"
    config_path = root / "run_config.json"
    protocol_path = root / "protocol.md"
    manifest_path = root / "frozen_manifest.json"

    cases_path.write_text(
        "\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n",
        encoding="utf-8",
    )
    annotations_path.write_text(
        "\n".join(json.dumps(ann, ensure_ascii=False) for ann in annotations) + "\n",
        encoding="utf-8",
    )
    protocol_path.write_text("# synthetic v0.6 protocol\n", encoding="utf-8")
    config: dict[str, Any] = {
        "config_version": "bc_v0_6_synthetic",
        "protocol_version": "bc_protocol_v0_6_synthetic",
        "dataset_version": "bc_cases_v0_6_synthetic",
        "real_run_authorized": real_run_authorized,
        "authorization": {
            "gate": "G1",
            "status": "granted" if real_run_authorized else "not_granted",
        },
        "knowledge_snapshot": {
            "corpus_dir": "knowledge_base/v0.5",
            "corpus_version": "v0.5-approved-1",
            "corpus_fingerprint": v05_fingerprint(),
            "manifest_sha256": v05_manifest_sha256(),
            "approved_unit_count": 5,
        },
        "repetitions": {"independent_generations_per_case_per_arm": repetitions},
        "budget": {
            "status": "synthetic",
            "currency": None,
            "max_total_amount": None,
            "max_requests": None,
            "max_attempts": None,
            "per_request_price_source": None,
            "unknown_cost_requests_counted_as_consumed": True,
        },
        "providers": {
            "llm": {
                "model": None,
                "model_identity_verified": False,
                "timeout_seconds": None,
                "max_retries": None,
            },
            "image": {
                "model_default": FAKE_IMAGE_MODEL,
                "model_identity_verified": False,
                "size": OUTPUT_SIZE,
                "images_per_generation": 1,
                "timeout_seconds": 120.0,
                "max_retries": 2,
            },
        },
        "credentials": {
            "base_url_env": "VIA_PROVIDER_BASE_URL",
            "api_key_env": "VIA_PROVIDER_API_KEY",
        },
        "thresholds": {
            "status": thresholds_status,
            "minimum_meaningful_gain": None,
            "allowed_completion_rate_difference": None,
            "max_latency_seconds": None,
            "max_cost": None,
        },
    }
    if budget_overrides:
        config["budget"].update(budget_overrides)
    if thresholds_overrides:
        config["thresholds"].update(thresholds_overrides)
    if providers_overrides:
        config["providers"]["image"].update(providers_overrides)
    if providers_llm_overrides:
        config["providers"]["llm"].update(providers_llm_overrides)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "manifest_version": "synthetic",
                "dataset_version": "bc_cases_v0_6_synthetic",
                "protocol_version": "bc_protocol_v0_6_synthetic",
                "config_version": "bc_v0_6_synthetic",
                "algorithm": "sha256",
                "roles": {
                    "protocol": str(protocol_path),
                    "config": str(config_path),
                    "dataset": str(cases_path),
                    "annotations": str(annotations_path),
                },
                "files": {
                    str(protocol_path): hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
                    str(config_path): hashlib.sha256(config_path.read_bytes()).hexdigest(),
                    str(cases_path): hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                    str(annotations_path): hashlib.sha256(annotations_path.read_bytes()).hexdigest(),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return Fixture(
        root=root,
        cases_path=cases_path,
        annotations_path=annotations_path,
        config_path=config_path,
        protocol_path=protocol_path,
        manifest_path=manifest_path,
        cases=cases,
        annotations=annotations,
        config=config,
    )


def make_runner(
    tmp_path: Path,
    fixture: Fixture,
    *,
    image_factory: Callable[[], Any] | None = None,
    repetitions: int | None = None,
    verify_frozen: bool = False,
    factory_mode: str = "test",
    settings: Settings | None = None,
    **kwargs: Any,
) -> PairedRunner:
    return PairedRunner(
        settings=settings or make_settings(),
        image_factory=image_factory or fake_image_factory(),
        output_root=Path(tmp_path) / "runs",
        manifest_path=fixture.manifest_path,
        cases_path=fixture.cases_path,
        annotations_path=fixture.annotations_path,
        config_path=fixture.config_path,
        protocol_path=fixture.protocol_path,
        repetitions=repetitions,
        verify_frozen=verify_frozen,
        knowledge_corpus_dir=REAL_V05_DIR,
        factory_mode=factory_mode,
        clock=fixed_clock(),
        monotonic=fixed_monotonic(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 读取工具
# ---------------------------------------------------------------------------


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def read_prompt_text(db_path: Path, prompt_artifact_id: str) -> str:
    repo = SQLiteRepository(db_path)
    try:
        stored = repo.get_prompt_artifact(prompt_artifact_id)
        return PromptArtifact.model_validate_json(stored.payload).prompt
    finally:
        repo.close()


__all__ = [
    "FAKE_IMAGE_MODEL",
    "REAL_V05_DIR",
    "FIXED_EPOCH",
    "POSITIVE_VALUES",
    "NO_HIT_VALUES",
    "EXPECTED_CANDIDATES",
    "make_settings",
    "fixed_clock",
    "fixed_monotonic",
    "fake_image_factory",
    "real_v05_dir",
    "v05_fingerprint",
    "v05_manifest_sha256",
    "make_case",
    "make_annotation",
    "default_synthetic_cases",
    "Fixture",
    "write_fixture",
    "make_runner",
    "load_json",
    "load_jsonl",
    "read_prompt_text",
]
