"""R1-B：生成 `evaluation/frozen_manifest_v0_3_r1.json`（内容哈希 sidecar）。

只读取四个 r1 冻结物并计算 sha256；不读系统输出、不读环境变量。旧清单不动。
用法（离线）：

    .venv/bin/python evaluation/tools/freeze_manifest_v0_3_r1.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ROLES = {
    "protocol": "evaluation/protocol_v0_3_r1.md",
    "config": "evaluation/configs/gate_a_v0_3_r1.json",
    "dataset": "evaluation/fixtures/core_v0_3_r1.jsonl",
    "annotations": "evaluation/annotations/core_v0_3_r1.jsonl",
}

OUTPUT = ROOT / "evaluation" / "frozen_manifest_v0_3_r1.json"


def sha256_file(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    files: dict[str, str] = {}
    for role, rel in ROLES.items():
        target = ROOT / rel
        if not target.is_file():
            raise SystemExit(f"missing r1 frozen file for role {role}: {target}")
        files[rel] = sha256_file(target)

    manifest = {
        "manifest_version": "frozen_manifest_v0_3_r1",
        "dataset_version": "core_v0_3_r1",
        "protocol_version": "gate_a_protocol_v0_3_r1",
        "config_version": "gate_a_v0_3_r1",
        "algorithm": "sha256",
        "frozen_by": "MVP v0.3 更改书 001 R1-B / 002 工包 C（r1 评测最小修订）",
        "parent_manifest": "frozen_manifest_v0_3",
        "rule": (
            "r1 评测口径的冻结物；任何修改必须生成新版本文件与新清单，不得覆盖。"
            "旧 core_v0_3 文件与旧清单只读保留，旧测试继续验证旧文件。"
        ),
        "roles": ROLES,
        "files": files,
    }
    OUTPUT.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT}")
    for rel, digest in files.items():
        print(f"  {digest}  {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
