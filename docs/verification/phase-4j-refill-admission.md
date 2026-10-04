# Phase 4J：多个冷 Key 共用回源并发名额

2026-10-01，Track B / Depth / Learning Mode。用户指出 Phase 4I 只复现了全局换代导致多 Key 集中回源，并未补上防护，随后确认推进“多 Key 回源并发上限”。本段补齐缓存韧性故事中的一个具体缺口，保留全局代号隔离旧回填的设计。

## 问题与正确行为

原实现只在 Redis 读取降级时申请两个进程共享名额。Redis 正常但详情 Key 不存在时，`fallback_scope()` 因 `degraded=False` 直接放行。因此，六个不同商品在全局 generation 换代后都可能同时加载 SKU；TTL 抖动分散自然过期，SingleFlight 合并同 Key，它们都不能限制六个不同 Key 的加载并发。

最小规则是：正常可缓存详情的真实重建与 Redis 故障回源共用现有两个名额；满员立即 503，不排队。已有故障请求不重复申请。正常缓存命中、owner 二次读取命中、成功共享 Future 结果无需申请重建名额。SingleFlight 等待超时或收到 owner 失败后，真正执行的独立加载也必须申请。

参数 2 是首轮实验值，不是数据库容量结论。`for_load=True` 只加在 `load_and_fill()` 确认未命中后的 scope。没有修改视图、TTL、generation、SingleFlight 规则、负缓存、配置或依赖。既有 `_fallback_admitted` 标志避免 Redis 降级请求在外层已经占名额后，内层再次申请；名额范围含实际加载与 Redis 回填，异常通过 `finally` 释放。

## 先失败，再实施

只新增当前策略的目标测试，生产实现未改时实际观察到：六个请求均 200，商品检查 SQL=6、SKU SQL=6、加载峰值=6。目标断言要求峰值 2，实际 **`1 failed in 7.98s`，`6 != 2`**。随后只修改缓存 scope 的条件，并让健康 miss 的重建传入 `for_load=True`。

替代方案是按商品精确失效，减少无关商品变冷，但要处理分类和 SKU 跨商品依赖、旧在途回填、提交后失效失败。当前共享名额直接约束多种同时未命中情况下的加载峰值，代价是部分请求暂时 503；它保留全局换代造成的大范围 miss，不应宣传成精确失效优化。另一个选择是等待名额，但会增加排队时延和等待线程，本轮采用已有的立即拒绝规则。

## 真实隔离服务与有序实验

- Windows、Python 3.11.9、Django 5.2.17、MySQL 8.4.10、Redis 8.10.1。测试库 `test_project_one`，隔离 Redis 6381，UUID 前缀清理，不执行 `FLUSHDB`，不操作开发数据。
- 六个上架商品，每个一个 SKU（129.00）和库存（10）。先预热，再正常保存商品一的新名称，确认换代、旧六 Key TTL 仍为正、新六 Key 不存在。
- 六线程 DRF APIClient；先让前两个请求进入加载，并在 SKU SQL 前用 Python Event 暂停。再启动另外四个，等待它们进入加载或被拒绝；最后释放。未注入 Redis 网络失败或真实 MySQL 延迟，所有 SQL 仍是真实 ORM/MySQL 查询。该调度用于验证边界，不是压测。
- 两个策略分别三轮：旧 `failure_only_baseline` 使用测试专用 fixture 忽略新的 `for_load` 参数，保留原先仅降级时申请名额的规则；新 `shared_refill_limit` 使用真实当前策略。每个策略 18 个冷波请求，另有恢复顺序读取和回填后的热读。预热、save、直接 Redis 检查和线程数据库连接不计入请求 SQL。
- 旧策略四个追加请求都能进入加载，峰值六；新策略四个追加请求均在前两个被释放前完成 503。没有使用固定 sleep 猜测线程顺序，也没有根据机器速度断言时延。

## 观察结果

三轮的计数完全一致：

| 每轮冷波 | 旧策略 | 共享名额 |
| --- | ---: | ---: |
| 请求数 | 6 | 6 |
| 正确 200 | 6 | 2 |
| 503 | 0 | 4 |
| 详情加载峰值 | 6 | 2 |
| 前两个加载者释放前已完成的 503 | 0 | 4 |
| 商品/分类检查 SQL | 6 | 6 |
| SKU SQL | 6 | 2 |
| 总 SQL | 12 | 8 |
| 随后顺序恢复读取的 SKU SQL | 0 | 4 |
| 再次热读的 SKU SQL | 0 | 0 |

冷波成功响应与更新后的预期字段完全一致。后续六个顺序读取全部 200，新策略中此前被拒绝的四个商品分别完成加载与回填。热读仍有每请求一次上架检查，不加载 SKU。六个商品最终仍需要六次 SKU 查询；保护收益是限制同时进行的重建，不能把冷波 SKU 从 6 到 2 说成消除了四份必需数据的查询。

