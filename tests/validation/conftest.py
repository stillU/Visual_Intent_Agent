"""tests/validation 的 pytest 配置位置（Rev.1 起仅此 docstring）。

本目录的共享工厂/常量（`ALL_PATHS`、`TYPICAL_VALUES`、`resolve_path`、
`path_snapshot`、`non_target_snapshot`、`full_intent`、`intent_with`、
`with_resolution`、`with_pinned`、`evidence`、`context`、`delta`、`set_delta`、
`clear_delta`、`pin_delta`、`unpin_delta`、`issue_codes`、`rejected_codes` 等）
已迁至唯一命名模块 `tests/validation/validation_helpers.py`，测试文件从该模块 import。

原因（ARCHITECTURE.md 第 7 节测试 helper 命名规则，Rev.1 冻结）：pytest 在多目录
参数下按 basename 缓存 `sys.modules["conftest"]`，不同目录的同名 conftest 会互相
覆盖并导致收集期 ImportError。本目录原本没有任何 pytest fixture/钩子，因此这里不
定义任何可导入符号（保留本文件仅为表述该目录的 pytest 约定）。
"""
