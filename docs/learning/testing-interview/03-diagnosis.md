# 03 请求失败后怎样排查：症状、状态与三个真实反例

2026-10-12，源码快照`407066c61b6fed59a172320e997e66fa637fc9eb`。[专题总览](../testing-current-overview-study-guide.md) · [安全复现](04-isolation.md) · [本轮核验](../../verification/testing-evidence-knowledge-base-publication.md)。这是已有代码和真实历史事故的阅读路径，不表示本轮已经排查新的线上故障。

**先确认请求停在哪一层、原业务是否已提交，再判断该查状态、依赖还是配置。** 状态码是线索，不能只凭一个503断言“Redis坏了”，也不能把超时直接翻译成“订单不存在”。

## 本专题前后变化：从“重试看看”到保留原业务身份

顾客点击秒杀，页面没拿到结果。第一次排查动作应保留用户、原Key、活动项、地址及当时响应，不应马上换Key创建新业务。否则你会丢掉对原尝试的确认，还可能制造重复意图。

本项目的幂等Key识别一次业务意图，Event ID识别原付款通知，Attempt识别付款尝试；它们不是全项目统一的请求日志ID。当前没有新增贯穿所有组件的分布式追踪系统，排查应先使用已有这些身份。

## 按请求顺序收窄，而不是同时猜所有组件

1. **页面/资源层。** HTML、JS、CSS有没有各自收到正确响应？HTML200不代表Vue执行或API成功。Admin HTML正常但样式丢失，看collectstatic、共享卷与静态URL；深路由刷新异常看入口回退。
2. **HTTP与身份层。** 实际路径、状态、响应体、Session和适用CSRF检查是什么？403不能统一解释为未登录；当前本人API拒绝、登录CSRF与网关鉴权各有自己的入口。
3. **应用与事务层。** 是进入业务前被429/503拒绝，还是事务执行中失败？用原Key/原Event及持久状态确认是否已有提交，不凭前端提示推断数据库结果。
4. **存储与依赖层。** 实际数据库/服务目标是什么，锁是否等待，缓存代号是否换成新值，Broker发布还是消费出了问题？同一个状态码可能对应不同位置。
5. **恢复层。** 先按原身份确认，再用对应获准恢复入口；核对恢复后终值。调查、补投、改配置、重置状态和清空数据是不同动作，不能混成一个“修复”。

这些步骤是建议的诊断顺序。本轮没有去读取业务库或日志，具体命令要回到对应验证/运维记录并确认目标与影响。网页入口与容器地址见[部署请求链](../deployment-request-chain-study-guide.md)，身份原因见[认证总览](../auth-current-overview-study-guide.md)。

## 已有错误线索怎样对应本项目

| 症状或持久状态 | 源码所表达的含义 | 首先核对什么 |
| --- | --- | --- |
| 秒杀409，`stock_busy` | 库存步骤1205/1213按当前规则退出整单事务 | 原Key有无订单、原事务是否回滚，别换Key重发 |
| 秒杀409，`checkout_busy` | HTTP等待适配捕获其他下单SQL的1205/1213 | 原请求身份、失败步骤、会话等待值恢复；5秒不是总HTTP截止 |
| Lua429，`flash_sale_rate_limited` | 原子准入窗口满，未进入这次下单事务 | 等窗口后原Key确认；拒绝不能证明旧单不存在 |
| Lua503，`flash_sale_admission_unavailable` | 额度结果未知、Redis异常或状态不可解释 | 准入目标与结果，不绕过保护向DB放行 |
| 支付Event ERROR | 还需看`failure_kind`和`error_code` | 原Event、次数、到期、BLOCKED/EXHAUSTED，不能全部自动重试 |
| 支付Event PROCESSING | 已持久领取，可能在处理或遗留 | 领取版本、租约和锁状态；不是看到租约时间就抢正在持锁的事务 |
| ES读入口503 | ES不可用、超时、结果无效或多目标别名等不可用条件 | 当前开关、实际别名目标、错误类型；前端不应伪装成空搜索结果 |
| 代理502 | Nginx没能正常取得上游响应 | web进程与连接，再结合日志；不能仅此断定数据库坏 |

搜索的`search_unavailable`是源码里的DRF异常默认code，当前没有统一异常handler把它一定输出成JSON的`code`字段；503响应通常是`detail`。表中的Lua/秒杀业务code不能外推为所有接口都有同一个错误格式。源码依据：[秒杀等待适配](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/promotions/http_services.py)、[搜索异常与入口](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/catalog/views.py)。

## 核心代码：为什么ERROR要再分类

