"""R1-B #10：运行身份解析（实际 Git commit + 工作区 dirty + 身份模式）。

正式 Run 必须记录真实代码身份，并在工作区不干净时拒绝启动；诊断 Run 显式标记且
不作为正式 Gate 证据。本模块只读 Git 元数据（`subprocess`，无网络），不读凭据，
不 import 产品包；测试可注入 fake runner 或直接把 `CodeIdentity` 传给 Runner。

设计为**无副作用**：`resolve_code_identity` 只查询，不写任何文件/仓库状态。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

#: 身份模式（写入 Run 记录；`unspecified` 为程序化/测试默认，不做正式门禁）。
IDENTITY_UNSPECIFIED = "unspecified"
IDENTITY_FORMAL = "formal"
IDENTITY_DIAGNOSTIC = "diagnostic"


class IdentityError(ValueError):
    """正式运行身份不满足要求（dirty 工作区 / 无法解析）。"""


@dataclass(frozen=True)
class CodeIdentity:
    """一次运行解析出的代码身份。"""

    commit: str | None
    dirty: bool | None
    code_version: str
    detail: str
    mode: str = IDENTITY_UNSPECIFIED

    @property
    def short_commit(self) -> str | None:
        return self.commit[:12] if self.commit else None

    def with_mode(self, mode: str) -> "CodeIdentity":
        return CodeIdentity(
            commit=self.commit,
            dirty=self.dirty,
            code_version=self.code_version,
            detail=self.detail,
            mode=mode,
        )


#: 允许测试注入的命令执行器：`(argv) -> (returncode, stdout)`。
CommandRunner = Callable[[Sequence[str]], "tuple[int, str]"]


def _run_git(argv: Sequence[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - 环境相关
        return 127, str(exc)
    return completed.returncode, completed.stdout


def resolve_code_identity(
    project_root: Path,
    *,
    package_version: str,
    component_version: str,
    mode: str = IDENTITY_UNSPECIFIED,
    command_runner: CommandRunner | None = None,
) -> CodeIdentity:
    """解析 Git commit 与 dirty 状态，拼出可复核的 `code_version`。

    Git 不可用（无仓库/无 git 可执行文件）时 commit/dirty 记 `None`，不伪造，
    `detail` 说明原因；`code_version` 退回包版本 + 组件版本。
    """
    run = command_runner or _run_git
    root = Path(project_root)
    commit: str | None = None
    dirty: bool | None = None
    detail: str

    code, out = run(["git", "-C", str(root), "rev-parse", "HEAD"])
    if code == 0 and out.strip():
        commit = out.strip()
        status_code, status_out = run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"]
        )
        if status_code == 0:
            dirty = bool(status_out.strip())
            detail = "git metadata resolved"
        else:
            dirty = None
            detail = "git rev-parse ok but git status failed; dirty state unknown"
    else:
        detail = "git metadata unavailable; commit and dirty state recorded as null"

    suffix = component_version
    version = f"{package_version}+{suffix}"
    if commit:
        version = f"{version}+{commit[:12]}"
        if dirty:
            version = f"{version}-dirty"
    return CodeIdentity(
        commit=commit,
        dirty=dirty,
        code_version=version,
        detail=detail,
        mode=mode,
    )


def enforce_formal_identity(identity: CodeIdentity) -> None:
    """正式 Run 门禁：必须能解析 commit，且工作区干净。

    诊断模式（`mode=diagnostic`）不调用本函数；`unspecified` 也不强制（测试/程序化）。
    """
    if identity.mode != IDENTITY_FORMAL:
        return
    if identity.commit is None:
        raise IdentityError(
            "formal run requires a resolvable git commit; "
            "run with --diagnostic to record a non-gate diagnostic run"
        )
    if identity.dirty is None:
        raise IdentityError(
            "formal run requires a known clean/dirty worktree state; "
            "run with --diagnostic to record a non-gate diagnostic run"
        )
    if identity.dirty:
        raise IdentityError(
            "formal run refuses a dirty worktree; commit or stash changes, "
            "or run with --diagnostic (result will be marked diagnostic, not gate evidence)"
        )


__all__ = [
    "IDENTITY_DIAGNOSTIC",
    "IDENTITY_FORMAL",
    "IDENTITY_UNSPECIFIED",
    "CodeIdentity",
    "IdentityError",
    "enforce_formal_identity",
    "resolve_code_identity",
]
