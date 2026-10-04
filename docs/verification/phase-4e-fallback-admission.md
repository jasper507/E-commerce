# Phase 4E：Redis 故障时的公开详情回源名额

## 已确认规则与范围

2026-09-27，Track B / Depth / Learning Mode。项目拥有者明确选择：
**故障回源名额已满，立即返回 HTTP 503，不排队等待。**

问题来自 Phase 4D 的实际结果：Redis 连接失败时，10 个浏览请求仍分别
查询 Product/Category 与 SKU/Inventory，共 20 SQL。请求合并减少同代
同商品重复工作，但没有限制故障下不同请求同时回源的数量。

最小设计为进程内 BoundedSemaphore（有限数量的通行证），本地候选名额
K=2。它限制此公开详情入口的受保护故障回源，不是每秒请求数、数据库
连接数或全站 MySQL 容量。2 是可复现实验参数，未测出生产安全值。

另一方案是等最多 50ms 后再拒绝：可吸收短拥挤，但占用等待线程并增加
延迟，等待时限也不限制等待者数量。用户选择立即拒绝，本轮不实现短等待。

## 实际业务路径与接口变化

```text
商品详情请求 → 捕获 Redis 代号
├─ 合法代号：维持各请求上架检查、缓存命中/请求合并
└─ 代号读取异常或非法：标记降级，在任何商品 SQL 前申请回源名额
    ├─ 取得名额：检查商品 → 加载 SKU/库存 → 返回 → finally 归还
    └─ 满员：503，不执行此请求的 Product/SKU SQL
```

- 若代号成功，但详情 payload GET 后来失败，上架 SQL 已经发生：申请名额
  只能限制后续 SKU 加载。这种拒绝有 1 SQL，不能声称所有 503 都零 SQL。
- Redis SET 失败时详情已经取到，保留成功结果，不把缓存写错误变成 503。
- 缓存关闭与 atomic 事务内主动绕过不标记 degraded，不申请故障名额。
  非法代号属于无法安全选择缓存代的降级，与连接失败原因不同。
- 同一请求可能经过外层和内层保护作用域，只实际取得一次名额。只由取得
  名额的作用域清理标记，slot 通过 finally 归还；成功、404、数据库异常
  不能泄漏名额。
- ProductDetailCache 仍每请求创建，不能跨线程共享这个状态对象。

新增响应（仅此详情故障保护满员）：

```http
HTTP/1.1 503 Service Unavailable
Content-Type: application/json

{"detail":"商品详情暂时繁忙，请稍后重试。"}
```

503 表示服务暂时无法处理，不表示商品不存在，也不是用户超出个人配额。
未增加 Retry-After 或自动重试，不承诺具体恢复时间。现有前端 catalog
请求工具能读取 detail 字符串（只读确认），本轮未改前端、未运行浏览器验收。

## 实际受控实验与原始数据

真实隔离 MySQL 8.4.10（`test_project_one`）及 Redis 8.10.1（测试端口
6381）。Redis 故障通过本机 bind 但不 listen 的端口制造真实连接拒绝，
不停止健康 Redis，不清空任何共享缓存。

先发 2 个请求，在 load_detail 用 Event 暂停，再发 8 个请求。后 8 个必须
在前 2 个释放前结束，之后放行前 2 个。人工保持重叠用于验证名额规则，
不是性能压测，也不能说任意自然 10 并发总会固定拒绝 8 个。

| 实测指标 | 结果 |
|---|---:|
| 请求总数 | 10 |
| 正常返回 200，内容与基准相同 | 2 |
| 满员返回 503 | 8 |
| 被拒绝请求的业务 SQL | 0 |
| 成功请求的业务 SQL | 每请求 2，共 4 |
| 同时处于受保护加载阶段的峰值 | 2 |
| 释放后下一请求 | 200，2 SQL |

逐请求记录与采集时间见
[`phase-4e-fallback-admission-data.json`](phase-4e-fallback-admission-data.json)。
线程计数使用 execute_wrapper，排除预建立数据库连接和准备操作。因此
“0 SQL”是零被测业务查询，不是证明没有数据库连接。

相比旧基线的 10 成功/20 SQL，本段在受控饱和时为 2 成功、8 拒绝/4 SQL。
减少查询以拒绝请求为代价，不能写成“同样成功流量提升 5 倍”。

附加目标验证覆盖：

- 404 和注入数据库加载异常后，仍能再次取得名额；异常不是被吞掉。
- 健康暖/冷缓存、缓存关闭与事务内查询，不受已占满故障名额影响。
- 注入晚 payload GET 故障，满员请求返回 503：1 上架 SQL、0 SKU SQL。
- 非法代号满员拒绝：零 Product/SKU SQL。

首次目标实际运行：`10 passed in 11.06s`。日志中的注入数据库异常、404
和 503 是测试预期路径，不是未处理的测试失败。

补充首/第二次 payload GET 故障后，最终11个保护用例均进入商品目录整体
回归：`68 passed, 6 subtests passed in 23.70s`。第二次故障发生在
singleflight 加载者复查时，满员503后禁止恢复请求加入任何遗留 Future，
并实际正常回源，验证加载登记没有残留。Django check 为0 issues、
`git diff --check` 通过，原始10请求JSON的状态、SQL及内容比对独立重算一致。

## 复现

PowerShell，根目录，已有本地 MySQL 服务运行：

```powershell
docker compose --profile cache-test up -d --wait redis-test
$env:PROJECT_ONE_TEST_REDIS_URL='redis://127.0.0.1:6381/0'
$env:PROJECT_ONE_FALLBACK_EXPERIMENT_OUTPUT="$env:TEMP\project-one-fallback-admission.json"
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_fallback_limit.py -q -s --tb=short
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests -q --tb=short
```

不要同时运行多个 pytest 进程共享该测试库。测试前缀随机，清理仅删除自身
前缀，无 FLUSHDB。现有 test_cache_burst 已适配新的 503 规则，同时记录
成功、拒绝及 SQL，没有扩大名额掩盖拒绝，也没有覆盖 Phase 4C/4D 历史数据。
其中未合并/合并两种策略现在都使用本轮的故障名额，不能把当前故障结果当成
原历史 20 SQL/全部成功实验。

## 结论边界与下一步

本段可以证明名额上限、满员拒绝位置和错误退出后的名额恢复。它仍不能
证明生产容量、全站 MySQL 保护、Redis 黑洞超时表现或跨进程总上限。

4 个进程各 K=2，可能合计 8 个受保护工作；其他接口、正常上架检查和
晚故障之前的 SQL 不受本段约束。该 semaphore 不是限速，也不是熔断：
每个请求仍先尝试 Redis，没有因为连续失败而暂时跳过 Redis。

没有新依赖、部署配置、模型、迁移或交易路径变化；新增 503 已由用户确认。
未修改开发数据库、前端或 `.env`，未 commit/push。

下一步由用户复述成功降级与故障保护的取舍，整理缓存故事的演示路径；
是否需要 Redis 熔断或跨进程保护，再依据超时/多进程实验决定，不默认堆技术。
