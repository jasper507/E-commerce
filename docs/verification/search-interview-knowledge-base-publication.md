# 商品搜索面试知识库：文档核对与飞书发布

2026-10-11。范围：一个搜索总览、五个业务问题页、两张时序图及现有电商知识库导航。仅整理和发布文档，不改变搜索、库存、支付或部署行为。

源码核对快照：`7ea1963f2a7eec6018852e7d3c17b007d1e6d190`。开始与保存前均确认main和远端main相等；保留既有6E指南修改，不纳入本次交付。

[搜索总览](../learning/search-current-overview-study-guide.md)及五页：[引入理由](../learning/search-interview/01-why-search.md)、[中文与索引](../learning/search-interview/02-chinese-index.md)、[候选回读](../learning/search-interview/03-read-path.md)、[索引切换](../learning/search-interview/04-index-switch.md)、[故障与一致性](../learning/search-interview/05-failure-consistency.md)。六页均反向链接本记录。

## 证据来源与边界

历史服务证据沿用[7A](phase-7a-search-baseline.md)、[7B](phase-7b-search-index-prototype.md)、[7C](phase-7c-search-read-api.md)、[7D](phase-7d-search-index-switch.md)及[7B脱敏事故](phase-7b-test-isolation-incident.md)。本轮静态源码/文档检查、图渲染和远端回读不等于重新运行API、ES、MySQL或部署验收。

当前ES独立读入口默认关闭、只接受指定真实测试来源；前端仍接MySQL，独立部署未开启ES，没有自动同步。没有新增同条件性能对照、真实资金、生产容量、自动恢复或多人切换证据。

## 本轮执行记录

工具实际版本：lark-cli 1.0.95、whiteboard-cli 0.2.13。使用lark-operations归档流程与lark-whiteboard时序图流程。六篇先确定面试读者任务、router.knowledge及图示用途，再调用init-draft获得独占工作区，然后生成XML；原始草稿工作区保留本机，不删除。没有修改权限、移动或删除旧节点。

| 检查 | 实际结果 |
|---|---|
| 源码/本地链接 | 六篇78个本地链接及源码行锚有效；10段Python摘录去空白后唯一匹配固定源码快照 |
| 文档画像 | 六篇最终assessment.status均passed；标题不用自动编号，避免双重编号 |
| 独立事实审核 | 五问题页、总览及两图均核对；修正7B日期、事故因果及图中400窗口/404页尾检查 |
| 远端全文回读 | 六篇正文、48个标题、77个超链接、10段代码按顺序和内容匹配候选；各篇标题一致 |
| 原生画板 | 读流程32节点、切换42节点，节点类型与可见文本对照本地一致，实际2560×2560预览均已查看 |
| 项目首页保护 | 只替换阅读顺序一段，revision 11→12；原标题、原画板token/block/序列化内容、其余正文与链接保持一致 |
| Wiki层级 | 原4栏目保留；新增1搜索总览，根下共5栏目；搜索总览下恰好5个问题页 |
| Git差异/敏感信息 | scoped diff空白检查通过；发布JSON未保存个人open_id，无凭据或.env数据 |

这些计数是文档检查，不是搜索性能、业务测试数量或掌握程度。原生节点回读比较忽略服务端省略的条件框顶层重复text，继续核对table.cells中的条件文本及富文本；首次未做此规范化的比较失败，修正比较规则后才通过。两张图为长时序，方形远端预览全览字较小；原生画板可放大，本地高清图保留，没有文字截断或遮挡。

首次本地发布辅助脚本因PowerShell编码和JSON数组枚举失误未通过，修正后才记录最终画像结果，不计作成功。第一次Docx直接导入Mermaid返回partial_success及`degrade_code=2107`；立即全文回读确认正文完整、没有画板。随后只在总览原图位置插入新空画板，04直接插入空画板；专属图任务以原生节点填充两板，固定幂等键各写一次，再回读/导出/视觉检查。最终没有空白画板，也没有重复追加正文。

