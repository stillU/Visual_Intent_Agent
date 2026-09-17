# 开放测试数据选择

版本、下载地址和实测校验值统一以 [sources.lock.json](sources.lock.json) 为准。以下是适配本项目的使用方案，不是这些数据集原作者给出的项目评测标准。

## 1. GenEval：优先准备

- 官方来源：[djghosh13/geneval](https://github.com/djghosh13/geneval)。仓库 MIT 许可，下载时同时保留对应提交的 LICENSE。
- 文件：`prompts/evaluation_metadata.jsonl`，已核实 553 条；single_object 80、two_object 99、counting 80、colors 94、position 100、color_attr 100。
- 数据包括 `prompt`、`tag`、`include` 等元数据；只把 prompt 作为原始用户文本，其他字段保留为来源注释，不偷偷注入用户输入。
- 用途：单主体、数量、颜色文本的工程输入；双主体、位置、属性绑定用于识别当前领域表达边界。
- 当前不安装其检测器、分割模型及完整评分环境。未来需要原版图像评分时，另建依赖环境并按官方流程执行。

## 2. T2I-CompBench：边界压力样本

- 官方来源：[Karine-Huang/T2I-CompBench](https://github.com/Karine-Huang/T2I-CompBench)。核对仓库 `License.txt`（MIT）和 `NOTICE.md`；附带模型等不应被视为统一 MIT 许可。
- 本包固定原始 `examples/dataset/` 下 color、shape、texture、spatial、non_spatial、complex 的六个 `*_val.txt`，各 300 条，共 1,800 条。不是把后续 CompBench++ 全部类别混算进来。
- 用途：颜色/形状/纹理约束及复杂关系的边界检查。系统若不能结构化表达某关系，应记录为不支持，不得删去关系后算成功。
- 只取文本；不下载原仓库生成模型、评分器或执行其安装脚本。

## 3. COCO-CN：中文真实描述

- 官方来源：[li-xirong/coco-cn](https://github.com/li-xirong/coco-cn)，仓库许可 MIT；官方 README 指向的托管页目前为 [AIMClab-RUC/COCO-CN](https://huggingface.co/datasets/AIMClab-RUC/COCO-CN)，数据卡标注 MIT。
- 已确认文件 `coco-cn-version1805v1.1.tar.gz` 和固定托管 revision。归档内部路径、字节数、哈希尚未核实，不能提前编造字段映射。
- 用途：中文自然表达和中英文领域词汇适配。优先人工中文描述，记录实际子集；不要混淆人工描述、翻译与机器翻译。
- 下载后先检查 README、许可及字段，再使用。按原始 image ID 分组保留原始划分，避免同图多描述跨开发/保留集。
- 文本许可不自动覆盖 COCO 原始照片；本阶段不下载照片。若未来需要图片，应逐项核查对应权利和使用条件。

## 最小准备规模（建议，不是已生成样本）

先准备 70 条原始文本：GenEval 20 条（single_object/counting 各 10），CompBench 30 条（六类各 5），COCO-CN 20 条人工中文描述。来源访问或许可受阻时交付可用部分，明确缺口，不用未经核实镜像替代。

抽样采用固定排序：每一类内按 UTF-8 字符串 `20260916|数据集ID|原始样本ID` 的 SHA-256 升序选取。原始 ID 使用 JSONL/文本的 1-based 物理行号；COCO-CN 则按归档实际 ID，并保留 image ID。清洗不能改变原始文件，派生数据另存。

先按相同原文、同图 ID、同一派生父样本分组去重，再抽样和划分；记录组键及去重规则，必要时从排序后续补足。不要为了凑数拆开同一组。保留集不用于挑知识、调整关键词或选择规则；正式评测前另行制定足量与统计设计。

每条派生记录至少保存：dataset_id、revision、source_file、original_id、group_id、language、original_text、supported_scope、unsupported_constraints、split、license、变更说明。此为暂存记录，不是领域 Schema。

开放数据不带本项目 IntentDelta、PIN、确认/委托状态和 RAG 期望结果。后续应另建 30 条明确标为“项目派生”的工程用例：三个知识路径各 10 条，覆盖委托、明确值、PIN、未确认条件、条件不符、模型不符、未审核知识、冲突、缺少知识及成功采用。实际断言以当前代码合同为准，不追求图像质量分数。

不得将派生用例结果称为官方 GenEval / CompBench 分数；不得把测试 prompt、答案或保留样本写入生产知识库。工程回归通过也不代表 RAG 有质量增益。
