# Phase 6D：HTTP 基础候选验证

日期：2026-10-08，Asia/Shanghai。
状态：**HTTP 基础候选已验证；等待政策未收到拥有者答复，未合入 main。**
面试收益是解释认证、CSRF、输入、服务端事实、幂等与事务怎样组成完整请求。
本轮只完成不依赖新等待政策的部分，不能宣称整个 Phase 6D 已完成。

## 1. 基点与范围

基点 `b4456bd1d498c7fe370e8a793f63e11ffa5f4fae`；开始前main和现有codex分支的两个远端均等于该提交，工作区干净。
复用 `codex/flash-sale-baseline` 隔离候选；无需空提交或创建新分支/worktree。
本轮不推进主分支集成，因为 [ADR 0001](../adr/0001-flash-sale-inventory-conditional-update.md) 明确将顾客等待选择留到HTTP接入前。

新增promotions的Serializer、View、URL及一个API测试文件，总URL新增一条include。
原秒杀services仅更新模块说明；去掉模块首docstring后，整个模块AST与基点完全相同。
库存条件更新、User/Address/资格/Key/锁序、价格、关闭/支付及六类事实不另建规则。
模型、迁移、配置、依赖、既有测试、前端、普通购物车和支付业务未改。

## 2. 接口合同与实际目标

| 请求 | 已验证的候选行为 |
| --- | --- |
| GET `/api/flash-sales/items/` | 匿名可读；五级启用状态与starts<=now<ends；活动价两位字符串、CNY、quantity=1、in_stock提示 |
| 公开查询边界 | 售罄/缺Inventory仍显示false；不暴露库存数字、客户、资格或订单；GET入口禁止写 |
| POST `/api/flash-sales/orders/` | 现有SessionAuthentication/CSRF；只用登录用户、活动项/地址ID及Header Key |
| 新建 / 重放 | 201新建、200同单；复用OrderSerializer，既有本人订单列表/详情仍适用 |
| 客户端篡改 | price/total/quantity/user_id/creation_scope等未声明字段被忽略，实际活动价89.00、qty1、当前登录用户、FLASH_SALE |
| 幂等 / 限购 | Key trim/格式；异参409，新Key同人同活动409；结束/改价/禁用后原Key仍重放已有订单 |
| 失败 / 所有权 | 无效输入400、他人地址404、不开放409、库存不足409；六类事实无残留，购物车不变；他人订单404 |
| scope / 顾客边界 | 普通购物车与秒杀可独立使用同Key；不同顾客同Key互不冲突 |

查询为不分页列表，只服务当前小规模学习范围，没有大规模列表容量结论。
有货提示不保证成交；原服务仍在事务内检查资格和权威库存。
重放返回原订单的当前事实，不能承诺响应逐字保持不变。
View仅映射OrderRuleError；现有库存UPDATE的1205/1213保持stock_busy，其他SQL的数据库错误传播没有改变。
本轮没有额外制造HTTP锁超时或注入HTTP数据库故障；相应原服务回归仍在相关测试中。

## 3. 实际检查与隔离

| 检查 | 结果 |
| --- | --- |
| HTTP目标 | 12 passed、162 subtests passed，19.73秒，首次通过 |
| promotions/ordering/payments + 现有登录API回归 | 207 passed、565 subtests passed，152.27秒 |
| 跳过 | 7项：显式负载1、支付到期锁实验6 |
| 未选入 | 12项真实RabbitMQ集成测试 |
| Django check / 迁移漂移 | 0 issues / No changes detected |
| 静态 | 新源码AST、原服务整个模块AST保留、范围与diff、本地链接和指纹核对 |
| 独立只读评审 | 未发现阻止基础候选保存的问题；要求补齐证据并在等待答复前不合main |

环境：已有MySQL8.4.10，127.0.0.1:3307；Python3.11.9、Django5.2.17、pytest9.1.1；READ-COMMITTED。
本机SESSION行锁等待50秒，rollback_on_timeout=false；同步支付与目录缓存关闭。
pytest只创建、迁移、写入和清理临时test_project_one。
前后测试库不存在、可见测试连接0、开发schema表数26，会话值后检仍50。
没有开发migrate/业务数据写入、GRANT修改、服务启停或部署。

APIClient(enforce_csrf_checks=True)实际经过URL/中间件/DRF/Service/ORM/响应。
新测试使用force_login建立真实Session，复用TransactionTestCase fixture，无外层测试事务掩盖提交边界；
原登录API另行进入回归，但本轮没有浏览器、真实HTTP网络或端到端界面验收。
没有重新执行负载矩阵、真实MQ/Redis或显式支付到期锁实验；不声称性能提升或生产容量。

## 4. 尚待确认的选择

已向拥有者提供两项：秒杀HTTP服务调用暂设5秒行锁等待并恢复原会话，或沿用现有会话值（本机50秒）。
**尚无答复。**没有按默认选项或沉默实施5秒，不增加新配置、自动重试或总请求截止时间。
若选临时值，须在原业务事务外设置/恢复，验证成功/失败后连接状态及恢复失败时保留已提交结果。
前置或其他下单SQL锁竞争还需要随该政策明确HTTP合同，不能全部冒充库存步骤的stock_busy。
上述设计均未实现或运行，不计入本轮通过项目。
参考 [MySQL会话行锁等待](https://dev.mysql.com/doc/refman/8.4/en/innodb-parameters.html#sysvar_innodb_lock_wait_timeout)、
[Django持久连接](https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections)。

## 5. 复现与文件

配置本地测试数据库授权、host/port、test库及上面的同步/缓存开关，密码只通过本地环境提供。
目标运行MYSQL_DATABASE=project_one，pytest实际写test_project_one；回归两数据库变量均为test_project_one。
负载、支付到期并发以及MQ/到期证据写出开关关闭。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_api.py -q --tb=short
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests backend/apps/ordering/tests backend/apps/payments/tests backend/apps/accounts/tests/test_session_auth_api.py -m 'not rabbitmq_integration' -q --tb=short
```

[数据与源码指纹](phase-6d-flash-sale-http-data.json)、[目标原始输出](phase-6d-flash-sale-http-target-output.txt)、
[回归原始输出](phase-6d-flash-sale-http-regression-output.txt)保存实际范围、测试方法与边界。
指纹将CRLF归一为LF。本轮输出没有另行打印逐请求ORM/SQL快照，不改写以前的原始证据。
解释及复述练习见 [学习指南](../learning/phase-6d-flash-sale-http-study-guide.md)。
