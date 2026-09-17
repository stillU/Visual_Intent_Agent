"""v0.5 Step 01：开放文本样本准备与项目派生工程样例（最小、纯标准库、可复现）。

本工具单独完成 Step 01 的资源下载、核验、抽样与工程样例准备，重点包括：

1. **COCO-CN 真实字段解析**。锁定托管归档里没有 JSON：人工中文描述位于
   ``imageid.human-written-caption.txt``，格式为 ``<image_id>#<序号>\\t<中文>``。
   本工具在归档安全检查通过后，只解析人工撰写描述（``human_written``）作为样本；
   人工翻译 / 机器翻译 / 分词变体只登记、不作为样本，避免混淆子集。
2. **项目派生工程样例**。以开发（development）池样本为父样本，附加**显式标注为
   项目添加**的确认 / PIN / 委托状态，供 Step 03/04 参考。记录带
   ``material_stage = "historical_pre_review"`` 与 ``current_release_input = false``：
   这是审核前的历史准备，**不是**当前发布验收输入，也不计为已执行覆盖；当前验收见
   ``tests/fixtures/knowledge/v0_5_acceptance_cases.json``。暂存标注不是新增运行时 Schema。

同时自行完成七个锁定文本的下载 / 哈希校验、许可归档、固定排序抽样与按组
70/30 划分，因此单独运行即可重建 Step 01 的全部暂存产物。

边界
----

- 只读取固定来源清单 ``docs/task_books/rag_resources/sources.lock.json``，**绝不改写**
  期望 sha256 / bytes / nonempty_rows；校验失败记为缺口。
- 下载设超时、单文件字节、单次运行总字节上限；不执行下载内容、不加载远端代码、
  不安装依赖、不调用真实图片 Provider、不运行正式评测。
- 原始文件只落盘不改写；派生物独立保存到暂存根下的 ``derived/``。
- 永不删除暂存根下已存在的其它目录（例如并发写入的 ``knowledge_review/``）；
  只创建自己的目录并写自己的文件。
- COCO-CN 归档：先做 tar 安全检查（绝对路径 / ``..`` / 链接 / 特殊成员 / 成员数 /
  解包总大小），再只提取文本与许可；``._*`` AppleDouble 元数据与 ``.py`` 脚本一律跳过。
- 归档不含内部 LICENSE/README 时如实记为缺口，许可只沿用仓库 / 数据集卡结论。

产物（可重建）
--------------

- ``derived/download_record.json``：七个文本 + 许可 + COCO 归档的核验与缺口记录。
- ``derived/source_samples.jsonl``：70 条暂存来源样本（GenEval 20 + CompBench 30 + COCO-CN 20）。
- ``derived/split_manifest.json``：按组 70/30 的开发 / 保留划分统计。
- ``derived/coco_cn_field_report.json``：归档实际子集、字段与样本登记。
- ``derived/project_derived_samples.jsonl``：30 条项目派生工程样例。
- ``README.md``（暂存根）：来源、许可、复现命令与缺口说明。

用法::

    uv run python tools/prepare_v0_5_resources.py \
        --staging data/staging/rag_resources_20260916 \
        --coco-archive /path/to/coco-cn-version1805v1.1.tar.gz
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

TOOL_NAME = "tools/prepare_v0_5_resources.py"
LOCK_SCHEMA = "resource-handoff.v1"
DOWNLOAD_RECORD_SCHEMA = "resource-download-record.v1"
SOURCE_SAMPLES_SCHEMA = "resource-source-samples.v1"
SPLIT_MANIFEST_SCHEMA = "resource-split-manifest.v1"
FIELD_REPORT_SCHEMA = "coco-cn-field-report.v1"
PROJECT_DERIVED_SCHEMA = "project-derived-engineering-sample.v1"

DEFAULT_STAMP = "20260916"
SPLIT_RATIO = 0.7

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_TAR_MEMBERS = 4096
DEFAULT_MAX_UNPACK_BYTES = 256 * 1024 * 1024

#: DATASETS.md 的最小准备配额（子类配额 + 总量上限）。
QUOTAS: dict[str, dict[str, Any]] = {
    "geneval": {"strata": {"single_object": 10, "counting": 10}, "total": 20},
    "t2i_compbench_original": {
        "strata": {
            "color": 5,
            "shape": 5,
            "texture": 5,
            "spatial": 5,
            "non_spatial": 5,
            "complex": 5,
        },
        "total": 30,
    },
    "coco_cn": {"strata": {}, "total": 20},
}

GENEVAL_SCOPE: dict[str, tuple[list[str], list[str]]] = {
    "single_object": (["single_object"], []),
    "two_object": ([], ["two_object_relation"]),
    "counting": (["counting"], []),
    "colors": (["color"], []),
    "position": ([], ["position_relation"]),
    "color_attr": ([], ["attribute_binding"]),
}
COMPBENCH_SCOPE: dict[str, tuple[list[str], list[str]]] = {
    "color": (["color"], []),
    "shape": (["shape"], []),
    "texture": (["texture"], []),
    "spatial": ([], ["spatial_relation"]),
    "non_spatial": ([], ["non_spatial_relation"]),
    "complex": ([], ["complex_composition"]),
}
COCO_SCOPE: tuple[list[str], list[str]] = (["natural_chinese_description"], [])

#: COCO-CN 归档内的描述子集。只有 ``used_for_samples`` 为真的人工撰写描述入样。
COCO_CAPTION_SUBSETS: tuple[tuple[str, str, bool], ...] = (
    ("imageid.human-written-caption.txt", "human_written_caption", True),
    ("imageid.human-written-caption.bosonseg.txt", "human_written_caption_wordseg", False),
    ("imageid.manually-translated-caption.txt", "manually_translated_caption", False),
    ("imageid.manually-translated-caption.bosonseg.txt", "manually_translated_caption_wordseg", False),
    ("imageid.machine-translated-caption.bosonseg.txt", "machine_translated_caption_wordseg", False),
)
COCO_TAGS_FILE = "imageid.human-written-tags.txt"

#: macOS AppleDouble 资源叉 / 打包元数据：不是数据，一律跳过。
APPLEDOUBLE_PREFIX = "._"

#: 三条知识路径的受授权候选值（与 PromptEngine 的 DELEGATED_CANDIDATES 一致，仅用于
#: 项目派生暂存样例的标注；不复制为运行时权威表，权威表仍在代码里）。
PATH_CANDIDATES: dict[str, tuple[str, ...]] = {
    "lighting.character": ("soft", "dramatic", "natural"),
    "composition.framing": ("close_up", "medium_shot", "wide_shot"),
    "camera.depth_of_field": ("shallow", "deep"),
}

#: DATASETS.md 要求的十种覆盖状态（顺序固定，保证派生样例可复现）。
PROJECT_COVERAGE_TAGS: tuple[str, ...] = (
    "delegated",
    "explicit_value",
    "pinned",
    "unconfirmed_condition",
    "condition_mismatch",
    "model_mismatch",
    "unapproved_knowledge",
    "conflict",
    "missing_knowledge",
    "successful_adoption",
)


class ResourceError(Exception):
    """工具级错误（清单非法、路径不安全、暂存缺失等）。"""


class FetchError(ResourceError):
    """下载失败或超出限额。"""


class UnsafeArchiveError(ResourceError):
    """归档未通过安全检查。"""


@dataclass(frozen=True)
class Limits:
    timeout_seconds: float = DEFAULT_TIMEOUT
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES
    max_tar_members: int = DEFAULT_MAX_TAR_MEMBERS
    max_unpack_bytes: int = DEFAULT_MAX_UNPACK_BYTES


@dataclass
class FetchState:
    downloaded_bytes: int = 0


@dataclass
class Sample:
    """一条暂存的派生来源样本（不是领域 Schema）。"""

    dataset_id: str
    revision: str
    source_file: str
    original_id: Any
    group_id: str
    language: str
    original_text: str
    supported_scope: list[str]
    unsupported_constraints: list[str]
    split: str = ""
    license: str = ""
    change_note: str = ""
    image_id: str = ""
    source_note: str = ""
    _sort_key: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "revision": self.revision,
            "source_file": self.source_file,
            "original_id": self.original_id,
            "group_id": self.group_id,
            "language": self.language,
            "original_text": self.original_text,
            "supported_scope": list(self.supported_scope),
            "unsupported_constraints": list(self.unsupported_constraints),
            "split": self.split,
            "license": self.license,
            "change_note": self.change_note,
            "image_id": self.image_id,
            "source_note": self.source_note,
        }


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_nonempty_rows(path: Path) -> int:
    count = 0
    with open(path, "rb") as handle:
        for line in handle:
            if line.strip():
                count += 1
    return count


def sort_key_for(dataset_id: str, original_id: Any, stamp: str = DEFAULT_STAMP) -> str:
    payload = f"{stamp}|{dataset_id}|{original_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def _safe_relative(raw: str, *, label: str) -> PurePosixPath:
    normalized = str(raw).replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ResourceError(f"unsafe path in {label}: {raw!r}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise ResourceError(f"unsafe path in {label}: {raw!r}")
    return PurePosixPath(*parts)


def _basename_from_url(url: str, fallback: str) -> str:
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    return name or fallback


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def load_lock(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != LOCK_SCHEMA:
        raise ResourceError(f"unexpected lock schema: {data.get('schema')!r}")
    if not isinstance(data.get("datasets"), list) or not data["datasets"]:
        raise ResourceError("lock file has no datasets")
    return data


# ---------------------------------------------------------------------------
# 下载与校验
# ---------------------------------------------------------------------------


def fetch_asset(
    url: str,
    dest: Path,
    limits: Limits,
    *,
    state: FetchState,
    opener: Callable[[str, float], Any] | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """复用已有文件；缺失时在限额内下载。失败抛 ``FetchError``。"""

    dest = Path(dest)
    if dest.exists():
        return {
            "status": "reused",
            "reused": True,
            "bytes": dest.stat().st_size,
            "sha256": sha256_file(dest),
        }
    if offline:
        raise FetchError("offline_mode: file missing and downloads disabled")

    open_url = opener or urllib.request.urlopen
    remaining = limits.max_total_bytes - state.downloaded_bytes
    if remaining <= 0:
        raise FetchError("total_bytes_limit_reached")

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    written = 0
    try:
        with open_url(url, timeout=limits.timeout_seconds) as response:
            with open(partial, "wb") as out:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limits.max_file_bytes:
                        raise FetchError(
                            f"file_bytes_limit_exceeded: {written} > {limits.max_file_bytes}"
                        )
                    if state.downloaded_bytes + written > limits.max_total_bytes:
                        raise FetchError(
                            "total_bytes_limit_exceeded: "
                            f"{state.downloaded_bytes + written} > {limits.max_total_bytes}"
                        )
                    out.write(chunk)
        if written == 0:
            raise FetchError("empty_download")
        partial.replace(dest)
    except FetchError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - 网络/IO 失败统一转为缺口
        partial.unlink(missing_ok=True)
        raise FetchError(f"fetch_failed: {type(exc).__name__}: {exc}") from exc

    state.downloaded_bytes += written
    return {
        "status": "downloaded",
        "reused": False,
        "bytes": written,
        "sha256": sha256_file(dest),
    }


def verify_locked_file(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """对照锁清单核验 sha256 / bytes / nonempty_rows；绝不改写期望值。"""

    path = Path(path)
    actual = {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "nonempty_rows": count_nonempty_rows(path),
    }
    mismatches: list[str] = []
    for name in ("sha256", "bytes", "nonempty_rows"):
        want = expected.get(name)
        if want is None:
            mismatches.append(f"{name}:missing_expected")
        elif actual[name] != want:
            mismatches.append(f"{name}:{actual[name]}!={want}")
    if not mismatches:
        status = "verified"
    elif any(item.endswith(":missing_expected") for item in mismatches):
        status = "missing_expected"
    elif any(item.startswith("sha256:") for item in mismatches):
        status = "hash_mismatch"
    elif any(item.startswith("bytes:") for item in mismatches):
        status = "bytes_mismatch"
    elif any(item.startswith("nonempty_rows:") for item in mismatches):
        status = "rows_mismatch"
    else:
        status = "missing_expected"
    return {"status": status, "actual": actual, "mismatches": mismatches}


# ---------------------------------------------------------------------------
# tar 安全检查（COCO-CN）
# ---------------------------------------------------------------------------


def _check_tar_member(member: tarfile.TarInfo) -> None:
    normalized = str(member.name).replace("\\", "/")
    stripped = normalized.strip("/")
    if not stripped or stripped == ".":
        if member.isdir():
            return
        raise UnsafeArchiveError(f"empty member name: {member.name!r}")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise UnsafeArchiveError(f"absolute member path: {member.name!r}")
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UnsafeArchiveError(f"path traversal member: {member.name!r}")
    if member.issym() or member.islnk():
        raise UnsafeArchiveError(f"link member rejected: {member.name!r}")
    if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
        raise UnsafeArchiveError(f"special member rejected: {member.name!r}")
    if not (member.isreg() or member.isdir()):
        raise UnsafeArchiveError(f"unsupported member type: {member.name!r}")


def inspect_tar(path: Path, limits: Limits) -> dict[str, Any]:
    """只读遍历并验证归档；任一不安全项立即拒绝。"""

    members: list[str] = []
    total = 0
    with tarfile.open(path, "r:*") as tar:
        for member in tar:
            if len(members) + 1 > limits.max_tar_members:
                raise UnsafeArchiveError(f"too many members: > {limits.max_tar_members}")
            _check_tar_member(member)
            members.append(str(member.name))
            if member.isreg():
                total += member.size
                if total > limits.max_unpack_bytes:
                    raise UnsafeArchiveError(
                        f"unpack size exceeds limit: > {limits.max_unpack_bytes}"
                    )
    return {"member_count": len(members), "members": members, "total_unpack_bytes": total}


def _coco_data_allowed(name: str) -> bool:
    """只允许文本 / 许可类普通文件；跳过 AppleDouble 与脚本。"""

    parts = [p for p in str(name).replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        return False
    base = parts[-1]
    if base.startswith(APPLEDOUBLE_PREFIX):
        return False
    if any(p.startswith(APPLEDOUBLE_PREFIX) for p in parts):
        return False
    suffix = PurePosixPath(base).suffix.casefold()
    if suffix == ".py":
        return False
    lower = base.casefold()
    if lower in {"license", "license.txt", "license.md", "notice", "notice.md",
                 "readme", "readme.md", "readme.txt", "copying", "copying.txt"}:
        return True
    if lower.startswith(("license", "readme", "notice", "copying")):
        return True
    return suffix in {".txt", ".json", ".jsonl", ".csv", ".tsv", ".md", ".yaml", ".yml"}


def safe_extract_coco(
    archive: Path, dest: Path, limits: Limits
) -> dict[str, Any]:
    """安全检查通过后，只提取文本 / 许可普通文件到 ``dest``（可重复运行）。"""

    inspection = inspect_tar(archive, limits)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    extracted: list[str] = []
    skipped: list[str] = []
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            if not member.isreg():
                continue
            if not _coco_data_allowed(member.name):
                skipped.append(str(member.name))
                continue
            normalized = str(member.name).replace("\\", "/")
            parts = [p for p in normalized.split("/") if p not in ("", ".")]
            target = dest.joinpath(*parts)
            resolved = target.resolve()
            if dest_resolved not in resolved.parents:
                raise UnsafeArchiveError(f"extraction escape: {member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                skipped.append(normalized)
                continue
            with source, open(target, "wb") as out:
                shutil.copyfileobj(source, out)
            extracted.append(normalized)
    return {
        "member_count": inspection["member_count"],
        "total_unpack_bytes": inspection["total_unpack_bytes"],
        "extracted": extracted,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_geneval(path: Path, dataset: dict[str, Any], root: Path) -> tuple[list[Sample], list[dict[str, Any]]]:
    samples: list[Sample] = []
    gaps: list[dict[str, Any]] = []
    source_file = _rel(path, root)
    with open(path, "rb") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                gaps.append({"code": "geneval_parse_error", "line": lineno, "detail": str(exc)})
                continue
            prompt = row.get("prompt") if isinstance(row, dict) else None
            if not isinstance(prompt, str) or not prompt.strip():
                gaps.append({"code": "geneval_missing_prompt", "line": lineno})
                continue
            tag = str(row.get("tag", "")) if isinstance(row, dict) else ""
            supported, unsupported = GENEVAL_SCOPE.get(tag, ([], []))
            samples.append(
                Sample(
                    dataset_id=dataset["id"],
                    revision=dataset.get("revision", ""),
                    source_file=source_file,
                    original_id=lineno,
                    group_id="",
                    language="en",
                    original_text=prompt,
                    supported_scope=list(supported),
                    unsupported_constraints=list(unsupported),
                    license=str(dataset.get("license", "")),
                    change_note=(
                        "derived from locked GenEval prompt; benchmark metadata fields kept as "
                        "source_note only, never injected as user expression"
                    ),
                    source_note=f"tag={tag}" if tag else "",
                    _sort_key=sort_key_for(dataset["id"], lineno),
                )
            )
    return samples, gaps


def parse_compbench(
    files: Sequence[Path], dataset: dict[str, Any], root: Path
) -> tuple[list[Sample], list[dict[str, Any]]]:
    samples: list[Sample] = []
    gaps: list[dict[str, Any]] = []
    for path in files:
        category = path.name.split("_val")[0]
        supported, unsupported = COMPBENCH_SCOPE.get(category, ([], []))
        source_file = _rel(path, root)
        with open(path, "rb") as handle:
            for lineno, raw in enumerate(handle, start=1):
                text = raw.decode("utf-8", errors="replace")
                if not text.strip():
                    continue
                samples.append(
                    Sample(
                        dataset_id=dataset["id"],
                        revision=dataset.get("revision", ""),
                        source_file=source_file,
                        original_id=lineno,
                        group_id="",
                        language="en",
                        original_text=text.rstrip("\n").rstrip("\r"),
                        supported_scope=list(supported),
                        unsupported_constraints=list(unsupported),
                        license=str(dataset.get("license", "")),
                        change_note=(
                            "derived from locked T2I-CompBench validation text; raw file untouched"
                        ),
                        source_note=f"category={category}",
                        _sort_key=sort_key_for(dataset["id"], lineno),
                    )
                )
    return samples, gaps


def parse_coco_caption_file(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """解析 ``<image_id>#<序号>\\t<中文>`` 形式的 COCO-CN 描述文件。

    返回 ``(records, gaps)``。不猜字段：只有同时拿到 ``image_id`` 与非空文本才产出记录。
    """

    records: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    try:
        raw_text = Path(path).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        gaps.append({"code": "coco_decode_error", "file": str(path), "detail": str(exc)})
        return records, gaps
    for lineno, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            continue
        left, tab, text = line.partition("\t")
        if not tab:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            left, text = parts
        left = left.strip()
        text = text.strip()
        if not left or not text or "#" not in left:
            continue
        image_id, _, caption_index = left.partition("#")
        if not image_id:
            continue
        records.append(
            {
                "original_id": left,
                "image_id": image_id,
                "caption_index": caption_index,
                "text": text,
                "row_index": lineno,
            }
        )
    return records, gaps


def discover_coco_subsets(extracted_dir: Path) -> dict[str, Any]:
    """登记归档内实际存在的描述子集与字段；不修改任何文件。"""

    root = Path(extracted_dir)
    report: dict[str, Any] = {"subsets": [], "tags_file": None}
    for filename, label, used in COCO_CAPTION_SUBSETS:
        matches = sorted(root.rglob(filename))
        if not matches:
            report["subsets"].append(
                {"file": filename, "label": label, "present": False, "used_for_samples": used}
            )
            continue
        path = matches[0]
        entry: dict[str, Any] = {
            "file": _rel(path, root),
            "label": label,
            "present": True,
            "used_for_samples": used,
            "nonempty_rows": count_nonempty_rows(path),
            "declared_field_shape": "<image_id>#<caption_index>\\t<text>",
        }
        if used:
            # 只有入样子集做完整字段解析；其余子集仅登记存在与行数，避免混淆子集。
            records, gaps = parse_coco_caption_file(path)
            entry["records"] = len(records)
            entry["distinct_image_ids"] = len({r["image_id"] for r in records})
            entry["gaps"] = gaps
        else:
            entry["parsed_for_samples"] = False
        report["subsets"].append(entry)
    tag_matches = sorted(root.rglob(COCO_TAGS_FILE))
    if tag_matches:
        path = tag_matches[0]
        nonempty = count_nonempty_rows(path)
        report["tags_file"] = {
            "file": _rel(path, root),
            "present": True,
            "nonempty_rows": nonempty,
            "used_for_samples": False,
            "field_shape": "<image_id> <tag> <tag> ...",
        }
    else:
        report["tags_file"] = {"file": COCO_TAGS_FILE, "present": False, "used_for_samples": False}
    return report


def build_coco_samples(
    extracted_dir: Path,
    dataset: dict[str, Any],
    root: Path,
    field_report: dict[str, Any],
) -> tuple[list[Sample], list[dict[str, Any]]]:
    """只从人工撰写描述子集构建 COCO-CN 样本。"""

    samples: list[Sample] = []
    gaps: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    for entry in field_report["subsets"]:
        if entry.get("present") and entry.get("used_for_samples"):
            chosen = entry
            break
    if chosen is None:
        gaps.append(
            {
                "code": "coco_human_written_subset_missing",
                "dataset_id": dataset["id"],
                "detail": "archive does not contain imageid.human-written-caption.txt",
            }
        )
        return samples, gaps

    path = Path(extracted_dir) / chosen["file"]
    records, parse_gaps = parse_coco_caption_file(path)
    gaps.extend(parse_gaps)
    if not records:
        gaps.append({"code": "coco_no_text_records", "dataset_id": dataset["id"]})
        return samples, gaps
    for record in records:
        samples.append(
            Sample(
                dataset_id=dataset["id"],
                revision=str(dataset.get("revision", "")),
                source_file=f"extracted/{dataset['id']}/{chosen['file']}",
                original_id=record["original_id"],
                group_id="",
                language="zh",
                original_text=record["text"],
                supported_scope=list(COCO_SCOPE[0]),
                unsupported_constraints=list(COCO_SCOPE[1]),
                license=str(dataset.get("license", "")),
                change_note=(
                    "derived from COCO-CN human-written Chinese caption subset after safe "
                    "extraction; translations and word-segmented variants not used"
                ),
                image_id=record["image_id"],
                source_note=f"subset={chosen['label']}",
                _sort_key=sort_key_for(dataset["id"], record["original_id"]),
            )
        )
    return samples, gaps


# ---------------------------------------------------------------------------
# 分组、抽样、划分
# ---------------------------------------------------------------------------


def group_samples(records: Sequence[Sample]) -> list[dict[str, Any]]:
    """按相同原文 / 同图 ID 做并查集分组，并在组内按原文去重。"""

    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    record_keys: list[list[tuple[str, str]]] = []
    key_owner: dict[tuple[str, str], int] = {}
    for index, record in enumerate(records):
        keys: list[tuple[str, str]] = []
        if record.original_text.strip():
            keys.append(("text", normalize_text(record.original_text)))
        if record.image_id:
            keys.append(("image", str(record.image_id)))
        record_keys.append(keys)
        for key in keys:
            if key in key_owner:
                union(index, key_owner[key])
            else:
                key_owner[key] = index

    buckets: dict[int, list[int]] = {}
    for index in range(len(records)):
        buckets.setdefault(find(index), []).append(index)

    groups: list[dict[str, Any]] = []
    for members in buckets.values():
        seen_text: dict[str, int] = {}
        kept: list[int] = []
        duplicates: list[Any] = []
        for index in sorted(
            members, key=lambda i: (records[i]._sort_key, str(records[i].original_id))
        ):
            record = records[index]
            text_key = normalize_text(record.original_text)
            if not text_key:
                kept.append(index)
                continue
            if text_key in seen_text:
                duplicates.append(record.original_id)
                continue
            seen_text[text_key] = index
            kept.append(index)
        keys = sorted({key for index in kept for key in record_keys[index]})
        digest = sha256_bytes("\n".join(f"{k}={v}" for k, v in keys).encode("utf-8"))
        group_id = f"{records[members[0]].dataset_id}:grp:{digest[:16]}"
        for index in kept:
            records[index].group_id = group_id
        groups.append(
            {
                "group_id": group_id,
                "members": kept,
                "duplicates_removed": [str(item) for item in duplicates],
                "order_key": min(records[i]._sort_key for i in kept),
            }
        )
    groups.sort(key=lambda g: (g["order_key"], g["group_id"]))
    return groups


def _stratum_of(record: Sample) -> str:
    for prefix in ("category=", "tag="):
        if record.source_note.startswith(prefix):
            return record.source_note.split("=", 1)[1]
    return ""


def sample_groups(
    records: list[Sample],
    dataset_id: str,
    *,
    stamp: str = DEFAULT_STAMP,
    backfill: bool = True,
) -> tuple[list[Sample], list[dict[str, Any]]]:
    """按组分层抽样；整组保留，绝不拆开一组。"""

    quota = QUOTAS.get(dataset_id, {"strata": {}, "total": 20})
    strata_quota: dict[str, int] = dict(quota.get("strata", {}))
    total_quota: int = int(quota.get("total", 20))
    gaps: list[dict[str, Any]] = []
    if not records:
        gaps.append({"code": "no_records", "dataset_id": dataset_id})
        return [], gaps

    for record in records:
        record._sort_key = sort_key_for(record.dataset_id, record.original_id, stamp)

    groups = group_samples(records)
    taken: set[int] = set()
    stratum_count: dict[str, int] = {name: 0 for name in strata_quota}
    total_count = 0
    chosen: list[int] = []

    def try_take(group_index: int, *, enforce_strata: bool) -> bool:
        nonlocal total_count
        members = groups[group_index]["members"]
        if total_count + len(members) > total_quota:
            return False
        touched: dict[str, int] = {}
        for index in members:
            name = _stratum_of(records[index])
            touched[name] = touched.get(name, 0) + 1
        if enforce_strata:
            for name, count in touched.items():
                if name in strata_quota and stratum_count.get(name, 0) + count > strata_quota[name]:
                    return False
        for name in touched:
            stratum_count[name] = stratum_count.get(name, 0) + touched[name]
        total_count += len(members)
        chosen.extend(members)
        return True

    for name in strata_quota:
        for group_index in range(len(groups)):
            if group_index in taken:
                continue
            if not any(_stratum_of(records[i]) == name for i in groups[group_index]["members"]):
                continue
            if try_take(group_index, enforce_strata=True):
                taken.add(group_index)

    if backfill and total_count < total_quota:
        for group_index in range(len(groups)):
            if group_index in taken:
                continue
            if try_take(group_index, enforce_strata=False):
                taken.add(group_index)
            if total_count >= total_quota:
                break

    for name, target in strata_quota.items():
        achieved = stratum_count.get(name, 0)
        if achieved < target:
            gaps.append(
                {
                    "code": "stratum_shortfall",
                    "dataset_id": dataset_id,
                    "stratum": name,
                    "target": target,
                    "achieved": achieved,
                }
            )
    if total_count < total_quota:
        gaps.append(
            {
                "code": "total_shortfall",
                "dataset_id": dataset_id,
                "target": total_quota,
                "achieved": total_count,
            }
        )
    selected = [records[i] for i in sorted(chosen, key=lambda i: records[i]._sort_key)]
    return selected, gaps


def assign_splits(
    records: list[Sample], *, ratio: float = SPLIT_RATIO
) -> tuple[list[Sample], dict[str, Any]]:
    """按组划分开发 / 保留池；同一组绝不跨池。"""

    groups = group_samples(records) if records else []
    total = len(records)
    target = int(total * ratio + 0.5)
    development = 0
    for group in groups:
        members = group["members"]
        split = "development" if development < target else "holdout"
        for index in members:
            records[index].split = split
        if split == "development":
            development += len(members)
    manifest = {
        "samples": total,
        "groups": len(groups),
        "development": sum(1 for r in records if r.split == "development"),
        "holdout": sum(1 for r in records if r.split == "holdout"),
        "development_target": target,
        "holdout_target": total - target,
    }
    return records, manifest


# ---------------------------------------------------------------------------
# 项目派生工程样例（暂存标注，不是运行时 Schema）
# ---------------------------------------------------------------------------


def _project_annotation(path: str, tag: str) -> dict[str, Any]:
    candidates = PATH_CANDIDATES[path]
    primary = candidates[0]
    secondary = candidates[1]
    base: dict[str, Any] = {
        "added_by": "project",
        "benchmark_metadata_reused_as_user_expression": False,
        # 阶段标记：这些是 Step 01（审核前）的暂存工程标注，**不是**当前发布验收输入。
        # 当前验收清单见 tests/fixtures/knowledge/v0_5_acceptance_cases.json。
        "material_stage": "historical_pre_review",
        "current_release_input": False,
        "stage_note": (
            "Step 01 pre-review staging annotation, kept as historical preparation. It is not an "
            "input to the current v0.5 acceptance list and must not be counted as executed "
            "coverage; current acceptance is defined by "
            "tests/fixtures/knowledge/v0_5_acceptance_cases.json."
        ),
        "knowledge_state_basis": (
            "knowledge_base/v0.4 (corpus_version=v0.4-draft-1, 9 draft / 0 approved) as observed "
            "in Step 01 before the human review"
        ),
        "knowledge_state_current": (
            "knowledge_base/v0.5 (corpus_version=v0.5-approved-1, 5 approved) after the "
            "2026-09-16 human review; this staging record does not speak for the current state"
        ),
        "note": (
            "confirmation / PIN / delegation below are project additions for offline "
            "engineering coverage; the source dataset contains none of them"
        ),
    }
    if tag == "delegated":
        base.update(
            {
                "resolutions": {path: "user_delegated"},
                "confirmed_facets": {},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "user_delegated",
                "knowledge_state": "draft_only",
                "expected_disposition": (
                    "knowledge may propose an authorized candidate for the delegated path; "
                    "no user value is invented"
                ),
            }
        )
    elif tag == "explicit_value":
        base.update(
            {
                "resolutions": {path: "user_specified"},
                "confirmed_facets": {path: primary},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "not_delegated",
                "knowledge_state": "draft_only",
                "expected_disposition": "knowledge must not overwrite an explicit user value",
            }
        )
    elif tag == "pinned":
        base.update(
            {
                "resolutions": {path: "user_specified"},
                "confirmed_facets": {path: primary},
                "pinned_paths": [path],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "not_delegated",
                "knowledge_state": "draft_only",
                "expected_disposition": "PIN blocks knowledge adoption for that path",
            }
        )
    elif tag == "unconfirmed_condition":
        base.update(
            {
                "resolutions": {},
                "confirmed_facets": {},
                "pinned_paths": [],
                "confirmation_state": "not_confirmed",
                "delegation_state": "not_delegated",
                "knowledge_state": "draft_only",
                "expected_disposition": (
                    "a draft rule conditioned on this path must not be adopted: the path is "
                    "missing from the confirmed Intent"
                ),
            }
        )
    elif tag == "condition_mismatch":
        base.update(
            {
                "resolutions": {path: "user_specified"},
                "confirmed_facets": {path: secondary},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "not_delegated",
                "knowledge_state": "draft_only",
                "expected_disposition": (
                    f"a draft rule whose condition requires {primary!r} must be rejected when "
                    f"the confirmed value is {secondary!r}; rejection reason is recorded"
                ),
            }
        )
    elif tag == "model_mismatch":
        base.update(
            {
                "resolutions": {path: "user_delegated"},
                "confirmed_facets": {},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "user_delegated",
                "knowledge_state": "draft_only",
                "target_model_note": (
                    "project context names a model outside the unit target_models; no "
                    "wildcard mapping is assumed"
                ),
                "expected_disposition": "model mismatch makes the unit ineligible; snapshot keeps the model",
            }
        )
    elif tag == "unapproved_knowledge":
        base.update(
            {
                "resolutions": {path: "user_delegated"},
                "confirmed_facets": {},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "user_delegated",
                "knowledge_state": "draft_only",
                "expected_disposition": (
                    "Step 01 historical/planned: knowledge_base/v0.4 held 9 draft / 0 approved, so "
                    "build_units() was empty and the path fell back transparently. This is not a "
                    "claim about the current corpus: knowledge_base/v0.5 (v0.5-approved-1) now "
                    "holds 5 approved units, and current coverage is tracked by "
                    "tests/fixtures/knowledge/v0_5_acceptance_cases.json."
                ),
            }
        )
    elif tag == "conflict":
        base.update(
            {
                "resolutions": {path: "user_delegated"},
                "confirmed_facets": {},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "user_delegated",
                "knowledge_state": "draft_only",
                "conflict_note": (
                    "two draft proposals target the same path with different candidates; no "
                    "silent overwrite is allowed"
                ),
                "expected_disposition": "deterministic conflict handling with recorded decisions",
            }
        )
    elif tag == "missing_knowledge":
        base.update(
            {
                "resolutions": {path: "user_delegated"},
                "confirmed_facets": {},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "user_delegated",
                "knowledge_state": "absent",
                "expected_disposition": "empty-knowledge fallback; no value is fabricated",
            }
        )
    elif tag == "successful_adoption":
        base.update(
            {
                "resolutions": {path: "user_delegated"},
                "confirmed_facets": {},
                "pinned_paths": [],
                "confirmation_state": "confirmed_intent",
                "delegation_state": "user_delegated",
                "knowledge_state": "draft_only",
                "expected_disposition": (
                    "Step 01 historical/planned state: as of 2026-09-16 before the human review, "
                    "production knowledge was 9 draft / 0 approved, so success could not be claimed "
                    "and reviewer/reviewed_at could not be fabricated. The human review has since "
                    "approved v0.5-approved-1 (5 units, reviewer=change, "
                    "reviewed_at=2026-09-16T10:25:59Z); the three current positive cases must load "
                    "that final snapshot and are tracked in "
                    "tests/fixtures/knowledge/v0_5_acceptance_cases.json, not in this staging record."
                ),
            }
        )
    else:  # pragma: no cover - guarded by PROJECT_COVERAGE_TAGS
        raise ResourceError(f"unknown coverage tag: {tag!r}")
    return base


def build_project_derived_samples(development: Sequence[Sample]) -> list[dict[str, Any]]:
    """以开发池样本为父样本生成 30 条项目派生工程样例（三路径 × 十状态）。"""

    if not development:
        return []
    ordered = sorted(
        development, key=lambda r: (r.dataset_id, r.group_id, r._sort_key, str(r.original_id))
    )
    records: list[dict[str, Any]] = []
    index = 0
    for path in PATH_CANDIDATES:
        for tag in PROJECT_COVERAGE_TAGS:
            parent = ordered[index % len(ordered)]
            index += 1
            records.append(
                {
                    "derived_schema": PROJECT_DERIVED_SCHEMA,
                    "staging_only": True,
                    "runtime_schema_change": False,
                    "derived_id": f"v0.5-step01:project:{path}:{tag}",
                    "knowledge_path": path,
                    "coverage_tag": tag,
                    "source_sample": {
                        "dataset_id": parent.dataset_id,
                        "revision": parent.revision,
                        "source_file": parent.source_file,
                        "original_id": parent.original_id,
                        "group_id": parent.group_id,
                        "split": parent.split,
                        "license": parent.license,
                        "language": parent.language,
                    },
                    "original_text": parent.original_text,
                    "project_added_annotations": _project_annotation(path, tag),
                    "assertions_status": (
                        "historical pre-review Step 01 record; not executed as current v0.5 "
                        "acceptance and not counted as executed coverage. Current acceptance is "
                        "tracked by tests/fixtures/knowledge/v0_5_acceptance_cases.json against "
                        "knowledge_base/v0.5 (v0.5-approved-1)."
                    ),
                }
            )
    return records


# ---------------------------------------------------------------------------
# 来源准备
# ---------------------------------------------------------------------------


def _license_assets(dataset: dict[str, Any]) -> list[tuple[str, str]]:
    assets: list[tuple[str, str]] = []
    if dataset.get("license_url"):
        assets.append(("license", dataset["license_url"]))
    if dataset.get("notice_url"):
        assets.append(("notice", dataset["notice_url"]))
    return assets


def _fetch_licenses(
    dataset: dict[str, Any],
    staging: Path,
    limits: Limits,
    state: FetchState,
    offline: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for kind, url in _license_assets(dataset):
        name = _basename_from_url(url, "LICENSE" if kind == "license" else "NOTICE.md")
        dest = staging / "licenses" / dataset["id"] / name
        try:
            outcome = fetch_asset(url, dest, limits, state=state, offline=offline)
        except FetchError as exc:
            gaps.append(
                {"code": "license_fetch_failed", "kind": kind, "url": url, "detail": str(exc)}
            )
            entries.append({"kind": kind, "url": url, "status": "failed", "detail": str(exc)})
            continue
        entries.append(
            {
                "kind": kind,
                "url": url,
                "status": outcome["status"],
                "reused": outcome["reused"],
                "path": _rel(dest, staging),
                "bytes": outcome["bytes"],
                "sha256": outcome["sha256"],
            }
        )
    return entries, gaps


def _prepare_locked_text_source(
    dataset: dict[str, Any],
    staging: Path,
    limits: Limits,
    state: FetchState,
    offline: bool,
) -> dict[str, Any]:
    file_entries: list[dict[str, Any]] = []
    verified: list[tuple[Path, dict[str, Any]]] = []
    gaps: list[dict[str, Any]] = []
    status = "verified"
    for spec in dataset.get("files", []):
        expected = {
            "bytes": spec.get("bytes"),
            "sha256": spec.get("sha256"),
            "nonempty_rows": spec.get("nonempty_rows"),
        }
        entry: dict[str, Any] = {
            "path": spec.get("path", ""),
            "url": spec.get("url", ""),
            "expected": expected,
        }
        try:
            rel = _safe_relative(spec["path"], label=dataset["id"])
        except ResourceError as exc:
            entry["status"] = "unsafe_path"
            entry["detail"] = str(exc)
            gaps.append({"code": "unsafe_path", "path": spec.get("path", ""), "detail": str(exc)})
            status = "failed"
            file_entries.append(entry)
            break
        dest = staging / "raw" / dataset["id"] / rel
        try:
            fetch = fetch_asset(spec["url"], dest, limits, state=state, offline=offline)
        except FetchError as exc:
            entry["status"] = "fetch_failed"
            entry["detail"] = str(exc)
            gaps.append(
                {"code": "fetch_failed", "path": spec.get("path", ""), "detail": str(exc)}
            )
            status = "failed"
            file_entries.append(entry)
            break
        entry["reused"] = fetch["reused"]
        verification = verify_locked_file(dest, expected)
        entry["status"] = verification["status"]
        entry["actual"] = verification["actual"]
        entry["mismatches"] = verification["mismatches"]
        file_entries.append(entry)
        if verification["status"] != "verified":
            gaps.append(
                {
                    "code": verification["status"],
                    "path": spec.get("path", ""),
                    "mismatches": verification["mismatches"],
                }
            )
            status = "failed"
            break
        verified.append((dest, spec))

    licenses, license_gaps = _fetch_licenses(dataset, staging, limits, state, offline)
    gaps.extend(license_gaps)

    samples: list[Sample] = []
    if status == "verified":
        if dataset["id"] == "geneval":
            samples, parse_gaps = parse_geneval(verified[0][0], dataset, staging)
        else:
            samples, parse_gaps = parse_compbench([p for p, _ in verified], dataset, staging)
        gaps.extend(parse_gaps)

    return {
        "record": {
            "id": dataset["id"],
            "official_url": dataset.get("official_url", ""),
            "revision": dataset.get("revision", ""),
            "license": dataset.get("license", ""),
            "status": status,
            "files": file_entries,
            "licenses": licenses,
            "gaps": gaps,
        },
        "samples": samples,
        "gaps": gaps,
        "status": status,
    }


def _prepare_coco_source(
    dataset: dict[str, Any],
    staging: Path,
    limits: Limits,
    state: FetchState,
    offline: bool,
    coco_archive: Path | None,
    coco_archive_url: str | None,
) -> dict[str, Any]:
    spec = (dataset.get("files") or [{}])[0]
    entry: dict[str, Any] = {
        "path": spec.get("path", ""),
        "url": spec.get("url", ""),
        "expected": {
            "bytes": spec.get("bytes"),
            "sha256": spec.get("sha256"),
            "nonempty_rows": spec.get("nonempty_rows"),
        },
        "locked_status": spec.get("status", "unknown"),
    }
    gaps: list[dict[str, Any]] = []
    licenses, license_gaps = _fetch_licenses(dataset, staging, limits, state, offline)
    gaps.extend(license_gaps)
    status = "archive_missing"

    if coco_archive is None and coco_archive_url:
        dest = staging / "raw" / dataset["id"] / _basename_from_url(
            coco_archive_url, "coco-cn.tar.gz"
        )
        try:
            fetch = fetch_asset(coco_archive_url, dest, limits, state=state, offline=offline)
            coco_archive = dest
            entry["fetch_status"] = fetch["status"]
            entry["reused"] = fetch["reused"]
        except FetchError as exc:
            gaps.append({"code": "archive_fetch_failed", "detail": str(exc)})
            entry["status"] = "fetch_failed"
            return {
                "record": {
                    "id": dataset["id"],
                    "official_url": dataset.get("official_url", ""),
                    "hosting_url": dataset.get("hosting_url", ""),
                    "hosting_revision": dataset.get("hosting_revision", ""),
                    "revision": dataset.get("revision", ""),
                    "license": dataset.get("license", ""),
                    "status": "failed",
                    "files": [entry],
                    "licenses": licenses,
                    "gaps": gaps,
                },
                "samples": [],
                "gaps": gaps,
                "status": "failed",
                "field_report": None,
            }
    if coco_archive is None:
        gaps.append(
            {
                "code": "archive_not_available",
                "dataset_id": dataset["id"],
                "detail": (
                    "no local archive and no --coco-archive-url; the lock has no prior archive "
                    "hash, so nothing is downloaded implicitly"
                ),
            }
        )
        entry["status"] = "not_downloaded"
        return {
            "record": {
                "id": dataset["id"],
                "official_url": dataset.get("official_url", ""),
                "hosting_url": dataset.get("hosting_url", ""),
                "hosting_revision": dataset.get("hosting_revision", ""),
                "revision": dataset.get("revision", ""),
                "license": dataset.get("license", ""),
                "status": "not_downloaded",
                "files": [entry],
                "licenses": licenses,
                "gaps": gaps,
            },
            "samples": [],
            "gaps": gaps,
            "status": "not_downloaded",
            "field_report": None,
        }

    archive = Path(coco_archive)
    if not archive.is_file():
        gaps.append({"code": "archive_missing", "path": str(archive)})
        entry["status"] = "archive_missing"
        return {
            "record": {
                "id": dataset["id"],
                "revision": dataset.get("revision", ""),
                "license": dataset.get("license", ""),
                "status": "failed",
                "files": [entry],
                "licenses": licenses,
                "gaps": gaps,
            },
            "samples": [],
            "gaps": gaps,
            "status": "failed",
            "field_report": None,
        }

    archive_bytes = archive.stat().st_size
    archive_sha = sha256_file(archive)
    entry["new_archive_hash"] = {
        "bytes": archive_bytes,
        "sha256": archive_sha,
        "note": (
            "hash computed in this Step 01 run; the hosting lock had no prior archive hash, so "
            "this is a new record, not a backfill of already-downloaded history"
        ),
    }
    if archive_bytes > limits.max_file_bytes:
        gaps.append(
            {
                "code": "archive_bytes_limit",
                "bytes": archive_bytes,
                "limit": limits.max_file_bytes,
            }
        )
        entry["status"] = "oversized"
        return {
            "record": {
                "id": dataset["id"],
                "revision": dataset.get("revision", ""),
                "license": dataset.get("license", ""),
                "status": "failed",
                "files": [entry],
                "licenses": licenses,
                "gaps": gaps,
            },
            "samples": [],
            "gaps": gaps,
            "status": "failed",
            "field_report": None,
        }

    extract_dir = staging / "extracted" / dataset["id"]
    try:
        extraction = safe_extract_coco(archive, extract_dir, limits)
    except UnsafeArchiveError as exc:
        gaps.append({"code": "unsafe_archive", "detail": str(exc)})
        entry["status"] = "rejected_unsafe"
        return {
            "record": {
                "id": dataset["id"],
                "revision": dataset.get("revision", ""),
                "license": dataset.get("license", ""),
                "status": "failed",
                "files": [entry],
                "licenses": licenses,
                "gaps": gaps,
            },
            "samples": [],
            "gaps": gaps,
            "status": "failed",
            "field_report": None,
        }

    entry["status"] = "safely_extracted"
    entry["extracted_text_files"] = extraction["extracted"]
    entry["skipped_non_text_or_metadata"] = extraction["skipped"]
    entry["member_count"] = extraction["member_count"]
    entry["total_unpack_bytes"] = extraction["total_unpack_bytes"]

    if not any(_coco_data_allowed(name) and name.casefold().endswith("license") for name in extraction["extracted"]):
        gaps.append(
            {
                "code": "coco_internal_license_absent",
                "dataset_id": dataset["id"],
                "detail": (
                    "archive carries no LICENSE/README; license relies on the upstream repository "
                    "LICENSE and the hosting dataset card (both MIT) plus the separately archived "
                    "licenses/coco_cn/LICENSE copy"
                ),
            }
        )
    if any(APPLEDOUBLE_PREFIX in PurePosixPath(n).name for n in extraction["skipped"]):
        gaps.append(
            {
                "code": "coco_appledouble_metadata_skipped",
                "dataset_id": dataset["id"],
                "detail": "macOS AppleDouble ._* members and .py scripts are not data and were skipped",
            }
        )

    field_report = discover_coco_subsets(extract_dir)
    field_report["schema"] = FIELD_REPORT_SCHEMA
    field_report["dataset_id"] = dataset["id"]
    field_report["archive"] = {
        "path": _rel(archive, staging),
        "bytes": archive_bytes,
        "sha256": archive_sha,
        "member_count": extraction["member_count"],
        "total_unpack_bytes": extraction["total_unpack_bytes"],
    }
    field_report["license_note"] = (
        "no internal license file in the archive; repository LICENSE + dataset card are MIT, "
        "images are out of scope and were not extracted"
    )
    samples, parse_gaps = build_coco_samples(extract_dir, dataset, staging, field_report)
    gaps.extend(parse_gaps)
    if samples:
        status = "extracted_unlocked_hash"
    else:
        status = "gap"
        gaps.append({"code": "no_coco_text_records", "dataset_id": dataset["id"]})
    used_entry = next(
        (
            e
            for e in field_report["subsets"]
            if e.get("present") and e.get("used_for_samples")
        ),
        None,
    )
    entry["sample_records_parsed"] = int(used_entry.get("records", 0)) if used_entry else 0
    entry["present_subset_rows"] = {
        e["label"]: e.get("nonempty_rows")
        for e in field_report["subsets"]
        if e.get("present")
    }
    entry["subset_used_for_samples"] = used_entry["label"] if used_entry else None
    return {
        "record": {
            "id": dataset["id"],
            "official_url": dataset.get("official_url", ""),
            "hosting_url": dataset.get("hosting_url", ""),
            "hosting_revision": dataset.get("hosting_revision", ""),
            "revision": dataset.get("revision", ""),
            "license": dataset.get("license", ""),
            "status": status,
            "files": [entry],
            "licenses": licenses,
            "gaps": gaps,
        },
        "samples": samples,
        "gaps": gaps,
        "status": status,
        "field_report": field_report,
    }


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def prepare(
    lock_path: Path,
    staging: Path,
    limits: Limits | None = None,
    *,
    coco_archive: Path | None = None,
    coco_archive_url: str | None = None,
    stamp: str = DEFAULT_STAMP,
    backfill: bool = True,
    offline: bool = False,
) -> dict[str, Any]:
    limits = limits or Limits()
    lock = load_lock(lock_path)
    staging = Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    state = FetchState()

    records: list[dict[str, Any]] = []
    all_samples: list[Sample] = []
    all_gaps: list[dict[str, Any]] = []
    split_stats: dict[str, Any] = {}
    field_report: dict[str, Any] | None = None
    lock_verified = 0
    archive_extracted = 0

    for dataset in lock["datasets"]:
        if dataset["id"] == "coco_cn":
            outcome = _prepare_coco_source(
                dataset, staging, limits, state, offline, coco_archive, coco_archive_url
            )
            if outcome["field_report"] is not None:
                field_report = outcome["field_report"]
            if outcome["status"] == "extracted_unlocked_hash":
                archive_extracted += 1
        else:
            outcome = _prepare_locked_text_source(dataset, staging, limits, state, offline)
            if outcome["status"] == "verified":
                lock_verified += 1
        records.append(outcome["record"])
        all_gaps.extend(outcome["gaps"])
        if not outcome["samples"]:
            continue
        selected, sampling_gaps = sample_groups(
            outcome["samples"], outcome["record"]["id"], stamp=stamp, backfill=backfill
        )
        outcome["gaps"].extend(sampling_gaps)
        all_gaps.extend(sampling_gaps)
        outcome["record"]["gaps"] = list(outcome["gaps"])
        _, stats = assign_splits(selected)
        split_stats[outcome["record"]["id"]] = stats
        all_samples.extend(selected)

    all_samples.sort(
        key=lambda r: (r.dataset_id, r.group_id, r._sort_key, str(r.original_id))
    )
    development_pool = [s for s in all_samples if s.split == "development"]
    project_derived = build_project_derived_samples(development_pool)

    totals = {
        "samples": len(all_samples),
        "development": sum(1 for r in all_samples if r.split == "development"),
        "holdout": sum(1 for r in all_samples if r.split == "holdout"),
        "groups": len({r.group_id for r in all_samples}),
        "downloaded_bytes": state.downloaded_bytes,
        "sources_locked_verified": lock_verified,
        "sources_archive_extracted_unlocked": archive_extracted,
        "project_derived_samples": len(project_derived),
    }

    derived = staging / "derived"
    download_record = {
        "schema": DOWNLOAD_RECORD_SCHEMA,
        "generated_by": TOOL_NAME,
        "stamp": stamp,
        "lock_schema": lock.get("schema"),
        "lock_verified_on": lock.get("verified_on"),
        "staging_root": str(staging),
        "limits": asdict(limits),
        "sources": records,
        "gaps": all_gaps,
        "totals": totals,
        "derived": {
            "download_record": _rel(derived / "download_record.json", staging),
            "source_samples": _rel(derived / "source_samples.jsonl", staging),
            "split_manifest": _rel(derived / "split_manifest.json", staging),
            "coco_cn_field_report": _rel(derived / "coco_cn_field_report.json", staging),
            "project_derived_samples": _rel(
                derived / "project_derived_samples.jsonl", staging
            ),
        },
    }
    split_manifest = {
        "schema": SPLIT_MANIFEST_SCHEMA,
        "stamp": stamp,
        "split_ratio_target": SPLIT_RATIO,
        "sort_rule": "sha256('<stamp>|<dataset_id>|<original_id>') ascending",
        "grouping_rule": (
            "union by same normalized original text / same image_id (parent group); whole "
            "groups are deduplicated, sampled and split without leakage"
        ),
        "datasets": split_stats,
        "totals": {
            "samples": len(all_samples),
            "development": totals["development"],
            "holdout": totals["holdout"],
            "groups": totals["groups"],
        },
    }

    _write_json(derived / "download_record.json", download_record)
    _write_jsonl(derived / "source_samples.jsonl", (r.to_dict() for r in all_samples))
    _write_json(derived / "split_manifest.json", split_manifest)
    if field_report is not None:
        _write_json(derived / "coco_cn_field_report.json", field_report)
    _write_jsonl(derived / "project_derived_samples.jsonl", project_derived)
    _write_staging_readme(staging, download_record, split_manifest, field_report)
    return download_record


def _write_staging_readme(
    staging: Path,
    download_record: dict[str, Any],
    split_manifest: dict[str, Any],
    field_report: dict[str, Any] | None,
) -> None:
    """写暂存根 README；只写自己的文件，不动 knowledge_review/ 等并发目录。"""

    totals = download_record["totals"]
    lines = [
        "# v0.5 Step 01 暂存数据（可复现，非生产语料）",
        "",
        "- 日期：2026-09-16",
        "- 性质：**暂存原始文本、许可、样本与分组记录**。不是评测集，不是生产知识。",
        "- 依据：`docs/task_books/mvp_v0.5/01_dataset_preparation.md`、",
        "  `docs/task_books/rag_resources/DATASETS.md`、`sources.lock.json`。",
        f"- 生成工具：`{TOOL_NAME}`（纯标准库；下载有超时与大小上限；不执行远端内容）。",
        "",
        "## 目录",
        "",
        "| 路径 | 内容 |",
        "|---|---|",
        "| `raw/` | 七个锁定文本的原始字节（不清洗改写） |",
        "| `raw/coco_cn/` | COCO-CN 固定托管 revision 归档（如本地提供） |",
        "| `licenses/` | 各来源 LICENSE / NOTICE 副本 |",
        "| `extracted/coco_cn/` | 归档安全解包后的文本（跳过图片、AppleDouble 与脚本） |",
        "| `derived/download_record.json` | 逐来源核验、哈希与缺口 |",
        "| `derived/source_samples.jsonl` | 70 条暂存来源样本 |",
        "| `derived/split_manifest.json` | 按组 70/30 开发 / 保留划分 |",
        "| `derived/coco_cn_field_report.json` | COCO-CN 实际子集与字段 |",
        "| `derived/project_derived_samples.jsonl` | 30 条项目派生工程样例（**审核前历史准备**；不是当前发布验收输入） |",
        "| `knowledge_review/` | **另一 Agent 的 Step 02 审核前材料，不属于本步，勿覆盖** |",
        "",
        "## 实测结果",
        "",
        f"- 样本：{totals['samples']} 条（开发 {totals['development']} / 保留 {totals['holdout']}，"
        f"组 {totals['groups']} 个）。",
        f"- 锁定文本源核验通过：{totals['sources_locked_verified']} 个；"
        f"无锁定哈希但成功解包归档：{totals['sources_archive_extracted_unlocked']} 个。",
        f"- 项目派生工程样例：{totals['project_derived_samples']} 条。",
        f"- 本次运行联网下载字节：{totals['downloaded_bytes']}。",
        "",
        "## 固定规则",
        "",
        "- 抽样排序：`sha256(\"<stamp>|<dataset_id>|<original_id>\")` 升序。",
        "- 分组：相同原文 / 同图 ID / 同一父样本并查集合并，整组抽样与划分，不跨开发/保留。",
        "- 划分：按组约 70% 开发、30% 保留；不要求每个子类精确比例。",
        "- 保留池本版只校验格式与来源，不用于挑知识、调关键词或回归断言。",
        "",
        "## 复现",
        "",
        "```bash",
        "uv run python tools/prepare_v0_5_resources.py \\",
        "    --staging data/staging/rag_resources_20260916 \\",
        "    --coco-archive /path/to/coco-cn-version1805v1.1.tar.gz",
        "```",
        "",
    ]
    if field_report is not None:
        lines += [
            "## COCO-CN 字段",
            "",
            f"- 归档：`{field_report['archive']['path']}`，"
            f"sha256 `{field_report['archive']['sha256']}`。",
            "- 样本只取 `imageid.human-written-caption.txt`（人工中文描述）；",
            "  人工翻译 / 机器翻译 / 分词变体只登记不使用。",
            f"- 归档内无 LICENSE/README：`{field_report['license_note']}`。",
            "",
        ]
    gaps = download_record.get("gaps") or []
    if gaps:
        lines += ["## 已知缺口", ""]
        for gap in gaps:
            lines.append(f"- `{gap.get('code')}`：{gap.get('detail', gap)}")
        lines.append("")
    lines += [
        "## 边界",
        "",
        "- 未调用真实图片 Provider、未运行正式评测、未下载图片或评测模型。",
        "- 第三方文本是否可再分发需另行确认；本目录默认不入 Git。",
        "- `knowledge_review/` 由另一 Agent 维护，本工具只创建自己的目录并写自己的文件。",
        "",
    ]
    (staging / "README.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="v0.5 Step 01 dataset sample preparation (stdlib only)"
    )
    parser.add_argument("--lock", default="docs/task_books/rag_resources/sources.lock.json")
    parser.add_argument("--staging", default="data/staging/rag_resources_20260916")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--max-tar-members", type=int, default=DEFAULT_MAX_TAR_MEMBERS)
    parser.add_argument("--max-unpack-bytes", type=int, default=DEFAULT_MAX_UNPACK_BYTES)
    parser.add_argument(
        "--coco-archive",
        default=None,
        help="locally held COCO-CN tar.gz; hashed, safety-checked and text-extracted",
    )
    parser.add_argument(
        "--coco-archive-url",
        default=None,
        help="explicit COCO-CN archive URL; if omitted, the lock hosting URL is used only "
        "when --allow-coco-download is also given",
    )
    parser.add_argument(
        "--allow-coco-download",
        action="store_true",
        help="permit downloading the archive from the lock hosting revision",
    )
    parser.add_argument("--stamp", default=DEFAULT_STAMP)
    parser.add_argument("--no-backfill", action="store_true")
    parser.add_argument("--offline", action="store_true", help="refuse all network reads")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    limits = Limits(
        timeout_seconds=args.timeout,
        max_file_bytes=args.max_file_bytes,
        max_total_bytes=args.max_total_bytes,
        max_tar_members=args.max_tar_members,
        max_unpack_bytes=args.max_unpack_bytes,
    )
    coco_url = args.coco_archive_url
    if coco_url is None and args.coco_archive is None and args.allow_coco_download:
        lock = load_lock(Path(args.lock))
        for dataset in lock["datasets"]:
            if dataset["id"] == "coco_cn":
                coco_url = (dataset.get("files") or [{}])[0].get("url")
    record = prepare(
        Path(args.lock),
        Path(args.staging),
        limits,
        coco_archive=Path(args.coco_archive) if args.coco_archive else None,
        coco_archive_url=coco_url,
        stamp=args.stamp,
        backfill=not args.no_backfill,
        offline=args.offline,
    )
    json.dump(record["totals"], sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
