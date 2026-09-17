# RAG 相关知识与证据边界

目标是为现有九条 draft 的真实审核提供依据，不是收集大量文字后直接批准入库。当前内容字段只用于说明；可执行选择仍受已确认 Intent、精确条件和候选白名单约束。

## 可开放复用的资料

| 来源 | 适合提取的内容 | 不足以推出的规则 |
|---|---|---|
| [Blender 4.5：Cameras](https://docs.blender.org/manual/en/4.5/render/cameras.html) | 景深、焦点和 F-stop 的概念，参数改变与虚化的关系 | 特写一定浅景深；坐姿必须浅景深 |
| [Blender 4.5：Light Objects](https://docs.blender.org/manual/en/4.5/render/lights/light_object.html) | 光源尺寸与阴影软硬的关系 | 室内一定 soft；户外一定 natural；soft 与 natural 必然互斥 |
| [Wikibooks：Shot Sizes](https://en.wikibooks.org/wiki/Movie_Making_Manual/Cinematography/Shot_Sizes) | Wide Shot、Medium Shot、Close-Up 的景别描述 | 单主体必须 close_up；户外必须 wide_shot |

Blender 手册许可见 [Copyright](https://docs.blender.org/manual/en/4.5/copyright.html)：除例外外为 CC BY-SA 4.0 或后续版本。保留署名、来源、许可链接和修改说明；衍生文本按适用的相同方式共享要求处理。标志、代码、图片等需分别检查，不能一概沿用正文许可。本次已读取页面；4.5 URL 仍可能更新，引用时保存访问日期、章节和实际引用文本哈希。

Wikibooks 固定修订为 [oldid=4313350](https://en.wikibooks.org/w/index.php?title=Movie_Making_Manual/Cinematography/Shot_Sizes&oldid=4313350)。已见页面 CC BY-SA 声明；精确许可版本与附加条款须沿页脚许可链接再次确认后才复制入库，当前不宣称已完成这一项。保留修订页及作者历史链接。部分程序化请求返回 403；遇阻请报告或使用允许的阅读入口，不绕过访问控制。

## 可查阅但不能混作开放知识语料的来源

- [Nikon：Understanding Focal Length](https://www.nikonusa.com/learn-and-explore/c/tips-and-techniques/understanding-focal-length)：用于核对焦距/视角概念；未核实开放复用许可，不能批量复制文章或图片。景别、焦距、画幅比例不是同一字段。
- [Qwen-Image 官方仓库](https://github.com/QwenLM/Qwen-Image)：用于后续查询模型兼容性，仓库代码为 Apache-2.0；权重、素材等另查自身许可。项目 Provider 别名 `qwen-image-3.0` 与具体模型版本的对应关系尚未在本包确认，不能据此填兼容性结论，更不能把官方演示里的提示词扩写方式直接移入确定性编译器。

## 如何变成可审核知识

1. 为每条旧规则建立外部证据卡：原规则 ID、事实、原始来源、章节/修订、许可、原文定位、项目推断、反例和剩余疑点。此卡保存在暂存资料中，不向冻结 KnowledgeUnit 添加新字段。
2. 区分物理概念、创作惯例和项目默认偏好。资料只支持概念时，结论应是“仅供审核参考”，不是“规则已验证”。例如浅景深不等于透视压缩，wide_shot 也不等于广角镜头。
3. 当前领域没有光圈、光源尺寸等字段，不能为了套用光学事实扩展条件语言或 Schema；不能用正文表达式代替精确条件。
4. 无依据的强规则可以建议收窄、暂缓或移除，但先交审核人决定。现有九条不要求强行保留或凑足数量。
5. 只有条件可表达、候选合法、来源完整的建议才生成独立 draft 提案。reviewer/reviewed_at 保持 null；按当前合同选择准确来源类型。改写第三方资料不应冒充无第三方来源的 `project_original`。
6. 人工审核决定、语料版本发布、哈希更新和正式评测另行授权。许可义务随语料一起交付，不能因为项目代码许可不同而丢弃。

不得从测试集提取答案作为知识；检索关键词不得针对保留集反向定制；不引入网络动态检索、向量库或模型自由改写来扩大本批次范围。
