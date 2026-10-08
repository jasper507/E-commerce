# 订单列表联合索引正式落地验证 — 2026-10-09

配套[学习指南](../learning/phase-3-order-list-index-study-guide.md)。开始时main/远端均e85e592；实现前源码版本与本片文件SHA256见[正式计划记录](phase-3-order-list-index-plans.json)。旧[查询基线](phase-3-order-query-baseline.md)是历史证据，本片不重复五轮性能测量。

## 范围、规则与停止条件

用户选中“下一片建议只正式落地这一个订单列表联合索引”并要求开始。本片Supporting/Pair，补齐证据到可维护迁移的工程闭环。

- 只给Order增加普通`(user, created_at, id)`索引及0005 AddIndex迁移；保留原幂等唯一约束。
- 查询、分页契约、身份过滤、快照和下单/库存/支付规则不改。
- 验证隔离库正反迁移、已有数据/响应不变、正式索引实际查询路径和相关订单回归后收片；不追加游标/摘要/缓存或负载矩阵。
- 开发数据库迁移不包含在本轮执行范围，后续要说明影响并取得确认。

## 本轮实现

Order.Meta.indexes声明`ordering_order_user_created_id`，字段user/created_at/id，30字符索引名；Django5.2.17生成0005_order_list_index，仅AddIndex操作，依赖0004及可替换用户模型。未改历史迁移。

旧显式基线实验增加按实际索引列序的保护：若已有(user_id, created_at, id)，在创建夹具和ANALYZE前抛出明确错误，指向隔离旧版本e85e592；历史JSON/raw保持不变。

新增迁移往返测试与显式启用的正式计划检查。日常测试只运行小数据迁移测试，正式大数据计划检查和旧性能实验默认skip；不把优化器在极小数据下的选型或耗时阈值写进普通CI。

## 环境与隔离

Windows/Python3.11.9、Django5.2.17、DRF3.18.1，现有MySQL8.4.10/127.0.0.1:3307，仅test_project_one。启用计划检查时collection先限制test环境、TEST.NAME与默认库不同；测试中再核对当前库。

运行前只读检查test_project_one不存在、可见测试连接0、开发schema30表；独立串行运行并由pytest建库/迁移/销毁，未与其他测试占用者并行。没有运行开发库migrate、业务写入或修改服务/账号/配置/依赖，没有直接查看或输出.env敏感值。表数只是辅助证据，隔离同时依靠连接库核对与测试生命周期。

## 真实迁移与正式查询计划

目标命令设置：DJANGO_ENV=test、MYSQL_TEST_DATABASE=test_project_one、CATALOG_CACHE_ENABLED=false、PAYMENT_EVENTS_ASYNC=false、PROJECT_ONE_ORDER_QUERY_BASELINE=0。

```powershell
$env:PROJECT_ONE_ORDER_INDEX_VERIFY='1'
# 再次执行需选择新的JSON文件名；拒绝覆盖已有证据。
$env:PROJECT_ONE_ORDER_INDEX_OUTPUT='C:\project-one-ecommerce\docs\verification\phase-3-order-list-index-plans.json'
.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_order_list_index.py -q -s
```

本轮**2 passed，13.33秒**；[目标输出](phase-3-order-list-index-target-output.txt)。

1. 初始测试库应用最新迁移，再退到0004；创建两笔已有订单及完整地址/明细，保存三表字段与响应；实际执行0004→0005→0004→0005，每步内容和响应一致，普通索引存在/消失符合预期，原(user,scope,key)唯一约束仍在，finally恢复0005。旧基线保护在0004允许、0005拒绝。
2. 在正式迁移结构下创建本人/他人各5,000单、10,000地址、20,000明细，复用上一片固定UUID和显式时间置换/ties夹具及独立期望。首页/深页归属、完整快照与返回字段通过；分段测量使用生产orders_for/Paginator/Serializer，并与当前API响应核对。计划源于这些实际SQL，不使用FORCE INDEX。

