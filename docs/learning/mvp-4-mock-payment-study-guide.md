# MVP-4 Mock Payment + Completion 学习指南

> 文档状态：MVP-4 源代码已经落地；本指南记录已确认规则、当前实现、学习重点和验证标准。进程内 SQLite 支付专项与全项目非并发回归已经运行，但真实 MySQL 目标测试尚未完成，因此不能把支付并发与数据库约束写成“已通过 MySQL 验证”。精确测试数量与限制见配套验证记录。

## 1. 先记住结论

MVP-3 只完成到“创建待支付订单并预留库存”：顾客已经表达了购买承诺，但系统还不知道钱是否付成功，预留商品也还没有变成真正售出的商品。

MVP-4 当前代码实现的最小主链是：

```text
PENDING_PAYMENT Order + RESERVED InventoryReservation
→ 创建 PaymentAttempt
→ 数据库短事务先提交
→ 事务外调用 Mock Payment Gateway
→ Payment Event 先进入 Event Inbox
→ 校验签名、订单、金额、币种和状态
→ PaymentAttempt = SUCCEEDED
→ Order = PAID
→ Reservation = COMMITTED，reserved_stock 被核销
→ Admin 模拟发货：Order = SHIPPED
→ Customer 确认收货：Order = COMPLETED
```

这条主链在真实 MySQL 目标测试和浏览器验收完成后，才算第一次真正打通 Project One 的完整最小业务闭环：

```text
浏览 → 登录 → Cart → Address → Order/预占
→ Mock Payment/库存核销 → 模拟发货 → 确认收货
```

虽然支付是 Mock（模拟的），但下列工程问题都是真实的：

1. **State Machine（状态机）**：订单和支付尝试只能沿合法方向变化；
2. **Network Boundary（网络边界）**：数据库不能拿着锁等待外部 Gateway；
3. **Double Idempotency（双层幂等）**：创建支付尝试不能重复，支付事件也不能重复产生副作用；
4. **Inbox Pattern（收件箱模式）**：事件先可靠保存，再校验和处理；
5. **UNKNOWN（结果未知）**：网络超时不等于支付失败，必须查单收敛；
6. **Inventory Commit（库存核销）**：支付成功只能把同一份预留库存核销一次；
7. **Authorization（后端权限）**：Customer、Admin 各自只能执行合法动作。

本阶段属于 `Depth / Learning Mode`。Vue 页面和普通 Serializer 样板可以快速浏览，但状态机、事务边界、双层幂等、Inbox、UNKNOWN 和库存核销必须真正掌握。

## 2. 这一层实现了什么

### 2.1 支付领域事实

| 概念 | 本阶段作用 | 为什么不能省略 |
|---|---|---|
| `PaymentAttempt` | 保存一次主动支付尝试、状态、金额、币种、Provider 身份和幂等信息 | 一张 Order 可以先失败，再合法发起下一次尝试；支付失败不等于订单失败 |
| `PaymentEvent` / `Event Inbox` | 保存 Provider 事件原文、唯一事件号、校验与处理结果 | 重复、错误或暂时处理失败的事件不能被悄悄丢掉 |
| `MockGatewayPayment` | 一对一保存 Mock Provider 对某次 Attempt 的 `PENDING/SUCCEEDED/FAILED` 结果 | 它模拟“供应商侧自己的事实”，让创建支付和后续查单读取同一结果 |
| `Provider Adapter` | 统一“创建支付、查单、验签、解析事件”的边界 | Order 领域不能被某家 Provider 的字段和协议污染 |
| `Mock Payment Gateway` | 后端可控的支付供应商测试替身 | 不处理真钱，也能稳定制造成功、失败和 UNKNOWN 等可验证场景 |
| Inventory `COMMIT` | 将成功支付对应的 Reservation 从 `RESERVED` 改为 `COMMITTED` | 预留不等于售出，支付成功后必须留下最终库存事实和审计流水 |
| `Shipment` | 一对一保存由哪个 Admin 在什么时间模拟发货 | `SHIPPED` 不只是状态字符串，还要保留最小履约事实 |
| 确认收货转换 | Customer 把自己的 `SHIPPED` Order 改为 `COMPLETED` | 当前最小版只改变 Order 状态，不另建收货记录 |

核心版继续只用 `PaymentAttempt` 表达支付状态，不额外创建一张容易与它失配的 `Payment` 状态表。

### 2.2 协定 API

