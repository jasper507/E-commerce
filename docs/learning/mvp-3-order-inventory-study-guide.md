# MVP-3 Order + Inventory Reservation 学习指南

## 1. 先记住结论

MVP-3 是 Project One 第一次进入真正的交易核心。MVP-1 的 Cart 只是“购买意向”，MVP-2 的 Address 只是“当前可修改资料”；MVP-3 才把这些临时数据转换成不可随意改变的交易事实：

```text
Cart + 当前 Address + 当前 SKU/Inventory
→ Order + OrderItem Snapshot + AddressSnapshot
→ InventoryReservation + InventoryMovement
→ available_stock 减少、reserved_stock 增加
→ 成功后清理 CartItem
```

最重要的不是多了几张表，而是这整组变化必须具备四个性质：

1. **Server Authority（服务端权威）**：价格、金额、可售状态和库存都由后端重新读取；
2. **Atomicity（原子性）**：订单、快照、库存预留、库存流水和购物车清理要么全部成功，要么全部回滚；
3. **Idempotency（幂等）**：网络重试不能创建第二张订单或重复预留库存；
4. **Historical Snapshot（历史快照）**：以后修改商品、价格或地址，历史订单仍保持下单当时的事实。

本阶段属于 `Depth / Learning Mode`。你不需要背所有 Serializer、Vue 和 CSS，但必须能解释业务不变量、事务边界、幂等分支、行锁、失败回滚和测试证据。

## 2. 这一层生成了什么

### 2.1 后端交易模型

| 模型 | 本阶段作用 | 为什么存在 |
|---|---|---|
| `Order` | 保存订单状态、金额、公开订单号和幂等信息 | 表示一次已经被服务端接受的交易请求 |
| `OrderItem` | 保存商品名、SKU、规格、单价、数量、折扣和行金额快照 | 当前商品改名、改价或优惠规则变化后，历史订单不能变化 |
| `AddressSnapshot` | 保存下单时的完整收货信息 | 当前 Address 可修改或删除，订单地址不能跟着变化 |
| `InventoryReservation` | 记录哪一个 OrderItem 预留了多少库存及过期时间 | 为后续支付核销、取消/超时释放提供唯一业务身份 |
| `InventoryMovement` | 记录 available/reserved 的变化量 | 为库存审计、排错和以后对账保留证据 |

### 2.2 API

| Method | Path | 行为 |
|---|---|---|
| `GET` | `/api/orders/` | 只列出当前用户自己的订单 |
| `POST` | `/api/orders/` | 使用 Cart 和选中 Address 创建待支付订单 |
| `GET` | `/api/orders/{public_id}/` | 只读取当前用户自己的订单详情 |

创建请求体只有：

```json
{
  "address_id": 7
}
```

同时必须携带：

```http
Idempotency-Key: checkout-550e8400-e29b-41d4-a716-446655440000
```

客户端不提交可信的单价、总价或库存值。即使额外伪造 `total: "0.01"`，后端也会忽略并按当前 SKU 价格重新计算。

### 2.3 薄 Vue 结账入口

新增 `/checkout`：

- 读取当前 Cart 和 Address；
- 默认选中默认地址；
- 展示金额，但明确它不是最终事实；
- 每次逻辑下单生成一个 Idempotency-Key；
- 网络不确定时保留同一个 Key 重试；
- 成功后展示公开订单号、状态、金额和地址快照。

Vue 不负责扣库存、算最终金额、判断地址所有权或保证幂等。

## 3. 相比 MVP-2 有什么新改变

| 维度 | MVP-2 Minimum Address | MVP-3 Order + Reservation |
|---|---|---|
| 数据性质 | 当前资料，可修改、可删除 | 交易事实，不应作为普通 CRUD 修改或删除 |
| 业务范围 | 单一用户的地址规则 | Cart、Address、Catalog、Inventory、Ordering 跨领域协作 |
| 事务复杂度 | 默认地址多行切换 | 多张业务表、库存数字和 Cart 清理的一次原子提交 |
| 并发对象 | 同一 User 的地址写入 | User、Cart、CartItem、SKU/Inventory 与重复下单请求 |
| 重复请求 | 设默认天然可重复 | 创建订单必须用 Key + 请求摘要识别重放与冲突 |
| 历史事实 | Address 本身不是历史 | 商品、价格和地址全部复制为快照 |
| 金额 | 无金额 | Decimal、服务端计算、数据库公式约束 |
| 失败影响 | 地址保持原状态 | 订单不留半成品、库存不变化、Cart 不清空 |

