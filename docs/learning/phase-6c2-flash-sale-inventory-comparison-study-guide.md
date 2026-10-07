# Phase 6C-2：在完整秒杀事务中比较库存预占办法

日期：2026-10-07。Depth / Learning Mode；加强“库存并发与秒杀正确性”主线。
本片延续 [Phase 6C-1](phase-6c1-flash-sale-contention-study-guide.md)，保留默认 NOWAIT，
将条件 UPDATE 作为测试中的实验候选。正确性与受控等待结果由本片实测补齐；
当前不选性能赢家，不将候选接入正式策略选择。

本片新预测题尚未收到学习者回答，不预设已经答对或掌握。

## 1. 先明确业务规则，再比较算法

顾客 A、B 使用各自的地址，购买同一个活动商品，每张订单固定买一件。
活动价 89.00 元，普通 SKU 价 129.00 元；起点为 `available_stock=1, reserved_stock=0`。
A、B 的普通购物车分别有 2、3 件商品，秒杀下单不会删除或修改这些购物车内容。

Invariant（不变量）是请求怎么交错都必须保持的规则：

- 共享库存只有一件，最多一张完整订单提交成功。
- 同一顾客、同一活动商品，最多一条 `HELD/1` 或 `CONSUMED/1` 的阻挡资格。
- 成功订单对应明细、地址快照、资格、预占和 RESERVE 流水，数量及关联必须一致。
- 被拒绝或中途失败的请求不残留订单、Key 映射、资格、预占或流水。
- 仅一张成功待付订单时，库存为 `0/1`；商品原价和两人购物车保持不变。

资格约束保护“每个顾客限购”；库存算法保护“不同顾客争用同一件库存”。
两者不能互相替代。资格定义见 [FlashSalePurchase](../../backend/apps/promotions/models.py)，
支付和关闭规则继续沿用 [Phase 6B](phase-6b-flash-sale-lifecycle-study-guide.md)。

## 2. 同一个事务，只有库存预占办法不同

Transaction（事务）使本次下单的数据库改动一起成功，任一步抛错则一起撤销。
入口仍是 [create_flash_sale_order()](../../backend/apps/promotions/services.py)，公开参数不增加策略选择。

```text
锁本顾客 User → 检查秒杀 scope 下的幂等 Key
→ 锁自己的 Address，并核对归属
→ 检查活动、SKU、商品、分类是否可购买
→ 检查本顾客是否已有阻挡资格
→ 写 Order、AddressSnapshot、OrderItem、HELD/1 Purchase
→ _reserve_flash_sale_inventory(sku)：预占一件库存
→ 写 RESERVED InventoryReservation 和 RESERVE InventoryMovement
→ 整体提交；任一步失败，整体回滚
```

本片把原库存代码提取到私有函数 `_reserve_flash_sale_inventory()`，默认实现仍为 NOWAIT。
实验使用 Patch（测试期间暂时替换函数）把这一处换成条件更新，A、B 都走同一候选。
两种算法共用上面的订单、资格、预占和流水逻辑，避免复制整段流程后比较两份不同实现。

代价是候选只能通过测试运行；它不是新增的正式配置、HTTP 参数或策略入口。
提取函数本身也不会提交事务：锁与写入一直保留到外层事务提交或回滚。

## 3. 当前 NOWAIT 怎样保护库存

Row Lock（行锁）协调多个事务对同一数据库记录的修改。
当前服务先通过 `SELECT ... FOR UPDATE NOWAIT` 取得库存行锁，再检查和修改库存：

```text
取到锁，available >= 1 → available -= 1，reserved += 1
取到锁，但没有记录或 available < 1 → insufficient_stock
他人占用库存锁 → MySQL 3572 → stock_busy
```

NOWAIT（遇到锁占用不等待）让这条取库存锁的 SQL 提前失败。
`stock_busy` 只表示当前拿不到锁，不能据此判断最终是否有库存。
其他 SQL，例如顾客锁或外键检查，仍可能等待；整个下单流程并不因此变成“所有步骤都不等待”。