两份冷波逐请求证据（另含恢复与热读 SKU 数量）：

- [旧策略](phase-4j-refill-failure_only_baseline.json)
- [共享名额](phase-4j-refill-shared_refill_limit.json)

额外目标测试覆盖：同 Key follower 等待 200ms 后不能绕过满员预算；加载异常释放名额；失败的 owner 不残留在 SingleFlight；满员时热命中仍 200；已有 Redis 故障前拒绝仍是零 SQL，晚期失败拒绝仍仅上架检查 SQL。历史 Phase 4D burst、Phase 4F 慢加载和 Phase 4I 多 Key 实验显式用旧策略 fixture 重放，保留历史 JSON，不把旧基线当当前防护测试。

## 保护边界

| 路径 | 名额范围与拒绝前 SQL |
| --- | --- |
| 健康 Redis、详情 miss | SKU 加载和回填受限；拒绝前已执行一次商品/分类检查 |
| Redis 代号或负标记读取早期失败 | 已有外层名额保护；满员在商品 SQL 前拒绝 |
| 详情 GET 晚期失败 | 在加载 SKU 前申请；拒绝前已有商品/分类检查 |
| 正常详情 hit 或成功共享结果 | 不申请重建名额；正常上架检查保留 |
| 显式禁用缓存或事务内旁路 | 保留原路径，本段不新增名额限制 |

这不是全站数据库并发上限：正常上架检查和不存在 slug 的查询不被此次健康 miss 名额保护；多个进程各有两个名额；长耗时加载或回填会占用名额。本实验的固定 2 个 200、4 个 503 来自有序暂停，自然调度下六个快请求可能都成功。没有新的时延、HTTP 吞吐或生产容量结论，也未解决全局失效粒度与可靠失效补偿。

## 复现

已有隔离测试服务可用时，在仓库根目录运行：

```powershell
$env:PROJECT_ONE_TEST_REDIS_URL = 'redis://127.0.0.1:6381/0'
$env:PROJECT_ONE_REFILL_OUTPUT_DIR = 'C:\project-one-ecommerce\docs\verification'
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_refill_limit.py backend/apps/catalog/tests/test_fallback_limit.py -q -s --tb=short
Remove-Item Env:PROJECT_ONE_REFILL_OUTPUT_DIR
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests -q --tb=short
```

未设置 Redis URL 会跳过，跳过不表示通过；指定但不可达会失败。pytest 创建/销毁隔离测试库并清理自己的 Redis 前缀，勿并行启动共享测试库的 pytest 进程。运行时出现的 503 和注入 DatabaseError 日志是目标场景，最终以断言和 pytest 结果判断。

## 实际检查与源码位置

- 加入保护后的目标与既有名额测试：`15 passed in 12.02s`，生成两份对照 JSON。
- 完整目录回归：`112 passed, 6 subtests passed in 43.10s`。
- 审查后仅增强目标断言，明确检查失败 owner 后新请求不能 join 旧 Future、健康拒绝不打开 Redis 熔断，以及每个冷请求都有一次商品检查；最终相关目标复测 `15 passed in 11.22s`。生产代码未再改变。

生产逻辑只修改 `backend/apps/catalog/cache.py` 的 scope 参数与条件、重建调用和说明。当前源码 SHA256：`C55B44C8C6FB9D4FD00067B7529C077EBC2CBA5555F1DB5D896B5446A2FC4A10`。Git 基点 `4e1a1ae211bd9bfab37c14c85aed11d3cd580eef`，含前序未提交实现，单独基点无法复现。

新增 `test_refill_limit.py`；调整 `test_fallback_limit.py` 的当前规则并加入历史策略 fixture，Phase 4D/4F/4I 三份旧实验明确使用它。更新本记录、两份新 JSON、Phase 4I 后续状态与 CONTEXT。没有修改依赖、配置、模型、迁移、视图文件、路由、响应字段、订单、支付、前端或开发数据；健康缓存 miss 满员时新增的 503 是本轮明确接受的行为变化。未 commit/push，已有未提交修改保留。

## 面试练习

1. 为什么六个不同商品最终仍各查一次 SKU，而共享名额仍有保护价值？
2. 为什么应先完成同 Key 合并，再给真实加载申请名额？把名额放在每个请求入口会有什么代价？
3. 为什么健康 miss 的 503 已经有一次 SQL，而 Redis 早期故障的 503 可以是零 SQL？
4. 如果应用运行四个进程，两个名额是否意味着全站最多两个加载？

本段完成之后，先由项目拥有者解释并发上限、拒绝代价、查询总量与保护边界，再收束缓存面试故事并推进支付状态与幂等。