MVP-2 是 Supporting 能力；MVP-3 是第一块真正需要在面试中深入讲解的 Depth 能力。

## 4. 当前业务规则与 Test Oracle

Test Oracle 是“测试凭什么判断正确”的规则。本阶段使用 Charter 中已经确认的行为：

### 4.1 成功规则

- Actor 必须是已登录 Customer；写请求必须通过 CSRF；
- 只能选择自己的 Address；
- Cart 必须非空；
- Category、Product、SKU 必须仍可售；
- 每个 SKU 的 `available_stock >= CartItem.quantity`；
- 后端重新读取当前 SKU 价格，使用 `Decimal` 计算；
- `subtotal = Σ(unit_price × quantity)`；
- MVP-3 `discount_total = 0.00`、`shipping_fee = 0.00`；
- `total = subtotal - discount_total + shipping_fee`；
- 创建 `PENDING_PAYMENT` Order；
- 复制 OrderItem 与 Address 快照；
- 为每个 OrderItem 创建一条 Reservation 和一条 RESERVE Movement；
- 库存从 available 移到 reserved，总量不凭空增减；
- 全部成功后，才清理本次 CartItem。

### 4.2 失败规则

- 缺少或非法 Idempotency-Key：`400`；
- 地址不存在或不属于当前用户：统一 `404`；
- Cart 为空：`400`；
- 商品失效或库存不足：`409 Conflict`；
- 同 Key + 不同规范化请求：`409 Conflict`；
- 任一步失败：不能留下 Order、OrderItem、Snapshot、Reservation 或 Movement；库存和 Cart 保持原状态。

### 4.3 重复规则

- 同一用户 + 同一创建订单端点 + 同 Key + 同一个规范化请求：返回第一次创建的 Order；
- 重放时即使 Cart 已被第一次请求清空，也必须先识别原 Order，不能返回“购物车为空”；
- 重放不能第二次减少 available、增加 reserved 或新增流水；
- 同 Key + 不同 `address_id`：不能猜测用户意图，返回 `409`。

## 5. 完整执行流程

一次成功下单：

```text
CheckoutView
→ POST /api/orders/
   body: {address_id}
   headers: Session Cookie + CSRF + Idempotency-Key
→ DRF SessionAuthentication / CSRF
→ CreateOrderSerializer 校验 address_id
→ OrderCollectionView 校验 Idempotency-Key
→ create_order_from_cart()
→ transaction.atomic() 开启 MySQL 事务
→ 锁当前 User（串行化同一用户的创建订单/地址写入）
→ 查找相同 user + key 的现有 Order
   ├─ 同摘要：返回原 Order
   └─ 不同摘要：409
→ 锁并读取自己的 Address
→ 锁 Cart 与 CartItems
→ 按 sku_id 固定顺序锁 Inventory
→ 重新校验可售状态、库存和当前价格
→ 服务端计算 Decimal 金额
→ 创建 Order
→ 创建 AddressSnapshot
→ 创建 OrderItem Snapshot
→ available -= quantity
→ reserved += quantity
→ 创建 Reservation
→ 创建 RESERVE Movement
→ 删除本次 CartItems
→ COMMIT
→ OrderSerializer 返回 201
```

注意：事务中没有 Payment Gateway 网络调用。数据库锁不能在等待外部网络时长期占用；支付会在后续 MVP 的事务外开始。

## 6. 最重要的业务逻辑

### 6.1 Cart 不是订单

Cart 允许商品后来改价、下架或库存被其他人买走。它只表示“用户想买什么”，不承诺最终成交事实。

因此下单必须重新读取：

```text
当前商品状态
当前 SKU 状态
当前 SKU 价格
当前 available_stock
```

如果直接相信购物车显示值，用户可篡改请求价格，或用过期库存数据产生超卖。

### 6.2 available 与 reserved 为什么分开

假设库存原来是：

```text
available = 10
reserved = 0
```

用户创建数量 2 的待支付订单：

