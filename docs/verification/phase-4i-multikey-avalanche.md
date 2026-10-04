# Phase 4I：多 Key 同时变冷的最小实验

后续状态（2026-10-01）：[Phase 4J](phase-4j-refill-admission.md) 已补上健康 miss 的共享回源名额。本页保留换代前的基线证据；对应测试使用明确的历史策略重放，不能把本页六个全部进入加载当作当前保护行为。

本段属于 Track B / Depth / Learning Mode，服务于面试中的 Redis 缓存一致性与韧性故事。目标只是在现有实现上复现一个限制：修改一个商品后，多个**不同商品**的详情缓存会一起失效，随后的请求会集中回源。先记录事实和取舍，不在本段引入新的防护组件。

## 业务问题与现有路径

公开商品详情使用 `generation + product_id` 作为 Redis Key。普通 `Product.save()` 在数据库事务提交后切换整个目录的 generation。这样，已在途的旧请求即使晚到并回填，也只能写旧代 Key；新请求不会读到它。代价是只改一件商品，也会让其他商品的旧 Key 对新请求不可见。

每个正常详情请求仍会查询 MySQL 的 Product/Category，确认上架状态；命中缓存时不再加载 SKU。缓存未命中时，现有 SingleFlight（同 Key 在途加载合并）只合并**相同详情 Key**。本实验的六个请求使用六个不同 Key，所以不能把六次加载合成一次。详情 TTL 为 `30±5` 秒，抖动可以分散自然过期，但不改变全局 generation 切换造成的逻辑失效。

这里的关键不变量是响应内容正确：保存后应看到改过的商品名，其他商品内容仍正确。要观察的失败场景是数据库加载量的集中增加，而非错误响应。

## 实验方法

- Windows、Python 3.11.9、Django 5.2.17、MySQL 8.4.10、Redis 8.10.1。真实隔离 MySQL 测试库 `test_project_one`、测试 Redis 端口 6381；UUID 前缀 Key，不执行 `FLUSHDB`。商品及 Redis Key 由 fixture 清理，不改开发库。
- 六个上架商品，每个商品一个上架 SKU 和一条库存。每轮先预热六个详情，再让六个线程各读一个**不同**的商品，记录 `warm_before_rotation`。
- 修改第一个商品的名称并执行正常 `save(update_fields=("name",))`。确认 generation 已改变、旧的六个详情 Key 都还具有正 TTL、新代六个 Key 均不存在。随后同时放行六个线程各读一个商品，记录 `cold_after_rotation`；最后再读六个 Key，记录 `warm_after_refill`。共三轮、九组、54 个被测请求。
- 使用 DRF `APIClient` 走路由、视图、ORM、真实 MySQL/Redis；线程屏障仅对齐客户端发起，不保证六条 SQL 严格同一时刻运行。每个线程独立数据库连接。`execute_wrapper` 只计请求内 SQL；建数据、预热、`save` 和直接 Redis 检查不计入表中 SQL。
- 时长从发起请求到解析 JSON，排除线程数据库连接和屏障等待。p50/p95/p99 使用 nearest-rank；每组六个样本，p95 和 p99 都等于该组最大值。吞吐是本机六个 APIClient 线程的 `6 / 组时长`，不是 HTTP 服务或生产容量。

原始逐请求记录：[phase-4i-multikey-avalanche-data.json](phase-4i-multikey-avalanche-data.json)。

## 实际观察

每轮旧 Key 的六个 TTL 在换代后仍为正：第 1 轮 `30,35,28,30,27,35` 秒；第 2 轮 `30,33,35,28,26,25` 秒；第 3 轮 `31,27,33,34,29,30` 秒。新代 Key 此时均不存在，因此是**逻辑上一起变冷**，不是六个物理 TTL 同时到期。

| 轮次 | 场景 | 200/请求 | 商品检查 SQL | SKU SQL | 总 SQL | p50 ms | p95/p99 ms | 组吞吐 req/s |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 换代前热 | 6/6 | 6 | 0 | 6 | 12.516 | 32.066 | 176.876 |
| 1 | 换代后冷 | 6/6 | 6 | 6 | 12 | 12.599 | 15.728 | 361.209 |
| 1 | 回填后热 | 6/6 | 6 | 0 | 6 | 6.730 | 7.810 | 617.043 |
| 2 | 换代前热 | 6/6 | 6 | 0 | 6 | 5.768 | 7.303 | 690.663 |
| 2 | 换代后冷 | 6/6 | 6 | 6 | 12 | 12.003 | 15.102 | 353.824 |
| 2 | 回填后热 | 6/6 | 6 | 0 | 6 | 5.800 | 8.170 | 701.008 |
| 3 | 换代前热 | 6/6 | 6 | 0 | 6 | 6.041 | 7.786 | 725.944 |
| 3 | 换代后冷 | 6/6 | 6 | 6 | 12 | 13.636 | 15.675 | 338.400 |
| 3 | 回填后热 | 6/6 | 6 | 0 | 6 | 11.457 | 39.436 | 147.977 |

