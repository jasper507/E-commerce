# Phase 6B：秒杀资格随支付和订单关闭一起转换

日期：2026-10-07。Depth / Learning Mode。
本片接上 Phase 6A 的服务层下单，使活动资格与订单、支付、库存保持一致。
实现与测试源码已静态核对，生命周期目标测试及相关回归已实际通过。
本片没有秒杀 HTTP 创建端点或前端入口，没有新增 RabbitMQ/Worker 验证。

## 本片前后变化

- **开始前：** 6A 只能建单并占住活动资格；支付、取消和到期入口暂时被保护，避免订单或库存变化后资格仍停在 HELD。
- **本片改变：** 把正常支付消费资格、真正未付关闭释放资格接入原事务；旧单迟到成功只处理原单退款，不能动新单资格。
- **完成后与边界：** 本片形成服务层的下单、支付和关闭闭环；付后取消仍保留已消费资格，未确认付款不能释放。HTTP、页面及完整库存策略对照当时仍未完成。

**后续衔接：** [6C-1 验证两位顾客争最后一件](phase-6c1-flash-sale-contention-study-guide.md)。[学习导航](README.md)

## 1. 业务问题：库存已经变化，资格不能停在旧状态

[Phase 6A](phase-6a-flash-sale-checkout-study-guide.md) 已经能创建一张数量为 1 的活动订单，
用 HELD/1 购买记录占住名额。该阶段曾保护支付、取消和到期入口，因为资格转换尚未接入。
这是阶段历史；本片将转换接入原服务，并移除这些临时保护。

假设活动价 89.00、普通 SKU 价 129.00，库存为 `available=5, reserved=0`。
创建 O1 后，可售库存减 1、预占库存加 1，即 `4/1`，资格为 HELD/1。
接下来必须回答三个不同的问题：

- 正常付款成功：预占被核销，这次正常购买消耗活动资格。
- 未付订单真正取消或到期关闭：预占被释放，活动资格也释放。
- 正常付款后取消：库存恢复并模拟退款，但已经消耗的活动资格保持不变。

这片加强的是“库存并发与秒杀正确性”故事，也复用“支付状态与幂等”故事。
重点是几类事实一起正确提交，不是增加新的秒杀基础设施。

## 2. 不变量：哪些事实必须始终一致

Invariant（不变量）就是无论请求顺序如何变化，都必须守住的业务事实。
本项目已确认的规则是同一顾客、同一活动商品只能有一次正常成功购买，每张活动订单固定一件。

| 触发条件 | Order | Purchase 状态/标记 | Reservation | 库存及副作用 |
| --- | --- | --- | --- | --- |
| 创建 O1 | PENDING_PAYMENT | HELD / 1 | RESERVED | `5/0 → 4/1`，RESERVE 一次 |
| O1 正常支付成功 | PAID | CONSUMED / 1 | COMMITTED | `4/1 → 4/0`，COMMIT 一次 |
| O1 未付且真正关闭 | CANCELED 或 EXPIRED | RELEASED / NULL | RELEASED | `4/1 → 5/0`，RELEASE 一次 |
| O1 正常支付后取消 | CANCELED | 仍 CONSUMED / 1 | RESTORED | `4/0 → 5/0`，RESTORE 和模拟退款各一次 |
| O1 有未确认支付结果 | 仍 PENDING_PAYMENT | 仍 HELD / 1 | 仍 RESERVED | 保持 `4/1`，拒绝关闭 |

Purchase 是 [FlashSalePurchase](../../backend/apps/promotions/models.py)，每张活动订单各有一条。
`blocking_marker` 是让数据库识别“仍阻挡新购买”的标记，不是数量或支付次数。
HELD 和 CONSUMED 都使用 1，RELEASED 使用 NULL。

`UNIQUE(customer, flash_sale_item, blocking_marker)` 使同一顾客和活动商品只能有一条标记 1。
MySQL 允许多条 NULL 历史；状态 CHECK（数据库检查约束）要求状态与标记严格对应。
数据库 CHECK 不会比较另一张表的订单状态，因此跨表一致性还需要服务检查和事务。

“AVAILABLE”只是理解资格可用的概念，不是本项目 Purchase 的已实现状态。
初次购买时可以没有记录；旧记录 RELEASED/NULL 且没有别的阻挡记录时，也可以重新申请。
重新购买仍须满足活动有效、地址归属、商品可售、库存足够等条件。

