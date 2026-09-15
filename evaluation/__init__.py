"""Gate A 评测层（MVP v0.3）。

本包只承载评测代码，不属于产品业务包 `visual_intent_agent`：

- `evaluation.models`          —— Baseline A 合同模型（pydantic v2，frozen/extra=forbid）
- `evaluation.direct_baseline` —— Baseline A 执行器（依赖注入 Provider/Settings/输出目录）

冻结数据与文档（`protocol.md`、`fixtures/`、`annotations/`、`configs/`、
`frozen_manifest_v0_3.json`）保持只读。

字节码缓存说明（Step 02 追加，仅新增本文件、不改任何冻结物）：
Step 01 冻结的凭据卫生测试 `tests/evaluation/test_dataset_contract.py` 会把
`evaluation/` 目录树下**所有文件**按 UTF-8 文本读取，而 `.pyc` 字节码缓存不是合法
UTF-8，会导致该冻结测试失败。本包因此在导入时（1）关闭解释器字节码写入、
（2）清理本包已有的 `__pycache__`，使 `evaluation/` 始终只包含源码与冻结数据。
该副作用只影响 `sys.dont_write_bytecode`，不改变任何被导入模块的行为。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

#: 关闭字节码写入：保证本包目录内不产生 `.pyc`（见模块 docstring）。
sys.dont_write_bytecode = True

#: 清理可能由先前未开启上述开关的导入写下的缓存（自身缓存也会在导入时被写）。
shutil.rmtree(Path(__file__).resolve().parent / "__pycache__", ignore_errors=True)
