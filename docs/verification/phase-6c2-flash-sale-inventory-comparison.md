# Phase 6C-2：同一完整秒杀事务的 NOWAIT / 条件 UPDATE 对照

日期：2026-10-07。Depth / Learning；强化库存并发与秒杀正确性。

## 问题、方案与代价

库存1件时，NOWAIT在别人持锁期间拒绝本次请求，首单即使回滚也需要顾客重试。条件UPDATE候选可能保留正在进行的请求，在首单结束后检查库存并更新。本片在相同完整订单事务里比较这两个行为，守住最多一单提交、失败无残留与每顾客资格限制。

最小方案是从正式秒杀服务提取 `_reserve_flash_sale_inventory(sku)`，默认仍执行原NOWAIT/check/save；公开参数与返回契约不变。候选仅在测试 `_conditional_reserve_inventory()` 使用 `available_stock >= 1` 与F表达式执行可售-1/预留+1，以更新行数0/1判定；测试期间只patch这一步，A/B同用一个策略，继续走原Order/快照/Item/Purchase/Reservation/Movement代码。

与复制完整checkout或新增公开策略开关相比，共用这一处私有库存步骤可保持其余业务条件一致，不增加运行配置。代价是候选仍只能由测试选用，不能据此声称已经正式采用。候选UPDATE后还要读取Inventory、建立预占与流水，完整下单并非一条SQL。等待期间数据库连接、事务与已经取得的User/Address/Purchase锁仍保留，其资源成本尚未测量。

## 实现与判定标准

- 正式服务只提取原库存步骤；将helper内联的AST与基点8465d2a的原checkout相同，NOWAIT/错误处理/save字段未改变。
- 6C-1仅把建数、快照和断言划为FlashSaleContentionFixture，原14个方法body/signature AST不变。新比较测试继承无test_方法的fixture，collect数量29，没有重复继承原3项实验。
- 条件候选仅在新test文件。UPDATE 0行必须抛OrderRuleError退出服务atomic，回滚此前Order/Purchase；1行后继续原预占/流水。没有提前提交库存或改变Purchase→Inventory顺序。
- 4项受控对照：两策略 × A提交/回滚。A在外层atomic调用真实完整服务后暂停，自己能读六类事实各1、库存0/1；B和观察连接只能见已提交事实0、库存1/0。
- NOWAIT：真实库存SELECT FOR UPDATE NOWAIT得到3572，B在release前完成。A提交后B原Key库存不足；A回滚后B原Key重试成功。
- 候选：先看到B真实服务端Inventory UPDATE且尚未结束，再release。A提交后0行+库存不足、B全回滚；A回滚后1行，B第一次等待请求直接创建。成功Key重放返回原单，失败/回滚顾客无Key映射或资格残留。
- 候选同顾客库存5不同Key竞争：只一单成功，另一单限购409且没有发库存UPDATE；库存4/1、两cart2/3不变。
- 候选实际Movement写完后注入异常：确认六类事实和库存改变已写，再向外抛错；全部回滚到0事实/库存1/0。撤除故障后同Key重试与重放，最终六事实各1、预占/流水一次。

## 环境与观察范围

Python3.11.9、Django5.2.17、pytest9.1.1、MySQL8.4.10；Windows/AMD64/24逻辑CPU。已有MySQL容器127.0.0.1:3307健康，Docker未配置单容器CPU/内存限额（0），不代表宿主/VM资源无限。

只读预检test_project_one不存在、无可见测试连接、应用已有隔离库授权，未发现pytest/支付Worker。没有启停服务、增加GRANT、开发migrate或业务写入。目标入口MYSQL_DATABASE=project_one，pytest实际连接其创建/迁移的test_project_one再清理；回归入口明确test_project_one。DJANGO_ENV=test，支付异步和catalog缓存关闭，旧实验/证据写入开关关闭。

应用账号读取performance_schema.data_lock_waits能力检查返回1142，本片没有扩大权限或切换特权观察连接。只读information_schema.processlist，按B ID及当前test库筛选，保存COMMAND=Query、实际库存UPDATE和原STATE。A尚未release，B未done且库存语句未completed，然后才放行A。**这不是RECORD WAITING、锁索引或B→A阻塞图证据；STATE=updating本身也不是锁类型结论。**

