# Phase 4H：先核对面试主线，再复现缓存穿透

2026-09-27 的真实基线与候选记录保留如下；2026-09-30 的最小实现和复测
追加在文末。本段属于 Track B / Depth / Learning Mode。

## 文档核对与范围

PROJECT_CHARTER.md 第1、2节和 PROJECT_INSTRUCTIONS.md 的面试优先门槛
要求一条最小业务闭环与三条技术故事：库存并发/秒杀、缓存一致性/故障
回退、支付状态/幂等。用户要能独立解释、修改、验证，技术不为简历名词。

用户当前要求控制首个项目深度，CONTEXT第27、28节与Phase4G学习指南
约定：最小读取熔断→穿透→多Key雪崩→缓存故事→支付。本段贡献是观察
不存在商品的重复数据库查询，比较最小方案及可见性代价，不扩展治理。

Charter头部及Phase4勾选、README部分活跃段落仍是旧进度；当前Phase4G
实现与证据已更新到CONTEXT第28节。旧段落不能用来重复做已完成MVP或
重新加入已有SingleFlight。本轮核对而不重写整体文档/有日期的历史记录。

## 当前请求流与方法

当前公开详情：捕获Redis代号→MySQL按slug检查商品/分类上架→找到商品
才继续取详情。不存在slug在SQL后返回404，没有保存这个不存在结果，
所以再次请求仍执行相同查询。Redis健康，故障熔断不处理这种正常404。

- Windows，Python3.11.9、Django5.2.17、MySQL8.4.10、Redis8.10.1。
- MySQL测试端口3307，Redis测试端口6381；只写test_project_one，UUID
  Key前缀清理，不用FLUSHDB；不写开发库/开发Redis，不修改.env。
- 真实DRF APIClient，经路由/视图/ORM至JSON；顺序请求，没有HTTP服务器
  或并发压测。setup、预热及直接exists检查排除在请求SQL计数之外。
- 固定现有Mouse M1，SKU M1-BLACK、价格129.00、库存10。missing slug
  使用UUID并先确认数据库中不存在。同一地址和不同地址分开记录。
- 每场景6请求，共18；SQL分商品查询与SKU查询，命令记录来自应用
  execute_command，不含测试控制命令/所有握手，不是Redis服务端执行数。
- 不计响应性能，本次只证明查询量与响应语义。99个目录测试是上阶段
  历史结果，本轮没有重跑；本次不宣称新的生产容量、延迟或优化比例。
- Git基点4e1a1ae211bd9bfab37c14c85aed11d3cd580eef，工作区有未提交
  实现，单独该commit无法复现。采集源码SHA256如下：

| 文件 | SHA256 |
| --- | --- |
| backend/apps/catalog/cache.py | E87ED8350340C6B98D5970A303917467661694658758ECC40D45292080DBB370 |
| backend/apps/catalog/views.py | 48E578561C344ED46D6B4A1A87504AD846347DF00466CB3F00C4886DD69E6CA5 |
| backend/apps/catalog/tests/test_cache_penetration.py | DE17E04531BBDE090CC910BA20FD01CC025E46C96B08F8B4CEE6D23225112922 |

## 实际基线

| 场景 | 请求 | 状态 | 商品SQL | SKU SQL | 应用GET | 熔断状态 |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| 同一不存在slug | 6 | 全部404 | 6 | 0 | 6 | Closed |
| 六个不同不存在slug | 6 | 全部404 | 6 | 0 | 6 | Closed |
| 已有商品暖详情 | 6 | 全部200且内容正确 | 6 | 0 | 12 | Closed |

目标测试实际 **1 passed in 9.54s**。原始记录：
[phase-4h-penetration-baseline-data.json](phase-4h-penetration-baseline-data.json)。
每个缺失请求都有1商品SQL和1代号GET；404之前不走payload路径。
这证明当前重复缺失请求重复查库，不证明MySQL已过载或恶意流量容量。

## 最小候选与新判定

Negative Cache（负缓存：暂时保存不存在结果）候选只针对确实没有Product
的slug；已有商品/分类隐藏仍做SQL检查并返回原404，不写负标记。
Key包含捕获的generation与slug，值只认一个明确标记，TTL候选5秒。

替代是保持每次查库：路径简单、没有负记录造成的新商品可见性窗口，
但重复缺失查询不减少。负缓存减少同地址重复SQL，代价是健康商品多
一次Redis GET、短期Key占用，以及绕过save时存在可见性窗口。
不同随机slug仍各自查一次，首次并发也没有合并，本段不处理它们。

本候选要求确认的新Oracle（测试判定：哪些结果算正确）：

1. 同一缺失地址全部404；第一请求1SQL，标记有效时后续0SQL；过期再查。
2. 正常200仍有Product/Category数据库检查；隐藏商品不做负缓存。
3. 正常save且成功换代后，新请求不选择旧负标记；旧在途写只能写旧代。
4. bulk_create/直接SQL/批量改slug可能不换代而暂时仍404；TTL从实际
   写标记起算，不能保证“数据库创建后严格最多5秒”。
5. 负标记GET进读取熔断，读失败/跳过沿用既有2名额/满员503；负SET
   尽力执行，失败保留404。禁用、事务内与已降级时不使用负缓存。

PROJECT_CHARTER.md第762行要求在用户确认正确行为前，不同时定义新
Oracle和完整实现。现有用户确认的是熔断与本段推进方向，这组负缓存
规则正在征求确认；没有把5秒或后续0SQL写成已验证事实。