```text
available = 8
reserved = 2
```

总物理数量仍是 10，但其中 2 件暂时不能卖给其他人。后续支付成功时核销 reserved；取消或超时时释放回 available。本阶段只做“预留”，不提前实现核销和释放。

### 6.3 Snapshot 为什么必须复制

订单创建后，用户把地址从深圳改到广州，管理员把商品从“无线鼠标 M1”改名并把价格从 ¥129 调到 ¥999。历史订单仍必须显示：

```text
无线鼠标 M1 / ¥129.00 / 深圳旧地址
```

否则客服、物流、审计和争议处理看到的都不是交易发生时的事实。

### 6.4 Idempotency-Key 解决什么

用户点“创建订单”，服务端已经提交，但响应途中断网。浏览器不知道成功还是失败，于是重试。

没有幂等：

```text
第一次：Order A + 预留 2
第二次：Order B + 再预留 2
```

有幂等：

```text
第一次：创建 Order A
第二次同 Key 同请求：返回 Order A，不再执行副作用
```

Key 不是万能去重。服务端还保存规范化请求摘要，防止同一个 Key 被错误地拿去提交另一个地址。

## 7. 最重要的工程逻辑

### 7.1 Transaction Boundary（事务边界）

`transaction.atomic()` 包围：

```text
Order
+ OrderItem
+ AddressSnapshot
+ InventoryReservation
+ InventoryMovement
+ Inventory 数字变化
+ CartItem 删除
```

例如两个商品中第二个库存不足，整个事务失败：第一个商品即使已经走到中间步骤，也不能留下扣减或半张订单。

### 7.2 Lock Ordering（固定锁顺序）

当前基线先锁 User，再锁 Address/Cart/CartItem，库存按 `sku_id` 排序后锁定。固定顺序降低多个请求以不同顺序拿锁而形成死锁的风险。

当前使用 `select_for_update()` 是最简单正确基线。它的代价是热点 SKU 的请求可能排队、锁等待增大。后续库存旗舰实验会比较：

1. 行锁：先锁后检查再更新；
2. 原子条件更新：`UPDATE ... WHERE available_stock >= quantity`；
3. 必要时再评估 Redis Lua 流量准入。

没有真实并发、吞吐和延迟数据前，不能宣布哪个方案最终胜出。

### 7.3 Database Constraints（数据库约束）

Service 是主要业务入口，数据库继续做最后防线：

- `(user, idempotency_key)` 唯一；
- Order 金额非负；
- `discount_total <= subtotal`；
- Order `total` 必须符合金额公式；
- OrderItem 数量 1–99；
- OrderItem `line_total = unit_price × quantity`；
- 一个 OrderItem 最多一条 Reservation；
- 同一 Reservation 每种 Movement 类型最多一条；
- Inventory available/reserved 都不能小于 0。

数据库约束不能替代完整 Service，但可以拦截绕过 Service 的错误代码和未来回归。

### 7.4 Public ID（不可猜测公开编号）

数据库内部使用 BigInt 主键，API 暴露 UUID `public_id`。连续的 `1, 2, 3` 容易被枚举，也会暴露订单规模；UUID 更适合作为公开稳定标识。权限仍必须用 `public_id + user` 查询，UUID 本身不等于授权。

## 8. 核心代码阅读顺序

### 第一层：一定要精读

1. `backend/apps/ordering/tests/test_order_api.py`
   - 先看成功、回滚、快照、幂等和所有权测试；
2. `backend/apps/ordering/tests/test_order_concurrency.py`
   - 看两个线程为什么只产生一个 Order；
3. `backend/apps/ordering/services.py` 的 `create_order_from_cart()`
   - 这是本阶段最核心代码；
4. `backend/apps/ordering/models.py` 的 `Order`、`OrderItem`、`AddressSnapshot`；
5. `backend/apps/inventory/models.py` 的 Reservation 和 Movement。

### 第二层：理解调用链，不必逐行背

1. `backend/apps/ordering/views.py` 的 Order Views；
2. `backend/apps/ordering/serializers.py` 的 Order Serializers；
3. `backend/apps/ordering/order_urls.py`；
4. `backend/config/urls.py`。

### 第三层：快速看边界即可

