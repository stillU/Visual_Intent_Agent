# v0.4 后置修复批次（索引）

- 日期：2026-09-16｜状态：**离线修复完成，生产知识待审核，正式评测未执行**
- 基线：`HEAD b039b9b`，分支 `feature`，工作区 dirty 且**未提交**（无 commit / push / stash）

本目录只做索引与状态摘要，细节以任务书与交接文档为准，不在此复制长内容。

## 状态摘要

| 项目 | 状态 |
|---|---|
| F1 代码修复（写库失败 → 受控 FAILED） | 通过（离线，6 个新测试） |
| F2 代码修复（`eligibility.v1` 快照 + 消费端二次校验 + 裁定持久化 + CLI 展示） | 通过（离线） |
| 旧数据库兼容（v1→v2、旧 payload 可读、旧 Realization/Prompt 复用） | 通过 |
| 全量离线回归 | `uv run pytest -q` = **2543 passed, 3 deselected**；`git diff --check` 通过 |
| 生产知识审核（`knowledge_base/v0.4/`） | **未审核**：9 条全 `draft`、0 `approved` |
| 正式评测 / 真实 Provider / 图片批次 / Gate 判定 | **未执行** |

## 文档索引

- 任务书：[docs/task_books/post_v0.4_fixes/README.md](../task_books/post_v0.4_fixes/README.md)
- 整批交接：[docs/handoffs/post_v0_4_fixes_001_handoff.md](../handoffs/post_v0_4_fixes_001_handoff.md)
- 生产知识审核清单：[docs/handoffs/post_v0_4_knowledge_review_checklist.md](../handoffs/post_v0_4_knowledge_review_checklist.md)
- F2 架构裁定（`eligibility.v1` / `adoption_decisions`）：[docs/handoffs/architecture_decision_006.md](../handoffs/architecture_decision_006.md)
- v0.4 知识合同与语料（ADR-005）：[docs/handoffs/architecture_decision_005.md](../handoffs/architecture_decision_005.md)
- 语料目录说明：[knowledge_base/v0.4/README.md](../../knowledge_base/v0.4/README.md)

## 边界声明

- 生产知识**尚未人工审核**；agent 未把任何 `draft` 改为 `approved`，未填写 `reviewer` /
  `reviewed_at`。审核决定必须由真实审核人作出。
- 本批不构成知识质量、RAG 收益或图片质量结论；工程测试通过不等于 RAG 有效。
- 未冻结代码身份：工作区未提交，`b039b9b` 之上为 dirty 树。
