# 真实订单查询基线验证 — 2026-10-09

后续进度：模型加入正式列表索引后，当前旧实验会明确拒绝把带索引状态测成原基线；本页JSON/raw及数值固定为e85e592历史实验。复现应使用该版本的隔离checkout，不能直接在最新版重跑下面的旧命令。正式迁移证据见[后续验证](phase-3-order-list-index.md)。

配套[学习指南](../learning/phase-3-order-query-baseline-study-guide.md)。业务源码92709b1；新增实验代码哈希见[原始数据](phase-3-order-query-baseline-data.json)。本片是Supporting/Pair实验，重点是完整列表成本与索引判断，不是接口功能或正式索引迁移。

## 范围、预期与停止条件

用户选中上一片“真实订单查询基线”并要求开始下一步。捕获生产orders_for/Paginator/Serializer生成的SQL，核对Order、AddressSnapshot、OrderItem索引；比较旧式全量、首页、第201页，再只在隔离测试库改变一个索引。

预期是各阶段本人归属、创建时间/ID排序、地址/商品快照与金额不变；索引是否改变计划和耗时由实测决定，不设置必须变快的通过条件。完整SQL、成本分层、候选路径和边界足够解释后收片，不扩大游标/缓存/并发负载。

## 环境与安全边界

- Python3.11.9、Django5.2.17、DRF3.18.1、MySQL8.4.10，Windows x64；CPU AMD Ryzen7 8840HX，12核24线程，物理内存16,299,335,680字节。
- Docker引擎可见24 CPU、7,889,158,144字节内存，这是引擎可用资源，不是MySQL专属配额。InnoDB buffer pool134,217,728字节，sort buffer262,144字节，会话READ-COMMITTED。
- 仅现有MySQL127.0.0.1:3307；没有启动或修改Redis/MQ/ES、服务配置或依赖。
- 开始前test_project_one/test_order_query_baseline均不存在、可见测试连接0；实验结束后同样为0。开发schema表数前后30，只作辅助检查；安全结论同时依赖测试库名称检查和Django测试生命周期，不能仅由表数推出无数据写入。
- 默认运行跳过实验；明确启用时collection先限制test环境、TEST.NAME=test_project_one及默认NAME不同；写入前再核对当前连接库、MySQL和autocommit。使用该原有测试库前必须确认不存在、没有占用，串行运行；勿与其他测试聊天同时使用。
- 第一次尝试更独立的test_order_query_baseline因账号1044权限限制在建库阶段失败，未产生数据；保留[失败输出](phase-3-order-query-baseline-setup-error-output.txt)。未扩大账号权限，改用已有授权的test_project_one。最初针对旧库名guard的collect-only拒绝也是预期保护检查，不算业务测试通过。
- pytest创建/迁移/销毁可丢弃测试库；临时CREATE/DROP INDEX、ANALYZE TABLE和夹具UPDATE仅发生在该库。未运行开发库迁移或业务写入，未直接查看或输出.env敏感值。

## 数据与测量方法

合成本人5,000单、他人5,000单；共10,000地址、20,000明细，每单两种SKU、总额2.00。UUID确定生成；创建后明确更新created_at为2026-01-01 UTC加`(id*37)%503`秒，避免auto_now_add覆盖传入时间，包含ties且时间优先于ID。期望顺序来自预存ID及夹具时间公式，不用被测ORDER BY生成oracle。这是读取夹具，不模拟真实库存/支付业务事实或用户规模。

每阶段每形状热身一次、五次测量，单线程、热缓存不flush；形状顺序轮转。先原索引阶段，再候选阶段，并非两索引之间随机交替；DDL和预热可影响状态，报告所有样本、中位数及范围，不计算p95、吞吐或生产容量。

计时边界：

