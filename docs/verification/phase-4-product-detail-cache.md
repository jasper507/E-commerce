# Track B / Phase 4A：商品详情 Cache-Aside 基线

## 问题、规则与选择

服务于“浏览 → 下单”闭环和 Redis 一致性/韧性面试故事。重复商品详情请求
重复读取公开目录及 SKU 库存提示。原 View 已使用 JOIN 和预取，不存在随
SKU 数量增长的 N+1 查询问题，因此不能预先假定 Redis 会带来巨大收益。

本段沿用已确认规则：MySQL 是事实来源；缓存仅供浏览；交易重新读取
MySQL；数据库提交后尽力失效，Redis 故障回源。没有增加数据库字段、迁移、
交易缓存、Lua、锁、MQ、列表缓存或自定义 Admin 页面。

最小方案：`PublicProductDetailView` 每次用原有 Product + Category SQL
确认当前公开可见性，然后读取 Redis 的公开详情 JSON；缺失时再预取 SKU +
Inventory、序列化、回填。GET 响应、游客权限与写请求的 405 契约保留。

Key 为 `{prefix}:catalog:detail:v1:{product_pk}`，包含命名空间、用途、
格式版本和稳定主键。初始 TTL 为 `30 ± 5` 秒，实际每次回填 25–35 秒；
这是可修改的实验参数，不是最优配置结论。命中不续期，不缓存 404。

方案比较：

| 方案 | 每个正常详情请求的 MySQL 查询 | 收益与代价 |
| --- | --- | --- |
| 原始 MySQL | 2 | 简单，不依赖 Redis；每次读取 SKU/库存 |
| 本段缓存 | 未命中 2，命中 1 | 省去命中时的 SKU/库存查询与序列化；增加 Redis 往返、陈旧窗口与失效维护 |
| 全量返回 Redis，无可见性检查 | 命中 0 | 更少 SQL，但下架遇到失效失败或旧值回填时可能继续公开详情；本段不采用 |

本段没有证据证明 Redis 延迟总体优于 MySQL，也没有用查询数减半推导
吞吐量或接口耗时翻倍。后续测量需控制预热、样本量、并发、数据量和资源。

## 写入与故障边界

- 当前目录写入口是 Django Admin 的普通模型 `save()`。Signal 在提交后
  删除 Product Key；Category 修改删除关联全部商品的 Key；SKU 修改删除
  所属商品，改归属时同时删除旧、新商品。Slug 改名不改变缓存 Key。
- 事务回滚不删除原缓存。事务内详情读取绕过缓存，防止未提交数据进入
  Redis，也避免本事务读取其他事务留下的旧副本。
- Product/Category 已提交下架后才发起的新请求，在 MySQL 可见性查询得到
  404，即使批量 update 绕过失效。已在途请求可能完成此前观察到的读取。
- 库存写入不触发目录失效；价格、活跃 SKU、库存有货提示等浏览字段可以
  暂时陈旧。库存预占、核销、释放及下单规则完全保留在 MySQL。
- Redis 客户端连接/命令超时各 100 ms，重试次数 0。GET 失败后本次请求
  跳过 SET，回源；SET 失败仍返回 MySQL 结果；DEL 失败不推翻已提交保存。
  这不是整个请求耗时上界，也没有熔断或集中回源保护。
- 无效 JSON、非对象或顶层字段不匹配视为未命中；不声称验证所有嵌套字段
  损坏。Redis URL 需要有效；配置错误与服务连接故障不是同一种问题。
- `update`/`bulk_update`/外部 SQL 绕过信号；硬删除不是当前 Admin 支持的
  操作。以后增加写入口必须重新审查失效责任。

## 2026-09-26 实际证据

环境：Windows、Python 3.11.9、Django 5.2.17、DRF 3.18.1、pytest 9.1.1、
redis-py 8.1.0、MySQL 8.4.10、Redis 8.10.1。MySQL 为现有本地 Docker
服务；pytest 仅使用隔离 `test_project_one`。Redis 为新建的项目测试容器，
`127.0.0.1:6381`，无持久化卷。未修改 `.env` 或开发库。

测试数据每个用例重建：公开分类、商品、SKU 各一个，初价 129 元、可售库存
10 件；分类关联和 SKU 转移用例添加第二个商品；交易用例添加测试顾客、地址
和购物车。每个测试使用独立 UUID Key 前缀，仅删除自身前缀，未使用 FLUSHDB。
未固定随机 seed；TTL 抖动仅验证设定范围，没有做雪崩性能实验。

先在修改前运行原详情查询数测试，观察到：

```text
CATALOG_BASELINE detail_mysql_queries=2
1 passed in 6.52s
```

修改后原目录回归：`16 passed, 6 subtests passed in 7.74s`。
新增缓存集成测试：`20 passed in 12.52s`，其中命中测试的实际输出：

```text
detail_cache_sample miss_sql=2 hit_sql=1 miss_ms=10.848 hit_ms=1.901
(local single sample; not a performance benchmark)
```

它只有一组本机顺序请求，冷请求还包含首次路由初始化等开销。
这些耗时不作为性能提升倍数、p95/p99、线上延迟或 QPS 结论。
可支持的结论是：本轮首次未命中 2 SQL，后续命中 1 SQL，公开响应相同。

补充测试数据库必须为 MySQL / `test_` 前缀的断言，以及恢复后的连接池清理
后复测：`20 passed in 13.14s`，再次观察 `miss_sql=2 hit_sql=1`，本机单样本
为 `miss_ms=11.136 hit_ms=1.746`。两轮有效结果均保留，没有挑选更快结果
推导性能结论；第二轮是当前文件版本的专项结果。

