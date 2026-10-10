# Phase 6C-1：一件库存，两个不同顾客同时抢

日期：2026-10-07。Depth / Learning Mode；加强“库存并发与秒杀正确性”主线。
本片只验证当前完整秒杀下单服务的库存竞争，先建立后续方案比较的基线。
你已正确预测：A 持有库存锁时，B 执行 NOWAIT 会立即收到库存繁忙。
接下来用 A 提交与回滚两个变式检查原因和边界。

## 本片前后变化

- **开始前：** 已验证同顾客限购与资格生命周期，但不同顾客争同一件库存，还需要在完整秒杀事务中建立可复现基线。
- **本片改变：** 用独立 MySQL 连接安排同时竞争、首单提交及回滚，记录真实 NOWAIT 错误、整单回滚和原 Key 重试终值。
- **完成后与边界：** 本片没有换算法：NOWAIT 会先拒绝竞争者，首单后来回滚也需重新请求。三个受控场景支持正确性，不证明性能、所有交错或普遍无死锁。

**后续衔接：** [6C-2 在相同完整事务中比较条件 UPDATE](phase-6c2-flash-sale-inventory-comparison-study-guide.md)。正式策略后来在 [6C-5](phase-6c5-flash-sale-inventory-failures-study-guide.md) 切换。[学习导航](README.md)

## 1. 先确定正确结果

两个不同顾客 A、B，各有自己的地址；购买同一个活动商品，各买一件。
起点是 `available_stock=1, reserved_stock=0`，活动价为 89.00 元。

Invariant（不变量）就是请求如何交错都必须保持的业务规则。本片要求：

- 两人竞争这一件库存，最多一张订单成功提交。
- 成功订单有对应的明细、地址快照、购买资格、库存预占和 RESERVE 流水。
- 被拒绝或中途失败的请求，不能留下本次订单、资格或预占。
- 仅有一张成功待付订单时，库存为 `0/1`，预占数量合计为 1。

“每位顾客限购一件”和“大家共享一件库存”是两个约束。
Purchase 的顾客联合唯一键保护前者；A、B 是不同顾客，可以各自申请资格，
但他们仍须争用同一个 Inventory 库存记录。

## 2. 为什么只有事务还不够

Transaction（事务）让本次订单的数据库改动一起提交，失败时一起撤销。
Row Lock（行锁）协调不同事务对同一库存记录的修改，避免同时用旧库存作决定。
二者分别解决“本次请求不能只成功一半”和“多个请求不能争卖同一件库存”。

历史 [Phase 3 超卖复现](../verification/phase-3-inventory-race-baseline.md) 曾观察到：

```text
A 在事务外读到 available=1、reserved=0
B 也在事务外读到 available=1、reserved=0
A 进入事务，按旧值建单、写库存 0/1、建立一件预占并提交
B 进入事务，也按旧值建单、写库存 0/1、再建立一件预占并提交
结果：两张订单、两件预占，库存表却只有 reserved=1
```

这时库存数字仍非负，数据库的非负 CHECK（检查约束）也可能通过，
但业务上已经接受两人购买仅有的一件商品。只检查库存表“没有负数”不足以证明正确。
进入事务不会自动刷新已读到的 Python 库存对象；这段错误示范没有重新读取或锁定库存。
它是既有最小错误示范的历史结果，没有复制完整秒杀下单，也不是本片重新复现的结果。

## 3. 当前服务怎样运行

源码入口：[create_flash_sale_order()](../../backend/apps/promotions/services.py)。
本片直接调用服务；秒杀下单 HTTP 入口仍待后续切片。

```text
各自锁 User → 检查本顾客、秒杀 scope 下的幂等 Key
→ 各自锁 Address，并校验地址归属
→ 普通读取活动、SKU、商品、分类，校验可购买状态
→ 检查本顾客是否已有阻挡购买记录
→ 写 Order、AddressSnapshot、OrderItem、HELD/1 Purchase
→ 取得共享 Inventory 的 FOR UPDATE NOWAIT 行锁
→ 检查库存；available 减 1，reserved 加 1
→ 写 RESERVED 预占和 RESERVE 流水
→ 一起提交；任一步抛错则整体回滚
```

NOWAIT（不等待锁）表示这条取锁 SQL 遇到他人占用立即报错；服务把指定 MySQL 错误转为
`stock_busy`。它不保证整个请求的其他 SQL 都不会等待，例如用户锁或外键检查仍可能涉及锁。
活动与目录的这次联查没有主动加排他锁；不同顾客也锁不同 User/Address。

名额先写、库存后锁，所以 B 到达库存时可能已在自己的事务中写了订单与 Purchase。
这些内容尚未提交；库存繁忙异常越过最外层事务边界后，会一并撤销。
不能在事务中吞掉异常，再把这些半成品提交。

## 4. 两种受控交错，先预测后验证

