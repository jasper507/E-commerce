# Phase 6F：Lua 秒杀入口准入验证

日期：2026-10-09。工作类型：Depth / Learning；配套[学习指南](../learning/phase-6f-flash-sale-admission-study-guide.md)。

能力目标：解释库存更新正确为什么仍需入口保护、原子计数保护了哪些数据库成本，以及依赖故障和原 Key 确认可用性的取舍。最小充分证据为真实并发额度、真实订单事务对照、拒绝无新增业务效果、恢复后幂等；达到后停止，不追加算法矩阵或 Redis 库存预扣。

## 本片规则与范围

- 拥有者已确认：Redis 无法确认名额返回503；满额时所有有效POST先429，包括已提交订单的原Key确认。恢复后仍以原Key、原参数确认；429/503不能证明旧订单不存在。
- Session/CSRF、参数及Key格式校验后，先申请准入，再调用HTTP等待适配及原MySQL订单事务；后两者的实现不改。
- 全秒杀POST入口共用一个Key。首次准入SET带PX开启固定持续窗口，后续成功INCR，拒绝不增加计数、不续期。预算表示尝试，后续业务失败也不退次数；不是库存、购买资格或并发租约槽。
- 独立Redis客户端、连接/读超时各100ms、最多16连接、零自动重试。Redis异常、坏状态或不可解释结果503；不向数据库放行，不自动修复计数。
- 新增默认关闭的本地/测试配置，示例为20次/1000ms；它不是数据库容量结论。所有应用实例必须使用同一URL/前缀。实际`.env`、部署配置、依赖及开发库未改。

## 环境与安全边界

Windows / Python3.11.9 / Django5.2.17 / DRF3.18.1 / redis-py8.1.0；Docker MySQL8.4.10、Redis8.10.1。

只启动已有`cache-test`下的`redis-test`，127.0.0.1:6381，关闭持久化；开发Redis6380未启动。测试显式设置独立Redis URL，pytest建立并销毁`test_project_one`，fixture再次核对MySQL和`test_`前缀。每个测试只删除自己的随机单Key，不FLUSHDB、不扫描删除共享数据。

测试结束后只读观察：测试schema0、可见测试连接0，默认开发schema仍30表。没有执行开发库migrate或业务写入。这一计数检查不是开发数据逐行校验；隔离测试Redis在验证后停止。

## 实际执行与结果

| 执行 | 结果 | 证据 |
| --- | --- | --- |
| 第一次目标验证 | 12 failed / 2 passed / 1 teardown error，19.67秒 | [初次raw](phase-6f-flash-sale-admission-target-initial.raw.txt) |
| 修正测试后目标验证 | 14 passed，13.49秒 | [目标raw](phase-6f-flash-sale-admission-target.raw.txt) |
| 补强过期时间及原Key故障Oracle后，目标＋相关回归 | **53 passed / 325 subtests passed，61.45秒**；包含本片14项及既有39项 | [最终raw](phase-6f-flash-sale-admission-regression.raw.txt) |
| Django check / makemigrations --check --dry-run | 0问题 / No changes detected | 静态系统与迁移检查；未应用开发迁移 |
| 代码与材料检查 | scoped diff、摘录、互链、JSON与最终raw一致 | 静态检查，不算业务/浏览器实验 |

首轮失败源于新测试错误使用不存在的`sold_stock`字段，以及Redis factory替换作用域覆盖fixture清理；没有把失败结果计为通过。修正为真实available/reserved字段和预占/流水状态，缩小patch作用域，保留原始失败。最终复审补比较PEXPIRETIME（DUMP不包含TTL），并覆盖已有订单及新Key在真实连接失败时503、恢复原Key200。

初次失败日志仅去除pytest输出的行尾空白，以通过Git差异检查；所有行、异常内容和数值保留，未修改失败结果。

本轮命令（pytest会修改且销毁隔离测试库，并仅写随机测试Redis Key；不要与其他pytest进程共享该测试库）：

```powershell
docker compose --profile cache-test up -d --wait redis-test
$env:DJANGO_ENV='test'
$env:PROJECT_ONE_TEST_REDIS_URL='redis://127.0.0.1:6381/0'
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_admission.py -q -s
.\.venv\Scripts\python.exe -m pytest backend/apps/promotions/tests/test_flash_sale_admission.py backend/apps/promotions/tests/test_flash_sale_api.py backend/apps/promotions/tests/test_flash_sale_http_wait.py backend/apps/promotions/tests/test_flash_sale_checkout.py backend/apps/promotions/tests/test_flash_sale_lifecycle.py -q -s
.\.venv\Scripts\python.exe backend/manage.py check
.\.venv\Scripts\python.exe backend/manage.py makemigrations --check --dry-run
```