原生节点形式的source导出返回`no code blocks found in whiteboard`。本地Mermaid是可编辑图源，远端原生节点可编辑；没有宣称从远端还原出Mermaid。写入结果、原始节点与实际预览都保存于图资产目录。

## 已发布的位置

既有空间`7693476782908935142`，项目根节点`WN1jwXDj0iPXm7kwLyHcmneenlb`，未创建新空间或数据库。

| 页面 | Wiki节点 | Docx对象 |
|---|---|---|
| [商品搜索总览](https://ocnbkcx1ug58.feishu.cn/wiki/KxWQwuGFsiqO3Wk4m7TcZxXBnih) | KxWQwuGFsiqO3Wk4m7TcZxXBnih | HBiyd9nJloSVcGxayeYcm7gWnkb |
| [为什么用ES](https://ocnbkcx1ug58.feishu.cn/wiki/DmgkwAVBpiggdFkjj8ectYmPndf) | DmgkwAVBpiggdFkjj8ectYmPndf | FrnmdQzjqoCvU8xWYHKcR4XCntf |
| [中文怎样被搜出](https://ocnbkcx1ug58.feishu.cn/wiki/USNKwkcJ9idNvUkUJzhcvBDbnUt) | USNKwkcJ9idNvUkUJzhcvBDbnUt | Flc4dsp5moVJAgxl7TacoEZTngd |
| [为什么回读MySQL](https://ocnbkcx1ug58.feishu.cn/wiki/OxKOweFPWi4q2Kkc9YLc37DWnph) | OxKOweFPWi4q2Kkc9YLc37DWnph | QC6TduAjsoRYh7xxBpPcrq0Cnvd |
| [重建与安全切换](https://ocnbkcx1ug58.feishu.cn/wiki/AIHbw0c6CiOJnGkOrJkccSZvnvg) | AIHbw0c6CiOJnGkOrJkccSZvnvg | STxmdbrxqoNtgZxZc27c1Bkinhd |
| [故障与陈旧数据](https://ocnbkcx1ug58.feishu.cn/wiki/CMWBwMhsiinZ4XkHoJjcRR7SnZb) | CMWBwMhsiinZ4XkHoJjcRR7SnZb | IIgodc9bjo7QL7x5XGWctiCQnBb |

读流程画板`CIOHwtC8yhTCSAbqq4QcmkQFnBg`，block `doxcnzxyxbLNUjvCfmEzoOS6y9g`，位于搜索总览；切换画板`JxXBw98fkhVfSwbR0dRcoqVjnLg`，block `doxcnbF7YGxLugf3MzQvJ9Vwpsc`，位于切换问题页。保护的原根画板仍为`SDuuwOD4dhwiy6bzOgocuEuPn2b` / `doxcnHonhnwhfczqeCIO1wexzZg`。

## 本地交付与未改范围

新增搜索总览、五问题页、两图源/预览、发布候选与回读材料、本记录；仅更新学习README和项目总览的导航。材料见[发布目录](../learning/search-knowledge-base-2026-10-11/README.md)、[最终检查](../learning/search-knowledge-base-2026-10-11/final-checks.json)、[读流程图](../learning/assets/search-interview-2026-10-11/read-sequence/diagram.mmd)和[切换图](../learning/assets/search-interview-2026-10-11/switch-sequence/diagram.mmd)。

应用源码、依赖、模型/迁移、配置、开发业务数据、服务启动状态和已有支付/Redis/秒杀正文没有修改。没有运行pytest、manage.py检查、前端构建或新的ES/MySQL服务实验；文档变更采用链接/摘录/画像、实际远端回读与图检查即可。

建议且用于本次文档保存的commit message：`docs: add interview-focused product search knowledge base`。发布草稿保留本地，Git只保存作用明确的资料与脱敏检查；既有6E指南修改保持未提交。