1. `frontend/src/api/orders.ts`：Key、CSRF 和 POST；
2. `frontend/src/views/CheckoutView.vue`：选择地址、提交、显示结果；
3. `frontend/src/types/order.ts`；
4. CSS 和普通页面结构不需要背。

## 9. 只看三段核心代码时看什么

如果时间很少，只看以下三个逻辑块：

### 核心一：先识别幂等重放

```python
existing = Order.objects.filter(
    user=locked_user,
    idempotency_key=idempotency_key,
).first()
```

同摘要就返回原 Order；异摘要就 409。它必须发生在“Cart 是否为空”之前，因为第一次成功后 Cart 已经清空。

### 核心二：事务内重新校验并锁库存

```python
Inventory.objects.select_for_update()
```

读取购物车时看到“有货”不代表下单时仍有货。真正决定能否预留的是事务内锁定后的 MySQL 库存。

### 核心三：成功末尾才清 Cart

```python
CartItem.objects.filter(pk__in=item_ids).delete()
```

它仍在同一事务内。后续步骤失败时删除也会回滚，所以用户不会遇到“订单没创建、购物车却空了”。

## 10. 理想学习顺序

### Round 1：完全不看代码，只讲业务

用一个具体例子回答：

```text
购物车：M1-BLACK，2 件，每件 ¥129
库存：available 10，reserved 0
地址：深圳南山区
```

成功后应该有哪些记录？库存变成多少？Cart 是否保留？地址后来修改会不会影响订单？

目标：提升业务建模能力，不被 Django 类名带跑。

### Round 2：只看测试名和断言

先读 API、Model、Concurrency 测试，不看实现。自己写下：

- 正常下单的 Arrange / Act / Assert；
- 第二个 SKU 缺货时哪些数据必须保持不变；
- 同 Key 同请求与同 Key 异请求分别如何判断。

目标：学会把业务规则转换成可执行 Test Oracle。

### Round 3：读数据模型和约束

画出：

```text
User 1─N Order 1─N OrderItem 1─1 Reservation N─1 Inventory
                1─1 AddressSnapshot
Reservation 1─N InventoryMovement
```

目标：提升表关系、不变量和审计建模能力。

### Round 4：精读 Service 的事务流程

不要一开始逐行看。先把函数分成六段：

1. 锁用户并判断幂等；
2. 读取地址和购物车；
3. 锁库存并验证；
4. 计算金额；
5. 写订单、快照、预留与流水；
6. 清购物车并提交。

目标：理解 Transaction Boundary、Lock Ordering 和失败回滚。

### Round 5：读 HTTP 和薄前端

沿着：

```text
CheckoutView → POST → URL → View → Serializer → Service → ORM/MySQL → Response
```

目标：能定位每层职责，避免把业务正确性写进 Vue。

### Round 6：关闭参考，重建核心

不看现有 Service，先写 15–25 行伪代码，再尝试独立补一个规则，例如：

- 商品变为 inactive 时返回什么；
- Key 超过 128 字符怎样拒绝；
- 多商品中最后一个缺货怎样证明全部回滚。

目标：从“看懂”升级到“能修改”。

### Round 7：准备实验问题

回答：行锁正确，但是否吞吐最好？怎样用固定数据、并发数、p95/p99、锁等待和超卖数比较行锁与原子条件更新？

目标：把当前基线连接到库存并发旗舰主线。

## 11. 业务能力训练

1. 为什么 Cart 不应该锁价、也不应该在加入时预占库存？
2. 为什么下单成功后清购物车，而不是请求一开始就清？
3. 为什么地址和商品都要保存 Snapshot？哪些字段必须保存？
4. 待支付订单为什么使用 reserved，而不是直接把商品视为已售？
5. Reservation 为什么需要唯一业务身份和过期时间？
6. 用户重复点击按钮、浏览器超时重试、代理重试，三者在后端看起来有什么共同点？
7. 为什么同 Key 异请求不能直接创建新订单？
8. 为什么库存不足使用 `409 Conflict`，而不是把它当服务器 `500`？
9. 本阶段为什么不顺便接 Mock Payment？
10. 为什么取消、过期和释放库存不在本 MVP 一次全部写完？

## 12. 工程能力训练题

