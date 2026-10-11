# 项目、支付与 Redis 面试知识库重构验证

2026-10-11。本轮属于 Supporting / Delivery 文档整理，用户已确认按面试优先方案重构现有电商 Wiki。业务源码快照为 `29b7944860080ccddedb2c0f0c060c0a27658376`，Charter v1.16。十五页均已发布并读回核对；未完成的清理单独记录。

## 问题与授权范围

项目总览需要突出完整交易闭环与三条技术故事；支付入口新旧材料重复，七子页部分机制缺具体推演；Redis 长文有完整材料却缺问题层级。用户授权更新项目总览、支付总览及原七子页，重组 Redis 总览并创建五个问题子页。原节点和图优先复用，秒杀与项目复盘节点不修改；没有移动节点、改变权限或扩大业务实现。

## 学习资料与当前状态

- [项目总览](../learning/project-current-overview-study-guide.md)
- [支付总览](../learning/payment-current-overview-study-guide.md)
- [Redis 总览](../learning/redis-current-overview-study-guide.md)
- 发布候选、前后读回和执行记录位于 `docs/learning/kb-interview-restructure-2026-10-11/`。

开始前 `main` 和实时 `origin/main` 同为上述快照。既有 `docs/learning/phase-6e-flash-sale-browser-study-guide.md` 修改来自其他工作，保留且不纳入本轮提交。发布前已读取三个根页及七支付子页完整 XML，确认项目/支付/Redis分别为 revision 7/9/7、七子页均 revision 2；项目直接子节点4、支付子节点7、Redis子节点0。Redis完整XML实际有4个画板，其中思维地图在简版Markdown中降级为代码，不能据Markdown误判只有3图。

## 证据边界

本轮检查源码、历史实验、链接、XML与飞书写入/读回。历史 MySQL、Redis、RabbitMQ/Worker 实验仍保留原日期，不改写为本轮实测。没有运行 pytest、应用构建、迁移、业务数据库/缓存操作、故障实验或部署命令。最新9C真实HTTP登录/CSRF验收属于既有独立部署证据；交易、四可选模块启用、浏览器登录及运维恢复演练未由本轮验证。

## 发布与检查结果

| 检查 | 本轮实际结果 |
| --- | --- |
| 正文与知识层级 | 更新原有10页：项目、支付、Redis三个入口和七支付子页；在原Redis入口下创建五个问题页 |
| 最终读回 | 十五页逐页通过正文、标题层级、链接顺序、代码块核对；最终项目/支付/Redis根页revision为11/17/17，支付01/02/03/05/06为6、04/07为4，Redis五子页为3 |
| 原生画板 | 13个原块的ID、token、完整XML和顺序与发布前一致；本地查看了下载的四Redis图预览 |
| 节点保护 | 根直接子节点仍4，支付仍7且身份/标题/父节点不变；Redis新增恰好5个指定子节点；无移动、无权限变更 |
| 创作检查 | 最终十五份候选XML的`assessment.status`全部passed；标题自动编号已去掉，避免叠加手写中文序号 |
| 核心代码 | 23段Python摘录按非空行去公共缩进匹配固定29b7944源码连续片段；不是只匹配函数名 |
| 本地资料 | 指南、导航、SOP及本记录的相对链接目标和源码行号边界通过；正式指南计数从42增加到55 |
| 独立审查 | 项目/Redis及支付分别核对当前源码与抽核历史证据，未发现P1/P2问题；修正“本轮实现”和UUID字符长度表述 |
| 应用验证 | 没有新增业务代码，未运行pytest、构建、数据库或中间件实验；历史结果仍按各片原环境引用 |

本轮内容由业务场景进入，补具体请求流程与失败交错、设计理由、替代与代价、关键源码、证据边界和每专题重做取舍。支付明确旧Key重放位置、User锁粒度、不同事件号竞争同Order、Attempt与Event两套预算、T0–T3和四种中断窗口。Redis补当前3GET/1guard SQL、G1/G2早捕获、129→169→129→169反例、同Key合并/多Key预算与503代价、读熔断epoch、负缓存及随机slug边界。不是以篇幅或术语数量代替机制完整性。

首次范围替换因列表包装没有块ID被CLI拒绝，确认根页revision仍7后改用显式块ID列表；拒绝操作未计为通过。自动标题编号与手写序号叠加的问题随后修正，全部十五页再次读回通过。完整结果保存在发布manifest、合并JSONL、候选与前后快照中；本地没有改应用、配置、依赖或现有秒杀修改。

临时草稿清理未完成：递归删除以及改用精确文件路径/空目录删除两种请求均被自动审批规则拒绝，理由只返回`blocked by policy`。五个CLI临时工作区保留在本地忽略目录中，候选和创作决策已经归档，未尝试绕过拦截。此限制不影响已发布的十五页及读回结果。

发布入口：[电商项目](https://ocnbkcx1ug58.feishu.cn/wiki/WN1jwXDj0iPXm7kwLyHcmneenlb) · [支付](https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh) · [Redis](https://ocnbkcx1ug58.feishu.cn/wiki/O4iUwSvl1i4Je7keOL0ci1iAnLf)。当前素材足以进入整体复习，不因文档重构自动扩展搜索、部署栏目或故障实验；个人是否能独立解释仍待学习讨论验证。
