# Phase 6B：秒杀资格与支付、关闭的同事务生命周期

日期：2026-10-07。分类：Depth / Learning；加强库存并发与秒杀正确性，同时复用已验证的支付状态与幂等规则。

## 业务问题与方案

下单时只占用资格，必须明确何时消费、何时返还。单独提交订单状态后再更新购买资格，会留下“订单已关闭但资格仍占用”或“订单已支付但资格仍是HELD”的跨表不一致；如果反过来先释放资格再关闭订单，则旧预占尚未释放就可能允许新单。本片把资格转换放入原有 MySQL 业务事务，与订单、PaymentAttempt、库存预留和流水一起提交。相比提交后补写，事务内方案增加一把本订单 Purchase 记录锁，但无需新增补偿任务；代价是锁持有到业务事务结束。

| 情况 | Order | Purchase / blocking_marker | 库存与资格 |
| --- | --- | --- | --- |
| 秒杀下单 | PENDING_PAYMENT | HELD / 1 | 可售5→4，预留0→1；占用资格 |
| 正常支付成功 | PAID | CONSUMED / 1 | 预留1→0；消费资格，禁止新Key重购 |
| 真正未付取消/到期 | CANCELED / EXPIRED | RELEASED / NULL | 可售4→5，预留1→0；允许新Key重购 |
| 已付取消 | CANCELED | CONSUMED / 1 | 库存恢复并退款模拟；资格不返还 |
| 未付关闭O1的迟到成功 | 保持关闭 | O1保持RELEASED / NULL | 仅O1退款；不核销库存，不改变O2资格 |

CREATED/PENDING/UNKNOWN 的 Attempt 仍属于未确认支付，取消和到期必须暂缓。旧下单Key始终返回原订单，释放资格不改变幂等关系。

## 实现边界

- `promotions/lifecycle_services.py` 只按 `order` 锁定并核对本单购买记录，不按顾客查找当前活动名额。普通购物车订单直接复用原路径。
- 正常支付锁序为 Event → Order → Attempt → Purchase → Reservation → Inventory；取消/到期持有 Order 后核对 Attempt，再处理 Purchase 和库存。
- 正常成功时 HELD→CONSUMED；真正未付关闭时 HELD→RELEASED；已付取消只验证 CONSUMED。
- 支付 Inbox 的领取次数与失败记录仍使用既有独立事务。业务异常回滚订单、Attempt、库存与Purchase；事件的 ERROR、预算与调查信息保留。资格事实冲突记为 BLOCKED，不自动释放或盲目重试。
- 已关闭订单迟到成功分支仍先退款并返回，不调用正常资格消费分支。原 Attempt 的唯一退款关系防止重复效果。
- 移除6A临时支付/关闭保护。6A原7项下单测试及fixture方法保持原AST，删除第8项“生命周期未开放”的旧断言，新增11项生命周期测试。
- 没有新增秒杀下单HTTP接口、前端、依赖、迁移、MQ配置或后台调度。原有支付/取消入口的服务现在支持秒杀订单。

## 环境与执行保护

Python 3.11.9、Django 5.2.17、MySQL 8.4.10；已有Docker db容器健康，宿主127.0.0.1:3307。运行前只读确认既定 `test_project_one` 不存在、无其他可见测试连接、应用用户已有该隔离库权限。没有启动/停止服务或扩展GRANT，未发现pytest/支付Worker。

测试显式设置 `DJANGO_ENV=test`、`MYSQL_USER=project_one`、`MYSQL_TEST_DATABASE=test_project_one`、`PAYMENT_EVENTS_ASYNC=false`、`CATALOG_CACHE_ENABLED=false`。pytest建立并迁移临时库、执行夹具，再清理。目标运行的 `MYSQL_DATABASE=project_one` 仅为Django测试入口；实际测试连接由test runner切到test_project_one。回归入口显式使用test_project_one。原5D实验及旧证据写入开关关闭。

## 本轮实际结果

目标命令：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_lifecycle.py -q -s --tb=short
```

结果：**11 passed、13 subtests passed，23.26秒**。

- 正常成功、同事件/不同事件重放、旧Key返回原订单和新Key限购通过。最终 PAID / CONSUMED / 1，库存4/0，COMMIT仅1条。
- 已付取消恢复库存5/0、RESTORE仅1条、退款仅1次，资格仍CONSUMED/1，重购被阻止。
- 未付取消、到期和批量扫描释放到RELEASED/NULL，重复关闭不重复释放。三种未确认Attempt的cancel/expire/scan均未释放资格。
- 取消与到期各验证一次 O1关闭→O2重购→旧Key→O1迟到成功→同/不同事件重放。O1仍关闭、RELEASED/NULL、退款1次、COMMIT0；O2仍待支付、HELD/1、RESERVED，库存4/1不变。
- Purchase实际save完成后注入异常：支付业务全部回滚，Event保留ERROR/TRANSIENT/次数1、5秒重试门槛；未到期不重试，到期原事件次数2、仅一次核销。取消/到期实写后异常均回滚，重试仅一次释放。**1205错误是测试注入，并非本轮真实锁超时或死锁观测。**
- 合法数据库字段组合但业务不一致的资格夹具，被支付识别为 ERROR/BLOCKED/`flash_sale_purchase_invariant_error`，次数1且无自动重试；库存不动。关闭路径为单独到达资格校验，将测试Attempt置FAILED/NULL后验证同样拒绝释放；这属于故障夹具，不是业务操作。
- 两独立MySQL连接571（expiry）/572（payment）用Barrier同时放行。本轮到期返回 `payment_unresolved`，支付完成；最终 PAID/CONSUMED/1、库存4/0、COMMIT1、RELEASE0。**没有采集MySQL锁等待，未控制或验证两种取锁顺序，不能据此宣称无死锁或性能胜出。**

回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests backend/apps/ordering/tests backend/apps/payments/tests -m "not rabbitmq_integration and not redis_integration" -q --tb=short
```

结果：**171 passed、6 skipped、12 deselected、163 subtests passed，149.47秒**。6项跳过为5D显式锁观察实验，12项排除为真实RabbitMQ集成。本轮未重验旧MQ/Redis/5D证据。子场景数不是独立测试数或断言总数，测试耗时不是接口延迟/吞吐。

后检：test_project_one已清理、其他可见测试连接0，开发schema表数前后26；`manage.py check` 为0 issues、`makemigrations --check --dry-run` 为No changes detected。后检迁移历史校验只读使用既有project_one，未运行migrate。

## 证据与限制

- [目标原始输出](phase-6b-flash-sale-lifecycle-target-output.txt) 保留17条实际ORM读取快照；每条记录来自测试，而不是手写预期值。Event业务回滚后关联为空时，以记录外层event字段核对失败事实。
- [回归原始输出](phase-6b-flash-sale-lifecycle-regression-output.txt) 与 [JSON证据](phase-6b-flash-sale-lifecycle-data.json) 保存结果、环境、后检和归一化源码/输出指纹。源码基线为 `8d1bfed`，实际测试对象为本片增量，以最终提交和源码指纹定位。两轮测试均首次通过，无失败运行被丢弃。
- 本轮只验证服务与真实隔离MySQL。不含秒杀HTTP/前端验收、现有开发数据升级、逆向迁移、真实MQ/Redis重跑、锁等待实验或压测。
- 6A历史证据与其源码指纹保持原样；当前checkout测试数量由8变7，旧结果10项不能冒称当前测试集计数。

学习说明见 [Phase 6B指南](../learning/phase-6b-flash-sale-lifecycle-study-guide.md)。