没有显式`PROJECT_ONE_TEST_REDIS_URL`时，真实Redis测试按fixture跳过；这是源码中的启用边界，本轮最终测试有URL。给了URL却连不上会失败，不静默跳过。

## 原子额度与数据库保护证据

真实Redis的24个独立客户端用Barrier同时竞争，4名额：准确4次准入、20次拒绝，准入返回计数1/2/3/4，最终计数4且有TTL。另一目标验证满额不改变绝对过期时间、计数仍1，真实自然过期后重新准入。

HTTP对照是**8个线程处理24个请求**，每个顾客独立地址和Key、同一活动SKU、初始库存24、活动价89元；不是24个HTTP请求同时到达，也不是实际网络负载。两组均使用完整API入口、真实HTTP适配与真实MySQL下单事务，没有Mock订单服务。

| 观察 | 关闭准入 | 开启准入：4次/10秒 |
| --- | ---: | ---: |
| 201订单 / 实际BEGIN | 24 / 24 | 4 / 4 |
| 429 / 503 | 0 / 0 | 20 / 0 |
| 请求内SQL总数 | 480 | 80 |
| 每个429的请求内SQL / BEGIN | 无429 | 0 / 0 |
| available / reserved | 0 / 24 | 20 / 4 |
| 订单、明细、地址快照、资格、预占、RESERVE流水各数 | 各24 | 各4 |

每个201捕获20条SQL和一次BEGIN，全部订单金额89元、预占RESERVED、流水RESERVE；没有支付核销。本次请求SQL捕获在连接建立后开始，`force_authenticate`固定身份，排除了Session查询。**0 SQL只指这些被拒请求在固定身份之后没有执行下单SQL，不代表真实Session HTTP全链路零数据库访问。**

当前批次关闭/开启耗时297.00/125.00ms，只用来确认受控批次未跨越10秒窗口；成功订单数不同，不能把这两个值相除宣称购买提速或生产吞吐提升。[结构化证据](phase-6f-flash-sale-admission.json)直接提取最终raw，含逐请求状态/SQL/BEGIN，不另造测量值。

## 故障、重试与业务边界

- 非整数、零、无TTL、错误类型计数均503；值/类型和PEXPIRETIME不变；固定身份下0下单SQL，六类业务事实未增加。
- 真实本机保留的非监听socket端口导致Redis连接失败，本机实际日志为TimeoutError；不是停机或强称ConnectionRefused实验。已有订单原Key和新Key均503、0下单SQL，恢复后原Key200同单，业务效果仍一次。
- **真实EVAL成功后注入响应TimeoutError**：计数确实增加到1，但应用只调用一次EVAL、返回503，没有下单SQL/业务效果；原Key恢复后201只建一单。这是已执行后响应丢失的确定性注入，不是网络延迟实测。
- 满额已有订单同参/异参均先429，窗口恢复后同参200且完整响应相同、异地址409 `idempotency_conflict`，六类事实各一次。此用例为缩短等待，只把自己测试Key的TTL改为30ms；另一个用例验证300ms窗口自然到期。
- 地址业务404仍消耗一次预算，下一请求429，无任何订单效果；未登录、CSRF失败、无效payload/Key格式不消耗预算。关闭Gate时不创建Redis client、201原流程成立。
- 既有Session/CSRF API、HTTP五秒等待/恢复、下单事务、支付/关闭资格生命周期相关39项通过；没有重跑RabbitMQ崩溃矩阵、索引负载或浏览器验收。

## 未验证与停止条件

未测生产容量、真实网络迟缓、Redis驱逐/重启/主从切换、Cluster、多进程部署、限流用户公平性；本片不做滑动窗口、令牌桶、每用户限频或严格在途上限。键丢失可重开预算，跨窗口事务可并存；MySQL仍守住订单和库存事实。

前端源码静态核对：429/503走既有pending分支，完整购买意图保留；不在本片重跑浏览器，尚未展示Retry-After倒计时。默认关闭，开发演示尚未启用；默认库正式订单索引迁移也仍未应用。个人独立讲述尚未验收。

已达到本片停止条件。下一片建议开始商品搜索的最小业务/查询基线，再建立可重建的ES派生索引；先明确搜索字段、筛选和同步边界。ES仍未实现，不借本片宣称Lua库存预扣或整个项目收尾。