| Method | Path | 作用 |
|---|---|---|
| `POST` | `/api/payments/attempts/` | 为当前用户自己的待支付订单幂等创建 PaymentAttempt；需要 `Idempotency-Key` |
| `GET` | `/api/payments/attempts/?order_public_id=<uuid>` | 只列出当前用户该订单的支付尝试；页面刷新后用它恢复 Attempt 和 UNKNOWN 查单入口 |
| `GET` | `/api/payments/attempts/<uuid>/` | 只读取当前用户自己的某次 PaymentAttempt |
| `POST` | `/api/payments/attempts/<uuid>/reconcile/` | 对原 Attempt 查单并收敛 `CREATED/PENDING/UNKNOWN`，不能偷偷创建新 Attempt |
| `POST` | `/api/payments/mock/webhook/` | Mock Provider 使用模拟签名投递事件；不是 Customer 操作入口，也不代表真实资金 |
| `POST` | `/api/orders/<uuid>/confirm-receipt/` | 订单本人把 `SHIPPED` 订单确认成 `COMPLETED` |

`POST /api/payments/attempts/` 的业务输入只需要定位 Order，例如：

```json
{
  "order_public_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

同时携带：

```http
Idempotency-Key: payment-7f4e4be9-9969-42b4-8590-53e35b4de80c
```

所有 Customer API 都要求 Session 身份；Customer 的 POST 要求 CSRF。服务端从 Order 读取可信金额和币种，前端不能提交一个 `0.01` 元金额覆盖订单事实。

Mock Webhook 是 Provider-facing（面向支付供应商）的模拟入口，没有 Customer Session/CSRF，但并非公开的生产 Webhook：当前实现只在 `development/test` 且请求来源为 loopback（`127.0.0.1` 或 `::1`）时开放，请求体最大 16 KiB，并要求正确的 `X-Mock-Signature`。远端来源、非本地环境和超大请求在进入事件处理前就被拒绝。

签名校验先确认输入是 64 位 ASCII 字符串，再调用 `hmac.compare_digest()`。这样错误长度或非 ASCII 签名会被安全拒绝，不会让底层比较函数抛异常；同一个无效签名 payload 还会映射到确定性的 synthetic inbox ID，既保留审计事实，又不会抢占以后合法事件的真实 `event_id`。

### 2.3 新增页面入口和按钮位置

当前实现把按钮放在下列位置，避免把“正常顾客操作”和“故障注入工具”混在一起：

1. **主导航**
   - 新增【我的订单】入口；
   - 进入 `/orders`，只展示当前 Customer 自己的订单。

2. **`/orders` 我的订单列表**
   - 页面顶部提供【刷新订单】；
   - 每张订单卡片提供【查看详情】，进入 `/orders/:publicId`。

3. **`/checkout` 创建成功卡片**
   - 不直接塞入全部支付控制；
   - 只新增【查看订单】链接；
   - 链接进入 `/orders/:publicId`。

4. **`/orders/:publicId` 订单详情页**
   - `PENDING_PAYMENT` 且允许创建新 Attempt 时显示【发起模拟支付】；
   - 当前 Attempt 为 `CREATED/PENDING/UNKNOWN` 时显示【查询支付结果】；
   - 提供【刷新状态】，重新读取 Order 和该 Order 的 Attempts，不凭前端旧状态猜测结果；
   - Order 为 `SHIPPED` 时显示【确认收货】；
   - `PAID` 时只显示等待发货提示；
   - `COMPLETED` 时不再显示可改变状态的按钮。

5. **Django Admin 的 Order 列表页**
   - 在 Action 下拉框新增【模拟发货】；
   - 只能把合法的 `PAID` Order 转为 `SHIPPED`；
   - Admin Action 必须调用同一领域 Service，不能绕过状态机直接批量改字段。

普通 Customer 页面不提供“选择失败、伪造签名、乱序事件”等按钮。Mock 默认走成功；失败、UNKNOWN 和异常事件只通过自动测试或受保护的开发注入路径制造。

## 3. 相比 MVP-3 有什么真正改变

| 维度 | MVP-3 Order + Reservation | MVP-4 Payment + Completion |
|---|---|---|
| 业务位置 | 接受订单，库存暂时保留 | 确认支付事实，库存正式售出，并完成履约 |
| Order 状态 | 只产生 `PENDING_PAYMENT` | 合法推进 `PENDING_PAYMENT → PAID → SHIPPED → COMPLETED` |
| 库存 | `available → reserved` | 支付成功后 `reserved → committed/sold`，available 不再重复减少 |
| 外部依赖 | 全部在 MySQL 内 | 出现 Gateway 边界、超时和结果未知 |
| 幂等对象 | 创建 Order 请求 | 创建 Attempt 请求 + Provider Event 两层幂等 |
| 审计事实 | Order/Snapshot/Reservation/Movement | 再增加 Attempt、Event Inbox、Mock Provider 状态和 Shipment；确认收货暂只写 Order 状态 |
| 失败含义 | 下单整体回滚 | 支付失败只终结本次 Attempt，Order 仍可在有效期内再次支付 |
| 恢复能力 | 同 Key 重放原 Order | UNKNOWN 对原 Attempt 查单，页面刷新后能恢复状态 |
| 角色协作 | Customer 下单 | Customer 支付，Admin 发货，Customer 确认收货 |

### 3.1 业务能力上的提升

- 学会把“订单”“一次支付尝试”“支付供应商事件”分成三个不同业务事实；
- 理解支付失败不应把 Order 设计成一个含糊的 `PAYMENT_FAILED` 终态；
- 理解预留、核销、以后释放是库存生命周期的不同动作；
- 能处理“钱可能已经付了，但本地暂时不知道”的真实支付语义；
- 能建立 Customer 与 Admin 的履约责任边界。

### 3.2 工程能力上的提升

- 用状态机集中保护合法转换，而不是在多个 View 中随意写 `status = ...`；
- 把数据库事务与外部调用分开，缩短锁持有时间；
- 用 Adapter 隔离第三方协议；
- 用请求幂等和事件幂等分别控制两类重复；
- 用 Inbox 保留原始事件和处理证据；
- 用查单让 UNKNOWN 收敛，而不是把超时错误当成支付失败；
- 用数据库锁、Reservation 状态和唯一 Movement 保证最多核销一次。

## 4. 当前业务规则与 Test Oracle

Test Oracle 是“测试用什么预期判断实现正确”。MVP-4 的测试应先服从以下已经确认的规则。

### 4.1 默认成功例子

初始状态：

```text
Order = PENDING_PAYMENT，total = ¥258.00，currency = CNY
Inventory.available = 8
Inventory.reserved = 2
Reservation = RESERVED，quantity = 2
```

Customer 使用新 Idempotency-Key 发起 Mock 支付，默认 Mock 返回成功事件。正确结果是：

```text
PaymentAttempt = SUCCEEDED
Order = PAID
Inventory.available = 8       # 不再扣第二次
Inventory.reserved = 0
Reservation = COMMITTED
新增一条 COMMIT Movement：available_delta = 0，reserved_delta = -2
```

随后：

```text
Admin Action：PAID → SHIPPED
订单本人确认收货：SHIPPED → COMPLETED
```

### 4.2 Attempt 请求幂等规则

- 同一 Customer、创建 Attempt 端点、同 Key、同一规范化请求：返回原 Attempt；
- 同 Key 换成另一张 Order：返回 `409 Conflict`；
- 同 Key 重放不能第二次调用出新的业务副作用；
- 同一 Order 同时最多一个 `CREATED/PENDING/UNKNOWN` 活跃 Attempt；
- 已有 Attempt `SUCCEEDED` 后禁止再创建；
- 只有前一个 Attempt 明确 `FAILED/EXPIRED/CANCELED`，且 Order 仍是 `PENDING_PAYMENT`，才允许使用新 Key；
- 主动支付尝试最多 3 次，并由配置控制。

### 4.3 Event 幂等规则

- `(provider, event_id)` 唯一；同一事件重放不能再次处理；
- 原始 payload 必须保留，即使签名或字段错误；
- 成功事件必须校验签名、Order、Attempt、金额、币种和合法状态；
- 重复成功事件不能第二次核销 reserved、第二次创建 COMMIT Movement 或再次改变 Order；
- 即使 Provider 错误地用不同 event_id 重复发送相同“成功”语义，Attempt/Order/Reservation 状态和数据库唯一约束仍应形成第二道防线。

### 4.4 UNKNOWN 规则

- 网络超时只说明本地不知道结果，不等于 Provider 已失败；
- `CREATED/PENDING/UNKNOWN` 都属于活跃 Attempt，不能用新 Key再建一个；
- 页面刷新后使用 `GET /api/payments/attempts/?order_public_id=<uuid>` 找回原 Attempt；
- 【查询支付结果】调用 `POST /api/payments/attempts/<uuid>/reconcile/`，复用原 Attempt；
- 查单结果必须再经过可信校验和状态机，不能由前端直接写成成功。

### 4.5 失败和非法操作规则

- 支付失败：Attempt 可以变为 `FAILED`，Order 仍保持 `PENDING_PAYMENT`；
- 支付失败或 UNKNOWN：本 MVP 不释放库存，Reservation 仍保持 `RESERVED`；
- 错误签名、金额、币种或非法状态事件：Inbox 保留事件，但 Order、Attempt 的确认状态和库存不能被错误改变；
- Customer 不能发货；Admin 不能把非 `PAID` Order 发货；
- Customer 只能确认自己的 `SHIPPED` Order；他人订单统一不可见；
- `PAID` 不能由 Customer 直接确认完成，`COMPLETED` 不能回退；
- 重复确认已经完成的 Order 不得产生新的业务副作用。
## 5. 完整端到端流程

### 5.1 创建 Attempt 与调用 Gateway

```text
OrderDetailView
→ POST /api/payments/attempts/
   body: {order_public_id}
   headers: Session Cookie + CSRF + Idempotency-Key
