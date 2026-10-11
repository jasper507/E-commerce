# Project One 学习导航：按业务问题看项目变化

更新：2026-10-11。本页收录69份正式本地学习指南；归档草稿、图资产与飞书发布记录保留原样。支付/Redis重构新增13份，搜索与MySQL专题各新增总览及五问题页，秒杀再补当前总览与Lua问题页。[支付/Redis重构记录](../verification/payment-redis-interview-knowledge-base-restructure.md)、[搜索知识库记录](../verification/search-interview-knowledge-base-publication.md)、[MySQL知识库记录](../verification/mysql-interview-knowledge-base-publication.md)、[秒杀/Lua更新](../verification/flash-sale-lua-knowledge-base-update.md)只记录文档检查与发布，服务历史验证仍见各专题。

先读[项目当前总览](project-current-overview-study-guide.md)串起完整交易和三条技术故事，再读[支付当前总览](payment-current-overview-study-guide.md)、[Redis当前总览](redis-current-overview-study-guide.md)及其问题页。以面试为最高优先级：讲清业务流程、失败交错、设计理由、替代与代价、核心代码、真实证据和“如果重做”。原图与历史资料继续保留。

这次整理帮助你脱离源码讲清：原来有什么问题、改变哪一步、为什么有效、证据到哪里为止。先读每页新增的“本片前后变化”，再读关键机制、取舍与证据；不用一次背完全部资料。能独立解释一个正常例子、一个失败例子及剩余代价，就先整理理解，避免自动追加实验。

## 先分清历史阶段与当前进度

每页变化块记录**该片完成时**的状态，后续衔接链接告诉你后来做了什么。旧正文中的“当前”“下一片”默认属于页内日期，不代表今天又要重做；旧代码摘录也是阶段快照。[项目总纲](../../PROJECT_CHARTER.md)是当前范围的可信来源。

截至本轮核对：

- 订单列表分页、真实查询基线和正式联合索引已完成代码/隔离验证；开发库尚未应用索引迁移。
- 单活动SKU秒杀采用条件UPDATE；HTTP临时5秒行锁等待和浏览器原意图恢复已验证。5秒不是整个请求期限。
- Lua准入首片已实现且默认关闭；额度满返回429，未知Redis结果返回503，原Key确认也等待恢复。MySQL继续保存订单与库存事实。
- 商品关键词搜索及MySQL基线已完成；搜索前端构建通过、浏览器待验收。ES隔离原型、候选读接口与手动别名切换已有证据，回读MySQL当前可见性/价格库存；默认关闭、只接测试来源，旧列表与前端仍MySQL。开发启用与自动同步待做；7B事故已获确认恢复。
- 缓存主线阶段收束于4J；支付已有真实MQ/Worker故障与恢复证据，本期保留人工补漏，没有周期自动接管。文档与测试通过不代表个人已经掌握。

## 1. 最小交易闭环：先建立整体理解

| 指南 | 本片最重要的变化 | 阅读重点 |
| --- | --- | --- |
| [MVP-1 购物车](mvp-1-cart-study-guide.md) | 浏览商品 → 保存本人购买意图 | 价格由后端决定；购物车检查库存但不预占 |
| [MVP-2 地址](mvp-2-minimum-address-study-guide.md) | 购买意图 → 有收货信息 | 最多一个默认与至少一个默认分别怎样保证 |
| [MVP-3 下单与预占](mvp-3-order-inventory-study-guide.md) | 当前资料 → 订单快照和库存预留 | 同事务回滚、原Key重放、快照为何不能跟着资料变化 |
| [MVP-4 Mock支付与履约](mvp-4-mock-payment-study-guide.md) | 待支付 → 核销、模拟发货、收货 | 支付尝试与订单分开；这篇记录当时SQLite证据边界 |

前四篇是早期长版，可优先读变化块和业务规则。MVP-4的MySQL待验证属于当时状态；后续支付已有真实证据，请沿支付专题查阅，不能把它冒充MVP-4该轮已运行。

## 2. 订单查询：从“返回少”到“数据库怎样读”

当前复习入口是[MySQL与订单总览](mysql-current-overview-study-guide.md)，串起[数据/事务/快照](mysql-interview/01-data-transaction.md)、[幂等/锁/约束](mysql-interview/02-idempotency-locks.md)、[归属与分页](mysql-interview/03-pagination.md)、[完整查询成本](mysql-interview/04-query-cost.md)、[联合索引](mysql-interview/05-composite-index.md)。六页侧重业务流、取舍和真实证据，两图解释普通建单与订单读取；不把分页、索引和正确性约束混为同一种优化。

