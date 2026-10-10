# 支付总览：一笔189元订单怎样得到可信结果

2026-10-11，源码快照`4673b9f8ea95664ce4ab886e1a9e791e6ffbd42e`，项目总纲v1.15。[学习导航](README.md) · [本轮归档核对](../verification/project-payment-overview-knowledge-base-update.md) · [飞书支付](https://ocnbkcx1ug58.feishu.cn/wiki/PCQ7wP9kMikz0eklmNYcH1J1nwh)。这是当前阅读入口；[旧支付详版](payment-learning-and-interview-guide.md)保留当时流程与历史材料。

## 本专题前后变化

以前已有七个问题子页和真实实验，但入口仍带旧版本说明，完整流程、为什么用MQ和重做选择不够集中。本轮按当前源码补上业务流程、图、两处核心代码、证据和重做选择，保留原学习地图及七个子页。没有新增支付能力，没有重跑业务实验。

## 先抓住支付的业务问题

顾客买一只189.00元鼠标，下单把库存从可售20、预占0变成19、1，订单为PENDING_PAYMENT。支付要查清原付款、把可信结果应用到原订单，并在重复通知或进程故障后继续处理，不能重复核销。商品后来涨价，不改变189元订单快照。

本项目使用Mock Gateway（可控制结果的模拟支付供应商）。MySQL保存付款、订单和库存事实；默认同步处理事件。RabbitMQ与Celery的可选后台路径仅限本地开发/测试，独立部署本轮未开启；没有真实资金或第三方Sandbox。

## 从点击付款到库存核销

1. **先保存原付款尝试。** Session确定本人，付款请求带订单与支付幂等键。短事务先检查原Key与归属，再保存PaymentAttempt（一次付款尝试），金额来自订单。相同Key同订单返回原尝试；不同订单冲突；已有CREATED/PENDING/UNKNOWN时禁止第二个活跃尝试。
2. **提交后调用供应商。** 不持订单或库存锁等待网关。调用超时且尚未确认时，把原Attempt标为UNKNOWN（结果尚不明确）；并发通知已经确认的终态不会倒退。不能猜成失败或重新付款。普通GET只是读本地状态；显式查单询问供应商原Attempt，可信查单结果也走同一事件入口。
3. **先入箱，再处理。** 签名事件原文先在独立T0事务保存进Inbox（持久收件箱），按供应商事件号去重；相同号不同原文不能覆盖。处理时仍验签并核对金额、币种、订单和状态。无效签名的内部摘要编号不能抢占合法事件号。
4. **领取与业务提交分开。** T1先锁Event，提交处理次数、PROCESSING和默认60秒租约。T2再锁事件、订单和原尝试，完成库存与状态变化。同一事务里，Attempt变SUCCEEDED、Order变PAID、预占变COMMITTED、reserved从1变0、available仍19；秒杀资格变CONSUMED、COMMIT流水和Event终态一起提交。
5. **失败留下恢复依据。** T2出错先回滚业务效果，T3另用短事务复核领取版本后记录稳定错误与后续资格。重复执行终态事件不再次核销。HTTP202只说明收件留存，Broker接收和Worker ACK（确认消息已处理）也不能代替数据库付款事实。

![默认同步付款：先提交Attempt，再等网关，Inbox与业务事务分别提交。](assets/kb-overview-2026-10-11/payment-normal/diagram.png)

图展示默认同步成功及网关超时分支；可选MQ另见下一节。核心显式锁顺序为Event → Order → Attempt → 秒杀Purchase（若有）→ Reservation（inventory_id/id）→ Inventory（pk）。T1只锁Event；创建Attempt为User → Order → 已有Attempts → 秒杀Purchase；取消/到期从Order开始，不反向锁Event。JOIN、索引和约束可能产生额外物理锁，这不是完整InnoDB锁清单或全面无死锁证明。

## 必须解释的失败分支

| 分支 | 业务结果与恢复边界 |
| --- | --- |
| 原Attempt明确FAILED | 只结束这次尝试，订单仍待付、库存仍预占；规则允许时才创建新Attempt |
| CREATED/PENDING/UNKNOWN尚未确认 | 阻止取消/到期关闭，保留预占等待查单；15分钟不是保证释放的硬截止 |
| 无未确认支付，取消或到期关闭 | 原预占释放，可售恢复，RELEASE只一次 |
| 已关闭后迟到成功 | 原订单保持关闭，确认Attempt成功并记录一次模拟退款；库存不再次核销或释放 |
| 已支付且未发货取消 | 一次RESTORE恢复可售与模拟退款；秒杀已消费资格不重新释放 |

模拟退款记录不是实际资金到账；已付订单随后仍可由Admin模拟发货、本人确认收货。

## 为什么用MQ，另一种方法是什么

支付通知需要被可靠留存，而处理可能暂时遇到MySQL锁等待或Worker中断。MQ把处理交给后台，使HTTP入口能在保存收件后结束；它支持再次投递，但不保证业务效果只执行一次，幂等仍由原MySQL记录、事务和唯一约束维护。当前没有测得生产吞吐或HTTP性能提升，不能把后台化直接说成支付更快。

**有意义的替代是同步处理加Inbox人工恢复。** 当前默认就是它：部署简单、调用链直接，但请求承担业务处理等待，恢复依赖人工。可选MQ增加Broker/Worker、发布失败与ACK前中断窗口；MySQL仍是事实来源，两种入口调用同一个处理器。

![可选MQ：T0后发布原ID，漏投人工补投，T2后ACK，重投不重复效果。](assets/kb-overview-2026-10-11/payment-mq/diagram.png)

Inbox提交到消息发布之间仍有窗口。现有`recover_payment_events`已支持同步恢复、`--publish`人工补投和`--recover-stale`修复到期租约；先修复再补投，不重置次数。暂态故障首次在内最多3次，后续等待5/10秒；业务拒绝、不变量破坏和预算耗尽必须停下，不能无限重试。当前没有周期自动接管。

## 两处核心代码怎样支撑业务规则

第一段来自[支付源码192行](../../backend/apps/payments/services.py#L192)。`create_payment_attempt`有自己的atomic事务，返回后才调用网关；原Key复用跳过新调用，超时尝试保存未知，已确认终态不倒退：

```python
attempt, created = create_payment_attempt(
    user=user,
    order_public_id=order_public_id,
    idempotency_key=idempotency_key,
)
if not created:
    return attempt, False

provider = gateway or MockGateway()
try:
    payload, signature = provider.create_payment(attempt)
except (TimeoutError, ConnectionError):
    return mark_attempt_unknown(attempt_id=attempt.pk), True
```

第二段来自[支付源码364行](../../backend/apps/payments/services.py#L364)，处于支付业务atomic事务内。库存核销和付款、订单、秒杀资格一起改变，随后保存Event终态；任一步失败整体回滚：

```python
elif outcome == "SUCCEEDED":
    purchase = lock_flash_sale_purchase(order, expected_status=FlashSalePurchase.Status.HELD)
    _commit_inventory(order)
    attempt.status = PaymentAttempt.Status.SUCCEEDED
    attempt.active_marker = None
    attempt.save(update_fields=("status", "active_marker", "updated_at"))
    order.status = Order.Status.PAID
    order.save(update_fields=("status", "updated_at"))
    transition_flash_sale_purchase(purchase, target_status=FlashSalePurchase.Status.CONSUMED)
```

这些是核对过的真实摘录，不是可脱离外围事务直接运行的完整实现。

## 实际证据说明到哪里为止

- [5C2有限后台重试](../verification/phase-5c2-payment-mq-retry.md)：真实MySQL1205、RabbitMQ与Worker，3项场景验证次数、到期与停止；消息数量不等于处理次数。
- [5C3发布补漏](../verification/phase-5c3-payment-publication-recovery.md)：Broker故障后的首次与重试漏投，2项场景由新CLI人工补投原ID；补投成功不等于付款成功。
- [5C4中断恢复](../verification/phase-5c4-payment-worker-recovery.md)：真实SIGKILL的4个窗口，业务提交前回滚、提交后ACK前重投不重复核销/模拟退款；没有周期接管保证。
- [5D支付/到期竞争](../verification/phase-5d-payment-expiration-concurrency.md)：两种Order获锁顺序×三种未确认Attempt，共6个真实服务层MySQL场景；不是HTTP/MQ负载或全面死锁验证。

上述是历史已保存证据，本轮仅静态核对、图渲染、飞书读回；不是新业务测试或个人掌握验收。

## 如果重新做，我会怎样安排

我会更早把付款未知、事件暂态失败、提交后漏投、领取后中断这四类问题的恢复责任和允许等待写清，再设计状态、操作步骤和故障验证。现有统一恢复命令已经存在，不能说重做才增加；重做首先改变设计与验证的时机。

当前学习规模下人工兜底简单、可调查，剩余代价是依赖人发现与介入，没有无人值守恢复时限。若未来业务明确要求自动恢复，再评估受控周期扫描与告警，复用已有资格、预算与锁内复核。代价是调度故障、重复扫描/发布、候选饥饿、误报和人工调查；不能自动把UNKNOWN改失败、重置EXHAUSTED或解封BLOCKED。这是未采用的候选，不构成本轮新增实现范围。未来需要验证重复扫描、调度中断及耗尽可见性，才支持新的恢复结论。

下一步先按本页解释正常与一个失败流程，再维护受代码变化影响的页面；当前证据足以支持这里的机制解释，不自动追加故障实验。