取得锁之后，其他事务不能同时修改这条库存记录。
所以此处 Python 对象的减加操作在持锁保护下执行，与“先读旧值、以后才开事务写回”的错误基线不同。

## 4. 条件 UPDATE 怎样表达同一个业务动作

Atomic Conditional Update（原子条件更新）把“库存足够才预占”交给一条数据库语句执行。
候选的核心 ORM 表达式相当于：

```python
updated = Inventory.objects.filter(
    sku=sku,
    available_stock__gte=1,
).update(
    available_stock=F("available_stock") - 1,
    reserved_stock=F("reserved_stock") + 1,
)
```

F Expression（F 表达式）让数据库使用列的当前值计算，不先把数值读成 Python 旧快照再写回。
`available_stock__gte=1` 表示“可售库存大于等于一件”。这段代码相当于：

```sql
UPDATE inventory_inventory
SET available_stock = available_stock - 1,
    reserved_stock = reserved_stock + 1
WHERE sku_id = <本次 SKU ID>
  AND available_stock >= 1;
```

条件判断、可售减一、预占加一在同一条 UPDATE 中执行。
SKU 与 Inventory 一对一，所以这里应当只有两种正常结果：

- `updated == 1`：一条库存记录满足条件并被预占；继续读取这条 Inventory，创建预占和流水。
- `updated == 0`：没有满足条件的记录；抛出 `insufficient_stock`，让下单事务撤销前面写入的订单和资格。

抛错必须离开服务的事务边界，不能在里面吞掉异常后返回“下单失败”，却提交半张订单。
一条 UPDATE 足够完成库存决策，不表示完整下单只执行一条 SQL；后续仍有库存读取和业务写入。

**UPDATE 同样需要写锁。** “原子”不等于“不等待”。A 尚未结束事务时，
B 的条件 UPDATE 可能等待 A 提交或回滚，再依据可修改的库存判断行数。
若出现真正锁超时，这也是数据库错误；当前比较不能把它当成 NOWAIT 的 3572 或无限自动重试。

## 5. 用同一件库存追踪 A 提交与回滚

测试让 A 在外层 `atomic()` 调用完整服务。A 写完六类事实、库存变成 `0/1` 后暂停，
外层事务尚未提交。服务返回只是结束内部保存点，库存锁仍由外层事务持有。
Savepoint（保存点）用于撤销事务中的一段改动，不会提前提交外层事务。

B 使用另一条 MySQL 连接调用同一服务。Observer（观察连接）另外读取已提交事实和服务端执行信息。
这样比较的是同一完整下单流程，不是让 A 只手工锁库存、B 却完整下单。

以下是运行前确定的预期，不是提前宣称的实测结果：

| 算法 | A 持锁时 B | A 提交后 | A 回滚后 |
| --- | --- | --- | --- |
| 默认 NOWAIT | 本次请求返回 `stock_busy`，B 写入回滚 | B 原 Key 重试库存不足 | B 原 Key 重试可以建单 |
| 条件 UPDATE 候选 | 本次库存 UPDATE 尚未完成 | 修改 0 行，B 返回库存不足并回滚 | 修改 1 行，B 原请求直接继续并建单 |

条件 UPDATE 的区别在“本次请求仍在进行”：A 回滚后，B 不必先结束请求再发起一次重试。
NOWAIT 的 B 已经返回，A 后来回滚不会自动让已结束的请求继续。

A 回滚时，其订单、资格、预占、流水与库存修改一起撤销。
B 的失败请求也没有保存 Key 映射，所以可以用原 Key 再试。
Idempotency（幂等）在这里保证成功 Key 重放返回原订单，`created=False`，不会重复预占。
换 Key 不能凭空增加库存，也不能绕过仍为 `HELD/1` 或 `CONSUMED/1` 的限购资格。

还要验证条件 UPDATE 成功之后、流水实际写入之后再抛错：六类事实都消失，库存恢复为 `1/0`；
取消故障后用原 Key 重试仍能成功。这是证明整段事务一起撤销，不只证明 UPDATE 没有扣成负数。

