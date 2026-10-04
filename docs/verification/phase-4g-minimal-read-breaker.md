# Phase 4G：最小读取熔断接入与故障对照

2026-09-27，Track B / Depth / Learning Mode。加强缓存故障回退这条面试
故事：持续故障期间减少后续 Redis 尝试与等待，沿用现有回源名额。
范围仅公开商品详情、本进程；不添加通用框架或自动恢复一致性。

## 决定与实现

用户已接受一次指定连接/超时失败打开、冷却 1 秒作为首轮实验参数。
替代是保持 100ms 客户端短超时，每请求及时发现恢复但重复等待。
熔断的代价是瞬时失败也可能临时绕过缓存，并延后重新使用健康 Redis。
不宣称这些参数适合生产，不增加配置或基础设施。

- cache.py 建立共享 detail_reads。只有 ProductDetailCache 的代号读取和
  每次 payload GET 使用 run；get_catalog_generation 本身保持独立。
- 代号 GET→可选 SET NX→GET 作为一次逻辑操作；详情首次与加载者复查
  每个 GET 单独计。半开最多一个逻辑操作，返回后释放资格，不持锁查
  Redis/SQL或等 Future。初始化 SET NX 的网络失败也在该保护内。
- 跳过读取与真实读取失败都进入原降级路径：早期在商品 SQL 前申请
  2 名额；晚期只保护后续 SKU 查询。满员不排队、立即 503，finally 归还。
- 已知 auth/pool/server-loading 等 ConnectionError 子类不被认作网络
  失败；Closed 下其他异常不打开。不能把父类名等同所有真实网络根因。
- 原 on_commit 换代 SET 独立执行；回填写失败保留正常数据库响应。
  网络恢复不保证缓存最新，Phase 4F 的旧价边界没有修复。
- 各测试创建新实例并使用可控时钟；历史 Phase 4F 两个实验明确使用
  测试专用直通策略。原始历史 JSON 不覆盖。

## 方法与环境

- Windows、Python 3.11.9、Django 5.2.17、MySQL 8.4.10、Redis 8.10.1、
  redis-py 8.1.0。测试服务 127.0.0.1:3307、127.0.0.1:6381/0；本次恢复
  Docker 引擎及已存在的 redis-test 容器，没有创建新服务或改部署配置。
- 只写隔离 test_project_one；每个 fixture 用 UUID Key 前缀，只清理自己
  的 Key，无 FLUSHDB。开发库、开发 Redis、.env 和交易代码没有修改。
- Mouse M1、M1-BLACK、价格129.00、库存10；两方案共48请求，另有各自
  单独一次恢复代号读取探测。方案顺序固定：短超时，再最小熔断。
- 每方案先预热，4 波次×6线程：健康、首轮慢故障、继续慢故障、探测后
  恢复。测试 Redis 实际 CLIENT PAUSE 2500 ALL；继续波次在冷却及暂停
  窗口内。控制 PING 等待真实恢复，再单独记录代号探测，随后暖缓存波次。
- 复用 Phase 4F 波次采集器。APIClient 直接经路由/视图/ORM至 JSON，
  没经过 HTTP 服务器；计时排除 DB 建连与开始会合。没有人为保持SQL重叠。
- GET 是应用 execute_command 尝试次数，不是服务端执行或所有握手命令。
  累计 GET 耗时是6个请求操作耗时相加，不是整波墙钟耗时。
- 每方案首次有效采集一份，保存全部逐请求数据。每波只有6个样本，
  nearest rank 的 p95 即最大值；未测生产吞吐、资源饱和或长期分布。
  正常波次耗时差异也不能用于比较容量。峰值名额数不等于同时执行SQL数。
- Git基点 4e1a1ae211bd9bfab37c14c85aed11d3cd580eef；工作区有此前阶段
  未提交实现，仅该commit不能复现。当前核心源码SHA256如下：

| 文件 | SHA256 |
| --- | --- |
| backend/apps/catalog/cache.py | E87ED8350340C6B98D5970A303917467661694658758ECC40D45292080DBB370 |
| backend/apps/catalog/circuit_breaker.py | C0B8726B4D6FDD0BD178B9C6EFF5FE1DD2159FF8AA42C4521DB1B3D5A18CE449 |
| backend/apps/catalog/tests/test_read_breaker_recovery.py | 0CB0110137E072EF990503D7F7C04F1B1289010A78C1619B510C7076207342A9 |

## 对照结果

| 方案 | 波次 | 200/503 | SQL | GET/超时 | 名额峰值 | p50 ms | p95 ms | 累计GET ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 短超时 | 正常 | 6/0 | 6 | 12/0 | 0 | 12.853 | 15.461 | 68.276 |
| 短超时 | 首次故障 | 4/2 | 8 | 6/6 | 2 | 108.321 | 112.106 | 628.391 |
| 短超时 | 继续故障 | 3/3 | 6 | 6/6 | 2 | 111.665 | 125.775 | 661.491 |
| 短超时 | 探测后恢复 | 6/0 | 6 | 12/0 | 0 | 8.366 | 10.279 | 37.200 |
| 最小熔断 | 正常 | 6/0 | 6 | 12/0 | 0 | 5.946 | 6.976 | 22.147 |
| 最小熔断 | 首次故障 | 3/3 | 6 | 6/6 | 2 | 109.959 | 111.974 | 636.082 |
| 最小熔断 | 继续故障 | 2/4 | 4 | 0/0 | 2 | 2.147 | 5.487 | 0 |
| 最小熔断 | 探测后恢复 | 6/0 | 6 | 12/0 | 0 | 8.632 | 11.442 | 41.026 |