每场景fresh fixture运行一次，4项受控交错固定；同顾客并发赢家不预设，无随机数据、预热或持续压测。超时5秒和采证deadline3秒是实验失败界限，不是性能指标；1205不能伪装成NOWAIT的stock_busy。线程finally放行、join、close连接，所有候选patch覆盖线程整个生命周期。

## 本轮实际目标结果

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_inventory_comparison.py -q -s --tb=short
```

**6 passed、138 subtests passed，27.16秒**，首轮通过，没有隐藏失败运行。

| 算法 / A结局 | 实际持锁窗口 | A结束后B结果 | 原Key与终值 |
| --- | --- | --- | --- |
| NOWAIT / 提交 | A3522/B3523/观察3521；真实3572，B在release前stock_busy | B原Key重试insufficient_stock | 原单9重放created=False；六事实归A、库存0/1 |
| NOWAIT / 回滚 | A3526/B3527/观察3525；真实3572，B在release前stock_busy | 回滚后0事实/库存1/0；B重试创建Order14 | A无残留，六事实归B、库存0/1 |
| UPDATE / 提交 | A3512/B3513/观察3511；Query/UPDATE/updating，B未完成且A未release | 修改0行，B insufficient_stock，无残留 | 原Key重试仍不足；Order3重放幂等，库存0/1 |
| UPDATE / 回滚 | A3515/B3516/观察3514；Query/UPDATE/updating，B未完成且A未release | 修改1行，B原请求创建Order7，不需第二次请求 | A无残留，六事实归B、库存0/1 |

候选同用户连接3519/3520（观察3518）一笔创建Order8、一笔flash_sale_limit_reached，失败者库存SQL0次；库存4/1。故障场景真实UPDATE和Movement完成后异常回滚，六事实0/库存1/0；同Key重试Order2成功，两次有效UPDATE各影响1行，但回滚的那次没有保留任何预占或流水；成功重放无再UPDATE。

相关模块回归：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests -q --tb=short
```

**29 passed、260 subtests passed，74.04秒**。覆盖全部promotions模型、6A下单、6B资格生命周期、6C-1竞争和本次比较。目标与回归均首次通过。正式改动仅私有函数提取/fixture分层，按范围选择模块回归；未重跑完整ordering/payments或真实MQ/Redis/5D。

后检临时test_project_one已清理、可见测试连接0、开发schema表数前后26；check为0 issues，迁移漂移No changes detected。迁移历史只读使用既有project_one，未运行migrate。子场景数不另算独立测试或断言总数，测试耗时不是接口延迟或吞吐。

## 证据与下一步

[目标原输出](phase-6c2-flash-sale-inventory-comparison-target-output.txt)、[模块回归原输出](phase-6c2-flash-sale-inventory-comparison-regression-output.txt)与[JSON证据](phase-6c2-flash-sale-inventory-comparison-data.json)保存6条实际ORM记录、服务端SQL/连接/更新行数、未提交及最终快照、源码和输出指纹。更新行数与库存表终值之外，仍核对六类关系、顾客归属、quantity1/activity89/ordinary129、HELD1/RESERVED/RESERVE(-1,+1)及cart2/3不变。

本片支持隔离MySQL下这些服务规则与受控等待行为；未采锁等待图，没有性能排名、无死锁普遍证明、实际HTTP网络、前端、现有开发数据升级、逆向迁移或新MQ/Redis故障验证。Phase3与6A/B/C1历史输出不当成本轮重跑。

正式入口继续默认NOWAIT。下一片在同环境/完整事务下建立可复现负载，测成功/忙/不足、重试成本、延迟分布、连接占用及死锁，再依据证据决定是否采用候选并记录ADR（架构决定记录）。不因6项正确性通过就宣称更快。

教学说明见 [Phase 6C-2指南](../learning/phase-6c2-flash-sale-inventory-comparison-study-guide.md)。
