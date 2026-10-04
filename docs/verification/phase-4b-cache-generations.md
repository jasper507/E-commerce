# Track B / Phase 4B：旧回填的分代隔离

## 业务问题与判定标准

本段支持商品浏览闭环和 Redis 一致性面试故事。Phase 4A 已复现：读请求在
序列化后暂停，管理员提交修改并删除缓存，旧请求随后回填，使后来的请求
再次读到旧详情。原缓存 TTL 只限制每次 SET 后的存活时间。

本段不变量：**目录写入提交且提交回调成功换代后开始的新详情请求，不读取
旧代缓存。** 在途请求返回旧结果以及旧代物理写入都允许；新代必须回源
建立当前数据，随后可命中。不是数据库提交瞬间起的强一致承诺。

商品/分类下架仍实时查 MySQL；价格、SKU 和库存提示只用于浏览，订单金额、
库存预占和支付事实仍由 MySQL 决定。没有新字段、迁移、依赖、服务或业务 API。

## 最小方案与替代方案

采用全局随机 Generation + 分代 Key：

- 代号 Key：`{prefix}:catalog:generation:v2`，值为 UUID hex，不设 TTL。
- 详情 Key：`{prefix}:catalog:detail:v2:{generation}:{product_id}`，保留
  `30 ± 5` 秒 TTL，命中不续期。
- 在 `get_object()` 首条数据库读取之前构造 `ProductDetailCache`，捕获代号。
  同一请求始终使用这一代，回填时不重新读代号。
- 代号缺失时 GET → SET NX 随机提议 → 再 GET 实际赢家。随机重建避免 Key
  丢失后复用整数初值并选中仍存活的旧详情。
- Product/Category/SKU 普通 save 在 on_commit 成功后 SET 一个新随机代号。
  不再查询分类关联商品，也不再捕获 SKU 旧归属；全局换代覆盖这些范围。
- 旧请求晚 SET 只会写旧代；正常请求不再选择旧代，旧副本由 TTL 回收。
- Redis 读取故障或代号非法：本请求禁用缓存，读 MySQL，不回填。
  换代 SET 失败只记录日志，不推翻已提交的数据库保存。

选择依据与代价：

| 方案 | 收益 | 代价/局限 |
| --- | --- | --- |
| 删除 + TTL 基线 | 简单、局部失效 | 已复现删除后的旧回填 |
| 延迟双删 | 常见时序下可能减少窗口 | 延迟不能覆盖任意慢请求，需要可靠调度 |
| WATCH 条件回填 | 版本比较与写入可原子执行 | 额外监视、事务和失败处理；仅 GET 比较后 SET 不足 |
| 全局分代 Key（本段） | 通过 Key 隔离后续读取，不需要锁或条件写 | 所有详情逻辑失效；多一次 Redis GET；旧代短期占内存 |
| 数据库/每商品持久版本 | 可进一步细化失效或加强故障恢复 | 扩大表结构、写路径和跨分类/SKU 维护范围 |

当前低写小目录先用全局方案建立正确性证据，不据此认定高写或生产场景也
适合。命中现在是 **2 次 Redis GET + 1 SQL**；冷代号初始化还有 SET NX
和第二次代号 GET。正常未命中仍 2 SQL，回填多一次 SET。没有性能优胜结论。

## 环境与实际运行

2026-09-26，现有 Windows 项目 `.venv`：Python 3.11.9、Django 5.2.17、
DRF 3.18.1、pytest 9.1.1、redis-py 8.1.0；Docker MySQL 8.4.10 和
Redis 8.10.1，沿用 Phase 4A 服务。仅访问隔离 `test_project_one` 和
`127.0.0.1:6381` 的逐测试 UUID 前缀，没有 FLUSHDB、开发库迁移或数据修改。

测试数据每例重建一分类、一商品、一 SKU，初价 129 元、可售库存 10；
全局失效/归属测试添加第二商品，交易边界测试添加测试顾客、地址、购物车。
并发实验为进程内测试线程 + 独立数据库连接，不是浏览器网络或高并发负载。
没有性能 seed、预热轮次、QPS 或 p95/p99 测量；随机 UUID 不断言具体内容，
TTL 只验证范围或临时设 1 秒进行自然过期实验。

### 失败到修正的实际过程

1. 修改前原延迟旧回填用例：`1 passed in 6.08s`。该旧判定标准确认旧值
   被回填，并非一致性正确。
2. 只把判定改为“后来的请求返回新名”，原实现失败：

   ```text
   assert warm_payload["name"] == "New committed name"
   AssertionError: assert 'Mouse M1' == 'New committed name'
   1 failed in 6.95s
   ```

3. 接入分代后原缓存专项：`20 passed in 11.82s`，包含修正后的同一竞态：

   ```text
   CACHE_GENERATION late_old_set=true later_read=new miss_sql=2 next_hit_sql=1
   detail_cache_sample miss_sql=2 hit_sql=1 miss_ms=37.149 hit_ms=2.319
   (local single sample; not a performance benchmark)
   ```

4. 额外代号测试：`8 passed in 8.74s`，包含保留的 delete-only 最小反例：

   ```text
   delete_only_protocol_counterexample stale_refill=True (ordered storage experiment)
   ```