| 指南 | 本片最重要的变化 | 阅读重点 |
| --- | --- | --- |
| [订单列表分页](phase-3-order-list-pagination-study-guide.md) | 全量返回 → 每页20单 | 少返回不等于只扫描20行；本人过滤应先于分页 |
| [真实查询基线](phase-3-order-query-baseline-study-guide.md) | 猜测查询成本 → 实际SQL与索引对照 | 区分估计/实际、COUNT/列表、首页/深OFFSET |
| [正式联合索引](phase-3-order-list-index-study-guide.md) | 临时候选 → 模型与迁移 | 普通排序索引不能替代幂等唯一约束；结构声明不等于开发库生效 |

这组虽叫Phase 3，实际补片发生在秒杀浏览器片之后。编号用于归属模块，不是全项目严格时间顺序；旧基线复现需要对应历史版本和隔离库。

## 3. Redis缓存：正确性与故障保护分开理解

当前复习入口是[Redis总览](redis-current-overview-study-guide.md)，按五个业务问题深入：[命中为何仍查库](redis-interview/01-read-path.md)、[旧回填与换代](redis-interview/02-consistency.md)、[同Key与多Key并发](redis-interview/03-concurrency.md)、[故障与恢复](redis-interview/04-failure-recovery.md)、[不存在商品的负缓存](redis-interview/05-negative-cache.md)。下面保留历史切片，解释这些机制怎样逐步形成。

| 指南 | 本片最重要的变化 | 阅读重点 |
| --- | --- | --- |
| [4A 商品详情缓存](phase-4-product-detail-cache-study-guide.md) | 每次加载SKU → 缓存命中少查一次 | 命中仍检查商品可见性；删除缓存为何挡不住旧回填 |
| [4B 缓存换代](phase-4b-cache-generation-study-guide.md) | 同Key旧回填 → 新旧代隔离 | 成功换代后的新请求隔离；换代失败仍有边界 |
| [4D 同Key请求合并](phase-4d-detail-singleflight-study-guide.md) | 冷请求各自加载 → 本进程共享一次加载 | 只合并同Key，不能约束多个不同Key |
| [4E 故障回源名额](phase-4e-fallback-admission-study-guide.md) | 故障都回MySQL → 名额满暂拒绝 | 以可用性代价保护数据库；当时健康未命中仍有缺口 |
| [4F 慢故障实验](phase-4f-slow-faults-study-guide.md) | 只知道回退 → 观察超时与恢复限制 | 本片新增证据，没有改变故障策略 |
| [4G 最小读取熔断](phase-4g-minimal-read-breaker.md) | 持续等待故障Redis → 冷却后探测 | 状态、旧轮次隔离与本进程边界；不修复旧价 |
| [4H 最小负缓存](phase-4h-cache-penetration-study-guide.md) | 同一不存在商品反复查库 → 暂存404 | 正常不存在与Redis故障分开；不保证创建后立即可见 |

部分缓存片只有实验记录，没有独立学习指南：

- [4C 冷缓存并发基线](../verification/phase-4c-cache-bursts.md)为4D合并请求提供之前状态。
- [4I 多Key集中变冷](../verification/phase-4i-multikey-avalanche.md)说明全局换代的回源代价。
- [4J 共享重建名额](../verification/phase-4j-refill-admission.md)后来补上健康未命中与故障回源共用名额；不能把4E/4I的旧缺口当成当前行为。该片有真实实验与有序调度边界，参数2不是生产容量结论。

## 4. 支付与MQ：每一步解决不同的故障窗口

先用[支付当前总览](payment-current-overview-study-guide.md)理解189元订单，再按问题深入：[模型与状态](payment-interview/01-models.md)、[正常付款与提交](payment-interview/02-normal-payment.md)、[UNKNOWN与查单](payment-interview/03-unknown.md)、[幂等与拒绝](payment-interview/04-idempotency.md)、[MQ重试与恢复](payment-interview/05-mq-recovery.md)、[到期关闭与迟到成功](payment-interview/06-expiration.md)、[证据与面试表达](payment-interview/07-evidence.md)。当前内容按29b7944核对，保留七个原Wiki节点；[旧支付详版](payment-learning-and-interview-guide.md)仍保留2026-10-06历史快照。