## 复现与停止标准

已有隔离测试服务运行时，在仓库根目录运行：

```powershell
$env:PROJECT_ONE_TEST_REDIS_URL='redis://127.0.0.1:6381/0'
$env:PROJECT_ONE_PENETRATION_OUTPUT="$env:TEMP\project-one-penetration-baseline.json"
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_cache_penetration.py -q -s --tb=short
Remove-Item Env:PROJECT_ONE_PENETRATION_OUTPUT
```

pytest创建/销毁隔离测试库，清理UUID前缀；不要并行运行共享库/Redis的
pytest进程。指定服务不可达失败，未设URL会跳过，跳过不是通过。

确认规则后，只接入最小负缓存并验证同地址SQL对照、TTL、正常save后
可见、隐藏/上架检查与故障行为。证据足够后停止，继续多Key雪崩与支付。
本轮新增测试/原始JSON/学习验证文档，更新CONTEXT；不修改业务代码、
依赖、配置、迁移、交易、前端、开发数据，未commit/push，既有改动保留。

## 2026-09-30：最小负缓存实现与复测

用户给出首个测试的判断：两次 HTTP 请求独立，原实现没有保存不存在结果，
因此同一缺失地址的重复请求会重复查 MySQL；短期记住同地址的不存在结果
后，顺序 6 次请求应只有首次查询商品。最初只新增该测试，不改业务代码，
实际运行 **1 failed in 7.91s**：观察到商品 SQL 数组
`[1, 1, 1, 1, 1, 1]`，与预期 `[1, 0, 0, 0, 0, 0]` 不同。

之后复用 Redis，在 Product 查询前按捕获的 generation + slug 检查短期
`missing` 标记；只有 Product 确实不存在才写 5 秒标记。详情查询改为一次
获取 Product 与 Category，再显式检查二者上架状态，保持隐藏商品/分类的
404 且不误记“不存在”。普通 Product.save 提交后切换 generation，新请求
不再选择旧标记。标记 GET 使用原读取熔断与故障回源名额，SET 尽力执行。

隔离测试库 `test_project_one`、测试 Redis 6381、UUID Key 前缀和 DRF
APIClient 与基线一致；没有操作开发数据库或开发 Redis。验证结果：

| 观察项 | 原实现 | 最小实现 |
| --- | --- | --- |
| 同一不存在 slug 顺序 6 次 | 6 次商品 SQL；全 404 | 1 次商品 SQL；全 404 |
| 标记过期 | 不适用 | 重新执行商品 SQL，仍 404 |
| 正常 save 后创建同 slug | 不适用 | 切代后新请求 200 |
| 已有商品/分类下架 | 404 | 404，不写负标记；重新上架可见 |
| 标记 GET 故障且回源名额满 | 不适用 | 503、商品 SQL 0 |
| 标记 SET 故障 | 不适用 | 原 404 不变，下次再查 SQL |

目标测试 **8 passed in 8.51s**；目录回归 **107 passed, 6 subtests
passed in 36.32s**。首次目录回归曾有 4 处失败：一处旧故障注入按
“第几次 GET”命中新增标记读取，三处旧断言仍计较少的 GET。将注入目标
限定为详情 Key、并按实际新请求链更新计数后，相关 5 个目标用例通过，
再运行上述完整目录回归。优化前 JSON 未覆盖；基线测试现在显式关闭负
标记以重放原路径。

这些结果只证明同一缺失地址的顺序请求减少了商品 SQL，不是吞吐量或
生产抗压指标。5 秒是首轮实验值；过期测试检查真实 Redis TTL，并缩短
该测试 Key 的剩余有效期来验证过期路径，没有等待整整 5 秒。健康请求
多一次负标记 GET。不同随机 slug、同一 slug 首次并发到达可能仍各查库；
绕过 save 的写入、切代失败或晚到写标记可能带来短暂旧 404，不能承诺
“商品创建后严格最多 5 秒可见”。

验证命令在仓库根目录运行，`PROJECT_ONE_PENETRATION_OUTPUT` 保持未设置，
避免覆盖历史记录：

```powershell
$env:PROJECT_ONE_TEST_REDIS_URL = 'redis://127.0.0.1:6381/0'
Remove-Item Env:PROJECT_ONE_PENETRATION_OUTPUT -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests/test_cache_penetration.py -q --tb=short
.\.venv\Scripts\python.exe -m pytest backend/apps/catalog/tests -q --tb=short
```

另在 `backend` 目录运行 `..\.venv\Scripts\python.exe manage.py check`，
实际得到 `System check identified no issues (0 silenced).`；仓库根目录
`git diff --check` 通过。历史基线 JSON 没有改写。当前未提交工作树的
源码 SHA256（单独的 Git HEAD 不包含本段工作）为：

| 文件 | SHA256 |
| --- | --- |
| backend/apps/catalog/cache.py | 6A0858BFFED7DB712ADACCE6E9A60B168D41894913AFACC49DFD13D1FDD24EEA |
| backend/apps/catalog/views.py | 3F22F818786C48686370EFE65D6750E7B2FADD04AB6CADA1EA7E0F6A0CAB7CFD |
| backend/apps/catalog/tests/test_cache_penetration.py | EC89F7F235D19604796B36CCE57EA198737789A23648599061D174BD4A598C7F |