→ DRF Authentication / Permission / Serializer
→ Payment Service 开启短 transaction.atomic()
→ 锁当前 User 行（串行化同一顾客创建 Attempt 和复用 Key）
→ 查同 Customer + 端点 + Key
   ├─ 同请求：返回原 Attempt
   └─ 异请求：409
→ 锁属于当前 Customer 的 Order
→ 判断 Order 是否仍为 PENDING_PAYMENT
→ 检查成功 Attempt、活跃 Attempt 和最多三次规则
→ 创建 CREATED PaymentAttempt
→ COMMIT 短事务
→ 事务外调用 Provider Adapter / Mock Gateway
→ MockGatewayPayment 保存 Mock Provider 侧状态
→ Mock 默认生成成功 Payment Event
→ 事件进入 Inbox
```

关键边界是：**先提交 Attempt，再调用 Gateway**。如果把 Gateway 放进事务，慢网络会让 Order/Inventory 锁一直被占用；如果网络结果丢失，则应使用 UNKNOWN 和 reconcile 处理，而不是靠超长事务假装网络可靠。

诚实边界：当前 `MockGatewayPayment` 和核心业务表都保存在同一个 MySQL 服务中，`MockGateway` 也不是独立外部网络服务。代码刻意让 Adapter 在 Attempt 事务提交后才调用，SQLite 流程测试也检查了这个调用边界；这仍不等于真实第三方网络、跨系统故障或生产支付已获验证。

### 5.2 Event Inbox 与支付成功事务

```text
接收 Provider Event
→ receive_payment_event() 用一个短事务
   以 provider + event_id 幂等写入 Inbox，并保留原始 payload