- materialize_ms：完整查询集物化；分页包含COUNT、订单/地址、items，以及结果获取和ORM对象构造。
- serialize_ms：OrderSerializer.data与分页封套；断言不追加SQL。
- render_ms：JSONRenderer.render与UTF-8字节数。
- total_stage_ms：单次以上三个阶段的和，再对五次取中位数；各阶段中位数不能直接相加。
- execute_ms：驱动execute周围计时，含驱动及本地连接行为；mysqlclient普通游标可能缓冲结果，不等于纯服务器时间或完整ORM物化。
- EXPLAIN的rows是估计；EXPLAIN ANALYZE实际执行另行采集，含测量开销，不把它的时间当正常接口延迟。树节点rows/loops是该步骤产出，父子节点不能直接相加作总扫描行。

旧全量使用当前相同orders_for和Serializer重建原有全量数组路径，不是当前API支持一个all参数。当前分页分段计时复用生产类，并用真实APIClient固定身份比对完整SQL+参数及响应字节，支持拆分方法；不包括Session SQL、网络服务器或浏览器。

## 本轮结果

实验1 passed，21.24秒；[完整输出](phase-3-order-query-baseline-output.txt)。以下是五次中位数，括号为最小–最大；单位ms。全部有效样本保留在JSON。

| 阶段 / 形状 | ORM物化 | 序列化 | JSON渲染 | 三阶段和 | 响应字节 / SQL数 |
|---|---:|---:|---:|---:|---:|
| 原 / 全量 | 469.68 (380.10–512.45) | 199.94 (199.33–200.45) | 41.45 (41.28–41.82) | 711.57 (621.24–753.67) | 4,005,001 / 2 |
| 原 / 首页 | 8.79 (7.33–9.14) | 1.45 (1.33–1.54) | 0.16 (0.15–0.16) | 10.38 (8.81–10.75) | 16,108 / 3 |
| 原 / 深页201 | 12.89 (12.36–15.80) | 1.45 (1.33–1.52) | 0.17 (0.14–0.19) | 14.52 (13.84–17.50) | 16,146 / 3 |
| 候选 / 全量 | 466.15 (420.37–485.67) | 204.81 (202.59–208.69) | 42.66 (40.41–46.28) | 717.28 (670.03–731.58) | 4,005,001 / 2 |
| 候选 / 首页 | 5.70 (4.04–5.85) | 1.48 (1.31–2.64) | 0.18 (0.17–0.26) | 7.42 (5.53–7.73) | 16,108 / 3 |
| 候选 / 深页201 | 11.92 (10.88–14.36) | 1.45 (1.33–1.84) | 0.18 (0.15–0.19) | 13.55 (12.37–16.38) | 16,146 / 3 |

全量始终返回5,000单/10,000明细，分页20单/40明细。候选全量的execute总和有208.86ms样本，其余46.33–52.66ms；没有删除波动或用最佳值报告。全量总体未观察到明确收益，避免把索引说成解决序列化成本。

### 实际索引与计划

SHOW INDEX观察到Order只有主键id、public_id唯一索引和`(user_id, creation_scope, idempotency_key)`唯一索引，没有额外独立user_id或时间排序索引。地址有主键与order_id唯一索引；items有主键、order_id和sku_id外键索引。完整行和列次序在JSON。

| 查询步骤 | 原索引实际计划 | 候选实际计划 |
|---|---|---|
| 首页COUNT | 原幂等索引覆盖读取5,000，聚合输出1 | 候选覆盖读取5,000，聚合输出1 |
| 首页订单/地址 | 原幂等索引输出5,000→Sort→20；地址loops20 | 候选反向扫描输出20→20；无订单Sort，地址loops20 |
| 深页订单/地址 | 索引输出5,000→Sort输出4,020→OFFSET输出20；地址loops4,020 | 候选反向扫描输出4,020→OFFSET输出20；地址loops4,020 |
| 本页items | order_id范围读取40→按id排序40 | 同样40行与排序 |

原COUNT/订单EXPLAIN估计4,968、实际5,000；候选COUNT估计5,000、实际5,000。未用FORCE INDEX。filesort只说明额外排序，没有本次落盘证据。

原全量items计划沿PRIMARY索引实际读取20,000行，经order_id IN过滤输出10,000行；这与分页items范围读取40行不同。完整计划保留在baseline.all.plans，不能只据最终返回行数解释全量成本。

