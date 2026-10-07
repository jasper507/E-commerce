# Phase 6C-5：正式条件 UPDATE 与失败回滚验证

日期：2026-10-08，Asia/Shanghai。Depth / Learning Mode。
本片支持的面试回答是：条件更新如何保护库存、数据库语句失败为何还需要整单回滚、怎样用原 Key 安全重试。
停止条件已满足：最小正式替换、必要错误映射、真实隔离 MySQL 1205、回滚与重试，以及相关回归；没有追加负载矩阵。

## 1. 源码与授权范围

开始基点为 `79e2caa57606135a7a6823726138a43597631097`，前序成果已推送。
复用 `codex/flash-sale-baseline` 隔离库存事务改动，生成代码前工作分支远端已核对等于该基点。
拥有者明确选择沿用现有会话上限：本地观察为 50 秒，不增加秒杀专用配置；HTTP 接入前再确定顾客等待预算。

正式 [库存 helper](../../backend/apps/promotions/services.py) 改为按唯一 SKU、`available_stock >= 1` 条件更新，
同时扣可售、增预占；1 行继续原 GET/Reservation/Movement，0 行保持 `insufficient_stock / 409`。
仅该 UPDATE 的 1205/1213 转换为 `stock_busy / 409`，原异常保留为 cause，其他数据库错误继续上抛。
没有自动重试；错误离开原 `transaction.atomic()` 后整次下单回滚。

公开下单及指纹函数 AST（语法结构）与基点相同；User/Address、价格、Key、资格与锁序均保持。
原 NOWAIT helper 的函数体完整迁入测试，旧 NOWAIT 测试显式选用；条件测试直接调用正式实现。
三个既有测试文件共 31 个原有方法 AST 保持，只排除需要反转绑定的 `_strategy` 方法。
模型、迁移、普通下单、支付/关闭、配置、依赖及 HTTP/前端未改；历史原始证据没有重写。
负载文件的策略绑定做了静态检查，本片未实际重新执行负载矩阵。

## 2. 环境与隔离

- 已运行的本地 MySQL 8.4.10，127.0.0.1:3307；Python 3.11.9、Django 5.2.17、pytest 9.1.1。
- READ-COMMITTED；SESSION `innodb_lock_wait_timeout=50`，`innodb_rollback_on_timeout=false`，死锁检测开启。
- `DJANGO_ENV=test`、异步支付与目录缓存关闭。pytest 只创建、迁移、写入和清理临时 `test_project_one`。
- 前后测试库不存在、可见测试连接 0，开发 schema 表数均 26。没有开发库迁移或业务写入、服务启停、GRANT 修改。
- 只有实验线程复制连接 OPTIONS 设置 5/10/10 秒客户端超时；竞争线程自己的会话锁等待为 1 秒，实验结束关闭。

50 秒是本机现有会话观察，不能保证所有部署相同，也不是整次 HTTP 截止时间。
语义参考：[MySQL InnoDB 错误处理](https://dev.mysql.com/doc/refman/8.4/en/innodb-error-handling.html)。
默认锁等待超时只撤销当前语句；整次业务必须由应用事务边界保证回滚。

## 3. 实际运行结果

| 检查 | 实际结果 |
| --- | --- |
| 新失败测试 + 完整策略对照 | 9 passed、198 subtests passed，19.14 秒，首次通过 |
| promotions / ordering / payments 相关回归 | 183 passed、403 subtests passed，141.49 秒 |
| 显式实验跳过 | 7：负载 1 项、支付/到期锁实验 6 项 |
| 未选入 | 12 项真实 RabbitMQ 集成测试 |
| Django check / 迁移漂移 | 0 issues / No changes detected |

本片没有重跑真实 RabbitMQ、Redis、5D 或持续负载；既有证据仍保留当时的范围。
测试运行时长不能当作接口时延或性能提升。

## 4. 真实 1205 的证据链

三个独立连接：Observer CID 1685、Holder 1686、Contender 1687。
Holder 只锁库存、没有业务写入；Contender 执行完整正式下单。
UPDATE 开始前其事务内 Order、OrderItem、AddressSnapshot、FlashSalePurchase 各 1；Reservation、Movement 各 0。
Observer 按竞争连接 CID 在 PROCESSLIST 看到库存 UPDATE 尚未完成，Holder 未放行。

数据库实际抛 1205，记录的 UPDATE 耗时约 1.015 秒；只有一次库存 SQL 尝试。
服务返回 `stock_busy / 409`，cause 是实际数据库异常；退出 atomic、恢复 autocommit，没有待回滚事务。
Holder 仍持锁时，竞争连接与观察连接核对六类事实全 0、库存 1/0；失败 Key 未留下订单。
随后 Holder 放行，原 Key 重试创建 Order 6，再次重放返回同一 Order 6、`created=false`。
最终六类事实各 1、库存 0/1，资格 HELD/1、数量 1、活动价 89.00、预占与 RESERVE 流水各一次，购物车不变。

这证明本片受控窗口中的真实超时、完整回滚和原 Key 重试。
PROCESSLIST 不是锁索引/阻塞图证据；没有证明所有负载无死锁或生产容量。

## 5. 注入测试与正式并发回归

1213 与 2006 在库存 UPDATE 送往服务器前注入，`actual_sql_attempted=false`。
1213 验证仅已知错误转繁忙并保留 cause；2006 验证未知错误原样透传，没有伪装为库存不足或繁忙。
两者都确认此前四类事实确实存在于事务内，失败后全回滚，原 Key 重试与成功重放正常。
它们不证明真实循环死锁、网络断连或提交结果不确定时的恢复。

既有六项完整对照本片重新运行：首单提交后正式条件 UPDATE 返回 0 行；
首单回滚后等候者更新 1 行；库存 5 时同用户不同 Key 最多一单；
流水实际写入后注入异常全回滚。历史 NOWAIT 分支仍实际产生 3572。
更新 0 行包括库存记录缺失；本片继续沿用统一库存不足合同。
1205/1213 映射仅覆盖库存 UPDATE，前面的 User/Address 锁及其他 SQL 不在该映射内。

## 6. 复现与证据文件

在已配置本地隔离数据库授权的环境中设置上述 host/port、测试库、同步支付和缓存开关，密码只由本地环境提供。
目标运行时 `MYSQL_DATABASE=project_one`，实际写入由 pytest 转到 `MYSQL_TEST_DATABASE=test_project_one`；
回归时二者都为 `test_project_one`。负载、支付到期并发、MQ/到期证据写出开关均关闭。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_inventory_failures.py backend/apps/promotions/tests/test_flash_sale_inventory_comparison.py -q -s --tb=short
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests backend/apps/ordering/tests backend/apps/payments/tests -m 'not rabbitmq_integration' -q --tb=short
```

[数据与源码指纹](phase-6c5-flash-sale-inventory-failures-data.json) 保存三条新失败完整快照、六项对照摘要、环境与边界。
[目标原始输出](phase-6c5-flash-sale-inventory-failures-target-output.txt) 包含全部九条记录；
[回归原始输出](phase-6c5-flash-sale-inventory-failures-regression-output.txt) 保留实际结果。
SHA-256 统一按 UTF-8 字节将 CRLF 归一为 LF，方便跨 Git checkout 核对；旧 6C-2/3 指纹继续指向旧源码，不刷新历史数据。

正式服务层切换与本片验证完成，秒杀 HTTP、顾客等待预算、极简页面和端到端验收仍待后续切片。
学习解释见 [本片指南](../learning/phase-6c5-flash-sale-inventory-failures-study-guide.md)。