54 个响应均为 200 且内容匹配预期。三轮冷波各比热波多 **6 次 SKU SQL**，每个不同商品各一次；商品/分类上架检查始终是每请求一次，不应把 12 次总 SQL 全说成缓存失效新增的查询。第 3 轮回填后热波的 p95 比冷波还高；这组小样本时延与吞吐存在明显波动，不能据此声称响应一定变慢、数据库已被压垮，或量出容量阈值。

## 方案比较与边界

当前全局 generation 简单，而且旧在途回填与新请求天然隔离；代价是改一个商品会让所有详情逻辑失效。TTL 抖动针对自然过期，对本次全局切代没有保护。改成按商品精确失效可能减少无关回源，但必须正确处理分类修改影响多个商品、SKU 迁移影响旧和新商品、事务提交后的失效失败；现阶段复杂度超过这次最小证据的收益。预热或限流也有资源与响应代价，需要先明确实际峰值与目标，本段没有实现或宣称它们有效。

实验说明的是**多 Key 集中回源的风险**。它没有验证物理 TTL 同时到期、Redis 整体宕机、大流量下数据库过载、跨进程 SingleFlight 或真实 HTTP 吞吐。当前故障回源名额只约束 Redis 读取降级；健康 Redis 下的正常冷 miss 不经过这个名额限制。后续若数据或需求显示风险值得处理，再单独设计方案和失败测试；按本项目首轮面试范围，先收束缓存故事并进入支付状态与幂等。

## 复现与验证

在仓库根目录、隔离 MySQL 和 Redis 测试服务可用时运行：

```powershell
$env:PROJECT_ONE_TEST_REDIS_URL = 'redis://127.0.0.1:6381/0'
$env:PROJECT_ONE_AVALANCHE_OUTPUT = 'C:\project-one-ecommerce\docs\verification\phase-4i-multikey-avalanche-data.json'
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_cache_avalanche.py -q -s --tb=short
Remove-Item Env:PROJECT_ONE_AVALANCHE_OUTPUT
```

输出应有九行 `multikey_avalanche`、三轮各自的 `6/0 → 6/6 → 6/0`（商品检查/SKU SQL）以及 `1 passed`。没有设置测试 Redis URL 会跳过，跳过不代表通过；不要并行启动另一个共用测试库的 pytest 进程。复跑时 JSON 会被新测量覆盖，时延数字允许变化，SQL 与响应断言必须保持。

2026-09-30 采集的目标测试：`1 passed in 6.97s`。放宽与结论无关的新 Key TTL 下界以减少慢机器误报后，最终目录回归：`108 passed, 6 subtests passed in 38.68s`。没有修改生产缓存逻辑、依赖、配置、迁移、订单、支付或前端；先前未提交改动保留，未 commit/push。

Git 基点为 `4e1a1ae211bd9bfab37c14c85aed11d3cd580eef`，工作区有前序未提交实现，仅基点无法独立复现。此次采集所用源码 SHA256：

| 文件 | SHA256 |
| --- | --- |
| `backend/apps/catalog/cache.py` | `6A0858BFFED7DB712ADACCE6E9A60B168D41894913AFACC49DFD13D1FDD24EEA` |
| `backend/apps/catalog/views.py` | `3F22F818786C48686370EFE65D6750E7B2FADD04AB6CADA1EA7E0F6A0CAB7CFD` |
| `backend/apps/catalog/tests/test_cache_avalanche.py` | `A9F880C297B0E95E53FD71D59739671DCFB17AF85F950DD22BB56E7EC2B0730E` |

## 面试自测题

1. 六个 Key 的 TTL 都还没到期，为什么一个商品 `save()` 会让六个详情都查 SKU？
2. 为什么现有 SingleFlight 能合并同一商品的并发请求，却合并不了这里的六个请求？
3. 表中冷波 12 次 SQL，为什么只能说“额外增加 6 次 SKU SQL”？
4. 全局 generation 与按商品精确失效各有什么收益和代价？如果分类改名，精确失效要额外处理什么？
5. 这组数据能证明“缓存雪崩压垮了数据库”吗？你还需要怎样的实验才能支持这个说法？