来源：[支付处理失败分类](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/payments/services.py#L445)。

```python
def _classify_processing_failure(error):
    if isinstance(error, FlashSalePurchaseInvariantError):
        return PaymentEvent.FailureKind.BLOCKED, "flash_sale_purchase_invariant_error"
    if isinstance(error, PaymentInvariantError):
        return PaymentEvent.FailureKind.BLOCKED, "payment_invariant_error"
    if (
        isinstance(error, OperationalError)
        and connection.vendor == "mysql"
        and error.args
        and error.args[0] in {1205, 1213}
    ):
        return PaymentEvent.FailureKind.TRANSIENT, "database_transient_error"
    if isinstance(error, DatabaseError):
        return PaymentEvent.FailureKind.BLOCKED, "database_nonretryable_error"
    return PaymentEvent.FailureKind.BLOCKED, "unexpected_processing_error"
```

它只定义支付事件错误的分类，后续代码才锁原Event、验证领取次数，保存错误、计算下一次时间或EXHAUSTED。1205/1213可能符合有界重试，其他数据库异常和业务不变量错误需要停下调查；不能把这一政策照搬成秒杀HTTP自动重试，也不能从“依赖恢复”推导预算应该清零。

## 真实反例一：浏览器没结果，订单已经创建

6E先得到真实POST201，再由脚本让页面收不到响应。数据库已有89元订单、预占和购买资格，库存1/0→0/1。页面进入待确认，刷新后即使活动显示无货，仍用原用户、Key、活动项与地址确认，200返回同单；同Key改地址真实409，副作用不增加。

第一处失败是客户端接收结果，不是建单事务失败。它支持原意图恢复，但故障是脚本注入，不能称切断真实网络。换Key不是这个场景的安全恢复。原过程见[6E记录](../../verification/phase-6e-flash-sale-browser.md)。

## 真实反例二：原消息仍在队列，Event保持PROCESSING

5C4在T1提交领取后真实SIGKILL。实际观察到原消息回到ready、消费者数0，MySQL中的Event保持PROCESSING。实验先等租约到期，人工`--recover-stale`核对原领取版本并补投原ID，再启动Worker，观察原UUID重投与最终PAID、次数1→2、COMMIT1。

源码解释是：若未修复就直接处理消息，PROCESSING不能重新领取；Broker投递不会替应用改变这条持久状态。修复扫描也会跳过正在持锁的业务事务。这段资格解释不能冒充实验已经先启动Worker验证卡住的步骤。

第一处未完成是持久领取与后续业务处理之间的窗口。Broker投递与应用恢复资格各负责一段；没有周期补漏、心跳或全自动接管。默认租约60秒，实验覆盖为8秒；不能把实验等待时间承诺为恢复SLA。过程见[5C4记录](../../verification/phase-5c4-payment-worker-recovery.md)。

## 真实反例三：依赖恢复了，详情又读到旧价格

4F第三实验已缓存129元；临时让应用连接本机不监听端口，MySQL更新提交169元，但Redis换代失败。故障期间回源能读169，恢复正确连接后原代号/旧缓存仍可读到129，直到正常换代或过期处理。这个场景与该片另一个CLIENT PAUSE慢故障实验不同，是提交与缓存失效之间的缺口；不能因为“降级还能返回”就说恢复后一致。

后来的4G已经补连续故障绕过的读熔断，4J补健康冷Key共用重建名额；它们没有自动补偿这次提交后换代失败。旧4F慢owner重复加载也只是后来共享名额之前的历史基线，不能描述为当前仍没有回源保护。原因和边界见[4F记录](../../verification/phase-4f-slow-faults-and-recovery.md)与[缓存故障恢复页](../redis-interview/04-failure-recovery.md)。

三个反例分别指出客户端结果、应用持久资格、缓存失效窗口。排障目标是找到第一错误及后续影响，再选择对应恢复方法；扩大重试或重启所有服务不能替代这条因果链。

## 如果重新做，我会补可观察性与恢复门槛

候选是增加安全的请求关联日志，并将原Key/Event、事务结果、错误分类与恢复操作关联起来，避免只能手工拼线索。保留现有业务事实与有界预算；不会因日志更全就重置状态或自动接管。

新增成本是日志量、保留周期、访问权限和敏感信息过滤；不能把Cookie、签名或原付款敏感数据写日志。先选一条支付处理失败与一条秒杀原Key确认，验证日志能定位第一失败且不泄露信息，再决定是否扩大。当前没有实现统一trace ID、监控告警或自动缓存失效补偿。
