# Phase 6D-2：HTTP 临时5秒等待与连接恢复

日期：2026-10-08，Asia/Shanghai。项目拥有者明确选择“临时5秒并恢复原会话值”。
本片完成HTTP等待适配；第一片接口的JSON和原始输出保留为历史证据，不改写当时待答复的状态。
面试目标是独立解释数据库等待、整单回滚、请求幂等和持久连接恢复的不同边界。
适配属于Supporting/Pair，等待/事务失败属于Depth/Learning；停止于必要失败路径与相关回归，不追加负载矩阵。

## 1. 基点与最小改变

开始前现有codex/flash-sale-baseline及其远端均为`72e6291322171fb5dfbd65c345587b7c200e9c3e`，工作区干净；
main/远端main仍是`b4456bd1d498c7fe370e8a793f63e11ffa5f4fae`。前批已保存，无空提交、新分支或工作树。
本片复用现有隔离分支，通过验证后按持续授权保存并安全整合main；最终提交/远端以Git核对为准。

新增[HTTP适配](../../backend/apps/promotions/http_services.py)和[9项测试](../../backend/apps/promotions/tests/test_flash_sale_http_wait.py)，
View仅切换被调用的函数。原promotions/services.py与基点内容保持一致，既有API测试也未改。
模型、迁移、依赖、数据库配置、普通下单、支付/关闭、前端和开发业务数据未修改。

## 2. 已实现的合同

1. 在原业务事务外读取当前连接SESSION值、暂设5秒，再调用原服务的完整atomic。
2. 库存UPDATE的1205/1213仍返回stock_busy/409；其他原下单SQL的1205/1213在atomic退出后返回checkout_busy/409。
3. 原事务提交或回滚后，恢复同一物理连接的原值；不硬编码50，也不向替换后的连接写旧值。
4. 读取或设置失败时，不调用下单服务，丢弃连接并传播数据库错误。
5. 恢复失败时告警并尝试关闭、丢弃连接；保留成功、业务拒绝或原异常。关闭自身报错也不能覆盖原结果。
6. 拒绝已有外层atomic或关闭autocommit的调用。未知数据库错误不伪装库存不足，无自动重试或新Key。