→ 短事务提交
→ process_payment_event() 开启第二个 transaction.atomic()
→ 锁 PaymentEvent，并检查是否已经 PROCESSED/REJECTED
→ 在处理事务内校验模拟签名并解析 Provider 状态
→ 定位 Attempt/Order，校验金额、币种和引用
→ 锁 Order → PaymentAttempt
→ 按固定顺序锁对应 Reservation → Inventory
→ 再次检查合法状态与是否已处理
→ PaymentAttempt = SUCCEEDED
→ Order = PAID
→ 每条 Reservation：RESERVED → COMMITTED
→ Inventory.reserved_stock -= quantity
→ 写 COMMIT InventoryMovement
→ Event 标记为已处理
→ COMMIT
```

这里 `available_stock` 不再减少，因为它已经在 MVP-3 下单时减少过。支付成功核销的是 `reserved_stock`。

### 5.3 UNKNOWN 查单流程

```text
创建支付后本地只得到 CREATED/PENDING/UNKNOWN
→ 用户刷新订单详情
→ GET attempts/?order_public_id=<uuid>
→ 找到原活跃 Attempt
→ 点击【查询支付结果】
→ POST attempts/<attempt_public_id>/reconcile/
→ 在数据库事务之外调用 Adapter.query_payment()
→ 将查询结果转换为可信事件/统一结果
→ 复用同一事件处理 Service
→ 收敛为 PENDING / SUCCEEDED / FAILED，或继续 UNKNOWN
```

查单不是“重新支付”。它不能创建第二个 PaymentAttempt。当前 Mock 中，如果 Attempt 已提交、但进程在首次 Provider 创建前崩溃，`query_payment()` 会为**同一个 Attempt**补建缺失的 MockGatewayPayment，再返回该 Provider 状态；这属于恢复同一次尝试，不是新建支付尝试。

### 5.4 模拟发货与确认收货

```text
Admin 打开 Django Admin → Order 列表
→ 选择 PAID Order
→ Action 下拉选择【模拟发货】
→ 后端权限 + 状态机校验
→ ship_order() 创建 Shipment，并把 PAID → SHIPPED

