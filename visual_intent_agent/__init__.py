"""Visual Intent Agent v1.0.0rc1（候选交付；本轮仅静态整理，未测试/未验证）。

模块化单体：LLM 负责理解和表达，确定性代码负责状态。
本包 __init__ 不导出任何符号（避免 import 时副作用）；请按
docs/ARCHITECTURE.md 接口冻结表中的完整路径导入。
"""

__version__ = "1.0.0rc1"