| 指南 | 本片最重要的变化 | 阅读重点 |
| --- | --- | --- |
| [支付专题复盘](payment-learning-and-interview-guide.md) | 分散阶段记录 → 一张189元订单串起问题 | 横向整理而非新切片；旧快照与当前人工恢复边界分开 |
| [5A Inbox人工恢复](phase-5a-payment-inbox-recovery-study-guide.md) | 等外部重投 → 从已入箱事件恢复 | 入箱成功不代表处理成功；每条复用原业务处理器 |
| [5B 持久重试预算](phase-5b-payment-event-retry-study-guide.md) | 未分类重跑 → 按类型、到期时间与预算处理 | 暂时故障、业务拒绝、耗尽分别是什么 |
| [5C-1 MQ基线](phase-5c1-payment-mq-study-guide.md) | HTTP同步处理 → 提交后交给Worker | Broker确认不等于支付成功；真实重复投递证据 |
| [5C-2 MQ有界重试](phase-5c2-payment-mq-retry-study-guide.md) | 处理失败即退出 → 后台有限退避 | 持久预算与消息重试如何协作 |
| [5C-3 发布补漏](phase-5c3-payment-publication-recovery-study-guide.md) | 已提交但漏投 → 人工补投 | 提交后发布仍有窗口；MQ本身不能补漏 |
| [5C-4 Worker恢复](phase-5c4-payment-worker-recovery-study-guide.md) | 领取后中断可能卡住 → 租约与显式恢复 | 区分未提交、已提交未ACK；不要重置业务重试次数 |
| [5D 支付/过期竞争](phase-5d-payment-expiration-concurrency-study-guide.md) | 顺序例子 → 两种Order锁顺序的实证 | 终态、迟到成功与库存/退款副作用最多一次 |

ACK是Worker向队列确认消息已处理；业务成功仍以MySQL提交的支付、订单与库存事实为准。本期恢复仍有人工触发与调查成本；真实资金、周期自动补漏和生产容量不属于已验证成果。

## 5. 秒杀与Lua：先正确建单，再限制进入事务的尝试

当前复习入口是[秒杀总览](flash-sale-current-overview-study-guide.md)及[07 Lua原子准入](flash-sale-interview/07-lua-admission.md)。飞书原六页与六张旧图保留；当前入口区分MySQL内幂等顺序与启用Lua后的前置预算。原Key也可能先429/503，恢复放行后再确认原单；预算不是库存或严格在途槽。以下切片继续保留各自日期。

| 指南 | 本片最重要的变化 | 阅读重点 |
| --- | --- | --- |
| [6A 秒杀下单](phase-6a-flash-sale-checkout-study-guide.md) | 普通下单 → 活动价、限购与资格预占 | 完整交易不变量；当时支付保护尚待接通 |
| [6B 资格生命周期](phase-6b-flash-sale-lifecycle-study-guide.md) | 建单占资格 → 支付消耗/未支付关闭释放 | 旧单迟到事件不能污染新单资格 |
| [6C-1 竞争基线](phase-6c1-flash-sale-contention-study-guide.md) | 单顾客例子 → 双顾客抢一件的事务观察 | NOWAIT忙与没货不是同一个结果 |
| [6C-2 完整事务候选对照](phase-6c2-flash-sale-inventory-comparison-study-guide.md) | 只测库存 → 同一整单事务换库存步骤 | 相同业务产出下比较NOWAIT/条件UPDATE |
| [6C-3 有限负载对照](phase-6c3-flash-sale-inventory-load-study-guide.md) | 正确性 → 时延、重试与连接代价 | 等待与快速拒绝各有代价；合成负载不能外推生产 |
| [6C-4 策略决定](phase-6c4-flash-sale-strategy-decision-study-guide.md) | 实验候选 → 有理由的选型 | 仅选型与静态核对，当时代码仍NOWAIT |
| [6C-5 正式库存步骤](phase-6c5-flash-sale-inventory-failures-study-guide.md) | 认可方案 → 正式条件UPDATE与失败合同 | 0/1行、锁超时、未知异常与整单回滚 |
| [6D HTTP与等待](phase-6d-flash-sale-http-study-guide.md) | 服务函数 → 顾客可调用的安全HTTP | 后续6D-2落实临时5秒与恢复，不能说总HTTP最多5秒 |
| [6E 浏览器意图恢复](phase-6e-flash-sale-browser-study-guide.md) | HTTP能力 → 页面购买与刷新确认 | 原用户/Key/活动项/地址保持不变；响应丢失不是购买失败 |
| [6F Lua原子准入](phase-6f-flash-sale-admission-study-guide.md) | 有效POST都进事务 → Redis额度先筛选 | 限尝试速率，不扣库存、不保证在途数；429/503后保持原意图 |

6C-4没有单独验证文件：本片静态检查及所依据的6C-2/3历史证据记录在[ADR 0001](../adr/0001-flash-sale-inventory-conditional-update.md)。6D第一轮记录与[6D-2等待验证](../verification/phase-6d2-flash-sale-http-wait.md)也应区分阅读，避免混用等待政策。

