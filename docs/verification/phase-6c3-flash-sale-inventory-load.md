# Phase 6C-3：有限批次的库存策略负载与连接成本

日期：2026-10-08。Depth / Learning；服务于库存并发与秒杀技术主线。

## 本片问题与设计

6C-2已经验证同完整事务的NOWAIT与条件UPDATE候选正确性及受控等待行为。本片测相同有限负载下的业务结果、重试和连接成本。正式代码与默认NOWAIT保持；候选仍通过测试替换私有库存步骤，不新增公开接口或配置。

固定8个工作线程、24位不同顾客，各买1件同活动SKU；活动价89、普通价129、购物车数量2/3交替。库存两档为24与6。请求规则两档：仅一次，或仅stock_busy时总共最多3次，原Key、退避5/10ms；库存不足不重试。两策略×两库存×两规则×3次重复，共24个测量批次，策略和条件顺序轮换。每批重新建SKU/活动/用户/地址/购物车；另做每策略1次无竞争预热，排除计时样本。

业务Oracle先确定：成功数<=库存，6类事实数=成功数，可售=初始-成功、预留=成功；每成功顾客一套qty1/HELD1/RESERVED/RESERVE(-1,+1)，活动价/归属/地址正确；失败顾客Key/资格/预占无残留，购物车不变。NOWAIT有限重试允许库存未卖满，成功率作为成本结果。1205/1213/其他异常原样保存，批次invalid，不能悄悄排除失败或改成stock_busy。

## 测量口径

连接与数据提前准备。一次服务计时直接包create_flash_sale_order，传入准备好的User对象；不把测试的额外User查询或建连接混入业务耗时。全部worker预开连接后共同t0放行，8个会话先由既有应用账号PROCESSLIST只读确认。计时期间不拦截SQL或轮询服务器，不人工保持事务。

- attempt：单次业务调用起止，包含ORM、提交/回滚和数据库等待。
- journey：worker开始任务至最终结果，包含全部调用和真实退避。
- queue：t0至worker开始该顾客任务，是压测执行器排队。
- batch end-to-end：t0至顾客终值，含执行器排队；不含HTTP/前端/认证/支付。
- batch wall：t0至最后一个顾客终值。terminal吞吐=24/wall；成功订单吞吐=created/wall。均是有限批次观察，不是生产持续容量。
- 分位数用nearest-rank ceil(p*n)。24旅程的p95为第23名、p99为最大值；记录n及所有原值，不平均三轮分位数。延迟必须旁列created/busy/soldout数量，避免快速拒绝让全结果延迟显得较低。
- 每worker一条连接贯穿全批次，退避仍保持连接。连接开放区间截取到[t0,最后终值]，相加得connection-seconds；包含等待/退避/空闲，不是SQL CPU或锁等待秒。
- 服务活跃区间合计/峰值包含Python和数据库等待，不是同时执行SQL数。峰开放8主要由固定线程设计决定，不表示最小资源需求。

当前两种成功预占路径都使用两条库存SQL：NOWAIT的SELECT+save UPDATE，候选UPDATE+GET。因此不预设候选必然少数据库往返。

## 环境与保护

基点3f3afbc，工作区与两远端一致后复用既有codex/flash-sale-baseline。MySQL8.4.10健康于127.0.0.1:3307；只读预检test_project_one不存在、可见测试连接0、应用已有隔离库权限、开发schema表26。Session READ-COMMITTED，max_connections151。没有启停服务、扩GRANT或写开发业务数据。

宿主AMD Ryzen7 8840HX、12核24逻辑CPU、物理内存16,299,335,680字节；Docker可见24CPU、内存7,889,154,048字节，MySQL容器未设置额外CPU/内存限额（0）。没有资源独占或CPU固定，不保证其他宿主活动不影响结果。

实验连接单独复制线程的settings_dict和OPTIONS，connect_timeout5/read_timeout10/write_timeout10，SESSION innodb_lock_wait_timeout5；只影响测试会话，不修改配置文件或另一线程，超时用于失败/清理而非性能保证。该client参数已由独立连接的只读SELECT1验证接受。

pytest临时test_project_one负责建表/迁移/夹具/清理，显式DJANGO_ENV=test、支付异步与catalog缓存关闭。默认不执行负载实验，需PROJECT_ONE_TEST_FLASH_SALE_LOAD=true；普通回归保持关闭。

## 实际运行与数据

目标命令（先设置已说明的隔离MySQL与实验环境变量）：

```powershell
$env:PROJECT_ONE_TEST_FLASH_SALE_LOAD = 'true'
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_inventory_load.py -q -s --tb=short
```

首次通过：**1 passed，23.58秒**。完整执行2个预热与24个测量批次；测量共576旅程、767调用、250张订单，所有业务校验通过，0无效批次/0意外数据库异常/0观察到的1205或1213。NOWAIT测量调用出现383次真实3572，正常计入繁忙与重试，不能当成意外数据库故障。测试总耗时含建数与校验，不是吞吐指标。

