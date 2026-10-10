# Phase 4F：慢故障、慢加载与恢复边界

阅读入口（2026-10-10补链）：[本片学习指南](../learning/phase-4f-slow-faults-study-guide.md)。仅增加导航，以下日期、结果与限制保留原实验记录。

2026-09-27，Track B / Depth / Learning Mode。第一段只增加实验和证据，
保留 Phase 4B/4D/4E 的业务实现，不接入熔断、不改变拒绝规则。

业务问题是：商品详情依赖出错时，除了“还能不能返回”，还必须知道请求
等多久、重复做了多少数据库工作，以及依赖恢复后读到的内容是否正确。
本段加强 Redis 缓存一致性与故障降级这条面试主线，不扩展电商功能。

## 方法与环境

- Windows，Python 3.11.9，Django 5.2.17，真实 MySQL 8.4.10 与 Redis 8.10.1。
- 仅 `test_project_one` 与测试 Redis `127.0.0.1:6381/0`；每个 fixture 使用
  UUID 前缀，只清理自己的 Key，没有 FLUSHDB，没有修改开发库。
- 固定商品 Mouse M1、SKU M1-BLACK、初价129.00、库存10；价格恢复实验改为169.00。
- DRF APIClient 经路由、视图、ORM到响应 JSON；未经过真实 HTTP 服务器。
  并发线程预先建立数据库连接，业务计时与 SQL 不包含连接和开始会合。
- SQL 由线程本地 execute_wrapper 捕获；每个正常详情请求仍有一次
  Product/Category 上架检查，命中缓存不等于零数据库查询。
- 每个实验首次有效采集一次，三份 JSON 保存完整逐请求记录及 UTC 时间。
  之后目录回归再次执行三个实验，但没有覆盖首次原始记录。未测生产吞吐、
  CPU/容器资源饱和或长期统计分布，不能据此选生产容量。
- Git 基点 `4e1a1ae211bd9bfab37c14c85aed11d3cd580eef`，工作区有此前阶段未提交
  修改；仅该 commit 无法复现当前实现，需保留当前源码和对应实验文件。

采集对应文件 SHA256：

| 文件（仓库相对路径） | SHA256 |
| --- | --- |
| backend/apps/catalog/cache.py | AAF607FDC1215AFAB8412CDB3842FD495E6CF0F293B1BF54D5F3B8EEFF2C5D37 |
| backend/apps/catalog/views.py | 48E578561C344ED46D6B4A1A87504AD846347DF00466CB3F00C4886DD69E6CA5 |
| backend/apps/catalog/cache_signals.py | 505775C4736569EB7067A917C725B18F34B1D2343A8742B15E67BB95EB478B06 |
| backend/apps/catalog/tests/test_redis_slow_recovery.py | DF3E933D1C861F45488449594F8BFF1B8D7F65BAC2F3C512A6F6855B00C30753 |
| backend/apps/catalog/tests/test_slow_detail_loader.py | 9FB9B8623424CC3EBB016204251944CD5E1F12A3ECFF9283D4B25879CF6364C6 |

## 实验一：Redis 接受连接，但暂时不处理命令

