# Phase 6A：秒杀下单与活动限购的第一片

初始日期：2026-10-06；真实验收：2026-10-07。Depth / Learning Mode。
本片真实MySQL目标10项、相关订单/支付回归151项通过；它是服务层基线，没有秒杀 HTTP 或前端入口。

## 本片前后变化

- **开始前：** 普通订单已能用同一个 Key 重放，但同一顾客换 Key 购买，不受活动限购规则约束。
- **本片改变：** 增加活动商品、每单购买资格及秒杀幂等范围；一人一单、活动价快照与库存预占在同一事务中提交。
- **完成后与边界：** 本片结束时能从服务创建秒杀订单，库存仍用 NOWAIT；资格消费和释放尚未接入，所以暂时保护支付、取消和到期入口，没有 HTTP 或页面。

**后续衔接：** [6B 接通支付与资格生命周期](phase-6b-flash-sale-lifecycle-study-guide.md)。后续正式库存策略见 [6C-5](phase-6c5-flash-sale-inventory-failures-study-guide.md)。[学习导航](README.md)

## 本片解决什么

同一个顾客对同一个活动商品，用两个不同的 Idempotency-Key（识别一次请求的重试）同时购买。
即使库存足够满足两次购买，也只允许一张待支付订单占住资格。
普通下单只保护同 Key 重放；活动限购还要保护不同 Key 的重复购买。

范围：一个活动商品、每次固定一件、单仓库已有 Inventory、订单及地址/商品快照。
沿用正式库存行锁 NOWAIT，不替换成条件 UPDATE；Phase 3 对照只引用已有证据。
不新增 Redis Lua、分布式锁或秒杀 MQ。

## 数据如何表示

- FlashSale：活动开放时间与开关；开始时间包含，结束时间不包含。
- FlashSaleItem：活动与 SKU 的关联及活动价格；核心库存仍在原 Inventory。
- FlashSalePurchase：每张订单一条购买记录，保留该用户与活动商品的历史。
- Order.creation_scope：普通购物车和秒杀服务分别识别自己的幂等键；旧订单默认 cart_checkout。

| 购买记录状态 | blocking_marker | 本活动是否还能重新购买 |
| --- | --- | --- |
| HELD | 1 | 不可以，待支付订单占用资格 |
| CONSUMED | 1 | 不可以，已经正常购买成功 |
| RELEASED | NULL | 若无其他阻挡记录且活动、库存允许，可以 |

唯一约束为 `(customer, flash_sale_item, blocking_marker)`。MySQL 允许多条 NULL 历史，
但同一顾客和活动商品只允许一条标记1。状态 CHECK 明确要求 HELD/CONSUMED 非空且等于1，
RELEASED 为 NULL，不能让 SQL 的 NULL/UNKNOWN 判断绕过检查。
本片实际服务只创建 HELD，另外两种转换尚未接入业务。

有意义的替代方案：每用户/活动一条永久资格记录，保存状态和当前订单。
它读取当前资格更直接，但重购会切换订单指针，旧事件必须额外核对归属。
当前每单一条购买记录沿用 PaymentAttempt 的可空唯一键思路，保留历史关联；代价是维护状态和标记对应。

## 请求如何运行

当前入口是 `create_flash_sale_order()`，不是 HTTP。
未来 API 仍需补 Session/CSRF、请求字段与错误响应契约，不能把服务测试当成 API 验收。

```text
调用秒杀服务，输入 user、flash_sale_item_id、address_id、idempotency_key
→ 由 transaction.atomic 开始 MySQL 原子事务
→ 检查幂等键格式
→ 锁该 User
→ 在秒杀 scope 内检查旧 Key；同参数返回原订单，异参数冲突
→ 核对地址属于当前用户
→ 从 MySQL 读取活动、SKU、可售状态和活动价格
→ 检查活动名额
→ 创建 Order、AddressSnapshot、数量1的 OrderItem
→ 插入 HELD/1 购买记录，由唯一约束兜底
→ 取得 Inventory NOWAIT 行锁，检查可售库存
→ available减1、reserved加1，创建 Reservation 和 RESERVE Movement
→ 一起提交，返回 (order, True)
```

活动价89.00、普通SKU价129.00、库存5/0时，秒杀成功应为订单89.00、数量1、库存4/1。
普通购物车中已有2件仍保持原样。秒杀不把整车商品带入订单。
活动和目录信息采用本次读取观察到的快照，不冻结管理员修改到事务提交；没有严格下架互斥的证据。