## 6. 商品搜索：从MySQL基线到可重建查询副本

当前复习入口是[搜索总览](search-current-overview-study-guide.md)，按五个问题深入：[为什么用ES](search-interview/01-why-search.md)、[中文与索引](search-interview/02-chinese-index.md)、[候选回读](search-interview/03-read-path.md)、[重建切换](search-interview/04-index-switch.md)、[故障与一致性](search-interview/05-failure-consistency.md)。六页包含业务流程、核心代码、替代代价、真实证据与重做选择，两张时序图对应读请求和别名切换；默认关闭与仅测试来源的边界保留。

| 指南 | 本片最重要的变化 | 阅读重点 |
| --- | --- | --- |
| [7A 商品搜索基线](phase-7a-product-search-baseline-study-guide.md) | 浏览/分类 → 名称或描述完整关键词搜索 | 当时查询规则与COUNT/列表真实计划；公开API仍保持此行为 |
| [7B ES可重建原型](phase-7b-search-index-prototype-study-guide.md) | MySQL事实 → 独立搜索副本 | CJK命中/漏匹配、旧索引陈旧与Bulk部分失败；本片当时未接API |
| [7C ES候选读入口](phase-7c-search-read-api-study-guide.md) | 原型查询 → 独立API回读当前摘要 | 陈旧过滤、候选数、短空页和503；仅测试来源，未同步或切前端 |
| [7D 手动重建与切换](phase-7d-search-index-switch-study-guide.md) | 新候选建好 → 别名切换并回读确认 | 构建失败、结果未知和在途旧读；单会话/操作者，未自动同步 |

7D补上手动切换的成功/失败/未知边界；先整理理解并评估演示与收尾缺口，不自动扩大同步。导航与历史页链接更新是文档检查，不代表重跑旧浏览器或性能实验。

## 7. 部署：先理解完整运行方式，再分步执行

| 指南 | 本片最重要的变化 | 阅读重点 |
| --- | --- | --- |
| [9A 本机部署准备](phase-9a-local-deployment-study-guide.md) | 只运行依赖 → 独立Web部署候选与启动步骤 | Nginx/Gunicorn职责、容器地址、静态文件和独立数据卷；记录准备阶段 |
| [9B 首次部署运行](phase-9b-local-deployment-run-study-guide.md) | 候选配置 → 真实镜像、新库初始化与8080入口 | 36迁移、154静态文件、14项HTTP和只读浏览器；记录当时登录/交易与中间件未验收 |
| [9C 登录/CSRF与运维流程](phase-9c-session-csrf-operations-study-guide.md) | 公开读取通过 → 身份/写请求保护15项HTTP验收 | Session保持、CSRF轮换/拒绝与精确清理；更新/停机/备份参考未演练 |

拥有者确认先部署与版本收尾、后整体复习，不要求面试时展示网站。9A记录准备，9B记录首次基础运行，9C补真实HTTP登录/CSRF与运维参考；后者不是浏览器UI或备份恢复实测。部署基础证据够用后先整体梳理，是否追加恢复演练按收益决定；缓存/Lua/MQ/ES和公网HTTPS不按功能数量自动扩展。

## 其他辅助材料建议：按实际困惑再补

[项目复盘与重做选择](project-rebuild-retrospective-study-guide.md)横向解释用户提出的“如果重新做，会改哪里”：以缓存恢复、查询基线和测试隔离讨论理由、代价与证据。包含用户明确要求的口述例子；未来候选与已有改进分开，不新增业务实现。关联[归档核对](../verification/project-rebuild-retrospective-knowledge-base.md)。

最新安排：先部署与版本收尾，再整体复习；不要求面试时展示网站。下方辅助材料按后续实际困惑选用。

1. **小术语卡：** 每个模块只解释最容易卡住的3–5个词，用项目例子说明作用；例如索引/执行计划、幂等/ACK。需要时查，不另背一整套名词表。
2. **常见误解：** 每模块保留两三个容易说错的结论及证据边界，例如“有索引就只读20行”“用了MQ就不会漏事件”。能帮助你发现理解跳步。
3. **个人疑问页：** 由你记录“我能独立解释什么、哪里还讲不清”，再针对这些缺口讨论。AI写了指南、给了答案或测试通过，都不能替你勾选已掌握。

这三项仅为建议，本轮没有生成新的术语库、题库、参考答案或掌握记录。旧版图/练习没有删除；[面试优先写作规范](knowledge-base-authoring-sop.md)要求讲透关键机制，必要深度不受旧篇幅惯例限制。