模块回归显式关闭负载开关：**29 passed、1 skipped、260 subtests passed，36.58秒**。默认跳过项就是本片新实验；本轮未重跑完整ordering/payments或真实MQ/Redis。后检临时库不存在、可见测试连接0、开发schema表前后26；check0 issues、迁移漂移No changes detected。

独立分析重算命令（纯Python标准库，不连接数据库）：

```powershell
.\.venv\Scripts\python.exe scripts/analyze_flash_sale_load.py docs/verification/phase-6c3-flash-sale-inventory-load-target-output.txt --output docs/verification/phase-6c3-flash-sale-inventory-load-analysis.json
```

解析器核对24条件完整矩阵、2预热、终值与最后attempt一致、worker/CID一一对应、同worker服务区间不重叠、库存/6事实以及裁剪连接区间，再从raw纳秒算分位数与累计区间。分析通过，0无效记录；预热不进入下表，每个条件3轮，每轮旅程n=24。下表为三轮实际范围，**不是平均p95**。

| 库存 / 总尝试预算 | 策略 | 三轮成功数 | 三轮调用数 | 旅程p95范围(ms) | 批次连接开放范围(s) |
| --- | --- | --- | --- | --- | --- |
| 充足24 / 1 | conditional | 24,24,24 | 24,24,24 | 92.91–99.75 | 1.714–1.785 |
| 充足24 / 1 | nowait | 3,3,4 | 24,24,24 | 38.53–42.38 | 0.754–0.772 |
| 充足24 / 3 | conditional | 24,24,24 | 24,24,24 | 89.31–103.62 | 1.709–2.002 |
| 充足24 / 3 | nowait | 11,10,10 | 60,57,58 | 107.46–114.09 | 1.889–1.932 |
| 紧张6 / 1 | conditional | 6,6,6 | 24,24,24 | 83.86–94.41 | 0.991–1.050 |
| 紧张6 / 1 | nowait | 3,4,4 | 24,24,24 | 39.82–45.53 | 0.764–0.804 |
| 紧张6 / 3 | conditional | 6,6,6 | 24,24,24 | 90.01–94.55 | 1.016–1.072 |
| 紧张6 / 3 | nowait | 6,6,6 | 53,52,55 | 106.42–121.91 | 1.703–1.834 |

完整每批attempt/journey/queue/E2E p50/p95/p99、成功旅程分布、创建与终值吞吐、实际退避、client开放秒和服务活跃秒/峰值都在analysis.json。峰开放8来自固定worker容量及实际会话观测，不能据此说策略需要相同的最小资源。

本次主要观察：

- 充足库存且仅一次时，NOWAIT创建3/3/4单，20–21人以繁忙结束；条件候选每轮24单。NOWAIT的较短全结果延迟主要包含大量拒绝，不能说顾客购买完成更快。
- 充足库存允许3次时，NOWAIT调用60/57/58次，只创建11/10/10单；候选每轮24次调用创建24单。候选批次连接开放1.709–2.002秒，NOWAIT1.889–1.932秒，并非每轮总连接秒都更低；但每建成一单连接秒候选0.071–0.083、NOWAIT0.173–0.193。该比值包含拒绝请求成本，不能当每成功请求自身耗时。
- 紧张库存允许3次时，两策略都卖满6件，业务产出相同。候选24次调用，NOWAIT52–55次；旅程p95候选90.01–94.55ms、NOWAIT106.42–121.91ms；批次连接开放候选1.016–1.072秒、NOWAIT1.703–1.834秒。此条件下候选用较少尝试、较短旅程与较少连接开放秒达到相同产出。
- NOWAIT测量288旅程的终值为70创建/192繁忙/26库存不足，条件候选288旅程为180创建/108库存不足。不同库存和预算合计仅用于完整性核对，不把混合条件合并成单一性能基准。

这些本机数据支持下一步认真评估候选，而不是立即修改正式策略。样本很小、固定closed batch和固定退避、无HTTP网络或生产持续到达率；网络/数据库故障下的等待与有界超时行为仍需与选型一起确认。

证据文件：[目标全部raw](phase-6c3-flash-sale-inventory-load-target-output.txt)、[模块回归raw](phase-6c3-flash-sale-inventory-load-regression-output.txt)、[逐批分析](phase-6c3-flash-sale-inventory-load-analysis.json)、[环境与指纹](phase-6c3-flash-sale-inventory-load-data.json)、[独立分析源码](../../scripts/analyze_flash_sale_load.py)。全部有效批次保留，无失败批次被丢弃。原raw有完整jobs/attempts/backoff/worker时间和scoped ORM终值，分析文件不重复整套raw。


## 结论边界与下一步

本片是相同本机环境的小批次服务层观察，不含真实HTTP网络、前端、持续到达率、生产容量、锁等待图或稳定p99估计。未观察死锁也不能证明普遍无死锁。业务不变量、拒绝结果和异常数量必须与延迟和连接指标一起解释。

正式默认继续NOWAIT，候选尚未正式采用。数据解读后结合成功率、重试与资源成本形成方案选择/ADR；本片不自动变更策略、接口、依赖、模型或迁移，也不重跑MQ/Redis故障证据。

学习说明见[Phase6C-3指南](../learning/phase-6c3-flash-sale-inventory-load-study-guide.md)。
