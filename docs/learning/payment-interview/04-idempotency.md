# 04 重复、乱序与业务拒绝

2026-10-11，源码快照 `29b7944860080ccddedb2c0f0c060c0a27658376`。[支付总览](../payment-current-overview-study-guide.md) · [本轮核对记录](../../verification/payment-redis-interview-knowledge-base-restructure.md)。本页用两个执行者解释“为什么唯一事件号不够”；并发推演来自当前锁和状态判断，真实实验边界在文末注明。

## “幂等”到底限制哪一次重复

Idempotency（幂等）就是同一动作重复执行，业务效果保持和执行一次相同。189元付款有三个重复入口：顾客重复点击、供应商重复发通知、Worker再次执行。不能用一个Key声称全部解决。

付款请求的`(user,key)`决定返回原Attempt；供应商通知的`(provider,event_id)`决定原Event是否已入箱；最终库存与退款效果，则由锁后状态检查、原子事务和唯一约束共同保护。不同event_id可以描述同一付款成功，因此第三层不能省。

例如Event E收到10次：T0重复读取同一记录，T1对PROCESSED/REJECTED不会再领取资格，T2也有终态保护，次数不增长、库存不重复核销。若原E因暂态异常处于ERROR，则重投仍要满足失败类别、到期时间与总预算，不能“再来一条消息就马上再试”。

## 不同event_id的两名Worker怎样只核销一次

E1和E2原文都是合法189元成功通知，付款引用指向同一Attempt A、同一Order O，但通知号不同。数据库允许两个Event；它们各自T1 count1后进入业务处理。

| 时刻 | Worker 1处理E1 | Worker 2处理E2 |
| --- | --- | --- |
| 领取 | 锁E1后提交PROCESSING/count1 | 锁E2后提交PROCESSING/count1 |
| 业务开始 | 锁E1，取得O锁，再锁A | 锁E2，尝试取得O锁，等待 |
| 成功提交 | 看到O待付/A未确认，核销预占1→0，O PAID/A SUCCEEDED/E1 PROCESSED | 尚不能依据旧查询结果执行核销 |
| 继续 | 释放O锁 | 获得O和A锁，重读到PAID/SUCCEEDED |
| 收尾 | COMMIT流水仍一条 | 匹配合法重复成功，跳过核销，仅完成E2处理 |

结果是Event两条且各有一次处理，Attempt一条、付款一次、库存COMMIT一次。这不是“事件号去重”，是同一Order上的业务串行化加终态重查。如果E1事务整体失败，O/A变化回滚，E2获得锁后可能接手成功；E1之后恢复会看到终态，不应再次核销。

真实核心分支来自[支付服务345行](../../../backend/apps/payments/services.py#L345)，在取得Order/Attempt锁并核对金额后执行：

```python
if attempt.status == PaymentAttempt.Status.SUCCEEDED:
    if outcome != "SUCCEEDED" or order.status not in {
        Order.Status.PAID,
        Order.Status.SHIPPED,
        Order.Status.COMPLETED,
    }:
        return _reject(event, "terminal_attempt_conflict")
    lock_flash_sale_purchase(order, expected_status=FlashSalePurchase.Status.CONSUMED)
elif attempt.status in RETRYABLE_ATTEMPT_STATUSES:
    return _reject(event, "terminal_attempt_conflict")
elif order.status != Order.Status.PENDING_PAYMENT:
    return _reject(event, "order_state_conflict")
```

已成功分支不会进入后面的`elif outcome == "SUCCEEDED"`核销分支。普通订单没有活动资格，秒杀订单还核对原资格已CONSUMED。流水的`(reservation,movement_type)`唯一约束进一步防同类重复，退款的Attempt一对一防重复模拟退款；它们是兜底，不替代业务判断。

当前真实MQ基线使用一个prefork Worker顺序消费三条重复消息，不是“两名Worker不同事件号”的并发实测。上表是源码可解释的锁顺序推演，不能把它包装成新跑出的实验结果。

## 为什么成功后的失败要拒绝，而不是覆盖

供应商可能先通知成功，后又到达旧PENDING或FAILED。如果覆盖成功状态，订单已经PAID、库存已COMMITTED，却显示付款失败，并可能允许新付款。项目在锁后将这类冲突记录为REJECTED/terminal_attempt_conflict，保留原成功事实。

如果原Attempt明确FAILED且订单仍待付，后来同Attempt成功也在上述终态分支拒绝；项目不自动推翻已确认结束的尝试。但**Order已经CANCELED/EXPIRED时，可信成功先走更前面的迟到退款分支**，确认Attempt成功并模拟退款，不能笼统说“FAILED后任何成功都拒绝”。已付取消后的再次成功同样复用原模拟退款。具体边界见[关闭与迟到退款](06-expiration.md)。

## 合法签名为什么仍可能被拒绝

Signature（签名）证明原文符合本项目供应商密钥校验，不能证明原文属于正确订单或金额。处理器继续核对供应商引用、订单UUID、有限Decimal金额、币种，以及金额同时等于Order.total和Attempt.amount。189元订单收到“18.90 CNY”即使签名合法也不能核销；签名金额正确但引用他人原订单，也不能按前端意图换绑。

同一合法事件号的新原文不同，T0返回event_id_conflict，不覆盖之前留存。伪造签名则使用内容摘要生成`invalid-...`内部编号，保留审计又不给攻击者占用合法供应商事件号。之后真正合法同号通知仍可入箱。业务拒绝不走暂态重试，重复错误通知不会靠“多试几次”变成合法付款。

## 如果只依赖MQ的确认会怎样

Worker在T2提交后、ACK前中断，Broker可能再次投递原消息；因此ACK（消费完成确认）不等于数据库原子提交，也不能消除重复。试图先ACK再执行付款，则中断可能丢掉处理机会。项目使用延后ACK与MySQL幂等，让“消息可能多次、业务效果最多一次”由本地交易事实保证，不宣称端到端exactly-once。

只做Event唯一去重实现简单，但遗漏不同号的语义重复；加Redis分布式锁可以减少同时进入，却增加锁过期、持有者中断、旧持有者继续写入的问题，MySQL终态及约束仍不可省。当前数据库已是事实来源，选择短事务行锁更容易把状态与库存一起提交。

## 重做会提前写清哪些冲突

我会在实现前列出“同号同文、同号异文、不同号同成功、成功后失败、失败后成功、关闭后成功”的行为表，并说明哪些是正常重放、哪些要拒绝、哪些要模拟退款。这样能避免把所有重复都写成一个return，也能暴露迟到成功分支与终态顺序的真实取舍。若未来接真实供应商，对已FAILED后成功等冲突必须按对方资金语义重新审查，不能照搬Mock策略。

[5C1真实MQ记录](../../verification/phase-5c1-payment-mq.md)验证同Event三消息、伪造后合法通知；[5C4硬中断记录](../../verification/phase-5c4-payment-worker-recovery.md)验证业务提交后ACK前重投保持核销/退款一次；[早期MVP记录](../../verification/mvp-4-mock-payment.md)的不同号重复与乱序是SQLite烟测。证据分别使用，当前文档核对不补称多Worker并发或真实供应商语义。
