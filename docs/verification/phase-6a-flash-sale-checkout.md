# Phase 6A：秒杀下单基线的实现与真实 MySQL 验证

初始记录：2026-10-06；最新验收：2026-10-07。状态：服务层目标与相关回归通过，资格生命周期未接入。

## 业务问题与判定标准

服务库存并发/秒杀主线。库存充足时，同Customer+FlashSaleItem不同Key并发，
最多一张待支付订单；数量1、活动价由服务端生成快照，名额、订单与库存共同提交。
用户在本聊天接受上述规则并明确要求开始最小测试与下单实现。

## 2026-10-06：代码与方案

- Git基点2dacc9a，当前分支codex/flash-sale-baseline；分支从有本地C4/5D和Wiki材料的checkout创建。
  既有修改保留，没有commit/push或额外worktree。Git diff包含此前修改，不能全算成本片改动。
- 新增promotions活动/活动item/购买记录模型与服务、两组目标测试和迁移。
- Order新增creation_scope；普通下单限定cart_checkout，秒杀为flash_sale_checkout。
  唯一键为(user, scope, key)，旧订单默认普通范围；输入摘要区分活动item与地址。
- Purchase使用普通可空联合唯一键，严格检查状态与marker对应；当前服务仅创建HELD。
- 现有Order/快照/Inventory/Reservation/Movement复用；库存仍为正式NOWAIT基线。
- 支付创建/事件、取消、到期入口保护未接生命周期的秒杀订单。没有新HTTP/前端、Redis Lua或秒杀MQ。

替代方案与学习流程见[指南](../learning/phase-6a-flash-sale-checkout-study-guide.md)。

## 2026-10-07：真实 MySQL 验收

测试源码版本为 `be2b4f6`，本轮未修改业务代码或测试。环境在本轮开始时已恢复：
Windows、Python3.11.9、Django5.2.17、MySQL8.4.10，127.0.0.1:3307。
只读核对应用账号的既定test_project_one权限；测试库不存在、无可见测试连接，
没有运行中的pytest/支付Worker。没有启动/停止服务、增加授权或改用3306。

| 检查 | 实际结果 |
| --- | --- |
| promotions目标 | **10 passed、79 subtests passed，15.63秒** |
| ordering/payments相关回归 | **151 passed、83 subtests passed，100.58秒** |
| 回归未运行部分 | 6项5D显式锁等待实验skip；12项真实RabbitMQ集成deselected |
| 测试库后检 | test_project_one已清理，无测试连接；开发schema表数前后均26 |
| 静态检查 | manage.py check为0 issues；迁移漂移No changes detected |

Subtest是测试内分别记录的小场景；不把它们重复计为独立测试或SQL断言数量。
[目标原始输出](phase-6a-flash-sale-checkout-target-output.txt)、
[回归原始输出](phase-6a-flash-sale-checkout-regression-output.txt)和
[结构化证据与源码指纹](phase-6a-flash-sale-checkout-data.json)保留本轮结果。
指纹以UTF-8文件内容将CRLF统一为LF后计算，便于跨平台和Git checkout核对。

并发原始输出的连接编号为59、60：Key b创建订单25，Key a被flash_sale_limit_reached/409拒绝。
成功测试的后检断言确认Order、OrderItem、AddressSnapshot、Reservation、Movement、Purchase各1；
数量1、活动价89.00、普通SKU价129.00与购物车2件不变，库存5/0→4/1，购买记录HELD/1。
这些终值由真实数据库上的测试断言验证；结构化文件没有伪装成测试结束后的独立SQL快照。
另外覆盖同Key重放、异参数冲突、跨scope、地址归属、库存不足、写后回滚、入口保护和直接唯一/CHECK。

pytest创建新test_project_one并执行迁移，完成后删除它；没有执行开发库migrate。
目标命令见指南，回归为：

```text
python -m pytest backend/apps/ordering/tests backend/apps/payments/tests -m "not rabbitmq_integration and not redis_integration" -q --tb=short
```

使用进程级test环境、Host127.0.0.1/Port3307、MYSQL_TEST_DATABASE=test_project_one，
关闭目录缓存和支付异步；回归额外关闭5D实验及旧证据写入。目标MYSQL_DATABASE为project_one，
pytest实际切换至test_project_one；回归将MYSQL_DATABASE也显式设为test_project_one。
没有把环境变量写入.env。

边界：这是服务层二连接竞购，没有观测User记录锁等待、无死锁或性能证据；没有秒杀HTTP/前端验收。
没有接入资格核销/释放，不能宣称O1关闭后O2重购或迟到事件保护已完成。
本轮未重跑真实MQ/Redis/5D；新建测试库迁移不等于已有开发数据升级或逆向回退验证。
下一片先一起接正常支付核销与真正未付关闭释放，再验证重购、旧Key和旧事件归属。

## 2026-10-06：实现与静态检查（历史）

- `docker compose ps --format json`：Docker Linux Engine命名管道不存在，不能连接。
- 只读TCP检查：127.0.0.1:3307不可连接；3306可连接。没有改用3306、尝试凭据或启动/停止服务。
- `manage.py makemigrations ordering promotions --noinput`：生成两份迁移文件，未执行migrate；
  一致性历史读取因3307不可连接产生warning。
- `manage.py check`：0 issues。
- `manage.py makemigrations --check --dry-run`：No changes detected，同时保留上述数据库历史warning。
- pytest目标收集：10 tests collected in 0.06s（8项下单合约、2项直接模型约束），没有执行断言。
  收集成功只表示测试可发现，不表示业务断言已执行或并发正确。
- 新增与相关订单/支付测试合并收集：179 tests collected in 1.65s，未运行回归断言。
- 15份本片相关Python文件通过AST语法解析；两份文档的4个本地链接存在。
- 不连接数据库的迁移图检查通过，promotions/0001依赖ordering/0004_order_creation_scope。
  这只验证依赖图，不验证MySQL实际DDL或迁移执行。
- `git diff --check`通过；另有仓库LF/CRLF转换提示，没有空白错误。
- 独立只读复核未发现阻断问题；它不能替代真实数据库测试。

## 2026-10-06：目标与未验证范围（历史）

目标：库存5/0，两独立MySQL连接、同用户不同Key，一次创建一次限购拒绝，
Order/OrderItem/AddressSnapshot/Reservation/Movement/Purchase各1，库存4/1；
补幂等、跨scope、归属、写后回滚及服务入口保护；直接模型测试检查唯一/CHECK。

本轮没有创建、迁移、销毁测试数据库，没有MySQL目标/回归passed结果，没有性能数字或原始并发JSON。
因此不能声称限购、存储约束、迁移运行或普通订单回归已由真实数据库验证。
没有运行真实MQ，旧C4/5D证据不作为本片新验证。

后续：环境恢复后按指南运行隔离目标测试与相关订单/支付回归；
再逐片接正常支付资格核销、未付合法关闭释放及迟到事件保护。
本片不支持秒杀订单支付/取消/过期，不能宣称秒杀业务闭环完成。
新迁移默认scope保留旧普通订单语义；已有跨scope同Key数据时，逆向恢复旧唯一键会冲突。
没有更改开发数据、前端、依赖、部署配置、Redis实现、MQ任务/恢复预算或发布飞书。
