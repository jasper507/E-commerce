# 搜索知识库发布材料 — 2026-10-11

本目录保存六页候选正文、Presentation Decision初始化结果、画像检查、节点身份、写入日志及全文回读检查。原始CLI草稿工作区保留在本机并由本目录.gitignore忽略，不含应用运行数据。

真实搜索行为沿用源码快照`7ea1963f2a7eec6018852e7d3c17b007d1e6d190`与7A–7D历史验证；详见[本轮验证](../../verification/search-interview-knowledge-base-publication.md)和[搜索总览](../search-current-overview-study-guide.md)。

创建已完成，`created.json`是已有节点身份，不能另跑一次初始化或盲目再创建。`run-publication.ps1`逐次保存响应，先检查已知节点与版本，再执行局部写入。最后的`verify`只读取文档/节点并比较候选；`final_checks.py`只检查本地文件和生成候选，不运行业务服务。

首次Docx直接导入Mermaid返回`degrade_code=2107`，全文回读确认正文完整而图被省略。总览仅在原位置补插一块空画板，没有再次追加正文；04创建时直接插入空画板。两板分别由专属画板任务使用whiteboard-cli生成的原生节点填充，固定幂等键各写一次，随后回读32/42节点并查看真实预览。最终没有空白画板。

原生节点可在飞书编辑，source导出返回`no code blocks found in whiteboard`，因此没有宣称远端保存了Mermaid代码。本地可编辑.mmd、生成节点与实际导出预览均保留于[图资产目录](../assets/search-interview-2026-10-11)。