| 正式结构的实际步骤 | 首页 | 深页201 |
|---|---:|---:|
| 订单步骤采用索引 | ordering_order_user_created_id，反向 | 相同，反向 |
| 订单索引步骤rows / loops | 20 / 1 | 4,020 / 1 |
| 订单Sort | 无 | 无 |
| 地址查找loops | 20 | 4,020 |
| COUNT覆盖索引实际rows | 5,000 | 5,000 |
| items实际rows及自身排序 | 40，有Sort | 40，有Sort |
| 响应订单/明细、字节、订单数据SQL | 20/40，16,108，3 | 20/40，16,146，3 |

完整SQL/参数、SHOW INDEX、EXPLAIN估计及EXPLAIN ANALYZE树保存于JSON。此处rows是该步骤输出，不是所有节点相加后的总扫描量；COUNT聚合最终1行不表示只统计1笔。每页只一次观察，保存的单次时延不作性能对比或提升比例；不含Session、网络、浏览器和并发容量。

## 静态迁移检查与SQL预览

实际运行makemigrations ordering --name order_list_index，生成新文件；随后manage.py check为0 issues，makemigrations --check --dry-run为No changes detected。这两项不执行迁移。

sqlmigrate ordering 0005及--backwards仅收集SQL，未执行；[正向预览](phase-3-order-list-index-forward-sql.txt)、[反向预览](phase-3-order-list-index-reverse-sql.txt)。正向CREATE INDEX；反向预览还包含创建user_id外键支撑索引后DROP候选，因Django MySQL后端根据当前未应用0005的默认库元数据判断支撑索引。这是预览，不能冒充隔离库真实逆迁移逐条执行日志；迁移往返已另行真实验证，最终SHOW INDEX没有额外独立user_id索引。

## 相关回归

关闭PROJECT_ONE_ORDER_INDEX_VERIFY和PROJECT_ONE_ORDER_QUERY_BASELINE（设0），在相同隔离环境执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_order_list_pagination.py backend/apps/ordering/tests/test_order_api.py backend/apps/promotions/tests/test_flash_sale_api.py backend/apps/ordering/tests/test_order_list_index.py backend/apps/ordering/tests/test_order_query_baseline.py -q
```

本轮**29 passed、171 subtests passed、2个显式实验默认skip，27.44秒**；[回归输出](phase-3-order-list-index-regression-output.txt)。覆盖原分页6项、订单API10项、秒杀API12项和迁移往返1项，含本人归属、排序/快照/固定页长/错误页、同Key重放/异参数409、普通和秒杀下单事务边界。原查询基线的1/16项和分页片的28/63项均为历史结果，不当作本轮回归。

回归后再次只读确认test_project_one不存在、可见测试连接0、开发schema仍30表。没有重跑支付/MQ/Redis或浏览器矩阵。

本轮静态核对：计划JSON内4份源码SHA256与当前文件一致；指南唯一模型片段经AST与实际Order.Meta.indexes一致；48个本地资料链接有效，指南无图/源码导览/末尾题；独立只读审查确认计数、计划、逆SQL预览解释和边界一致；git diff --check通过。随后再次运行check为0 issues、makemigrations --check --dry-run无变化。静态检查不替代迁移与API实测。

## 未改、限制与下一步

仅模型新增索引、0005迁移、必要验证和资料；未改views/Serializer、接口、前端、业务状态机、库存/支付/MQ/缓存规则、配置和依赖；保留Phase6E指南用户修改。没有开发库迁移、浏览器或MQ/Redis故障矩阵重跑、Lua/ES实现。

开发库实际生效仍待后续单独说明确认。没有索引空间、写成本、大表DDL锁/耗时、冷缓存、生产或并发容量证据；COUNT/深OFFSET、跨页漂移、COUNT与页结果快照边界和大订单items仍存在。

达到本片迁移/路径/订单回归证据后，订单查询方向收束。下一片进入已确认Lua原子准入，先确定准入位置、忙时/Redis故障政策和到达MySQL的计量方式；不扩为Redis库存预扣。学习指南交付不表示个人独立理解已验收。
