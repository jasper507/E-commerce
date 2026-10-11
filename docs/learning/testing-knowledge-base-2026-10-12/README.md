# 测试证据与故障排查：发布材料

2026-10-12，文档归档；固定源码`407066c61b6fed59a172320e997e66fa637fc9eb`。本轮未执行应用测试、数据库恢复、故障注入或服务操作。

飞书[专题总览](https://ocnbkcx1ug58.feishu.cn/wiki/FwCFwuNLniXo19kIH7wcqAoAnxf)下有四个问题页：[证据与终值](https://ocnbkcx1ug58.feishu.cn/wiki/MyEBwaSUAiZG80k7uxhco5c8nzh)、[并发与故障验证](https://ocnbkcx1ug58.feishu.cn/wiki/TRrSw6Jmsiyt5Jkiu8Cccki5n7d)、[故障排查](https://ocnbkcx1ug58.feishu.cn/wiki/DWAHwAOPcigpRAkxg7WcMCl4nPe)、[安全复现与隔离](https://ocnbkcx1ug58.feishu.cn/wiki/AEpJwKY7Hi2bsyk2x7kcwnHynhf)。原项目根节点与其他八个模块保留。

本地阅读[专题总览](../testing-current-overview-study-guide.md)及[四页正文目录](../testing-interview/)；实际检查、历史材料边界与数量口径差异见[验证记录](../../verification/testing-evidence-knowledge-base-publication.md)。

- `project-target.json`、`project-before*.json`保存实时目标、目录与正文基线。
- `pages.json`、`draft-workspaces.json`保存五份CLI实际初始化的草稿身份，原始草稿留在忽略的`draft_*_folder/`中。
- `candidates/`保存正文和单段导航XML；`local-checks.json`核对五段核心代码、固定源码行号及本地链接。嵌套代码片段只去掉外层缩进，内容仍与固定源码一致。
- `created.json`保存五个唯一节点；`*-latest.json`和`*-verified.json`保存完整正文回读与语义比较。
- `journal/`保存格式评估、创建、写入及回读的脱敏响应；格式要求`assessment.status=passed`，文档写入要求`result=success`且无warnings。
- [并发图资产](../assets/testing-evidence-2026-10-12/concurrency-sequence/)保留Mermaid、PNG、生成原生节点及实际远端节点/预览；发布响应存为`published-*-result.json`。远端没有代码块，本地Mermaid可编辑。
- `root_navigation.py`只替换项目总览的阅读段落，保留其他正文、旧导航链接和所有原资源标识。
- [final-checks.json](final-checks.json)汇总五页正文、目录层级、五段核心代码和一张42节点原生画板的检查。

实际发布顺序是：实时读取目标 → 初始化五份草稿 → 生成并评估XML → 创建五个唯一节点 → 重新生成Wiki互链并再次评估 → 填写及逐页回读 → 核对新板为空并写入一次 → 更新根阅读段落 → 导出画板并查看实际预览 → 完整回读与最终静态检查。

`run-publication.ps1 -Mode verify`只读取飞书并更新本地证据；`parse`评估草稿，`export`读取画板。`create/fill/boards/root`会写入远端，本轮已完成，不应在复习时重复运行。脚本保留既有身份与空正文/空板检查；出现不确定结果先核对实际效果，不盲目追加文档或图。

图的实际写入及导出由并行绘图任务在资产目录单独执行，使用同一固定写入键`testing-20261012-concurrency-sequence-publish-1`；没有并行运行主发布脚本的日志写入部分。