Oracle（测试判定标准）就是先确定怎样的结果才算正确，避免根据运行结果临时改标准。
[本片测试](../../backend/apps/promotions/tests/test_flash_sale_contention.py) 让 A 在测试外层事务里
调用真实服务，完整写完 RESERVE 流水后暂停，保留尚未提交的事务和库存锁；
再由 B 的独立 MySQL 连接调用同一服务。B 真正返回后才放行 A。
服务自己的嵌套 atomic 在此处是 Savepoint（保存点，用于撤销事务中的一段改动），
函数返回并不会提前提交外层事务。这是测试延长未提交窗口的方式，没有替换业务 SQL。

| A 的后续操作 | A 尚未结束时，B 本次请求 | B 用原 Key 再请求 | 最终事实 |
| --- | --- | --- | --- |
| 提交 | `stock_busy`，本次写入回滚 | `insufficient_stock` | A 的六类事实各 1，库存 `0/1` |
| 中途抛错并回滚 | `stock_busy`，本次写入回滚 | 可以创建订单 | 回滚后先为零事实、库存 `1/0`；B 重试后六类事实各 1，库存 `0/1` |

六类事实指 Order、AddressSnapshot、OrderItem、FlashSalePurchase、InventoryReservation、
InventoryMovement。除了数量，还要检查记录之间的关联及顾客归属：回滚的 A 和被拒绝的 B
不能残留自己的订单 Key 或资格。成功 Key 再请求应返回原订单，`created=False`，不重复预占。

`stock_busy` 只表示“此刻拿不到库存锁”；`insufficient_stock` 表示拿到锁后发现库存不足。
A 后来回滚不会自动恢复 B 已结束的请求。B 必须重新发送请求，重新检查业务条件。
A 若提交，B 换一个 Key 也不会让 `available=0` 变成有货。

Idempotency（幂等）在这里表示成功 Key 重放不会重复建单。
失败请求的全部写入已回滚，所以该 Key 没有绑定订单；重试仍执行真实的库存检查。
是否成功取决于当时的已提交库存，不取决于换不换 Key。

## 5. 当前方案的代价与下片候选

NOWAIT 能让库存竞争请求提前收到“繁忙”，但首单即使随后回滚，已被拒绝的请求仍需要重试。
这就是本片要观察的行为代价。

Atomic Conditional Update（原子条件更新）让数据库一次执行“库存足够才扣减”，
通过实际更新行数判断结果。UPDATE 也要取得写锁；首单占用库存时，它可能等待，
再依据首单提交或回滚后的库存决定售罄或成功。
历史 [Phase 3 策略比较](../verification/phase-3-inventory-strategy-comparison.md) 只对比库存 SQL；
将候选放入相同完整下单事务进行比较，仍是后续切片。本片不替换正式算法。

## 6. 实测区与证据边界

本轮已在真实隔离 MySQL8.4.10 上运行。首轮为 `2 failed, 1 passed, 18 subtests passed in 18.65s`，原因在测试观察器：冻结的业务异常穿过生成器上下文，退出时修改回溯信息又引发 FrozenInstanceError。只把测试的错误捕获放在观察器内部、服务事务退出之后；未改业务 SQL、回滚或判定标准。

修正后目标 `3 passed, 42 subtests passed in 20.22s`；模块回归 `23 passed, 122 subtests passed in 58.92s`。子场景不另算独立测试或断言总数，这些耗时不是接口性能指标。

- 同时竞争：A连接3261创建成功，B连接3262返回stock_busy；最终六类事实各1、库存0/1，两购物车数量2/3不变。
- A提交：A3258、B3259。B实际执行NOWAIT并得到3572，在A放行之前完成返回；原Key重试库存不足，A成功Key重放原单且不新增流水。
- A回滚：A3254、B3255。B先返回繁忙；A回滚后六类事实全部0、库存1/0；B用原Key重试成功，A的Key与资格没有残留。

A事务内的真实快照为六类事实各1、库存0/1；B与观察连接在A尚未提交时只能读到已提交的空事实和库存1/0。因此“观察连接看不到记录”表示未提交隔离，而不是A尚未执行写入。

已保存 [首次失败原输出](../verification/phase-6c1-flash-sale-contention-initial-failure-output.txt)、
[目标通过原输出](../verification/phase-6c1-flash-sale-contention-target-output.txt)、
[模块回归原输出](../verification/phase-6c1-flash-sale-contention-regression-output.txt) 和
[完整验证记录](../verification/phase-6c1-flash-sale-contention.md)。后检临时测试库已清理，开发库未迁移或写入；check和迁移漂移检查通过。

本片范围是服务层、两顾客、一件库存、受控提交/回滚的 MySQL 正确性基线。
没有新增 HTTP、MQ、Redis 或依赖，没有性能测量或 MySQL 锁等待表观测。
测试人为暂停时间不是接口延迟，两个交错通过也不能推广成普遍无死锁或方案已经胜出。
既有 [Phase 6A](../verification/phase-6a-flash-sale-checkout.md) 与
[Phase 6B](../verification/phase-6b-flash-sale-lifecycle.md) 结果只作为各自范围内的历史证据。

复述练习：A 最终回滚时，为什么 B 第一次仍然收到繁忙？B 用原 Key 重试时，
又为什么可以成功，而不是被旧订单或旧资格阻挡？请分别用事务、行锁和幂等关系解释。