User锁让同用户的创建请求串行；唯一约束继续提供存储层防线。
名额插入先于库存锁，避免未来关闭流程与创建出现购买记录/库存的反向取锁。
唯一索引插入和User锁本身可能等待；NOWAIT不能证明整个请求无等待，也不证明完全消除死锁。
只把指定库存NOWAIT错误和指定购买名额唯一冲突转为业务拒绝，其余数据库异常原样抛出并回滚。

## 为什么暂时保护支付与关闭入口

完整规则已讨论：未付订单真正关闭后释放资格；正常购买成功后用掉资格；成功后取消不自动恢复资格。
这些转换还未实现。已有支付创建、事件处理、取消和到期服务因此明确保护秒杀scope，
避免产生库存已变而购买记录仍 HELD 的半完成事实；批量到期扫描只处理普通购物车scope。
秒杀实验订单尚不能支付、取消或到期关闭，不应通过服务往开发数据库投放此类订单。
下一片要一起接入正常支付核销与真正未付关闭释放，再验证 O1关闭后O2重购、旧事件不动O2。

## 测试怎样判断正确

[目标测试](../../backend/apps/promotions/tests/test_flash_sale_checkout.py) 包含：

1. 库存5、同用户不同Key、两条真实连接并发：一次创建，一次 flash_sale_limit_reached；
   六类交易记录各1，库存4/1，数量1，活动价89，购物车不变。
2. 同Key同参数只重放；异地址冲突；普通/秒杀同Key创建及重放各自的订单。
3. 他人地址拒绝、库存0不残留；真实写入流水后注入异常，六类事实一起回滚，原Key可重试。
4. 现有支付/取消/到期入口对未接生命周期的秒杀订单保持保护。

[模型测试](../../backend/apps/promotions/tests/test_flash_sale_models.py) 绕过User锁直接写模型，
独立检查 HELD/CONSUMED共同限购、多条 RELEASED/NULL 历史和非法状态标记组合。
只检查服务并发而不直接检查数据库约束，不能独立证明唯一约束实际生效。

## 怎样复现

2026-10-06曾因Docker/3307不可达而只做静态检查；2026-10-07环境已恢复，MySQL8.4.10健康。
本轮运行前确认既定test_project_one授权、测试库不存在且无共享pytest/Worker；没有增加权限。
后续复现仍需先确认环境和共享测试库隔离，再运行测试。
不运行开发库 migrate，也不执行任何停止服务或清理队列命令。

下面命令会创建、迁移并清理 `test_project_one`，应仅在上述隔离条件确认后执行：

```powershell
$env:DJANGO_ENV = 'test'
$env:MYSQL_HOST = '127.0.0.1'
$env:MYSQL_PORT = '3307'
$env:MYSQL_TEST_DATABASE = 'test_project_one'
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests -q -s --tb=short
```

环境变量仅在当前终端会话使用，不写 `.env`；完成后按原终端环境恢复。
目标并发输出 `FLASH_SALE_CHECKOUT_RACE` 应有两个不同连接编号、一笔创建和一笔限购拒绝。
成功断言还必须检查订单、快照、购买记录、库存及流水关系。测试耗时不是吞吐量或响应时延承诺。
完成后再做现有订单/支付相关回归，保留原始输出；没有实际执行就不写 passed。

## 迁移与回退边界

两份新增迁移已在本轮新建隔离测试库中执行；没有在开发库执行。
这不证明已有开发数据升级或逆向回退已验证。
ordering/0004先加默认scope和新唯一约束，再移除旧唯一约束。
如果以后同用户在不同scope形成同Key订单，逆向恢复旧 `(user, key)` 唯一约束会冲突；
不能承诺直接逆向migrate即可回退，不自动改写或删除这些历史。

[本轮检查与限制](../verification/phase-6a-flash-sale-checkout.md)。

## 学习者自己的解释

沿源码说清：为何库存要设5；Key为何不能替代限购；User锁与唯一约束各自保护什么；
异常后哪些事实必须一起消失；为何暂不接支付；怎样才算真实MySQL并发证据。
预测题：去掉购买记录唯一约束、但保留User锁，服务并发测试是否仍可能通过？
这也是增加直接模型约束测试的原因。
