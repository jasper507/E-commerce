# 秒杀与库存并发：飞书知识库发布记录

发布日期：2026-10-08（Asia/Shanghai）。固定源码快照：`18ffbe2f2bf674d31f3d2dc055976aedd41af010`。正文围绕业务场景、设计原因、替代方法、实测收益与代价展开，少量核心代码和时序图用于解释；不把技术名词或新增组件数量当作收益。

用户授权按已提议结构创建知识库，并使用 `lark-operations` 与 `lark-whiteboard`。批准目标是现有[电商项目总目录](https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb)，知识空间 `7693476782908935142`；新增“秒杀与库存并发”模块及六篇子页，保留原支付、Redis模块。总目录只追加新模块入口，不覆盖旧正文。

## 已发布七页

| 页面 | 实际链接 | 原生时序图节点数 |
| --- | --- | ---: |
| 秒杀与库存并发 | [打开总览](https://ocnbkcx1ug58.feishu.cn/wiki/B49NwtiePiCqqFkz61kc4ac9n79) | 22 |
| 01 重复下单：幂等与限购为什么分开 | [打开页面](https://ocnbkcx1ug58.feishu.cn/wiki/LoLFwPMLvi8WADkTsQDc3qfdnMd) | 29 |
| 02 抢最后一件：为什么选择条件 UPDATE | [打开页面](https://ocnbkcx1ug58.feishu.cn/wiki/PTjLwPhoAizQfWkHJdDcHXJFnK0) | 23 |
| 03 下单失败：为什么整单回滚与等待 5 秒 | [打开页面](https://ocnbkcx1ug58.feishu.cn/wiki/AOvlwoTENiyvgok63ivct7xGnWe) | 21 |
| 04 从付款到关闭：资格生命周期与 MQ 的业务价值 | [打开页面](https://ocnbkcx1ug58.feishu.cn/wiki/HDK7wuA36iLRG3k511lca7rInvb) | 32 |
| 05 响应丢失：为什么保留原购买意图 | [打开页面](https://ocnbkcx1ug58.feishu.cn/wiki/R1Duwgh8bimIyQkdUrWcDpNAnze) | 21 |
| 06 证据与演进：哪些结论可以向面试官讲 | [打开页面](https://ocnbkcx1ug58.feishu.cn/wiki/LaqzwVXSHiZsjzkPTuccYlt4n5g) | 无独立图 |

六张图共 148 个原生节点，Mermaid 转换成可编辑 OpenAPI 节点，不是把 PNG 当画板发布。

## 发布与核对范围

发布路线：本地草稿与图先审查 → 使用用户身份在已确认 Wiki 目录创建节点 → 确认新 Docx 为空后追加正文 → 补齐真实导航链接 → 写入原生画板 → 回读文档、Wiki层级和画板原始数据，导出远端实际预览。使用既有授权范围和直接 PowerShell CLI，没有为了发布扩充业务服务或接入新基础设施。

根代理已完成独立回读核对：七页正文、链接、代码块、表格与本地发布稿等价；六图节点数和文字与本地产物相符；新模块有六个子页，项目总目录原内容保留。保存的七份文档回读均成功；总览及01至05页revision3，06页计划更新后revision4，原revision3快照另存。六图的远端/本地节点数量为22/29/23/21/32/21。根代理已实际查看六张飞书导出预览；本地图渲染与几何检查资料保留。

这些检查验证的是发布内容和图，不是本轮重新运行应用、浏览器、MySQL、Redis或RabbitMQ测试。正文引用既有隔离实验及历史验收，不把有限负载当生产容量，不把故障注入当真实断网。特别保留：条件UPDATE仍有等待与连接成本；5秒为会话行锁等待预算，不是整个HTTP期限；浏览器响应丢失是在实际201后受控丢弃客户端响应。

工程事实与拥有者理解分开：正文和图不能证明用户已经掌握；整个秒杀链路的独立讲述验收仍待完成。现有证据足以整理当前MySQL版本，先形成可独立解释的故事，不自动增加实验。

## 本地资料与后续边界

- `publication-plan.json`：授权目标、固定提交、七页实际URL/文档/节点/画板映射与内容指纹。发布阶段状态字段可能先记录待回读；最终核验以根代理生成的 `verification-summary.json` 为准。
- 根目录 `draft.xml` / `publish.xml` 为总览草稿和真实导航发布稿；六个 `draft_*_folder` 内分别保存子页的草稿、发布稿及局部 `review.md`。
- `diagrams/<主题>/` 保存 `diagram.mmd`、`diagram.json`、`diagram.png` 和本地检查资料；`remote/` 保存写入响应、七页回读、层级结果、六图原始节点与远端导出预览。
- 根目录 `review.md` 由根代理校验器生成，汇总发布正文及飞书实际画板预览，供离线审阅；本记录不替代该完整审阅材料。`local-check.json` 保存七页本地体裁与链接检查。
- 项目规范v1.8已确认后续Redis Lua原子准入与ES商品搜索计划，06页计划说明同步更新并重新读回核对。本次未实现它们：Lua先验证数据库保护效果，MySQL仍为交易事实来源，不把范围扩为Redis库存预扣；ES另建搜索基线并验证索引一致性。两者都不作为当前秒杀知识库完成门槛。

本次发布整理未修改交易逻辑、依赖、数据库或部署配置。后续知识库沿用“业务问题 → 当前方案为何适合 → 替代与取舍 → 少量核心代码/时序图 → 真实证据及边界 → 独立复述”的方式维护。
