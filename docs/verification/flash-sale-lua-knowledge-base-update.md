# 秒杀/Lua知识库现状更新：验证与发布记录

2026-10-11。当前源码快照`96904bcc47562c51a1e4b9cc731f8ed5d40fdb75`。本轮只更新学习资料/飞书正文并新增Lua问题页及图，不改变业务源码、开关、数据库、部署、依赖或接口。

开始前确认main和远端相等，保留既有6E指南修改。旧秒杀发布是2026-10-08、固定18ffbe2的7页/6板归档；旧草稿与实验材料不覆盖，改动基于本轮fresh read。

[当前秒杀总览](../learning/flash-sale-current-overview-study-guide.md)与[07 Lua问题页](../learning/flash-sale-interview/07-lua-admission.md)反向链接本记录。原[6F切片指南](../learning/phase-6f-flash-sale-admission-study-guide.md)、[6F验证](phase-6f-flash-sale-admission.md)和原始JSON保留历史日期；搜索后续状态见[搜索当前总览](../learning/search-current-overview-study-guide.md)。

## 明确修正的口径

Lua准入已实现/隔离验证，默认关闭，本地/测试范围；搜索MySQL/ES已实现到手动别名切换，但测试入口、前端和同步边界保留。原Key在MySQL事务中先认原单，启用Lua的HTTP入口则先准入，429/503后不能判断旧单不存在。Lua并非库存预扣或严格在途上限，结果未知不重试/不退还预算。

## 本轮执行

这是文档补充，复用6F已有验证记录。本轮完成源码静态核对、飞书实际写入与回读；未重跑MySQL、Redis、MQ、ES或浏览器业务实验。

| 检查 | 实际结果与范围 |
|---|---|
| 源码与本地资料 | 新增2篇指南；两篇正文16个本地链接存在；4段核心代码匹配固定96904bc源码中的views.py、admission.lua、admission.py、services.py |
| 飞书写入前检查 | 新Lua正文及27个局部补丁的CLI格式评估均为passed |
| 原页面更新 | 在7个原页面上核对基线；27次局部操作修改6页，所有写入success且无warnings；每次操作后重新读取修订号与块ID |
| 原页面回读 | 7页全文语义、标题、标题层级、链接及代码核对通过；6张旧画板和其他原资源标识保持相同；04支付生命周期页正文与修订号完整不变 |
| 新Lua问题页 | 1页；回读9个标题、13个链接、4段代码与规划全文一致；仅1个新画板块 |
| 新图实际发布 | 空板检查后写入1次；导出48个原生节点，节点类型、可见文字及31个连接箭头的端点和方向与生成源一致；实际2560×2560预览已查看，包含参与者、提交/回滚、5秒等待、准入拒绝及原意图恢复 |
| 知识库层级 | 秒杀父节点身份未变；子节点从6到7，原6个标题、对象和节点身份保持相同，只增加07 Lua页 |
| 官方语义参考 | 本轮读取Redis官方Lua、EXPIRE和SET说明，核对脚本执行期间不交错、INCR保留期限、SET携带PX期限的表述；业务证据仍来自项目自身记录 |
| 敏感内容和范围 | 本轮提交资料扫描未发现个人open_id、访问Token或密钥字段；未修改业务源码、功能开关、部署、依赖或历史实验材料；既有6E指南修改保留且不纳入本轮提交 |

以上静态检查和回读结果分别保存在[local-checks.json](../learning/flash-sale-lua-update-2026-10-11/local-checks.json)、[final-checks.json](../learning/flash-sale-lua-update-2026-10-11/final-checks.json)、[七页回读记录](../learning/flash-sale-lua-update-2026-10-11/verified/)和[新图核验记录](../learning/assets/flash-sale-lua-2026-10-11/lua-sequence/published-verification.json)。[发布说明](../learning/flash-sale-lua-update-2026-10-11/README.md)保留目标身份、逐步响应及检查脚本。

## 飞书交付与保留的历史