对独立测试 Redis 执行 `CLIENT PAUSE 2500 ALL`，随后自动恢复。与立即连接
拒绝不同，这里实际经历客户端命令超时。暂停期间客户端仍可连接，Redis
恢复后可能处理已收到的命令，因此“客户端超时”不能证明“写入没有执行”。
参见 [Redis 官方说明](https://redis.io/docs/latest/commands/client-pause/)。

先预热，再顺序运行四轮，每轮6个并发请求。第一轮正常；第二、三轮都在
同一暂停窗口内；最后控制连接 PING 确认恢复再发第四轮。没有人为保持
数据库加载重叠，本地故障回源名额仍为2。

| 轮次 | 请求 | 200 | 503 | 业务 SQL | 详情加载 | 应用 GET 尝试 | TimeoutError | 回源名额峰值 | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 正常暖缓存 | 6 | 6 | 0 | 6 | 0 | 12 | 0 | 0 | 12.041 | 13.897 |
| 首轮慢故障 | 6 | 2 | 4 | 4 | 2 | 6 | 6 | 2 | 109.918 | 111.025 |
| 继续慢故障 | 6 | 2 | 4 | 4 | 2 | 6 | 6 | 2 | 112.713 | 115.121 |
| 自动恢复 | 6 | 6 | 0 | 6 | 0 | 12 | 0 | 0 | 9.828 | 10.650 |

共24请求：16个200、8个503、20 SQL、36次应用GET尝试、12次超时。
正常及恢复时所有成功 JSON 与预热基准一致，故障时成功请求也一致。
第一故障轮在暂停后0.046–2.495ms开始、109.671–111.894ms结束；
第二轮120.164–123.066ms开始、233.481–237.230ms结束，均在2500ms内。
恢复轮开始时间为暂停后2568.257–2570.141ms。

证据含义：8个拒绝请求都是零业务SQL，但都先尝试 Redis 并经历约100ms
超时。**“名额满立即拒绝”是在发现Redis故障之后立即拒绝，不是请求开始
就立即结束。** 继续故障轮仍有6次GET/6次超时，未实现连续失败后暂时绕过。
2成功/4拒绝是这次自然调度的结果，不是任意6并发都会得到的固定比例。

GET记录是应用客户端 execute_command 的逻辑调用，排除测试控制命令，
不等于 Redis 服务端执行次数或握手的全部网络命令。峰值2为成功取得
回源名额作用域数，不是实测同时执行SQL数或数据库连接总数。
每轮仅6个样本，采用 nearest rank，p95恰为最大值；不可用于生产SLO。

原始记录：[phase-4f-redis-data-slow.json](phase-4f-redis-data-slow.json)。

## 实验二：Redis 正常，原加载者超过等待预算

先预热再正常换代制造冷缓存。用 Event 在 `load_detail` 的SKU查询前暂停
原加载者，随后发4个等待者。实际 Future 等待预算仍为200ms，直到5个
请求都进入加载段才统一放行。未伪造Future超时，也没有注入Redis故障。

| 实测指标 | 结果 |
| --- | ---: |
| 请求 / 正常且JSON正确 | 5 / 5 |
| 等待200ms后超时的请求 | 4 |
| 详情加载次数 | 5 |
| 业务 SQL | 10（5次上架检查 + 5次SKU加载） |
| 同时进入加载段峰值 | 5 |
| 申请故障回源名额次数 | 0 |
| 应用Redis GET / SET尝试 | 15 / 5 |

原因：等待超时表示“共享结果尚未完成”，不表示Redis坏了；缓存仍正常，
`degraded=False`，所以原有故障名额不限制这些独立加载。
这是请求合并与故障限并发之间的边界，两种机制不能互相替代。

加载段峰值包含人工 Event 等待，不是5条SQL同时执行。242.589–254.458ms
的响应计时包含人为暂停，不是MySQL服务器慢查询实验，不是压测成绩，也
不证明200ms是总响应截止时间。超时后SQL仍继续执行，原加载者不被取消。

原始记录：[phase-4f-slow-loader-data.json](phase-4f-slow-loader-data.json)。

## 实验三：提交后换代失败，恢复重新命中旧价

这个实验独立于暂停实验。临时让应用连接本机不监听的保留端口，制造真实
连接拒绝；健康测试Redis保留原Key。这样可以确认本次换代SET无法连接，
避免将“暂停超时”误判为“写一定没有执行”。

| 顺序 | 实际行为 | 详情价格 | 业务 SQL |
| --- | --- | ---: | ---: |
| 1 | 冷读并缓存旧价 | 129.00 | 2 |
| 2 | MySQL正常SKU.save提交169.00；on_commit换代连接失败；故障期回源 | 169.00 | 2 |
| 3 | 恢复原Redis连接，代号仍是旧代 | 129.00 | 1 |
| 4 | 再次正常save，成功换代后新请求 | 169.00 | 2 |
| 5 | 新代暖缓存 | 169.00 | 1 |

测试确认真实 `catalog_cache_invalidation_failed` 日志，直接查MySQL仍为
169.00，恢复后旧详情还剩32秒TTL。不是数据库回滚，也不是正常save未触发
信号；**数据库已提交而缓存代号未变，旧副本恢复可读。** 后续成功save换代
恢复新读，只是验证成功换代边界，不是新增自动修复机制。

Redis读取熔断本身不能修复此问题。恢复时清理/换代还需要定义成功条件、
多进程行为、写故障记录与重试；只在单进程记标记可能丢失，也可能漏掉
其他进程的失败。当前仍接受浏览展示可能陈旧，下单仍取MySQL权威价格。

原始记录：[phase-4f-redis-data-stale.json](phase-4f-redis-data-stale.json)。

## 运行结果与复现

首次两个目标命令：慢加载 `1 passed in 7.43s`，慢Redis及恢复
`2 passed in 11.50s`。新增3个用例后目录整体回归实际结果：
**71 passed, 6 subtests passed in 26.90s**。

Django check为0 issues，`git diff --check`及新文档空白检查通过；三份JSON
的请求数、状态、SQL、GET与超时计数经独立重算一致。名额峰值通过复核
acquire后/finally归还的观测逻辑确认，逐请求JSON没有名额进入/退出时间线，
不能仅从这些记录独立重算峰值。

在已有本地测试服务运行时，PowerShell从仓库根目录依次执行：

```powershell
$env:PROJECT_ONE_TEST_REDIS_URL='redis://127.0.0.1:6381/0'
$env:PROJECT_ONE_SLOW_LOADER_OUTPUT="$env:TEMP\project-one-slow-loader.json"
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_slow_detail_loader.py -q -s --tb=short
$env:PROJECT_ONE_SLOW_REDIS_OUTPUT="$env:TEMP\project-one-slow-redis.json"
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_redis_slow_recovery.py -q -s --tb=short
Remove-Item Env:PROJECT_ONE_SLOW_LOADER_OUTPUT,Env:PROJECT_ONE_SLOW_REDIS_OUTPUT
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests -q --tb=short
```

前两个命令分别生成慢加载原始记录以及带 `-slow`、`-stale` 后缀的两份记录；
最后一个验证目录功能和实验隔离的回归。不要并行运行共享该测试库/Redis的
pytest进程：本测试会暂停整个测试Redis 2.5秒。未设置测试URL会跳过Redis
集成测试，不能把跳过当通过；指定服务不可用则应失败。

## 下一段设计门槛

| 已复现的问题 | 最小候选 | 有意义的替代与代价 | 还需确认/取得的证据 |
| --- | --- | --- | --- |
| 持续故障每请求仍等Redis超时 | 本地读熔断：连续失败后暂时绕过，少量探测恢复 | 保持短超时，简单且及时发现恢复，但持续支付等待成本 | 连续失败判定、暂停时间、探测者限制；对照GET数、延迟、SQL及503，不预写提升结论 |
| 健康缓存慢加载导致超时后重复回源 | 单独限制独立加载的并发 | 继续等待共享结果，少重复SQL但占请求资源；增加等待也不保证任务会完成 | 正常Redis时是否允许满员503，这是新的可用性规则，原故障503授权不能直接替代 |
| 故障写入后恢复命中旧副本 | 比较恢复换代与可靠失效补偿 | 保留TTL容忍展示陈旧，复杂度低但不能保证立即更新 | 明确允许的展示陈旧；换代失败、重启、多进程和故障记录必须各有验证 |

建议下一段先讲清读熔断的状态与恢复条件，只解决重复等待，再单独决定恢复
一致性策略。当前没有接入熔断或自动修复，没有改变交易、依赖、模型、迁移、
接口、前端、部署配置或开发数据，没有commit/push。