20 个用例包括：

1. 未命中回填、命中响应与 SQL 数；关闭开关保留 2 SQL 基线。
2. 把 TTL 设为 1 秒，等待真实 Redis 自然过期，回源读到改名后的值。
3. 四种坏 JSON/顶层结构值回源，随后可正常命中。
4. 热缓存情况下 Product/Category 批量下架仍返回 404。
5. 提交后失效、回滚保留；分类改名使全部关联详情刷新；SKU 转移失效旧新
   两份详情；事务内有缓存和无缓存时均不回填未提交数据。
6. 把客户端 URL 临时指向本机已占位但未监听的端口，实际连接被拒绝：
   GET 回源，数据库保存成功，DEL 失败记录日志。恢复健康 URL 后旧副本
   再次命中，保留失效丢失窗口的证据。没有停 Redis 或修改全局服务设置。
7. SET、DEL 超时分别使用异常注入补充检查；这些不是实际网络超时证据。
8. 热缓存显示有货，MySQL 改为零库存后下单 Service 拒绝，购物车保留、
   不生成订单；热缓存显示 129 元，MySQL 已改为 159 元，下单快照和金额
   均为 159 元。这里调用真实 Service，不包含 Session/CSRF 或浏览器网络。
9. 一个读线程在序列化后、SET 前暂停，主线程改名并提交/删除，再放行
   读线程：旧名被回填；新请求命中旧名，而 MySQL 已保存新名。

第 9 项是确定性编排的真实 MySQL + Redis 竞态实验，测试通过代表成功
复现第一版限制，**不代表解决该竞态**。它不是高并发压力或生产流量证据。
TTL 从旧请求 SET 时重新计算，不能承诺“修改提交后严格最多陈旧 35 秒”。

相关购物车、下单、完整下单并发、取消/过期、支付 API 与模型回归：
`70 passed, 10 subtests passed in 65.37s`。这轮回归保留默认关闭缓存的交易
基线；缓存开启情况下的陈旧价格/库存与真实下单关系由上面的专项用例证明。

已运行 Django 系统检查：`0 issues`；迁移漂移检查：`No changes detected`；
依赖检查：`No broken requirements found`；`git diff --check` 通过，只有工作树
LF/CRLF 提醒。未运行开发库 migrate、浏览器验收、
生产部署、commit 或 push。

## 复现

先安装依赖，再检查原 MySQL 与独立测试 Redis：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements\dev.txt
docker compose --profile cache-test up -d --wait redis-test
$env:PROJECT_ONE_TEST_REDIS_URL = 'redis://127.0.0.1:6381/0'
.\.venv\Scripts\python.exe -m pytest backend\apps\catalog\tests\test_detail_cache.py -q -s --tb=short
```

依赖命令修改本地虚拟环境，服务命令只启动可丢弃测试 Redis；pytest 创建、
使用和清理隔离 MySQL 测试库及随机前缀 Key。不要与另一个 pytest 进程共用
同名测试库。未设置测试 Redis URL 会 skip；设置了但不可连接会失败。

基线复测与相关回归：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\apps\catalog\tests\test_public_api.py -q
.\.venv\Scripts\python.exe -m pytest backend\apps\ordering\tests\test_cart_api.py backend\apps\ordering\tests\test_order_api.py backend\apps\ordering\tests\test_order_concurrency.py backend\apps\ordering\tests\test_checkout_contention.py backend\apps\ordering\tests\test_order_lifecycle.py backend\apps\payments\tests\test_payment_api.py backend\apps\payments\tests\test_payment_models.py -q --tb=short
```

## 下一步与面试说法

当前处于 Track B 第一段：MySQL 基线 → Cache-Aside → 提交后失效 →
真实故障回源及已知一致性窗口。基础方案已具备可审阅代码和可重复证据。

下一段沿用旧值回填实验，比较版本校验等最小方案，明确价格/SKU 状态的
浏览陈旧要求后再改进；随后才进行热 Key 回源与故障集中回源实验。

本次文件范围：

- `backend/apps/catalog/cache.py`、`cache_signals.py`、`apps.py`、`views.py`：
  缓存、提交后失效和详情接入。
- `backend/apps/catalog/tests/test_detail_cache.py`：真实缓存/交易边界证据。
- `backend/config/settings.py`、`.env.example`：本地实验开关、URL、前缀和 TTL。
- `requirements/base.txt`、`compose.yaml`、`pyproject.toml`：Redis 客户端、
  可选的本地开发/测试服务和测试 marker；现有 MySQL 服务定义保留。
- `README.md`、`CONTEXT.md`、本验证记录、对应学习指南：复现与持久交接。

本次未修改已有的订单、支付、库存及前端代码，未覆盖开始前的工作树改动。
独立测试 Redis 在本次结束时保留运行；开发 Redis 未启动，应用默认仍关闭
缓存。依赖已安装到项目 `.venv`。未暂存文件、commit 或 push。

面试可准确表述：“我在商品详情加入可重建的 Redis 缓存，实验中 SQL 从
未命中 2 条降到命中 1 条；每次保留 MySQL 下架检查，下单仍使用权威价格
和库存。我验证了过期和连接失败回源，也实际复现了提交后删除仍可能被
旧请求回填的竞态，所以当前基础方案尚不保证强一致。”
