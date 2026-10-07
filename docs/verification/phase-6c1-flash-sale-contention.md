# Phase 6C-1：完整秒杀下单的两顾客库存竞争基线

日期：2026-10-07。Depth / Learning；服务于库存并发与秒杀正确性。

## 本片问题与选择

库存只有一件，两个不同顾客购买同一活动商品。每顾客限购约束不能保护全体共享库存；必须验证真实下单服务，而不是只手工锁库存或复制算法。本片保留正式 NOWAIT 行锁流程，补同时竞争与首单提交/回滚两个受控交错，为完整事务的后续原子条件更新对照提供基线。

最小不变量：最多一张订单成功提交；成功单的Order、OrderItem、AddressSnapshot、FlashSalePurchase、InventoryReservation、InventoryMovement六类事实一一对应；被拒绝或回滚请求不留订单Key或资格；数量1、活动价89.00、普通SKU价129.00、最终库存0/1；两人购物车数量2/3保持不变。

替代候选是库存足够时一次UPDATE扣减并检查行数。它仍需写锁，可能等待首单提交/回滚后再决定结果；当前NOWAIT可提前拒绝，但首单后来回滚时，已被拒绝的顾客必须重试。本片没有改变正式算法或预判方案胜负。

## 实现与实验方式

仅新增 `backend/apps/promotions/tests/test_flash_sale_contention.py`，继承6A fixture建数，不修改旧fixture/tests或业务代码。

- 两个不同顾客各用独立MySQL连接，Barrier同时放行，调用真实 `create_flash_sale_order()`。允许失败结果为stock_busy或insufficient_stock，但必须恰好一单创建成功。
- 受控场景中，A在测试外层atomic调用完整服务。服务返回后仍未提交外层事务：A能读到自己写入的六类事实及库存0/1，再设置ready，等待release。
- B通过相同服务。execute_wrapper只观察真实库存SQL和原MySQL错误，不替换执行结果。记录 `FOR UPDATE NOWAIT`、3572、B返回时release尚未设置。
- B的服务atomic抛错退出后，其订单/资格写入已回滚。B和第三观察连接只看到已提交视图：此时六类事实0、库存1/0。**这不表示A没有写入**；A事务内的快照仍是六类事实各1、库存0/1。
- 等B返回、关闭连接并核对后，才允许A提交或注入异常回滚。检查结束后同Key重试与成功Key重放，以及全局/顾客归属事实。
- finally放行持锁线程、join并核对线程结束、close_all。超时仅用于实验失败和清理，不用于响应时间保证。

## 运行保护与环境

Python3.11.9、Django5.2.17、pytest9.1.1、MySQL8.4.10；Windows、AMD64、24逻辑CPU。MySQL镜像mysql:8.4.10，Docker配置未设置独立CPU/内存限额（0），不代表宿主或虚拟机资源无限。

已有db容器健康，127.0.0.1:3307。只读预检确认test_project_one不存在、无其他可见测试连接、应用用户已有隔离库权限；未发现pytest/支付Worker。没有启停服务或扩大GRANT。

运行显式使用DJANGO_ENV=test、MYSQL_TEST_DATABASE=test_project_one，关闭支付异步与catalog缓存。目标入口MYSQL_DATABASE=project_one；pytest实际建立、迁移并连接test_project_one，结束后清理。回归入口MYSQL_DATABASE=test_project_one。未对开发库运行migrate或业务写入。

三个场景各在fresh fixture运行一次；无预热、持续压测或性能统计。固定测试数据、不使用随机数；Barrier竞争赢家由调度决定，不预设特定顾客胜出。代码基点f713d03；精确新测试/现有源码指纹见JSON。

## 首轮失败及最小修正

目标首次结果为 **2 failed、1 passed、18 subtests passed，18.65秒**。同时竞争场景通过；两个带SQL观察器的场景收到 `FrozenInstanceError: cannot assign to field __traceback__`。

根因：项目 `OrderRuleError` 为frozen dataclass；Django execute_wrapper使用Python生成器上下文管理器。当stock_busy异常穿过该观察器退出时，contextlib试图赋值异常的__traceback__，被冻结对象拒绝，掩盖了原业务错误。无数据库最小复现也确认FrozenInstanceError的原__context__为OrderRuleError。

修正仅调整测试：在观察器内部、真实服务atomic已经抛错退出后捕获并记录OrderRuleError。没有在业务事务内吞异常，没有修改冻结异常定义、SQL、库存算法或判定标准。真实3572仍由观察器记录。首轮失败输出与修正前测试源码指纹均保留，未删除失败运行。

## 实际目标结果

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_contention.py -q -s --tb=short
```

修正后：**3 passed、42 subtests passed，20.22秒**。

| 场景 | 实际连接及返回 | 重试/终值 |
| --- | --- | --- |
| 同时抢库存1 | A连接3261成功，B连接3262返回stock_busy；观察连接3260 | 六类事实各1且归属A，资格HELD/1，库存0/1 |
| A未提交后提交 | A3258、B3259、观察3256；B在release前返回stock_busy；SQL实际NOWAIT/3572 | B原Key重试insufficient_stock；A原Key返回原Order4、created=False；六类事实各1 |
| A未提交后回滚 | A3254、B3255、观察3250；B在release前返回stock_busy；SQL实际NOWAIT/3572 | A回滚后全部0、库存1/0；B原Key重试创建Order3，A原Key无订单映射或资格；最终六类事实归B、库存0/1 |

订单ID可能有间断，因为事务回滚不保证回收自增编号；不能用ID是否连续判断回滚是否成功。

相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests -q --tb=short
```

结果为 **23 passed、122 subtests passed，58.92秒**，覆盖promotions全部模型、6A下单、6B资格生命周期与本片竞争测试。现有业务代码、旧测试、迁移、配置均未改，本轮按范围选择模块回归，未重跑完整ordering/payments或真实MQ/Redis/5D实验。

后检test_project_one不存在、可见测试连接0、开发schema表数前后26；Django check为0 issues，迁移漂移No changes detected。迁移历史只读使用project_one，未运行migrate。子场景数不是独立测试数或断言总数，运行耗时不是接口延迟或吞吐。

## 证据与限制

[首次失败输出](phase-6c1-flash-sale-contention-initial-failure-output.txt)、[通过目标输出](phase-6c1-flash-sale-contention-target-output.txt)、[模块回归输出](phase-6c1-flash-sale-contention-regression-output.txt)、[JSON证据](phase-6c1-flash-sale-contention-data.json)保存所有运行。目标输出的3条FLASH_SALE_CONTENTION记录来自实际ORM读取，包含A未提交、B退出后、观察连接释放前/后以及最终快照。

已观察真实库存NOWAIT SQL和3572，未读取MySQL锁等待表。只支持本机隔离MySQL下三个受控服务场景，不证明所有交错、普遍无死锁、生产承载或性能优劣。不含HTTP网络/前端、MQ/Redis、开发数据升级或逆向迁移。

Phase3已有 [naive失败基线](phase-3-inventory-race-baseline.md)、[库存SQL策略比较](phase-3-inventory-strategy-comparison.md)与[普通完整下单竞争](phase-3-checkout-contention.md)。本片引用历史证据，不冒称首次复现或本轮重跑。下一片把条件更新候选放入相同完整事务对照；负载、延迟/吞吐及最终ADR仍未完成。

学习说明见 [Phase 6C-1指南](../learning/phase-6c1-flash-sale-contention-study-guide.md)。