正常支付后 CONSUMED/1 会继续阻挡新 Key；付款后取消不会把它恢复为 HELD 或 RELEASED。
这不是“顾客一生只能生成一张订单”：未付合法关闭可以保留旧订单，再用新 Key 创建新订单。

## 3. 执行流程：在原订单上处理自己的购买记录

### 正常支付

PaymentAttempt（支付尝试）表示一次具体付款；PaymentEvent/Inbox（支付事件收件箱）保存可信结果，
便于重复投递、失败调查和恢复。前端显示成功不会直接决定订单成功。

```text
既有支付入口 → start_payment()
→ create_payment_attempt()：短事务锁 User、Order、已有 Attempt
→ 核对该订单的 HELD/1 Purchase，再创建或重放 Attempt
→ 提交 Attempt；在事务外调用 MockGateway
→ receive_payment_event()：独立持久化原始事件
→ process_stored_payment_event()：领取处理次数，再进入业务事务
→ process_payment_event()：验签、核对原订单/引用/金额/币种
→ 正常 SUCCEEDED 分支锁该 Order 自己的 HELD/1 Purchase
→ _commit_inventory()：Reservation 核销，reserved 减 1，写 COMMIT
→ Attempt=SUCCEEDED，Order=PAID
→ transition_flash_sale_purchase()：HELD/1 → CONSUMED/1
→ Event=PROCESSED，与上述业务事实一起提交
```

源码入口是 [payments/services.py](../../backend/apps/payments/services.py)。
`start_payment()` 先提交 Attempt，再调用网关，避免持有数据库行锁等待网络。
`create_payment_attempt()` 对新 Attempt 验证 HELD；同支付 Key 重放仍返回原 Attempt。
`process_payment_event()` 处理正常成功时，资格核对发生在库存锁之前。

核销只减 `reserved`，不再减 `available`：那件商品在下单预占时已从可售库存中移出。
相同 Event 重放直接返回；同一成功结果的另一 Event 也不再次核销。
活动正常购买成功必须看这一组已提交事实，不能只看 Attempt 的单个状态。

### 真正未付关闭

```text
cancel_order() 或 expire_pending_order()
→ 在原子事务中锁 Order，重查其状态
→ _close_pending_order_locked() 锁 Attempt
→ 若有 CREATED/PENDING/UNKNOWN：payment_unresolved，拒绝关闭
→ 若允许关闭：锁本订单 HELD/1 Purchase
→ 锁 Reservation、Inventory，释放预占，写 RELEASE
→ Order=CANCELED 或 EXPIRED
→ Purchase=RELEASED/NULL
→ 一起提交
```

[ordering/lifecycle_services.py](../../backend/apps/ordering/lifecycle_services.py) 的取消与到期共用
`_close_pending_order_locked()`。`expire_due_orders()` 扫描普通和活动的到期订单，每单单独事务；
未确认支付的订单计入跳过结果，等待原付款查单。

UNKNOWN（结果未知）表示本地还不知道供应商是否收款；CREATED/PENDING 也尚未确认结束。
预占时间已经到期，不能覆盖这个资金风险。时间戳到期本身也不会修改订单或资格，必须真正执行关闭事务。
可信 FAILED 只结束一次 Attempt，不会自动关闭 Order 或释放 HELD，随后仍要合法关闭订单。

### 正常支付后取消

`cancel_order()` 对 PAID 订单调用 `_cancel_paid_order_locked()`。
它保留既有“未发货才可取消”的限制，锁成功 Attempt，验证该订单 Purchase 已为 CONSUMED/1。
随后恢复可售库存、写 RESTORE、经 `simulate_refund()` 创建模拟退款，最后关闭订单。
这里没有资格释放转换；库存可以恢复，活动成功购买次数仍然已经用掉。

模拟退款复用 [refund_services.py](../../backend/apps/payments/refund_services.py) 与 Mock Gateway，
不代表真实资金退款或第三方供应商接入。

## 4. 核心源码：小 helper 为什么要校验并抛异常

[promotions/lifecycle_services.py](../../backend/apps/promotions/lifecycle_services.py) 只负责两件事：