普通驱动execute中位数：首页COUNT原1.56ms、候选1.62ms；首页订单/地址原4.26ms、候选1.10ms；深页订单/地址原9.32ms、候选8.11ms。COUNT仍扫描本人范围，深页仍做许多被跳过订单的地址查找，不据细小时间差宣称COUNT加速。

### 正确性与恢复

全部warmup和正式样本核对准确public_id顺序、本人地址、创建时间、两条SKU快照及金额；索引前后各形状响应哈希完全一致。APIClient首页和深页在两个阶段均200，SQL/参数与分段采集一致，响应字节一致。

候选在finally删除；静态解析JSON按索引名、列次序、列名、方向及唯一性比较，baseline/restored定义相同（不比较Cardinality）。三表行数仍10,000/10,000/20,000。删后首页计划恢复原索引读5,000及Sort；随后pytest销毁测试库。

## 执行与复现

运行前先只读确认test_project_one不存在、可见测试连接0，确认没有其他使用者；这一步不能由开启实验环境变量替代。

```powershell
$env:DJANGO_ENV='test'
$env:MYSQL_TEST_DATABASE='test_project_one'
$env:CATALOG_CACHE_ENABLED='false'
$env:PAYMENT_EVENTS_ASYNC='false'
$env:PROJECT_ONE_ORDER_QUERY_BASELINE='1'
# 改为新的证据文件名，实验拒绝覆盖已有JSON。
$env:PROJECT_ONE_ORDER_QUERY_OUTPUT='C:\project-one-ecommerce\docs\verification\order-query-new-run.json'
.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_order_query_baseline.py -q -s
```

关闭PROJECT_ONE_ORDER_QUERY_BASELINE或设为0时，该实验默认skip。目标是建立可解释的读取基线，不把计时阈值纳入日常CI。相关回归及本轮静态检查见下方补充。

### 相关回归与静态核对

在相同test环境、MYSQL_TEST_DATABASE=test_project_one、缓存/异步关闭，另设PROJECT_ONE_ORDER_QUERY_BASELINE=0后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_order_list_pagination.py backend/apps/ordering/tests/test_order_api.py backend/apps/ordering/tests/test_order_query_baseline.py -q
```

本轮16 passed、9 subtests passed、1实验默认skip，13.51秒；[原始输出](phase-3-order-query-baseline-regression-output.txt)。包含本人分页/快照/排序/固定页长/错误页码/查询预算及原订单API行为。没有改业务应用，未重跑支付、MQ、Redis或浏览器矩阵；上一片28/171及63/8的结果是历史，不写成本轮。

当前guard另以错误TEST.NAME=test_order_query_baseline执行collect-only，在collection阶段拒绝且pytest退出2，外层检查确认这是预期结果；[保护检查输出](phase-3-order-query-baseline-guard-output.txt)。没有创建库，此检查不计入业务通过数。

本轮静态检查：实验AST解析与保存的实验源码SHA256一致；JSON中前后索引结构及响应哈希一致；学习指南、验证记录、README和前片指南本地链接有效，指南仅两段重点SQL、无图/源码导览/末尾练习；数据表与原始数值经独立只读审查核对；git diff --check通过。最终回归后再次确认测试schema/连接0、开发表数30。静态检查不替代上面的真实数据库实验。

## 未改、限制与下一片

应用views/models/Serializer、接口、前端、迁移、配置和依赖未改；库存/支付/MQ/缓存规则未改。保留Phase6E指南原有用户修改，不混入提交。开发库尚未应用候选索引。

没有生产或并发流量、冷缓存、网络传输、真实浏览器端到端、索引空间/写成本、新增记录分页漂移或统计共享快照证据。固定两条items不能证明任意大订单响应有上限。

候选首页路径证据足够，下一片建议正式表达这一个索引并验证迁移/排序/归属/快照与订单回归；不同时换接口或增加负载矩阵。开发库迁移需另行说明并确认。之后进入已确认Lua原子流量准入；本轮未实现Lua/ES。
