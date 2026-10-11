# MySQL与订单知识库发布材料 — 2026-10-11

本目录保存六页候选、草稿决策与画像、节点身份、写入响应、远端全文回读及检查。CLI初始化生成的独占草稿工作区保留本机并忽略；没有保存应用业务数据、个人open_id或认证凭据。

发布流程沿用已有文档工具并按本轮范围调整：先init-draft，再核对源码/链接并生成XML、检查assessment.status；在项目根下创建MySQL总览及五问题页，正文写入后全文比对。两图先本地渲染/视觉检查，再由各自图任务将whiteboard-cli生成的原生节点写入本轮新板，随后导出/回读并查看实际预览。空板只是中间步骤，最终不保留空板。

上一轮搜索资料已观察到Docx直接导入Mermaid的解析限制，这轮直接采用原生节点方式，没有重复试验失败路径。source导出如无代码块便如实记录，本地Mermaid和远端可编辑节点分别保留；不能宣称从远端还原了Mermaid。节点语义比较包含条件框table.cells，忽略服务端省略的顶层重复文本。

创建已完成，不能盲目重跑init或create。created.json记录节点身份，run-publication.ps1在写入前检查现有节点/版本，响应立即落盘再判断。verify只读取并比较文档，final_checks.py只核对本地资料/已导出节点，不运行业务服务或迁移。根导航只替换阅读顺序一段，所有其他内容和原画板受保护。

阅读[总览](../mysql-current-overview-study-guide.md)与[本轮验证](../../verification/mysql-interview-knowledge-base-publication.md)。源码快照为`886600018badf0d157a6cd6d7164a308c4bd1c2b`，历史业务证据保留原日期。本轮没有pytest、数据库实验、开发库迁移或业务实现。