Customer 打开 /orders/:publicId
→ SHIPPED 时显示【确认收货】
→ POST /api/orders/<uuid>/confirm-receipt/
→ 校验订单所有权与当前状态
→ confirm_receipt() 将 SHIPPED → COMPLETED
→ 当前最小版不另建“收货记录”
```

前端隐藏按钮只改善体验，真正权限和状态检查必须在 Django 后端执行。

## 6. 两套状态机不能混在一起

### 6.1 Order 状态机

| 当前状态 | 本 MVP 使用的合法下一状态 | 谁/什么触发 |
|---|---|---|
| `PENDING_PAYMENT` | `PAID` | 已验证的成功 Payment Event |
| `PAID` | `SHIPPED` | Admin 模拟发货 |
| `SHIPPED` | `COMPLETED` | Order 所属 Customer 确认收货 |
| `COMPLETED` | 无 | 终态，不得回退 |

`CANCELED/EXPIRED` 已在总状态机中确认，但其触发、库存释放和迟到支付不属于本次最小闭环。

### 6.2 PaymentAttempt 状态机

| 当前状态 | 允许进入 |
|---|---|
| `CREATED` | `PENDING/UNKNOWN/SUCCEEDED/FAILED/EXPIRED/CANCELED` |
| `PENDING` | `UNKNOWN/SUCCEEDED/FAILED/EXPIRED/CANCELED` |
| `UNKNOWN` | `PENDING/SUCCEEDED/FAILED/EXPIRED/CANCELED` |
| `SUCCEEDED/FAILED/EXPIRED/CANCELED` | 无正常转换 |

为什么不能只用 Order 状态？

```text
Order 仍然想买这批商品：PENDING_PAYMENT
第一次 Attempt：FAILED
第二次 Attempt：SUCCEEDED
Order 最终：PAID
```

如果给 Order 增加 `PAYMENT_FAILED`，就会把“一次支付渠道尝试失败”和“整张订单已经结束”错误地混成一件事。

## 7. 双层幂等与三道防线

### 7.1 第一层：请求幂等

对象：Customer 主动创建 PaymentAttempt 的 HTTP 请求。

```text
同 Key + 同 Order → 返回原 Attempt
同 Key + 不同 Order → 409
```

它解决用户双击、浏览器重试、反向代理重试以及“服务端已经创建，响应却丢失”的问题。

### 7.2 第二层：事件幂等

对象：Provider 发来的 Payment Event。

```text
UNIQUE(provider, event_id)
```

它解决 Provider 因为没有收到确认而重复发送同一事件的问题。事件重复不等于创建 Attempt 请求重复，所以两层幂等不能互相替代。

### 7.3 库存核销的额外防线

即使出现新的 event_id 表达同一个成功事实，也要依靠：

- Attempt 已是终态 `SUCCEEDED`；
- Order 已是 `PAID` 或更后状态；
- Reservation 已是 `COMMITTED`；
- 同一 Reservation 的 `COMMIT` Movement 唯一；
- Inventory 和业务事实的数据库约束；

阻止第二次核销。这是 Defense in Depth（纵深防御）：不是把正确性押在单个 if 上。

## 8. 为什么需要 Inbox

错误做法：收到 Event 后先验签或处理，失败就直接返回并丢掉原文。这样以后无法回答：Provider 到底发过什么？为什么没成功？同一事件来过几次？

Inbox 的顺序是：

```text
Receive
→ Persist raw event
→ Verify
→ Parse
→ Process
→ Record result/error
```

无效签名事件仍要留审计证据，但不能抢占以后合法事件的真实 `event_id`。当前实现对“payload + 截断后的签名”计算确定性摘要，生成 `invalid-...` synthetic inbox ID：相同无效事件重复提交只保存一份，同时不会阻塞随后到达、携带合法签名的真实事件。

同步 baseline 并不等于可以省掉 Inbox。MVP-4 暂时不用 Celery/RabbitMQ，但仍保留事件事实；以后如果出现真实异步需求，异步 Worker 也应消费已保存的 Inbox，而不是把原始 HTTP 请求当作唯一证据。

## 9. UNKNOWN 为什么是面试重点

假设顾客点击支付：

```text
本地 → Provider：创建支付
Provider：支付成功并保存
Provider → 本地：响应途中超时
```

本地只知道“没收到响应”，不知道“Provider 没成功”。如果直接把它标成 `FAILED` 并允许新支付，可能出现重复付款。

正确思路：

1. 保留原 Attempt；
2. 标为或维持 `UNKNOWN`；
3. 禁止创建新活跃 Attempt；
4. 使用原 Provider 标识查单；
5. 把查单结果重新送入统一校验/状态处理；
6. 收敛成功、失败，或继续未知。

这也是为什么页面刷新后必须通过 `GET /api/payments/attempts/?order_public_id=<uuid>` 恢复原 Attempt，而不能丢掉状态后重新生成 Key 支付。

## 10. 核心代码阅读顺序

当前实现已经具备明确文件名。按“测试 Oracle → 核心 Service → 模型约束 → HTTP/页面”的顺序读；测试文件存在不等于已获得真实 MySQL 证据。

### 第一层：一定要精读

1. `backend/apps/payments/tests/` 中成功、幂等、重复事件、错误事件和 UNKNOWN/reconcile 测试；
2. `backend/apps/payments/services.py` 中按顺序精读 `create_payment_attempt()`、`start_payment()`、`receive_payment_event()`、`process_payment_event()`、`reconcile_payment_attempt()`；
3. `backend/apps/payments/models.py` 中 PaymentAttempt、PaymentEvent/Inbox 与 MockGatewayPayment；
4. `backend/apps/payments/gateway.py` 中 Provider Adapter 边界和 `MockGateway`；
5. `backend/apps/inventory/models.py` 及核销 Service 中 Reservation/COMMIT Movement 逻辑；
6. `backend/apps/ordering/models.py` 的 Shipment，以及 `backend/apps/ordering/fulfillment_services.py` 中 `ship_order()` 和 `confirm_receipt()`。

### 第二层：理解调用链，不必逐行背

1. `backend/apps/payments/views.py`、Serializers 和 URLs；
2. `backend/apps/ordering/views.py` 中确认收货入口；
3. `backend/apps/ordering/admin.py` 中【模拟发货】Action；
4. 配置中的 Mock Gateway、签名密钥和最大 Attempt 次数边界。

### 第三层：快速看前端边界

1. `frontend/src/api/payments.ts` 与订单 API：CSRF、Idempotency-Key、GET 恢复与 reconcile；
2. `frontend/src/views/OrdersView.vue`：我的订单列表；
3. `frontend/src/views/OrderDetailView.vue`：按 Order/Attempt 状态显示按钮；
4. `frontend/src/views/CheckoutView.vue`：成功卡片的【查看订单】链接；
5. Router、TypeScript 类型与 CSS 只需理解接口，不用背样板。

## 11. 只看四段核心逻辑时看什么

### 核心一：Attempt 先提交，Gateway 后调用

找到短事务结束的位置和 Adapter 调用的位置。能够指出：为什么两者不能放进同一个长事务，以及中间崩溃会留下什么状态。

### 核心二：Event 先入箱，再处理

找到 `(provider, event_id)` 幂等落库、原始 payload 保存、签名/金额/币种校验和处理结果记录的位置。

### 核心三：成功事件原子核销库存

找到锁 Event/Order/Attempt/Reservation/Inventory、`reserved_stock` 减少、Reservation 变为 `COMMITTED`、写 COMMIT Movement、Order 变为 `PAID` 和 Event 标记完成的同一处理事务。

### 核心四：UNKNOWN 复用原 Attempt

找到 reconcile 如何查询原 Provider 事实，并复用同一事件处理逻辑；确认它没有创建新 Attempt、没有绕过校验。

## 12. 理想的分轮学习顺序

### Round 1：不看代码，只讲业务事实

用“¥258、2 件商品、available=8、reserved=2”的例子，讲清支付成功、失败、UNKNOWN、发货、确认收货分别应该改变什么。

目标：先建立业务判断，不被 Django 类名带跑。

### Round 2：只看测试名和断言

自己写下五个 Oracle：默认成功、同 Key 重放、重复 Event、错误金额、UNKNOWN 后查单。先预测数据库状态，再看实现。

目标：把业务规则变成可执行证据。

### Round 3：画两张状态机

分别画 Order 和 PaymentAttempt，解释为什么 Attempt FAILED 时 Order 仍是 PENDING_PAYMENT。

目标：训练状态建模和非法转换意识。

### Round 4：画表关系和双层幂等

```text
Order 1 ── N PaymentAttempt
PaymentAttempt 1 ── N PaymentEvent
PaymentEvent N ── 0..1 PaymentAttempt（先入箱，验证解析后才关联）
PaymentAttempt 1 ── 0..1 MockGatewayPayment
Order 1 ── N Reservation ── N Movement
Order 1 ── 0..1 Shipment
```

在图上标出请求 Key、Provider event_id、Reservation COMMIT 唯一性。

目标：理解业务身份和数据库最后防线。

### Round 5：精读事务与网络边界

把支付链拆成三段：

1. 创建 Attempt 的短事务；
2. 事务外 Gateway 调用；
3. Event 处理和库存核销事务。

目标：理解为什么不能用一个大事务包住网络。

### Round 6：精读 UNKNOWN 和恢复

模拟“Provider 已成功但响应丢失”。沿订单详情刷新、Attempt 列表、reconcile、Adapter 查单、状态收敛走一次。

目标：从普通 CRUD 思维升级到故障恢复思维。

### Round 7：读角色权限和薄 Vue

沿着：

```text
按钮 → API → Session/CSRF → 所有权/角色 → Service → ORM/MySQL → Response
```

解释隐藏按钮为什么不能替代 Django 权限。

### Round 8：关闭参考，重建核心

不看 Service，写支付成功处理伪代码；再独立修改一个条件，例如：

- 最大 Attempt 次数从 3 改成 2；
- 给非法币种补测试；
- 让重复 COMMIT Movement 稳定失败；
- 给非 `PAID` Admin 发货补测试。

目标：从“看懂”升级到“能改、能证明”。

## 13. 业务能力训练题

1. 为什么一张 Order 可以有多次 PaymentAttempt？
2. 为什么一次 Attempt 失败不应该把 Order 改成 `PAYMENT_FAILED`？
3. 为什么前端“支付成功”页面不能成为支付事实？
4. 为什么下单时先预留，支付成功后才核销？
5. 支付失败和 UNKNOWN 时，为什么本 MVP 不立即释放库存？
6. 为什么 Customer 不能直接把 Order 改成 `PAID`？
7. 为什么模拟发货属于 Admin，而确认收货属于订单本人？
8. 为什么正常顾客页面不应该暴露“伪造签名/错误金额”选择器？
9. 为什么 Mock Payment 仍然有简历和面试价值？
10. 退款、取消、过期为什么不和首次支付闭环一次全部实现？

## 14. 工程能力训练题

1. 创建 Attempt 的短事务包含什么？Gateway 调用为什么在提交之后？
2. Attempt 请求幂等和 Event 幂等分别保护什么，能否合并成一个 Key？
3. Event 已有唯一约束，为什么 Reservation 和 COMMIT Movement 还需要额外防线？
4. 为什么必须先保存原始 Event，再验签和处理？
5. 需要校验签名、金额、币种、Order、Attempt 和状态中的哪些组合？
6. Provider 成功但本地响应超时时，数据库可能留下什么？怎样恢复？
7. reconcile 为什么必须复用原 Attempt？
8. 支付成功事务要锁哪些记录？固定锁顺序有什么价值？
9. 如何证明重复事件没有第二次核销库存？只看 HTTP 200 是否足够？
10. 前端按状态隐藏按钮后，为什么后端仍要拒绝非法转换？
11. 同步 Inbox baseline 与 Celery 异步消费相比，获得和失去了什么？
12. 哪些测试必须使用 MySQL，哪些状态机规则可以做快速单元测试？

## 15. 面试可能考察点

### 15.1 高频问题：支付怎样防重复？

建议回答结构：

```text
入口请求：Idempotency-Key + 规范化请求摘要，防用户/客户端重复创建 Attempt。
Provider 事件：UNIQUE(provider, event_id)，防相同回调重复处理。
领域状态：Attempt/Order/Reservation 状态机与 COMMIT Movement 唯一性，防语义重复。
事务：状态更新、库存核销和流水在同一 MySQL 事务。
证据：同 Key、重复 Event、多 event_id 重复成功和库存终值测试。
```

### 15.2 高频问题：为什么不能在数据库事务中调用支付网关？

```text
网络延迟和超时不可控，放在事务里会长期占有订单/库存锁，增加阻塞和死锁风险。
先在短事务中提交 Attempt，再调用 Gateway。
代价是出现崩溃/结果未知窗口，因此用持久 Attempt、UNKNOWN 和 reconcile 恢复。
```

### 15.3 高频问题：UNKNOWN 和 FAILED 有什么区别？

```text
FAILED 是可信事实：Provider 已明确告诉我们失败。
UNKNOWN 是本地知识不足：例如响应超时，Provider 可能成功也可能失败。
UNKNOWN 时禁止新建活跃 Attempt，必须对原 Attempt 查单或对账。
```

### 15.4 高频问题：支付成功怎样保证库存只核销一次？

```text
支付事件先幂等入箱；处理事务锁 Attempt、Order、Reservation 和 Inventory；
只允许 RESERVED → COMMITTED；reserved_stock 只减 quantity；
同一 Reservation 的 COMMIT Movement 唯一；状态和数据库约束共同防重复。
```

### 15.5 常见继续追问

- Provider 重复发相同 event_id 与不同 event_id 的相同成功事件，有什么区别？
- 如果 Event 已入箱，但处理事务失败，下一次怎样恢复？
- 如果 Attempt 已提交，但进程在调用 Gateway 前崩溃，怎么办？
- 如果 Gateway 已成功，但本地在保存 Event 前崩溃，怎么办？
- 为什么不用 Celery/RabbitMQ 立即异步处理？
- 如何验证模拟签名，而不把测试开关暴露给普通 Customer？
- 一个 Order 为什么最多只能有一个活跃 Attempt？
- 第三次失败后是否还能发起第四次？这个规则在哪里配置和测试？
- Customer 猜到别人的 Attempt UUID 后为什么仍拿不到数据？
- Admin Action 批量选择一张 PAID 和一张 PENDING_PAYMENT Order 时怎样处理？
- 为什么 `available` 在支付成功时不再减少？
- 取消/过期与支付成功同时发生时，未来怎样处理竞态？

最后一题属于后续 Payment Track 深化，不应在 MVP-4 baseline 中假装已经解决。

## 16. 当前限制与明确延期

MVP-4 目标是“最小可靠支付与完成闭环”，不是完整支付平台或完整 Payment Evidence Pack。本阶段明确延期：

- 待支付主动取消、15 分钟自动过期和库存释放；
- 已支付未发货取消、Refund Simulation；
- 终态后的迟到成功支付；
- 完整乱序事件矩阵、崩溃窗口注入和商业级 Reconciliation；
- Celery、RabbitMQ、Transactional Outbox；
- 支付宝/Stripe Sandbox；
- Production Payment、真钱与真实退款；
- 真实物流公司接口和复杂售后；
- 完整 Vue Admin。

Mock 默认成功；失败、UNKNOWN、错误签名、错误金额/币种和异常事件通过自动测试或受保护的开发注入验证。不能把“代码支持某个分支”写成“已经验证”，必须以实际测试输出为准。

## 17. Learning Done 标准

你达到以下标准，才算真正拥有 MVP-4：

- 不看代码，画出 Customer → API → Attempt → Gateway → Event Inbox → Order/Inventory 的完整流程；
- 区分 Order、PaymentAttempt、PaymentEvent、MockGatewayPayment、Reservation、Movement 和 Shipment 的职责；
- 独立画出 Order 与 PaymentAttempt 两张状态机，并指出非法转换；
- 用自己的话解释请求幂等与事件幂等为什么是两层；
- 用具体库存数字解释预留和核销，说明支付成功时为什么不再减少 available；
- 解释为什么 Attempt 必须先提交、Gateway 必须在事务外；
- 用“Provider 成功但响应超时”的例子解释 UNKNOWN 和 reconcile；
- 指出 Inbox 的原始事件、唯一标识、校验结果和处理结果各自作用；
- 说明 Customer、Admin 的所有权与角色边界；
- 沿 `/orders/:publicId` 的一个按钮画出 Vue → HTTP → Django → Service → MySQL 调用链；
- 不看答案，为一个重复或非法状态场景补测试；
- 只有在亲自看到命令输出后，才能引用验证记录中的通过数字；
- 能用 2 分钟面试口述说明“Mock 支付状态机 + 双层幂等 + Inbox + 库存最多核销一次”。
