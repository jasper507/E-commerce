# MySQL与订单面试知识库：文档核对与飞书发布

2026-10-11。范围是一个总览、五个问题页、普通建单/查询读取两张时序图及现有电商知识库导航。只有文档与图，不改变事务、接口、索引模型/迁移、数据库或部署行为。

源码核对快照：`886600018badf0d157a6cd6d7164a308c4bd1c2b`。开始前确认main与远端main相等；既有6E指南修改保留，不纳入本次交付。

[总览](../learning/mysql-current-overview-study-guide.md)及五页：[事务与快照](../learning/mysql-interview/01-data-transaction.md)、[幂等与锁](../learning/mysql-interview/02-idempotency-locks.md)、[分页归属](../learning/mysql-interview/03-pagination.md)、[查询成本](../learning/mysql-interview/04-query-cost.md)、[联合索引](../learning/mysql-interview/05-composite-index.md)。六页反向链接本记录。

## 历史证据与本轮边界

沿用[MVP-3](mvp-3-order-inventory.md)、[分页](phase-3-order-list-pagination.md)、[查询基线](phase-3-order-query-baseline.md)、[正式索引](phase-3-order-list-index.md)和[9B新部署库迁移](phase-9b-local-deployment-run.md)。正文注明历史日期、数据与范围，不重新运行业务实验。

普通多SKU下单仍为行锁；秒杀条件UPDATE范围单独说明。三条SQL只指固定认证下的订单数据查询；5000→20只指首页订单索引步骤；COUNT和深页OFFSET成本仍在。10/09索引片未迁移原开发库，9B新部署库包含0005；本轮不实时连接原开发库确认状态。

## 本轮执行

实际工具版本lark-cli 1.0.95、whiteboard-cli 0.2.13。使用lark-operations归档流程及lark-whiteboard的GPT/Mermaid时序图路线；六页先确定面试读者、router.knowledge、正文/图用途并实际init-draft，再生成发布XML。草稿工作区保留本地，未改权限、移动或删除旧节点。

| 检查 | 实际结果 |
|---|---|
| 源码及本地链接 | 六篇77个本地链接/行锚有效；10段Python摘录去空白后唯一匹配固定源码快照 |
| 文档画像 | 六篇最终assessment.status均passed；标题不加自动编号，避免重复编号 |
| 独立事实审核 | 六页和两图通过；收紧完整下单竞争记录的日期措辞，不把最初失败日期当恢复后通过日期 |
| 远端全文回读 | 六篇正文、47个正文标题、80个超链接、10段代码内容及顺序匹配；页面标题一致 |
| 原生画板 | 普通下单43节点、查询25节点，生成节点与远端可见文本/类型核对；实际导出预览查看 |
| 根页保护 | revision12→13，只替换阅读顺序一段；原根标题、画板token/block/内容、其他正文和链接保持 |
| Wiki层级 | 原5栏目保留，新增MySQL总览；根下共6栏目，MySQL下恰好5个问题页 |

原生条件框回读时服务端可能省略顶层重复text，语义比较继续核对table.cells与富文本，不能用字段省略推断条件缺失。两张图均为真实导出预览视觉核对；方形预览全览字较小，原生画板可以放大，本地高清PNG继续保留。

本轮采用上一轮已经核实的原生节点写图方式，没有再次使用失败的Docx直接Mermaid导入。各图写前确认本轮新板为空，固定幂等键分别只写一次；source导出没有代码块时如实保留结果，本地.mmd是可编辑源码，不宣称远端嵌入了Mermaid。

## 已发布的位置

沿用空间`7693476782908935142`及项目根`WN1jwXDj0iPXm7kwLyHcmneenlb`，没有新建空间/多维数据库。

| 页面 | Wiki节点 | Docx对象 |
|---|---|---|
| [MySQL与订单总览](https://ocnbkcx1ug58.feishu.cn/wiki/CNaVwwMJsidse6k5d7zc2VLSnEf) | CNaVwwMJsidse6k5d7zc2VLSnEf | XLlWddocEon7AMx0nV1c8BL6nkh |
| [事务与快照](https://ocnbkcx1ug58.feishu.cn/wiki/FttSwh1nai5p23k9ifdcLMo5nLc) | FttSwh1nai5p23k9ifdcLMo5nLc | IGkHdqofno7BRzxfAwAcEUPgnsd |
| [幂等、锁和约束](https://ocnbkcx1ug58.feishu.cn/wiki/MneywSqYCiQ8dbk5lXMc8EPyncf) | MneywSqYCiQ8dbk5lXMc8EPyncf | ZZqFdeJwYodBslxgPejcN8jNnCe |
| [本人分页与顺序](https://ocnbkcx1ug58.feishu.cn/wiki/VoiEwxT10ixPM8kN8wLcS7Rvnif) | VoiEwxT10ixPM8kN8wLcS7Rvnif | TeeRd91gao5AJQxynlMcAPpbnef |
| [完整查询成本](https://ocnbkcx1ug58.feishu.cn/wiki/EB5XwzJTziOqqVk7TF3cg8GTnsg) | EB5XwzJTziOqqVk7TF3cg8GTnsg | D5bCdGNcwo6YZBxDuPocLqbdneg |
| [联合索引](https://ocnbkcx1ug58.feishu.cn/wiki/X45TwLMm9iYDBukhAXecyF0Qnlc) | X45TwLMm9iYDBukhAXecyF0Qnlc | UtBMdhK5Ko9ofJxmrJrccao7nDg |

普通下单画板`KxXfwWvIZhv0AQbc4zDc4Q7Gn5i` / block `doxcng56jJPd9jxviUeUFAjHhRg`在总览；查询画板`Ygcowkej0hWgXRbiaXgcnCVVnKh` / block `doxcnPfRnl4A2DSr9rbUtPHGUBc`在查询成本页。原根画板`SDuuwOD4dhwiy6bzOgocuEuPn2b` / `doxcnHonhnwhfczqeCIO1wexzZg`保留。

## 交付文件与未改范围

新增MySQL总览、五问题页、两图源/渲染/原生节点/远端预览、发布检查材料及本记录；只更新学习README和项目总览的一段导航。见[发布材料](../learning/mysql-knowledge-base-2026-10-11/README.md)、[最终检查](../learning/mysql-knowledge-base-2026-10-11/final-checks.json)、[下单图源](../learning/assets/mysql-interview-2026-10-11/order-sequence/diagram.mmd)、[查询图源](../learning/assets/mysql-interview-2026-10-11/query-sequence/diagram.mmd)。

没有修改应用源码、依赖、模型、迁移、配置、实际数据库、服务启动或其他知识库正文。未运行pytest、manage.py检查、前端构建、业务HTTP或新的服务实验；本轮检查仅证明资料/图/发布正确，不把历史测试时间改为本轮通过。

scoped diff空白与敏感信息检查通过，个人open_id、密钥/.env未进入资料。Git仅保存这轮授权资料，原有6E指南修改保持未提交。建议且用于文档保存的commit message：`docs: add interview-focused MySQL and order knowledge base`。