入口：[秒杀与库存并发](https://ocnbkcx1ug58.feishu.cn/wiki/B49NwtiePiCqqFkz61kc4ac9n79)。新增：[07 Lua原子准入：为什么先保护进入事务的尝试](https://ocnbkcx1ug58.feishu.cn/wiki/YEZhw0ya7iQZUGkLHAMcOkFfnQf)。

旧总览增加当前状态、Lua开启后的流程与07导航；01幂等、02条件更新、03回滚等待、05原意图恢复和06证据演进同步修正相关边界。旧图继续表达原MySQL路径，注明Lua关闭或已经放行的适用前提；没有重绘或搬迁旧资源。旧代码、18ffbe2源码链接、6A—6E实验结论及问题练习保留。

新页围绕“减少进入订单事务的尝试”解释业务价值、Lua固定窗口、Python严格解释返回结果、HTTP等待适配与MySQL正确性、替代方案、6F真实证据、失败代价及“如果重新做”。图的本地[Mermaid源](../learning/assets/flash-sale-lua-2026-10-11/lua-sequence/diagram.mmd)、[完整PNG](../learning/assets/flash-sale-lua-2026-10-11/lua-sequence/diagram.png)和[实际远端预览](../learning/assets/flash-sale-lua-2026-10-11/lua-sequence/published-preview.jpg)保留。飞书图为原生节点；source导出返回“no code blocks found”，不能宣称远端保留Mermaid代码块。

## 执行中的失败与修正

- 首次本地片段检查少列services.py，导致其中一段未匹配。加入实际来源后4段均匹配；这不是业务测试失败。
- 首次创建调用因PowerShell对无属性title元素取值为空而在CLI参数解析处失败，没有创建页面。改用明确的XML节点取值和非空检查，并重新核对6个原子节点后，只成功创建1个新节点。见[创建输入失败记录](../learning/flash-sale-lua-update-2026-10-11/creation-input-failure.json)。
- 图的本地生成与审查已完成，后续协作代理因模型额度限制未能发布。主代理核对新板仍为空后完成原生节点写入，未重复创建或写入旧板。
- 新图写入成功后的首次导出返回2890007“whiteboard not ready”。没有再次写图；继续正文核验后，再导出原始节点、预览与source成功。最终检查使用成功导出的实际文件。
- 最终核验辅助脚本先因本地虚拟环境没有PIL、随后因汇总报告尚未生成就检查其自身链接而退出。改为标准库读取JPEG尺寸并调整自报告链接检查顺序，未安装依赖；修正后重新运行完整核验。

不将这些失败计作通过。每次修正均先检查目标实际状态，避免盲目重试创建或追加正文。

## 可讲的证据与停止条件

6F的24个Redis客户端4放行/20拒绝、8线程执行24个APIClient请求时订单事务BEGIN和新建订单从24降为4、SQL从480降为80，来自2026-10-09隔离实验，日期及原始材料不变。它们支持“部分尝试被挡在下单事务前”；认证被固定，不代表整个Session接口零SQL，也不代表生产吞吐或延迟提升。本轮没有新增负载数据。

Lua已实现并有该隔离证据，默认仍关闭；当前限制是本地/测试环境、共享固定窗口预算及失败关闭。Redis不预扣库存、不分配购买资格，准入次数也不是严格在途并发数。业务失败不退次数，EVAL结果未知不自动重试；原Key确认可能再次被429/503挡住。未知状态仍需保留原购买意图。

ES已到7D手动别名切换，与秒杀交易正确性分开；默认入口、前端MySQL路径、没有自动商品同步及单构建者边界不变。此处只更新旧“尚未实现”描述，不新增搜索工作。

本轮达到“当前代码与口径一致、原资源保留、新页与实际图可读、证据边界可追溯”后收束。新增恢复故障实验、浏览器真实链路、公平性或生产容量验证均未在本轮执行；面试回答应先用现有资料解释清楚设计与代价。