所有200内容正确、所有503零业务SQL。首次故障6个调用都已获准并在途，
一次失败打开不能取消其他调用。继续波次零GET说明当前冷却内后续请求
已绕过；持续故障到下一次冷却结束仍会探测，不意味着永远零读取。
200/503比例与SQL受自然调度影响，不能作为固定容量或拒绝比例。

单独恢复探测：最小熔断 Open→Closed，1 GET成功，28.284ms；短超时
对照1 GET成功，3.997ms。它们未包含在表中6请求波次，证明真实通信
恢复，没有任何自动换代或内容新鲜保证。

原始数据：[phase-4g-read-breaker-data.json](phase-4g-read-breaker-data.json)。
逐请求数、GET/超时、SQL、状态、p50与累计耗时经独立重算一致；名额
峰值由采集器统计，没有逐名额时间线，无法从该JSON独立重算峰值。

## 失败探测与接口边界

真实不可连接的本地保留端口配合可控时钟，实际观察：

1. 首次1 GET超时（114.373ms），转 Open，2 SQL正确返回。
2. Open时0 GET，2 SQL正确返回。
3. 推进冷却后1 GET探测超时（102.817ms），再次 Open。
4. 新冷却内0 GET，2 SQL正确返回。
5. 恢复连接并推进冷却，2 GET（代号+详情）成功，Closed，1 SQL。

这组时钟只控制状态，不是假 Redis。Windows 对未监听端口本次实际报
TimeoutError，不能保证一定 ConnectionError。
原始数据：[phase-4g-read-breaker-data-failed-probe.json](phase-4g-read-breaker-data-failed-probe.json)。

API专项还验证：Open跳过丢失代号的GET/SET NX；名额满503零SQL；半开
实际代号探测用Event保持时，另5个请求不等待且名额满503；代号初始化
SET NX注入超时触发早期保护；Open时正常save仍真正执行换代SET。
miss、坏JSON、非法代号、404及数据库异常不打开读取熔断。
旧晚结果与中断清理用纯状态测试，健康慢加载、晚GET失败与回填写失败
由原目录测试继续覆盖；不把受控异常注入全部说成真实依赖故障。

## 检查、失败记录与复现

- 纯状态专项实际17 passed in 0.14s。
- 首次目标1 failed、32 passed in 23.49s：慢故障对照通过且采集已保存，
  失败是另一个测试只认ConnectionError。新增API边界批次同一未修断言
  为1 failed、4 passed in 9.18s。修正为约定两类异常后，API/失败探测
  目标实际10 passed in 8.70s；没有改实现来掩盖该平台差异。
- 最终目录整体99 passed、6 subtests passed in 37.63s，无跳过。
  最终回归再次验证实验，但未覆盖首次有效原始采集。
- Django check 为0 issues，git diff --check 通过；新增文件空白与两份
  JSON解析检查通过，原始逐请求计数另经独立重算。

已有本地测试服务运行时，在仓库根目录依次执行：

```powershell
$env:PROJECT_ONE_TEST_REDIS_URL='redis://127.0.0.1:6381/0'
$env:PROJECT_ONE_READ_BREAKER_OUTPUT="$env:TEMP\project-one-read-breaker.json"
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_read_breaker_recovery.py backend/apps/catalog/tests/test_read_breaker_api.py -q -s --tb=short
Remove-Item Env:PROJECT_ONE_READ_BREAKER_OUTPUT
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests -q --tb=short
```

第一个命令在TEMP保存对照和-failed-probe两份文件，避免覆盖仓库首次
数据。测试会创建/销毁隔离库，并暂停测试Redis；不要同时运行共享这些
服务的pytest进程。未设URL会跳过，不能把跳过当通过；指定服务不可达失败。

本轮文件范围：cache.py、circuit_breaker.py；test_detail_cache.py 的实例
隔离与恢复时钟；test_fallback_limit.py 的恢复时钟；test_redis_slow_recovery.py
的历史基线与采集器复用；test_read_circuit_breaker.py 的异常分类；新增
test_read_breaker_recovery.py、test_read_breaker_api.py；CONTEXT.md、Phase4G
学习/验证记录及两份JSON。没有改视图/信号、模型、依赖、配置、迁移、
交易、前端或开发数据，未commit/push，先前阶段修改保留。

本段收束，下一步最小缓存穿透，再多Key雪崩、整理缓存面试故事、进入
支付。学习说明：[phase-4g-minimal-read-breaker.md](../learning/phase-4g-minimal-read-breaker.md)。