1. `transaction.atomic()` 包含哪些写操作？哪一个操作必须放在最后？
2. 为什么幂等检查要放在空 Cart 检查之前？
3. User 行锁解决什么？Inventory 行锁解决什么？两者能互相替代吗？
4. 为什么库存行按 `sku_id` 固定顺序锁？
5. Service 检查库存后，数据库为什么仍需要非负 CheckConstraint？
6. 请求摘要为什么使用规范化 JSON，而不是原始请求字符串？
7. UUID 防枚举后，为什么 Detail 查询仍要加 `user=request.user`？
8. 为什么 Payment Gateway 不能放进当前库存事务？
9. 如何证明失败没有清 Cart？只检查 HTTP 状态够不够？
10. 双线程测试证明了什么，又没有证明什么？

## 13. 面试可能考察点

### 13.1 高频主问题

**问题：你的下单接口怎样避免重复订单？**

建议回答结构：

```text
原因：网络超时会让客户端重试，重复 POST 可能重复扣库存。
方案：要求 Idempotency-Key，数据库唯一约束 user + key，保存规范化请求摘要。
行为：同 key 同摘要返回原订单；同 key 异摘要返回 409。
并发：同一用户先竞争 User 行锁，数据库唯一约束再做最后防线。
证据：API 重放测试 + 双线程同 key 测试，断言只有一个 Order/Reservation/Movement。
```

**问题：怎样保证下单和扣库存一致？**

```text
同一个 MySQL transaction.atomic 中完成订单、快照、库存预留、库存流水和 Cart 清理；
库存行 select_for_update 后重新检查 available；
任一步失败数据库整体回滚；
数据库 CheckConstraint 保证 available/reserved 非负。
```

**问题：为什么不用 Redis 直接扣库存？**

```text
当前 MySQL 是最终事实来源，先建立最简单正确基线。
Redis 以后可以做缓存或秒杀准入，但要处理 Redis 与 MySQL 的一致性和崩溃窗口。
没有实验前不把 Redis 当核心库存唯一事实。
```

### 13.2 常见追问

- `select_for_update()` 在什么事务隔离和索引条件下工作？
- 两个订单以不同 SKU 顺序锁库存会怎样？
- 行锁与原子条件更新的 Trade-off 是什么？
- 服务端已做幂等，为什么还需要数据库唯一约束？
- 请求已经成功但响应丢失时，前端应该生成新 Key 还是复用旧 Key？
- 地址删除后，订单为什么仍能显示地址？
- 为什么订单公开编号不用数据库自增 ID？
- Movement 与只看 Inventory 当前数字相比，多提供了什么？
- 订单过期时怎样保证库存最多释放一次？
- 支付成功与库存核销怎样避免重复回调造成二次核销？

最后两题属于后续 MVP，但必须能说明当前 Reservation/Movement 为它们准备了什么基础。

## 14. 当前限制与后续边界

MVP-3 只创建 `PENDING_PAYMENT` Order 和 `RESERVED` Reservation。本阶段明确没有：

- PaymentAttempt、Mock Payment 或 Webhook；
- 支付成功后的库存核销；
- 主动取消、15 分钟过期触发和库存释放；
- Refund Simulation；
- Redis、Lua、MQ 或分布式锁；
- 行锁与原子条件更新的性能胜负结论；
- 浏览器 E2E 或真实支付。

下一阶段应先接最小 Mock Payment 和支付状态事实，再实现核销/失败重试；库存并发旗舰实验需要单独形成 Evidence Pack，不能靠本阶段功能测试代替。

## 15. Learning Done 标准

你达到以下标准，才算真正拥有 MVP-3：

- 不看代码，讲清成功下单后七类数据怎样变化；
- 说出五个核心不变量；
- 解释为什么幂等检查在空 Cart 检查之前；
- 画出完整 HTTP → Service → MySQL 请求链；
- 在 Service 中指出事务边界、用户锁、库存锁和清 Cart 的位置；
- 用一个两商品例子解释失败回滚；
- 解释 Snapshot、Reservation 和 Movement 的不同职责；
- 能回答行锁的优点、代价和待比较候选；
- 不看答案，为一个新失败场景补测试；
- 能用 2 分钟面试口述说明“幂等创建订单 + 原子预留库存”。