## 6. 怎样验证，哪些话暂时不能说

[比较测试](../../backend/apps/promotions/tests/test_flash_sale_inventory_comparison.py) 记录真实 SQL、
修改行数、独立连接编号、A 未提交快照，以及 B 返回后的完整业务事实。
默认基线继续参考 [6C-1 测试](../../backend/apps/promotions/tests/test_flash_sale_contention.py)。
2026-10-07 实际运行隔离 MySQL8.4.10：目标 `6 passed, 138 subtests passed in 27.16s`，模块回归 `29 passed, 260 subtests passed in 74.04s`，均首次通过。子场景不另算独立测试或断言总数，耗时不是接口性能数据。

- NOWAIT 提交/回滚分别使用A3522/B3523与A3526/B3527；都先收到真实3572并在A放行前返回。提交后原Key库存不足，回滚后原Key重试创建。
- UPDATE 提交使用A3512/B3513；服务端看到B的Query/库存UPDATE/updating且尚未完成。A提交后更新0行，B库存不足，自己已写的订单和资格一起回滚。
- UPDATE 回滚使用A3515/B3516；同样先观测服务端库存UPDATE未完成。A回滚后更新1行，B原请求创建成功，无需另发一次请求。
- 库存5的同用户不同Key仅一单成功，另一单限购且未发库存UPDATE；完整流水实写后的故障也回滚六类事实，原Key再试及重放通过。

[目标原输出](../verification/phase-6c2-flash-sale-inventory-comparison-target-output.txt)、
[模块回归原输出](../verification/phase-6c2-flash-sale-inventory-comparison-regression-output.txt)与
[本片验证记录](../verification/phase-6c2-flash-sale-inventory-comparison.md)保留6条真实ORM快照及SQL/行数/连接证据。
后检临时库已清理，开发库未迁移或业务写入；check为0 issues，迁移漂移No changes detected。

应用账号当前没有读取 `performance_schema.data_lock_waits` 的权限，本片不扩大 GRANT。
使用现有账号读取 `information_schema.processlist`，定位 B 自己的连接：
必须看到 `COMMAND=Query`、实际库存 UPDATE SQL，且 A 尚未放行、B 尚未完成。
STATE 字段原样保存；不能把 `updating` 直接翻译为“已观测到指定记录锁等待”。

这组观察可以支持：**B 的库存 UPDATE 已到达数据库服务器，在受控持锁窗口内尚未完成；
A 放行后，提交与回滚分别对应修改 0 行与 1 行。**
它没有采集 `RECORD WAITING`、锁索引或 B→A 等待图，不声称拥有这些证据。
若只设置了“即将执行 SQL”的线程事件，却没有服务端查询证据，也不能把线程未返回当成真实数据库等待。

测试人为延长 A 的事务，是为了稳定安排交错，不是接口延迟或吞吐指标。
等待期间，B的数据库连接、事务及已取得的User/Address/Purchase锁仍保留；本片没有测量其资源成本。
这些结果不能证明普遍无死锁、生产容量、条件 UPDATE 性能更好，或候选已经正式采用。
本片没有新增 HTTP、前端、MQ、Redis、依赖或数据库迁移；正式默认仍为 NOWAIT。

后续待办是相同负载下的压测、等待期间的连接占用，以及 ADR（记录方案选择与取舍的决策文档）。
这些工作尚未在本片实现，必须有实际数据后才决定最终策略。

## 7. 复述与手写练习

先预测：A 已完成预占但随后回滚，为什么 NOWAIT 的 B 需要重试，而条件 UPDATE 的 B 可以在原请求内成功？
再用“问题 → 不变量 → 两种库存决策 → 事务回滚 → 代价 → 证据”复述一遍。

手写练习：给条件更新补上 `updated == 0` 的错误分支，说明错误为何必须越过事务边界；
再说明为什么判断成功以后，还需要创建 Reservation 和 Movement，不能只改库存数字就返回订单成功。