1. `lock_flash_sale_purchase(order, expected_status=...)`：普通订单返回 None；活动订单必须在事务中，
   按 `order=order` 锁自己的 Purchase，检查顾客与 Order.user 一致、状态与标记符合预期。
2. `transition_flash_sale_purchase(purchase, target_status=...)`：只允许 HELD/1 转为 CONSUMED/1
   或 RELEASED/NULL，并保存状态和标记。

调用者负责先取得 Order 行锁。helper 检查 `in_atomic_block`，不能据此自动证明调用者已经锁了 Order。
它不按顾客和活动查询“最新购买”，也不在锁住 Purchase 后再去锁另一张历史订单。

```python
purchase = lock_flash_sale_purchase(order, expected_status=FlashSalePurchase.Status.HELD)
_commit_inventory(order)
# 在同一业务事务内保存 Attempt=SUCCEEDED 和 Order=PAID。
transition_flash_sale_purchase(purchase, target_status=FlashSalePurchase.Status.CONSUMED)
```

这段摘录省略了中间保存语句；完整正常成功分支以 `process_payment_event()` 为准。
缺记录、顾客不符或状态/标记冲突会抛 `FlashSalePurchaseInvariantError`，不会临时补记录后继续核销。
否则数据损坏可能被伪装成一次正常购买。

## 5. 事务边界：业务回滚，调查记录保留

Transaction（事务）把一组数据库改动作为一个整体提交；中途失败就撤销本次尚未提交的业务改动。
资格不能在另一个事务中提前释放，也不能等支付提交后再通过 `on_commit` 补写。
否则会出现 Order 已 PAID 但 Purchase 仍 HELD，或库存还占着但新单已得到资格的窗口。

支付处理复用已有的三个阶段：

| 阶段 | 真正保存什么 | 后续业务失败时 |
| --- | --- | --- |
| 收件事务 | 原始 Inbox 事件 | 保留，避免丢掉供应商事实 |
| T1：`_claim_event_processing()` | 领取状态、处理次数、租约 | 保留，失败不会重置重试预算 |
| T2：`_process_claimed_payment_event()` | Order、Attempt、Purchase、库存、预占、流水及事件处理终态 | 本阶段一起回滚 |
| T3：`_record_event_processing_failure()` | ERROR、失败分类和下次重试时间 | 另事务保存，供调查或重试 |

例如，测试先把 Purchase 真正写成 CONSUMED，再注入 `OperationalError(1205)`。
T2 失败后应仍是 Order=PENDING_PAYMENT、Attempt=CREATED、Purchase=HELD/1、库存 `4/1`，无 COMMIT。
已经提交的 Inbox 和 T1 次数不消失；T3 保存 ERROR/TRANSIENT，按既有等待时间与次数预算允许重试。
该测试是受控故障注入，不能当成实际观察到一次 MySQL 锁超时。

`FlashSalePurchaseInvariantError` 则分类为 BLOCKED（阻止自动重试，等待调查），
错误码是 `flash_sale_purchase_invariant_error`，没有自动重试时间。
不能把资格冲突当作短暂数据库故障无限重试。
关闭流程也在一个事务中：即使已经执行 RELEASED 保存，之后抛异常也应撤销库存、订单和资格改动。

上述错误保留沿用原恢复框架；硬中断或错误留存自身失败仍可能留下 PROCESSING，按既有租约恢复规则处理。
所以“整体回滚”特指本次业务事务，不表示整个 Inbox 或之前已经提交的交易历史全部消失。

## 6. O1/O2：释放资格和请求重放是两件事

Idempotency（幂等）让同一次请求的重试返回原事实；它不会把旧请求改造成一次新购买。
订单幂等键为 `(user, creation_scope, idempotency_key)`，普通购物车和活动下单各自隔离。
活动服务先核对旧 Key 及请求参数，再判断活动资格，因此旧订单关闭后仍能重放。

下面使用与测试一致的库存 `5/0`：

