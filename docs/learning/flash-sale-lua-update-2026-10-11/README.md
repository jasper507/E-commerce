# 秒杀/Lua知识库更新：发布材料

2026-10-11，文档补充。业务源码固定在`96904bcc47562c51a1e4b9cc731f8ed5d40fdb75`；旧秒杀知识库的2026-10-08证据与18ffbe2链接保留。本轮未执行业务测试或修改运行开关。

实际目标是已有[秒杀与库存并发](https://ocnbkcx1ug58.feishu.cn/wiki/B49NwtiePiCqqFkz61kc4ac9n79)；新增[07 Lua原子准入](https://ocnbkcx1ug58.feishu.cn/wiki/YEZhw0ya7iQZUGkLHAMcOkFfnQf)。空间、父节点、原6个子节点身份均保持相同。

- [当前总览指南](../flash-sale-current-overview-study-guide.md)与[Lua问题页](../flash-sale-interview/07-lua-admission.md)是本地学习正文。
- [验证与发布记录](../../verification/flash-sale-lua-knowledge-base-update.md)说明实际执行、失败修正及未验证范围。
- [final-checks.json](final-checks.json)汇总源代码、层级、原资源和新图核验；[local-checks.json](local-checks.json)保留4段代码及链接检查。
- `before/`保留7页原始读取；`latest/`和`verified/`是最终读取与比较；`patch-plan.json`记录27个局部操作，`patch-journal/`保留逐步格式检查、写入响应和新修订读取。
- `created.json`、`lua-latest.json`和`lua-verified.json`记录唯一新增页及正文回读。CLI初始化草稿留在忽略的`draft_41a8505d_folder/`中；未覆盖旧秒杀草稿目录。
- [Lua图源与实际导出](../assets/flash-sale-lua-2026-10-11/lua-sequence/)含本地Mermaid、PNG、生成节点、远端原始节点和实际预览。原6张图没有调用更新接口。

实际执行顺序：读取目标与原页 → 初始化并评估新页XML → 创建唯一新节点 → 写入并回读正文 → 核对空新板后写入1次原生图 → 评估27个补丁 → 基于每次fresh read的修订号和块ID局部更新 → 再次回读7页、新页与目录 → 比较导出的实际原生图 → 保存验证记录。

发布脚本保留执行依据，不应把已完成的创建或追加命令当成每次复习都需要重跑的命令。`publish_article.ps1 -Mode verify`与`publish_patches.ps1 -Mode verify`只读取远端并更新本地核验记录；`final_observe.ps1`只读取目录；`final_checks.py`只核验本地文件。`-Mode create/fill/apply`会写远端，本轮已执行完成；以后更新先重新读取与规划。

图固定写入键为`flash-sale-lua-20261011-publish-1`。首次导出尚未就绪后未重写；后续导出成功。远端source导出没有代码块，本地Mermaid仍可编辑。