上述耗时都保留，不用一组冷/热请求推导性能改善。额外 baseline 用例用真实
MySQL/Redis 直接编排旧协议，是可重新运行的存储层反例；它没有冒充原完整
API。修正后的主竞态及首条 SQL 窗口验证仍走真实详情 API。

### 专项覆盖的行为

- 原 20 项：命中响应/查询量、禁用、过期、坏 JSON、下架、提交/回滚、分类
  改名、SKU 转移、事务绕过、真实连接被拒绝回源、写异常注入、库存/价格
  与下单权威边界、延迟旧回填隔离。
- 首条 Product SQL 已读旧名、`get_object()` 尚未返回时暂停，提交新名并
  换代后放行：旧响应和旧 Key 可以存在，但新代没有被污染。
- 暂停旧 SET，提交换代，再仅删除本测试代号 Key，重建第三个随机代号：
  放行旧 SET 后新请求仍读新值，没有重用旧命名空间。
- 两线程都真实 GET 缺失后用 Barrier 同步，各提出不同 UUID、真实 SET NX，
  两者最终获得同一个 Redis 获胜值，没有用 Mock 替代原子写入。
- 修改一个商品使另一个未修改商品首次 2 SQL、随后 1 SQL，证明全局成本。
- 旧详情以真实 1 秒 TTL 自然过期，代号 TTL 为 `-1`（没有到期时间）。
- SKU 普通 save 从 129 改为 169 元，换代后新详情展示 169，后续命中。
- 非法代号回源，正常 save 换代后恢复缓存。

相关目录、购物车、下单及支付 API 回归：
`65 passed, 28 deselected, 14 subtests passed in 47.66s`。28 个 Redis 用例
已在前两次专项中实际运行，这轮用 marker 排除，避免重复执行；本轮不是
全项目回归，没有重跑未修改的库存策略实验或完整并发压力场景。
Django `check` 为 0 issues，
`makemigrations --check --dry-run` 为 No changes detected，diff 空白检查通过。

## 保留的限制

- 只保证成功换代后的新请求。MySQL 提交与 Redis 回调之间有窗口；提交后
  进程崩溃或 Redis 换代失败仍可旧读，连接恢复不自动修复失效丢失。
- 真实连接故障用例继续观察到：MySQL 保存成功、换代失败，恢复后仍可命中
  旧代。故障用例不是通过“恢复必最新”的判定；保留该反例。
- 批量 update/bulk update、外部 SQL 和绕过 Admin 的硬删除不发 save 信号。
  当前三个目录 Admin 均禁止硬删除；本段没有扩大删除范围。
- 同代的库存提示仍可能直到 TTL 才更新。交易逻辑完全保留，不读取缓存。
- 全局换代会使所有详情回源，旧 Key 短期占内存；没有击穿、雪崩或集中回源
  容量结论，也没有熔断/请求合并。后续应测量该成本再决定局部版本或热点保护。
- 单 Redis 当前进程上的随机重建不等于处理 Redis 回滚旧快照、复制延迟或
  生产拓扑故障。缓存版本没有被宣称为订单或库存事实。

## 复现与文件范围

确保原 MySQL 和独立测试 Redis 运行，然后从根目录执行：

```powershell
$env:PROJECT_ONE_TEST_REDIS_URL = 'redis://127.0.0.1:6381/0'
.\.venv\Scripts\python.exe -m pytest backend\apps\catalog\tests\test_detail_cache.py backend\apps\catalog\tests\test_cache_generation.py -q -s --tb=short
```

两次专项执行分别观察 20 和 8 个用例，合并命令应包含这 28 个用例。
pytest 仅创建/清理隔离测试库和随机前缀 Key；不得并行共用同名测试库。
没有测试 Redis URL 时 skip，有明确 URL 但不可连接时 fail。

相关 API 回归：

```powershell
.\.venv\Scripts\python.exe -m pytest backend\apps\catalog\tests backend\apps\ordering\tests\test_cart_api.py backend\apps\ordering\tests\test_order_api.py backend\apps\payments\tests\test_payment_api.py -m 'not redis_integration' -q --tb=short
```

修改：catalog 的 `cache.py`、`cache_signals.py`、`views.py`、原缓存测试；
新增 `test_cache_generation.py`、本记录和对应学习指南；同步 CONTEXT、
英文/中文 README；Phase 4A 记录增加历史说明和后续链接。
没有修改 settings、依赖、Compose、模型、迁移、前端、订单/库存/支付实现。
没有改 `.env`、开发数据库，没有暂存、commit、push 或部署。

## 面试与下一步

本段可独立讲述：删除式 Cache-Aside 基线 → 实际旧回填反例 → 分代 Key
方案与全局成本 → 成功换代后的新请求隔离 → 故障失效仍存窗口。
完整教学和追问见[学习指南](../learning/phase-4b-cache-generation-study-guide.md)。

下一步观察热点冷启动与全局失效的回源数量，以及 Redis 故障集中回源。
先测得问题，再决定是否采用请求合并或更细粒度版本，不先堆分布式锁。