| 步骤 | O1 与自己的 Purchase | O2 与自己的 Purchase | 共享库存 |
| --- | --- | --- | --- |
| K1 创建 O1 | PENDING_PAYMENT、HELD/1 | 尚不存在 | `4/1` |
| 原付款收到可信 FAILED，再合法关闭 O1 | CANCELED 或 EXPIRED、RELEASED/NULL | 尚不存在 | `5/0` |
| K2 创建 O2 | 保持已关闭、RELEASED/NULL | PENDING_PAYMENT、HELD/1 | `4/1` |
| K1 同参数重放 | 返回原 O1，created=False | 保持不变 | `4/1` |
| 原 O1 可信迟到 SUCCEEDED | Attempt=SUCCEEDED；Order 仍关闭；Purchase 仍 RELEASED/NULL；模拟退款一次 | 保持待付、HELD/1 | `4/1` |

`process_payment_event()` 在正常消费资格分支之前，先处理“订单已关闭且结果成功”。
它更新原 O1 的 Attempt，创建 LATE_SUCCESS 模拟退款，Event=PROCESSED 并记录
`late_success_refund_simulated`；不核销库存，也不转换 Purchase。

此时 Attempt=SUCCEEDED 说明供应商的资金侧成功已被承认，但已关闭 O1 没有变成正常活动购买成功。
O2 的资格来自自己的 Purchase；迟到处理不按顾客/活动找当前占位，所以不触碰 O2。
重复原事件或另一个事件编号的相同迟到成功，仍只复用原 Attempt 的一次模拟退款。

测试夹具先通过真实 `start_payment()` 和可信 FAILED 事件确认 O1 未付，随后合法关闭。
需要迟到成功时，测试受控修改原 `MockGatewayPayment` 的结果，再通过 `query_payment()` 取得
与原引用、金额和签名一致的 SUCCEEDED。它是在构造异常供应商时间线，不是声称供应商正常会这样变化。
不能强行取消 CREATED/UNKNOWN 后声称释放正确，也不能让 Mock 查单仍为 FAILED 却随手伪造成功结果。

## 7. 取锁顺序与方案代价

Row Lock（行锁）让同一条数据的并发修改排队；锁顺序一致可以减少互相等待的风险。
原代码中的主要顺序是：

```text
正常事件处理：Event → Order → Attempt → Purchase → Reservation → Inventory
未付关闭：            Order → Attempt → Purchase → Reservation → Inventory
付后取消：            Order → 成功 Attempt → Purchase → Reservation → Inventory
创建活动订单：User → 自己的 Address → 新 Order/快照 → HELD Purchase → Inventory NOWAIT
创建新付款：User → Order → 已有 Attempt → HELD Purchase → 新 Attempt
```

关闭流程不额外锁 User。新单先占购买记录唯一键，再锁库存，避免创建和释放形成
“一方拿着库存等购买记录，另一方拿着购买记录等库存”的反向路径。
`NOWAIT` 的意思是这次库存锁冲突立即失败；User 锁、唯一索引和外键仍可能等待。
这份顺序不能证明所有索引/外键路径都无死锁，也不保证新购买会等待旧订单关闭后自动成功。
若新请求检查时旧 HELD 尚未释放，它可以得到限购拒绝；调用者之后可重新尝试新购买请求。

有意义的替代是“每顾客/活动一条永久资格记录，保存当前订单指针”。
它读取当前状态更直接，但重购会切换指针，旧通知必须额外核对订单归属，也容易引入
资格锁之后再锁历史 Order 的反向顺序。
当前每单一条 Purchase 保留历史、方便按原订单锁定；代价是多一条记录及一次锁查询，
还要同时维护状态/标记约束和所有正常生命周期入口。

## 8. 测试如何判断，证据能说明什么

[生命周期测试源码](../../backend/apps/promotions/tests/test_flash_sale_lifecycle.py) 覆盖以下
Test Oracle（判断结果是否正确的标准），这些预期已在本轮目标测试中通过：

- 正常成功及两种事件重放：PAID、CONSUMED/1、COMMIT 仅一次，新 Key 被限购。
- 付后取消：RESTORE 与模拟退款仅一次，仍 CONSUMED/1，新 Key 仍被限购。
- 未付取消、到期扫描及重放：RELEASE 一次、RELEASED/NULL，旧 Key 返回原单，新 Key 可以新建。
- CREATED/PENDING/UNKNOWN：取消和单单到期均拒绝，扫描跳过，库存与 HELD 均不变。
- 取消或到期后重购，再收旧单成功及重放：原单只退款一次，O2 的完整快照不变。
- 资格已经实际写入后注入支付或关闭错误：业务回滚；可重试支付等到门槛后只核销一次，关闭重试只释放一次。
- 购买记录与交易状态冲突：支付 ERROR/BLOCKED，关闭也不修改库存或资格，不自动重试。
- 两条真实连接同时处理支付与到期：最终 PAID、CONSUMED/1、COMMIT 一次，无 RELEASE。