告警只记录动作、异常类型和整数错误码，不记录异常原文、SQL、请求Cookie或秘密。
5秒是相应InnoDB行锁等待上限，多个语句可分别等待；不等于整个HTTP请求期限。
参考[MySQL会话变量](https://dev.mysql.com/doc/refman/8.4/en/innodb-parameters.html#sysvar_innodb_lock_wait_timeout)、
[Django连接复用](https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections)。
替代方案是继承原等待值，少三条会话SQL，但无法单独缩短本入口等待；没有生产等待目标或性能改善证明。

## 3. 真实MySQL证据

| 场景 | 实际观察 | 结果 |
| --- | --- | --- |
| 库存持锁 | Observer586/Holder587/Contender588；SESSION5；实际条件UPDATE抛1205，记录耗时5.0秒 | holder仍未释放时返回409/stock_busy；六类事实0、库存5/0，原连接恢复50 |
| 用户持锁 | Observer590/Holder591/Contender592；SESSION5；实际User FOR UPDATE抛1205，记录耗时5.0秒 | holder仍未释放时返回409/checkout_busy；无业务写入、库存5/0，原连接恢复50 |
| 成功/重放/限购拒绝 | 原值改为17；三次调用均17→5→17；只有首次有库存UPDATE且位于atomic内 | 201/200/409，原连接和原值保留；一张完整订单 |

两持锁连接只锁定记录，没有业务写入。观察连接PROCESSLIST在放行前看见未完成的目标SQL；
这证明实际目标语句未完成，不能作为具体锁索引、等待图或性能指标证据。
库存超时前Order/OrderItem/AddressSnapshot/FlashSalePurchase各1，Reservation/Movement各0。
超时后atomic已经退出、autocommit恢复；竞争连接与观察连接均核对无残留，购买意图没有失败订单记录。
释放持锁连接后，两种场景均以原Key得到201，再次同Key得到200/同一public_id。
最终六类事实各1，可售4/预占1、qty1、活动价89.00、HELD资格、一次预占和流水，购物车原两件不变。

“恢复原值”包含实际17秒的情况，证明没有写死恢复50。原默认50只是当前环境值。
耗时仅为这些受控语句的单次记录，不用于p95、吞吐、生产容量或所有请求都5秒内完成的结论。

## 4. 故障注入的范围

| 注入位置 | 验证内容 | 证据限制 |
| --- | --- | --- |
| 库存前1205、Movement真实INSERT后1213、用户锁前2006 | 分别stock_busy、checkout_busy、原异常；原值13恢复、完整回滚 | 1213不是实际死锁；2006不是实际网络断连 |
| SESSION读取失败、实际SET5后模拟确认失败 | 未调用下单服务，丢弃旧连接；新连接继承正常环境值 | 模拟错误分支，不是切断网络 |
| 成功提交后恢复失败，实际close后模拟关闭确认失败 | 201成功保留、两次安全告警、旧wrapper连接为空、新连接正常、原Key重放200 | 不证明真实网络故障或物理关闭失败均能回收资源 |
| 业务拒绝/未知主异常后恢复失败 | 原业务结果或原异常对象保留、连接丢弃、无业务残留 | 不证明提交结果不确定恢复 |
| 提交后主动替换连接，并设新值19 | 不把旧值17写入新连接，仍201成功 | 测试主动替换，不是真实断连 |

原始输出打印12条新场景快照，区分real_isolated_mysql_1205、注入错误、清理和连接替换。
新的两种真实超时每次目标SQL只尝试一次；未知错误保持可见，没有吞掉异常并继续使用失败事务。

## 5. 检查与环境

目标：21 passed、259 subtests passed，40.14秒，首次通过（9项等待测试 + 12项既有HTTP测试）。
promotions/ordering/payments + 原Session登录API回归：216 passed、662 subtests passed，262.10秒。
7项跳过（负载1、支付到期锁实验6），12项真实RabbitMQ集成未选入；该耗时不是性能比较数据。
Django check为0 issues；makemigrations --check --dry-run为No changes detected。
源码AST、核心模块内容保持、旧证据指纹、diff范围与本地链接经过静态核对。
本片自查，不把第一片的独立评审描述为本次新增代码的独立评审。

已有MySQL8.4.10、127.0.0.1:3307、READ-COMMITTED、rollback_on_timeout=false、deadlock_detect=true；
Python3.11.9、Django5.2.17、pytest9.1.1；同步支付、目录缓存关闭。
临时修改只作用于秒杀调用所在SESSION；没有SET GLOBAL或新的等待配置。
pytest创建、迁移、写入并清理临时test_project_one；没有操作开发库业务数据、GRANT、服务启停或部署。
预检和后检临时测试库不存在、可见测试连接0、开发schema表数26；后检SESSION和GLOBAL等待值均50。

APIClient(enforce_csrf_checks=True)经过URL、中间件、认证/CSRF、适配、原事务和响应。
TransactionTestCase避免外层测试事务隐藏提交边界；force_login建立真实Session。
本次不是浏览器、真实HTTP网络或前端端到端验收；未重跑负载矩阵、MQ/Redis或显式支付到期实验。

## 6. 复现与原始文件

使用上述本地测试环境，设置MYSQL_TEST_DATABASE=test_project_one、MYSQL_HOST=127.0.0.1、MYSQL_PORT=3307，
密码仅在本地环境提供；DJANGO_ENV=test、PAYMENT_EVENTS_ASYNC=false、CATALOG_CACHE_ENABLED=false。
目标MYSQL_DATABASE=project_one，pytest实际只写test_project_one；回归MYSQL_DATABASE=test_project_one。
PROJECT_ONE_TEST_FLASH_SALE_LOAD与PROJECT_ONE_TEST_EXPIRATION_CONCURRENCY均为false，不写旧MQ/到期证据。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_http_wait.py backend/apps/promotions/tests/test_flash_sale_api.py -q -s --tb=short
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests backend/apps/ordering/tests backend/apps/payments/tests backend/apps/accounts/tests/test_session_auth_api.py -m 'not rabbitmq_integration' -q --tb=short
```

[JSON快照与源码/输出指纹](phase-6d2-flash-sale-http-wait-data.json)、
[目标原始输出](phase-6d2-flash-sale-http-wait-target-output.txt)、
[回归原始输出](phase-6d2-flash-sale-http-wait-regression-output.txt)。指纹按UTF-8文件字节并将CRLF归一为LF。
前片JSON与raw保持；[指南](../learning/phase-6d-flash-sale-http-study-guide.md)更新为当前政策和复述练习。
下一片是最小顾客入口及端到端/独立讲述验收，开发数据操作另行确认；Redis Lua范围仍需另行决定。
