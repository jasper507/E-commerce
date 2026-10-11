# 测试证据与故障排查：知识库核验与发布记录

2026-10-12。Supporting / Delivery，文档归档。固定源码`407066c61b6fed59a172320e997e66fa637fc9eb`。[学习总览](../learning/testing-current-overview-study-guide.md)反向链接本记录。

拥有者选择“测试证据与故障排查”并要求开始，沿用既有电商项目Wiki与面试优先的写作方式。本轮整理总览及四个问题页、一张真实并发验证方法图；既有模块与历史证据保留，项目根阅读导航只做一个段落更新。

本轮只读源码、历史记录和原始材料，创建本地文档/图并发布飞书。不执行数据库测试、真实故障注入、服务操作、HTTP登录或业务写入；不读取真实.env。

## 历史证据与本轮边界

代表来源是3期查询计划、4F缓存慢故障、5C4真实Worker中断、5D支付/到期锁等待、6C5库存错误、6E响应丢失、6F准入、7B隔离事故、7C/D搜索及9B/C部署/身份HTTP。历史日期和通过/失败/未验证各保留，不能将测试总数、代码存在、文档检查或模拟浏览器响应说成生产性能与完整端到端。

## 本轮实际核验与交付

飞书[测试证据与故障排查总览](https://ocnbkcx1ug58.feishu.cn/wiki/FwCFwuNLniXo19kIH7wcqAoAnxf)下实际创建四个问题页：[证据与终值](https://ocnbkcx1ug58.feishu.cn/wiki/MyEBwaSUAiZG80k7uxhco5c8nzh)、[并发与故障](https://ocnbkcx1ug58.feishu.cn/wiki/TRrSw6Jmsiyt5Jkiu8Cccki5n7d)、[排查第一错误](https://ocnbkcx1ug58.feishu.cn/wiki/DWAHwAOPcigpRAkxg7WcMCl4nPe)、[隔离与事故教训](https://ocnbkcx1ug58.feishu.cn/wiki/AEpJwKY7Hi2bsyk2x7kcwnHynhf)。对应本地正文为[01](../learning/testing-interview/01-evidence.md)、[02](../learning/testing-interview/02-concurrency.md)、[03](../learning/testing-interview/03-diagnosis.md)、[04](../learning/testing-interview/04-isolation.md)。

| 实际检查 | 本轮观察 |
| --- | --- |
| 目标与层级 | 创建前实时核对空间、根节点和8个直接子节点；完成后9个直接子节点，新专题恰有4个子页，原8个节点的标题、对象与父节点保持一致 |
| 文档格式与写入 | 5份CLI实际初始化草稿；创建前与互链生成后均通过格式评估；5个唯一节点，正文只追加到空文档，写入success且无warnings |
| 完整回读 | 五页合计35个正文标题、72个链接、5段核心代码，与候选XML完整语义及顺序相符；均为revision 2 |
| 核心摘录 | 与固定源码的连续原文一致；04嵌套测试片段仅移除外层4格缩进；源码链接起止行存在，本地链接存在 |
| 根导航 | 只替换1个阅读段落，revision 15→16；其他正文、旧导航与原1张画板及所有资源标识保持一致 |
| 原生时序图 | 唯一新板写前为空，固定写入键只写入1次；42节点=30连接线+5生命线+2分支框+5注释 |
| 图的检查与目视 | 本地PNG3698×6518，几何检查0错误/0警告；实际远端JPEG2560×2560已查看；可见文字、类型和箭头方向匹配 |
| 原生坐标 | 服务端小数精度使严格四舍五入比较在临界点误报；改为逐端点0.02像素容差，实测最大差0.0001210937503像素；没有重新写图 |
| 独立只读审查 | 五指南、Mermaid与代表历史记录/JSON及源码的因果和证据范围一致；不以审查代替业务运行 |
| 文档静态核验 | `build_publication.py`、`inspect_publication.py`、`root_navigation.py`、`final_checks.py`通过；新增材料JSON可解析、无个人ID/凭据字段；Git diff空白检查通过 |
| 应用实验 | 本轮未执行pytest、HTTP/浏览器业务验收、数据库查询、真实故障或恢复；历史结果保留其日期与环境 |

实际原生画板没有代码块，source导出返回`no code blocks found in whiteboard`，不宣称能够反向导出Mermaid。可编辑[Mermaid](../learning/assets/testing-evidence-2026-10-12/concurrency-sequence/diagram.mmd)、[本地图](../learning/assets/testing-evidence-2026-10-12/concurrency-sequence/diagram.png)、[实际预览](../learning/assets/testing-evidence-2026-10-12/concurrency-sequence/published-preview.jpg)与[图核验](../learning/assets/testing-evidence-2026-10-12/concurrency-sequence/published-verification.json)保留。

完整材料与操作边界见[发布目录](../learning/testing-knowledge-base-2026-10-12/README.md)及[最终检查JSON](../learning/testing-knowledge-base-2026-10-12/final-checks.json)。使用`lark-operations`与`lark-whiteboard`，lark-cli1.0.95、whiteboard-cli0.2.13；所有个人操作显式`--as user --format json`。写入前保存目标，写入后完整回读，不迁移旧节点、不创建第二套专题。

### 必须保留的历史边界与差异

5D是同步服务层MySQL真实锁等待证据，未启MQ；没有删除锁后的失败对照。5C4是真实Worker SIGKILL与原UUID重投，修复/重发后才启动Worker，不写成自动恢复时限。6C5的1205真实发生，1213/2006为SQL前异常注入；6F真实EVAL后注入TimeoutError，6E真实201后阻断交给页面的响应，均不声称真实网络丢包。

4F旧价实验把应用Redis目标改为未监听本地端口；DB更新且换代失败，恢复连接后仍可能读旧缓存。该边界不能被后续熔断/重建名额改进掩盖；本轮没有声称已补可靠失效机制。部署页独立实例的四个相关功能开关保持关闭，不把历史隔离实验说成该实例已启用。

7B[公开事故JSON](phase-7b-test-isolation-incident.json)的删除、两次隔离恢复、开发恢复及独立提交回读四表计数均为1/104/1/26，合计132；[原Markdown](phase-7b-test-isolation-incident.md)与原型记录仍出现139。本轮只核对公开材料，没有读取私密原始binlog或重新操作数据，也未改写旧记录。新04采用可计算的132并明确保留139待核对，不能称原始总数已经完全统一。

后续可以核对这处数量文字，或围绕真实困惑补实验；本轮达到归档停止条件，未扩展统一追踪、全局测试守卫、自动恢复或新故障矩阵。

## 保护范围与停止条件

只修改本轮指南、图、发布材料与必要导航。既有6E用户修改保留，不纳入提交。业务源码、测试实现、配置、依赖、真实数据库/缓存/队列与原始历史记录不变。

达到“业务正确性判据清楚、证据边界准确、真实失败因果可复述、图文与代码相符、隔离风险不被掩盖、远端完整可读”即停止，不因整理而自动追加实验或替拥有者标记已掌握。
