"""R1-B：冻结清单解析（`--manifest` 的唯一实现，向后兼容旧清单）。

清单是"评测口径"的单一入口：由它解析协议 / 配置 / 数据集 / 标注四个角色的路径、
版本与内容哈希。设计目标：

- **向后兼容**：旧 `frozen_manifest_v0_3.json` 只有 `manifest_version`、`files`
  与版本字段，没有 `roles`；本模块按已知默认路径推断角色，因此旧清单仍可加载、
  旧 Runner 行为不变。
- **正式运行必须校验全部哈希**：`RunManifest.verify()` 对 `files` 中每个条目
  逐个复核 sha256，任何不一致或缺失都抛 `ManifestError`；不提供"关闭校验导入
  外部数据"的开关。
- 不做网络访问、不读环境变量、不 import 产品包（评测层可独立测试）。

清单最小结构（r1）：

    {
      "manifest_version": "frozen_manifest_v0_3_r1",
      "dataset_version": "core_v0_3_r1",
      "protocol_version": "gate_a_protocol_v0_3_r1",
      "config_version": "gate_a_v0_3_r1",
      "algorithm": "sha256",
      "roles": {
        "protocol": "evaluation/protocol_v0_3_r1.md",
        "config": "evaluation/configs/gate_a_v0_3_r1.json",
        "dataset": "evaluation/fixtures/core_v0_3_r1.jsonl",
        "annotations": "evaluation/annotations/core_v0_3_r1.jsonl"
      },
      "files": {"<repo-relative path>": "<sha256>", ...}
    }
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 四个角色名（协议第 5/8 节）。
MANIFEST_ROLES: tuple[str, str, str, str] = (
    "protocol",
    "config",
    "dataset",
    "annotations",
)

#: 旧清单不含 roles 时的默认角色路径（与 Step 01/02/03 冻结默认一致）。
_LEGACY_ROLE_PATHS: dict[str, str] = {
    "protocol": "evaluation/protocol.md",
    "config": "evaluation/configs/gate_a_v0_3.json",
    "dataset": "evaluation/fixtures/core_v0_3.jsonl",
    "annotations": "evaluation/annotations/core_v0_3.jsonl",
}


class ManifestError(ValueError):
    """清单缺失、结构非法或哈希不一致（正式 Run 必须拒绝启动）。"""


def sha256_file(path: Path) -> str:
    """文件内容 sha256（小写 hex）。"""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class RunManifest:
    """已加载的冻结清单（路径 + 版本 + 角色 + 哈希）。"""

    path: Path
    base_dir: Path
    manifest_version: str
    protocol_version: str | None
    dataset_version: str | None
    config_version: str | None
    algorithm: str
    roles: dict[str, str]
    files: dict[str, str]
    raw: dict[str, Any]

    # -- 解析 ---------------------------------------------------------------

    def resolve(self, role: str) -> Path:
        """把角色解析为绝对路径（未知角色 → ManifestError）。"""
        if role not in MANIFEST_ROLES:
            raise ManifestError(
                f"unknown manifest role {role!r}; expected one of {MANIFEST_ROLES}"
            )
        rel = self.roles.get(role)
        if rel is None:
            raise ManifestError(
                f"manifest {self.path} has no role {role!r} "
                "(add a 'roles' mapping or use a legacy-compatible manifest)"
            )
        return self._absolute(rel)

    @property
    def protocol_path(self) -> Path:
        return self.resolve("protocol")

    @property
    def config_path(self) -> Path:
        return self.resolve("config")

    @property
    def dataset_path(self) -> Path:
        return self.resolve("dataset")

    @property
    def annotations_path(self) -> Path:
        return self.resolve("annotations")

    def role_of(self, rel_path: str) -> str | None:
        """反查某相对路径所属角色（记录/校验用）。"""
        for role, rel in self.roles.items():
            if rel == rel_path:
                return role
        return None

    def _absolute(self, rel: str) -> Path:
        candidate = Path(rel)
        if candidate.is_absolute():
            return candidate
        return self.base_dir / candidate

    # -- 校验 ---------------------------------------------------------------

    def verify(self) -> None:
        """逐个复核 `files` 内所有哈希；任何缺失/不一致都抛 ManifestError。"""
        if self.algorithm != "sha256":
            raise ManifestError(
                f"unsupported manifest algorithm {self.algorithm!r}; expected 'sha256'"
            )
        if not self.files:
            raise ManifestError(f"manifest {self.path} declares no frozen files")
        problems: list[str] = []
        for rel, expected in sorted(self.files.items()):
            target = self._absolute(rel)
            if not target.is_file():
                problems.append(f"{rel}: missing file at {target}")
                continue
            actual = sha256_file(target)
            if actual != expected:
                problems.append(
                    f"{rel}: manifest={expected} actual={actual}"
                )
        if problems:
            raise ManifestError(
                "frozen manifest verification failed ("
                + "; ".join(problems)
                + "); the frozen inputs must not be modified "
                "(issue a new dataset/config version instead)"
            )
        # 角色路径必须都在 files 里冻结，避免“解析到未校验文件”。
        for role in MANIFEST_ROLES:
            rel = self.roles.get(role)
            if rel is None:
                raise ManifestError(f"manifest {self.path} is missing role {role!r}")
            if rel not in self.files:
                raise ManifestError(
                    f"manifest role {role!r} -> {rel!r} is not listed in 'files'"
                )

    def verify_hashes(self) -> dict[str, str]:
        """校验通过后返回实际哈希表（供 Run 记录；键为角色名 + manifest）。"""
        self.verify()
        return {
            "manifest": sha256_file(self.path),
            **{role: self.files[self.roles[role]] for role in MANIFEST_ROLES},
        }


def load_manifest(path: Path, *, base_dir: Path | None = None) -> RunManifest:
    """加载清单（不校验哈希；调用方决定何时 `verify()`）。"""
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest not found: {source}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot load manifest {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest {source} must be a JSON object")

    files = raw.get("files")
    if not isinstance(files, dict) or not files:
        raise ManifestError(f"manifest {source} must declare a non-empty 'files' mapping")
    for rel in files:
        if not isinstance(rel, str) or not rel.strip():
            raise ManifestError(f"manifest {source} has an invalid frozen path {rel!r}")

    roles_raw = raw.get("roles")
    if roles_raw is None:
        roles = dict(_LEGACY_ROLE_PATHS)
    else:
        if not isinstance(roles_raw, dict):
            raise ManifestError(f"manifest {source} 'roles' must be an object")
        roles = {str(k): str(v) for k, v in roles_raw.items()}
        for role in MANIFEST_ROLES:
            if role not in roles:
                raise ManifestError(
                    f"manifest {source} 'roles' is missing {role!r}"
                )

    return RunManifest(
        path=source,
        base_dir=Path(base_dir) if base_dir is not None else source.resolve().parent.parent,
        manifest_version=str(raw.get("manifest_version", "unverified")),
        protocol_version=(
            str(raw["protocol_version"]) if raw.get("protocol_version") is not None else None
        ),
        dataset_version=(
            str(raw["dataset_version"]) if raw.get("dataset_version") is not None else None
        ),
        config_version=(
            str(raw["config_version"]) if raw.get("config_version") is not None else None
        ),
        algorithm=str(raw.get("algorithm", "sha256")),
        roles=roles,
        files={str(k): str(v) for k, v in files.items()},
        raw=raw,
    )


__all__ = [
    "MANIFEST_ROLES",
    "ManifestError",
    "RunManifest",
    "load_manifest",
    "sha256_file",
]