最后一项用 Barrier（让两条线程从同一个起跑点继续）释放并发操作。
已有 CREATED Attempt 会阻止到期方关闭；若支付先完成，到期方则看到已支付而不修改。
它验证两条连接及终值，未观察 MySQL 锁等待，未控制谁先取得 Order 锁，
不能声称一次运行已经覆盖两种拿锁顺序或支付/关闭所有交错。

2026-10-07 的隔离 MySQL + 后端 Mock 目标结果为 `11 passed, 13 subtests passed in 23.26s`，
见 [本轮原始目标输出](../verification/phase-6b-flash-sale-lifecycle-target-output.txt)。
13 是参数子场景统计，不是另外 13 个独立测试方法，也不能直接称为断言总数；23.26s 是测试耗时，
不是秒杀响应时延或吞吐量。

原输出记录两条不同连接：到期连接 571 返回 `payment_unresolved`，支付连接 572 最终处理成功。
Order=PAID、Purchase=CONSUMED/1、库存 `4/0`、COMMIT=1、RELEASE=0。
取消和到期两种 O1→O2 场景均保持旧 Purchase=RELEASED/NULL、旧单退款一次，
新 O2=HELD/1 且共享库存 `4/1` 不受迟到事件影响。
资格实际写入后异常回滚及受控重试、交易状态冲突 BLOCKED 也已通过。
这些是本轮原输出与测试断言支持的事实，不补写未观测到的锁等待或第二种拿锁顺序。

promotions、ordering、payments 关联回归实际结果为
`171 passed, 6 skipped, 12 deselected, 163 subtests passed in 149.47s`，包括既有普通订单/支付行为和
[Phase 6A 测试](../../backend/apps/promotions/tests/test_flash_sale_checkout.py)，见
[回归原始输出](../verification/phase-6b-flash-sale-lifecycle-regression-output.txt)。
6 个跳过项属于 5D 显式锁观察实验，12 个排除项属于真实 MQ 集成；本轮未重新验证真实 MQ、Redis 或 5D 实验。
163 是参数子场景统计，不重复计作独立测试；149.47s 是测试耗时。
运行条件、完整命令、基点、失败修正与回归输出见
[本轮验证记录](../verification/phase-6b-flash-sale-lifecycle.md)，本页只讲规则与证据边界。
历史下单基线的证据在 [Phase 6A 验证记录](../verification/phase-6a-flash-sale-checkout.md)，不能代替本片验证。

上述结果支持隔离 MySQL + 后端 Mock 下这些服务规则和事务终值。
它们不证明秒杀 HTTP/前端验收、真实资金退款、生产吞吐量、所有并发交错或新增 MQ 故障恢复。
本片复用已有支付处理器；RabbitMQ 实际投递、Worker 中断等证据仍应查既有支付阶段记录。

## 9. 用自己的话回答

先尝试回答，再对照源码，不需要闭卷背下整份实现：

1. 为什么正常支付只减 reserved，而未付关闭要 available 加 1、reserved 减 1？
2. 为什么付后取消恢复库存，却不恢复活动资格？这是谁确认的业务规则？
3. 如果 Purchase 保存失败，哪些业务事实必须回滚，哪些 Inbox 记录仍保留？
4. 为什么旧 K1 应返回已关闭 O1，而不是创建 O2？为什么新 K2 仍要检查限购？
5. 为什么迟到 O1 的 Attempt=SUCCEEDED 不能直接当成活动购买成功？怎样保护 O2？
6. 行锁、唯一约束和状态 CHECK 分别解决什么问题，又分别不能证明什么？

预测题：O1 的预占已到期，但 Attempt=UNKNOWN。顾客点取消并立刻用新 K2 购买。
请预测 O1、Purchase、库存和 K2 的结果，并说明为什么应先查询原付款，不能仅凭时间戳释放名额。
