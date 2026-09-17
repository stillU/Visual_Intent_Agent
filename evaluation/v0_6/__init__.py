"""MVP v0.6 评测层（B/C 配对，RAG 关闭 × 开启 v0.5-approved-1）。

本包只承载评测代码，不属于产品业务包 `visual_intent_agent`：

- `evaluation.v0_6.records`       —— 输入解析 + 输出记录合同（`bc_records_v0_6`）
- `evaluation.v0_6.context`       —— 固定 revision ID + 确认门禁的隔离上下文装配
- `evaluation.v0_6.budget`        —— 只追加预算账本（发出前最坏预留，覆盖内部重试）
- `evaluation.v0_6.reporting`     —— 两种分母（比较对 60 / arm 运行 120）与配对结局汇总
- `evaluation.v0_6.paired_runner` —— B/C 编排、fail-closed 门禁、续跑与 CLI

冻结数据（`protocol.md` / `cases.jsonl` / `annotations.jsonl` / `run_config.json` /
`frozen_manifest.json`）由 Step 01 交付，本包**只读**，不改写。

与旧 `evaluation.runner`（Direct Baseline A × System B）分属不同协议与标签：本包不
复用其 `baseline_a` / `system_b` 标签、不复用 `aggregate_run`，也不把旧 Runner 改名。

字节码缓存说明（与父包 `evaluation/__init__.py` 同因）：Step 01 冻结的凭据卫生测试
会把 `evaluation/` 树下**所有文件**按 UTF-8 文本读取，`.pyc` 不是合法 UTF-8。父包在
导入时已全局关闭解释器字节码写入；本包在导入时再清理自己可能遗留的 `__pycache__`。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
shutil.rmtree(Path(__file__).resolve().parent / "__pycache__", ignore_errors=True)
