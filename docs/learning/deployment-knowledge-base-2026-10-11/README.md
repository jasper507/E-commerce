# 部署与完整请求链：发布材料

2026-10-11，文档归档。源码固定`57329d80fd6afd13f78b2f6e2ea45f61a898ea6b`。本轮没有重新部署、访问应用数据库或执行登录/交易测试。

飞书[部署与完整请求链](https://ocnbkcx1ug58.feishu.cn/wiki/LlbLwU9mSi2CoIkYi5tcELbKn4e)位于既有电商项目根节点下，只创建这一篇与一张原生时序图，没有另外创建子页。项目总览只替换一个阅读导航段落，原七个模块和根文档原资源保留。

本地阅读[完整指南](../deployment-request-chain-study-guide.md)；实际检查及历史证据边界见[验证记录](../../verification/deployment-request-chain-knowledge-base-publication.md)。

- `project-target.json`与`project-before*.json`记录实时目标、原七节点及根正文基线。
- `draft-workspace.json`记录实际CLI初始化返回；原始草稿在忽略的`draft_*_folder/`中保留。
- `build_publication.py`核对3段真实配置与固定源码，生成正文和评估XML；`local-checks.json`记录15条正文相对链接。
- `created.json`记录唯一创建身份；`deployment-latest.json`、`deployment-verified.json`记录完整正文回读：10个标题、27个链接、3段配置、一张画板。
- `journal/`保留评估、创建、写入、回读和图导出响应。评估要求`assessment.status=passed`，正文写入要求`result=success`且无warnings。
- [图资产目录](../assets/deployment-request-chain-2026-10-11/request-sequence/)保留Mermaid、PNG、生成原生节点、实际远端节点与预览，以及文字/类型/箭头核验。原生图不含代码块，本地Mermaid可编辑。
- `root_navigation.py`只替换阅读段落，保留其他正文、原导航链接与所有原资源标识。
- [最终检查](final-checks.json)汇总完整回读、精确层级和60个原生节点核对；它属于文档验证。

实际顺序：读目标与目录 → 初始化一份草稿 → 核对源码并评估XML → 创建一个唯一节点 → 重新生成与评估Wiki链接 → 填写空正文并回读 → 确认新板为空并写入一次 → 导出与查看实际预览 → 修改一个根导航段落 → 最终回读与静态核验。

`run-publication.ps1 -Mode verify`只读取飞书并更新本地证据；`parse`只评估草稿，`export`只读取画板。`create/fill/boards/root`会写入远端，本轮已执行，复习时不应重跑。脚本检查已创建身份、空正文和空画板，画板使用固定幂等键；失败先核对实际作用，不能盲目追加。

从项目目录调用脚本后，脚本会进入材料目录。同一次PowerShell执行中继续调用时要使用脚本绝对路径，避免相对路径失效。本轮有一次后续相对路径未找到，填写命令当时未执行；随后使用绝对路径完成既有唯一节点，没有重复创建。
