# Phase 5C-4：支付 Worker 中断后，怎样恢复处理中事件

本段属于 Track C / Depth，接着
[C3 发布补漏](phase-5c3-payment-publication-recovery-study-guide.md)，补齐
“事件已经被领取，但 Worker 没能完成”的恢复入口。按项目拥有者要求，
先实现并交付学习材料，再结合源码学习；仍然只做支付这一条深度主线。

## 解决什么业务问题

189 元鼠标订单预占 1 件库存：available=19，reserved=1。
原成功通知已存入 Inbox，任务也被 RabbitMQ 收到。Worker 领取资格后，
MySQL 留下 PROCESSING/count=1；紧接着进程被强制结束，还没有确认订单。

MQ 可以重新投递原任务，但原处理入口看到 PROCESSING 会拒绝再次领取。
C3 补发布也不选择 PROCESSING。这样虽然保护了重复核销，事件却可能
永远停在处理中，订单一直待付款。这是前一个基线保留下来的具体限制。

另一种中断发生得更晚：订单 PAID、支付 SUCCEEDED、库存 COMMIT 与
事件 PROCESSED 已经一起提交，但消息还没 ACK。重投此时应该只读取
终态，不能第二次核销，也不能再花一次处理预算。

ACK（消息确认）告诉 Broker 这条任务已经处理完；数据库提交保存业务
事实。这两件事发生在不同系统，不能假设它们能同时完成。

PaymentAttempt.UNKNOWN 是尚不清楚网关付款结果，仍需要查原支付结果。
本段 PROCESSING 是已有原事件正在处理，两者不能用一个“超时”混在一起。
恢复原通知不会创建新订单、重新付款或改变 189 元订单金额快照。

## 最小设计：租约、锁和版本门槛

Lease（租约：记录这一轮处理从何时开始、何时可以考虑恢复）新增两个
允许为空的时间字段：processing_started_at 与 processing_lease_until。
普通领取的租约默认 60 秒，真实隔离实验用 8 秒缩短等待。配置名为
PAYMENT_EVENT_PROCESSING_LEASE_SECONDS，必须是正整数。

60 秒不是 Celery 的任务时间限制，不会自动 kill Worker，也不表示
60 秒一到就把已经提交的付款回滚。它给人工恢复入口提供候选判断时间。
本段没有心跳续租、Beat 定时接管或新的一套支付状态机。

| 保护 | 具体作用 |
|---|---|
| 已提交的次数与租约 | Worker 消失后仍能知道原事件已领过几次资格 |
| Event 行锁 | T2 正在改业务时，恢复者不能同时改同一事件 |
| status/count 门槛 | 已修复或新版本已领取时，旧 Worker 不能继续提交 |
| 同一业务事务 | 付款、订单、库存与事件终态一起提交或回滚 |

Fence（版本门槛：旧执行者必须证明自己仍持有这一轮资格）沿用处理次数。
例如旧 Worker 记着第 1 次，新 Worker 已领取第 2 次；旧 Worker 再来时
次数不符，不能执行支付。即使尚未新领取，修复后的 ERROR 也不满足
旧 Worker 要求的 PROCESSING 状态。

一个活着但较慢的 Worker 若已进入 T2 并持有 Event 行锁，即使租约到期，
恢复扫描也要跳过它。到期只是候选条件，不是“确定 Worker 已死”的证明。

## 完整执行流程

```text
前端发起原 Mock 支付 / 后端接到成功通知
→ 原事件独立入箱并提交 → 事务之外发布内部 ID → HTTP 202 表示已受理
→ Worker 收到原 ID
→ T1 短事务：锁事件、检查资格、count+1、写开始与到期时间、提交 PROCESSING
→ T2 业务事务：再次锁 Event，核对 PROCESSING 与原领取次数
   → 验签、金额/币种与原订单状态
   → 支付确认、订单转换、库存副作用、事件终态一起提交，清除租约
→ Worker ACK

若 Worker 在终态提交前硬中断
→ 原 T2 未提交修改由数据库回滚；已提交 T1 仍留下 PROCESSING 与租约
→ 人工等到租约到期，再 --recover-stale
→ 锁内确认同一原版本过期 → 只修复处理元数据，不付款、不发 MQ
→ 若预算未耗尽，ERROR/TRANSIENT，next_retry_at=本次修复时间
→ 由 C3 --publish 补投，或由 Broker 重投原任务 → 共用入口领取下一次

若 Worker 在终态提交后、ACK 前硬中断
→ MySQL 已是终态 → Broker 重投 → 共用入口直接返回终态
→ 不修复、不额外领取、不重复核销或退款
```

T1/T2 是两段数据库事务的教学名字。T1 记录资格，T2 完成业务；
它们之间进程可以消失，所以只靠 Python 的 except 不能覆盖硬中断。

## 人工恢复只修哪些事件

新增 `recover_payment_events --recover-stale`，与 `--publish` 互斥。
一轮先选固定过期 PROCESSING 批次及原 count/deadline 快照，再逐条处理。
只有 count=1、2、3，且已有明确到期时间、已到期的事件可以进入候选。

逐条使用 `select_for_update(skip_locked=True)`：在事务里锁事件，遇到
别的事务正在持锁就跳过。拿到锁后再次核对状态、次数、租约到期时间，
避免扫描后事件已经完成、重新领取或改变时间却仍被旧快照修复。
这种跳过锁行为见 [Django 行锁文档](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update)。

| 锁内复查结果 | 本轮动作 |
|---|---|
| 无法拿锁、原版本变化、尚未到期或已终态 | 跳过 |
| 同一过期 PROCESSING，count=1或2 | 转 ERROR/TRANSIENT，next_retry_at=now |
| 同一过期 PROCESSING，count=3 | 转 ERROR/EXHAUSTED，不安排下一次 |

开始时间也必须存在且不晚于到期时间，否则不选；这同时排除了空租约
和明显不一致的时间记录。取得资格时用锁拿到之后的实际当前时间写
租约，避免把拿锁之前等待的时间算进新一轮有效窗口。

成功修复会清除 processing_lease_until，保留最近开始时间与累计次数。
修复不是新的业务处理，不会把 count=1 重置为 0；后面 Worker 真正
领取时才变成 count=2。
第 3 次中断也已经花掉最后一次预算，不能通过人工命令无限复活。
修复原因记录为 processing_lease_expired，只说明处理租约过期，不能
将它解释成网关扣款失败。

历史 PROCESSING 如果租约为空，本段不虚构过去的开始时间或到期时间，
不自动修复。它们需要先调查，不能通过新增字段就宣称历史任务已恢复。

## 修复和补发布为什么分两步

`--recover-stale` 只改 Event 的处理元数据，不直接支付、不改订单或库存，
也不等待 Broker。其后 `--publish` 才把原 ID 交给队列；Worker 仍然调用
已有业务入口，不走绕过验签、金额校验或库存不变量的特殊恢复路径。

如果 Broker 自己保留了未 ACK 的原任务，它也可能重投。修复不意味着
自动重投一定会在所需时刻发生：原任务可能先到而被 PROCESSING 门槛
跳过，所以仍保留显式补发布入口。

[RabbitMQ 可靠性说明](https://www.rabbitmq.com/docs/reliability) 要求消费者
能够处理重新投递。因此本段的承诺是重复消息不重复业务副作用，
而不是“Broker 只发一次”或“函数只运行一次”。

## 方案取舍

只依赖 late ACK（处理后确认）代码更少，却解决不了 MySQL 中遗留的
PROCESSING 门槛；反过来，无条件把所有 PROCESSING 改回 ERROR 会抢走
仍在运行的 Worker，还可能绕过已经耗尽的预算。

本段选“持久租约 + 原行锁/版本门槛 + 人工单轮修复”：规则清楚，
无需新调度器，能够复现硬中断及慢任务竞争。代价是人工恢复、没有
无人值守时限保证，数据库连接长期挂住的行锁也需要另外调查。

心跳续租、周期扫描或专门任务调度可以进一步提高自动化，但增加
续租故障、时钟与运维规则；本期先把中断窗口和重复副作用证据做实。

## 操作指南

在项目根目录 PowerShell 使用专用测试服务，先准备 Worker 镜像与 Broker：

```powershell
docker compose --profile payment-mq-test build payment-worker-test
docker compose --profile payment-mq-test up -d --wait rabbitmq-test
```

这两条不启动开发 Worker。前提是已有隔离 MySQL 测试配置；下面实验
会迁移、写入并清理 test_project_one，会强制结束专用测试 Worker。
不要同时运行共用测试库、payment-events-test 队列或测试 Broker 的实验。
端口是 AMQP 5673、管理 API 15673；vhost 为 project_one_test。

```powershell
.\scripts\verify_payment_mq.ps1 -Stage crash
.\scripts\verify_payment_mq.ps1 -Stage crash -WriteEvidence
```

第一条运行验证；需要另写
[本期结构化证据](../verification/phase-5c4-payment-worker-recovery-data.json)
时选第二条，无须连续运行两遍。
[验证脚本](../../scripts/verify_payment_mq.ps1) 在内存读取测试凭据，临时
设置测试进程环境，不打印完整 Compose 配置。crash 自动加载
[专用覆盖配置](../../compose.payment-mq-crash-test.yaml)，检查隔离库、
vhost、队列、测试 Worker 标志与 8 秒租约，再运行本期真实测试。
finally 停止测试执行者并还原调用者环境与目录；失败或清理失败必须
看具体输出，不能当成通过。

需要单独看租约、候选、版本与迁移规则，可运行下列隔离 MySQL 测试：

```powershell
$env:DJANGO_ENV = "test"
$env:CATALOG_CACHE_ENABLED = "false"
$env:MYSQL_TEST_DATABASE = "test_project_one"
$env:PAYMENT_EVENTS_ASYNC = "false"
$env:PAYMENT_EVENT_PROCESSING_LEASE_SECONDS = "60"
.\.venv\Scripts\python.exe -X utf8 -m pytest backend\apps\payments\tests\test_event_processing_lease.py -q --tb=short
```

它会写入并清理测试库，也含真实 MySQL 线程行锁测试与隔离迁移测试；
受控时钟、模拟异常用于检验规则，不能代替 Docker kill 或消息重投证据。

新字段的迁移只在隔离测试库验证，本轮没有迁移开发库。普通 manage.py
使用当前配置连接；DJANGO_ENV=test 本身不保证切到 pytest 的测试库。
以下命令只是已经审查连接目标和迁移之后的操作示例，不能无审查复制
到开发库运行。`-X utf8` 让 Python 的中文终端输出使用 UTF-8。

先只读看过期候选：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --recover-stale --dry-run --limit 20
```

dry-run 不修复、不发消息、不领取预算。selected 只表示过期候选，
不是确定已经死亡的 Worker 数，也不是支付成功数。

确认目标和实际影响后，才对原内部 Inbox ID 修复并补发布：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --recover-stale --event-id 7
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --publish --event-id 7
```

第一条写 MySQL 处理元数据；第二条需要异步开关与 Broker，发任务后
Worker 才可能改变订单和库存。指定 event-id 不能提前租约或重置预算；
limit 只是本轮批量，不是累计重试次数。`--publish --recover-stale`
同时出现会被拒绝；不带两种新模式仍保留原同步恢复入口。

两种新模式都只允许 development/test；元数据修复本身不要求异步开关。
修复输出为 `selected=... retryable=... exhausted=... skipped=...`。
retryable 是变成可重试 ERROR 的数量，exhausted 是保留耗尽的数量，
两者都不是付款成功数。skipped 可能来自锁占用或原快照已经变化。

## 真实硬中断怎样验证

实际实验见
[mq_crash_worker.py](../../backend/apps/payments/tests/mq_crash_worker.py) 与
[test_event_mq_crash.py](../../backend/apps/payments/tests/test_event_mq_crash.py)。
它使用 Windows 主进程、真实 MySQL/RabbitMQ 与 Linux prefork Worker；
prefork 是通过独立子进程执行任务。启动器调用原函数，只增加限时暂停
与日志观察；暂停处同时断言当前是否处在数据库事务中。
随后 `docker kill -s SIGKILL` 整个专用 Worker 容器，不能用抛出一个
Mock 异常替代进程消失。30 秒内没有 kill 会报错，防止实验悄悄继续。

| 四个真实场景 | 暂停点 atomic | 实际核对结果 |
|---|---|---|
| T1 已提交、T2 未开始 | False | PROCESSING/count=1 留存；等到期修复后成功为count=2、库存19/0、一次COMMIT |
| T2 已写、尚未提交 | True | 活着且租约到期时跳过；kill后业务回滚，恢复成功为count=2、一次COMMIT |
| PAID已提交、尚未 ACK | False | 原成功为count=1，原任务重投加一条重复任务后仍只有一次COMMIT |
| 迟到成功的模拟退款已提交、尚未 ACK | False | 订单仍CANCELED，count=1，库存20/0、一次RELEASE、零COMMIT、同一退款记录 |

第二个场景先让活着的 Worker 在 T2 持锁直到 8 秒租约到期，要求扫描
selected=1、skipped=1、retryable=0，且快速返回。随后真实 kill，核对
未提交订单/库存修改回滚，再修复和补发布。不是在这个真实场景中
让旧 Worker 自然提交；正常慢线程最终提交的规则另在
[租约测试](../../backend/apps/payments/tests/test_event_processing_lease.py) 检验。

第四个场景的合法准备顺序是：先同步处理同一 Attempt 的有效 FAILED
通知，再取消 189 元原订单、释放库存，最后接收迟到的 SUCCEEDED 通知。
CREATED Attempt 尚未确认结果时，项目规则禁止直接取消；不能为了
构造退款场景就绕过这条边界。同步处理只用于准备第一条 FAILED 通知，
后面的迟到成功仍由真实 MQ Worker 执行。

迟到成功事务创建 RefundSimulation（模拟退款记录）后，在提交与 ACK
之间被 kill。重投不能重新打开订单，也不能再生成退款记录。该场景
最终有 2 条 Inbox（FAILED 与 SUCCEEDED）、1 个 Attempt、1 条模拟退款、
1 次 RELEASE、0 次 COMMIT；表中的 count=1 指被 kill 的迟到成功事件。
模拟退款不是实际资金退回。

每次 kill 后用 AMQP 被动声明查询实际队列，要求 ready>=1 且
consumer_count=0，确认原未 ACK 消息已经回队、旧消费者已退出。
task_prerun（任务开始前的观察信号）记录 task_id 与 redelivered；
日志必须存在 redelivered=True 的投递，并且它的任务 UUID 与最初
redelivered=False 的那条相同。新发布任务的 UUID 不同，不能冒充原消息
重投。这与 [RabbitMQ 自动回队说明](https://www.rabbitmq.com/docs/confirms#automatic-requeueing)
描述的连接中断后未确认消息回队边界相符。

前两个场景在保留原任务的同时另补发布一条；后两个终态场景也主动
发布一条重复任务。重启 Worker 后，要等两条消息都 ACK、队列收尾，
再核对原事件、订单、支付、库存和退款终值；不是只等第一条任务结束。

C4 故障链路保留 Broker 的原未 ACK 任务，让它真正自动重投；不在
中断与恢复之间清队列。C3 为隔离人工补投贡献而刻意 purge 的做法不能
搬过来，然后声称证明了 Broker 自动重投。每个场景仍要看数据库终值，
消息数量、日志和 ACK 只是帮助定位执行窗口的观察。

首次真实运行为 3 passed / 1 failed（71.39 秒），失败在退款场景的准备：
未解决的 CREATED Attempt 直接取消被业务规则拒绝。修正只补了上述
合法 FAILED→取消的测试前置流程，没有放宽取消规则或支付状态机。
修正后的真实 C4 最终运行为 4 passed（81.71 秒）。四个场景均观察到
同一原任务 UUID 的 redelivered=True；每个场景最终 ack_delta=2、ready=0、
unacknowledged=0。前两个事件成功时 count=2，后两个已提交终态仍为1。

租约、发布补漏、原恢复、重试、任务重试和异步入口六模块目标回归为
69 passed / 62 subtests（28.13 秒）。更广的支付与订单回归随后实际运行，
151 passed / 12 skipped / 83 subtests（96.51 秒）。12项skip需独立真实MQ配置，
C1基线3 passed（41.98秒）、C2重试3 passed（65.37秒）、C3发布补漏2 passed
（73.87秒），加本期C4四项共12项均独立通过；不是由普通回归推断MQ成功。

第三次过期变 EXHAUSTED、不出现第四次领取，已在上述规则测试检验；
本期四个真实场景没有对同一事件连续执行三次 Worker kill，不能把二者
合并成“三次真实中断已验证”。结果、状态快照与投递证据见
[本期结构化记录](../verification/phase-5c4-payment-worker-recovery-data.json)，
容器启动或单独的 ACK 数不能替代业务终值断言。

## 实际核心代码与阅读顺序

先看 [事件模型](../../backend/apps/payments/models.py) 的租约字段，再看
[services.py](../../backend/apps/payments/services.py) 中 T1 领取与 T2
status/count 核对；最后看
[recovery.py](../../backend/apps/payments/recovery.py) 的锁内过期复查，和
[管理命令](../../backend/apps/payments/management/commands/recover_payment_events.py)
怎样分开元数据修复、同步处理、异步发布三种动作。

核心关注点是：次数在哪次事务持久化、旧执行者怎样被挡住、租约何时
清除、恢复者何时只跳过。不要仅记住命令参数而漏掉业务提交边界。

T1 在取得 Event 锁、资格与预算检查通过后，实际写入以下字段：

```python
event.processing_attempt_count += 1
event.processing_status = PaymentEvent.ProcessingStatus.PROCESSING
event.next_retry_at = None
event.processed_at = None
# 租约从拿到 Event 锁后的实际领取时刻开始，不能把锁等待计入新租约。
event.processing_started_at = timezone.now()
event.processing_lease_until = event.processing_started_at + timedelta(
    seconds=settings.PAYMENT_EVENT_PROCESSING_LEASE_SECONDS
)
```

这些字段在 T1 的 save 与提交后才成为持久资格。下一段实际 T2 包装仍
先锁 Event，再核对次数；原支付处理器继续负责完整业务校验：

```python
@transaction.atomic
def _process_claimed_payment_event(*, event_id, processing_attempt):
    event = PaymentEvent.objects.select_for_update().get(pk=event_id)
    if (
        event.processing_status != PaymentEvent.ProcessingStatus.PROCESSING
        or event.processing_attempt_count != processing_attempt
    ):
        return event
    # 原支付处理器是业务事务内部的原语；所有外部入口先经过同一资格检查。
    event = process_payment_event(event_id=event_id)
    event.failure_kind = PaymentEvent.FailureKind.NONE
    event.next_retry_at = None
    event.processing_lease_until = None
    event.save(update_fields=("failure_kind", "next_retry_at", "processing_lease_until"))
    return event
```

实际单条修复函数展示了为什么“已过期”仍要经过锁与原版本检查：

```python
@transaction.atomic
def release_stale_payment_event(*, event_id, processing_attempt, lease_until, as_of):
    """只修复已过期且本次拿得到锁的原领取记录，不执行支付或重置预算。"""
    event = PaymentEvent.objects.select_for_update(skip_locked=True).filter(pk=event_id).first()
    if event is None or (
        event.processing_status != PaymentEvent.ProcessingStatus.PROCESSING
        or event.processing_attempt_count != processing_attempt
        or not 0 < processing_attempt <= MAX_EVENT_PROCESSING_ATTEMPTS
        or event.processing_lease_until is None
        or event.processing_lease_until != lease_until
        or event.processing_lease_until > as_of
        or event.processing_started_at is None
        or event.processing_started_at > event.processing_lease_until
    ):
        return None
    # 慢 Worker 持有业务事务的 Event 锁时已被跳过；旧版本之后会被状态/次数挡住。
    event.processing_status = PaymentEvent.ProcessingStatus.ERROR
    exhausted = event.processing_attempt_count == MAX_EVENT_PROCESSING_ATTEMPTS
    event.failure_kind = PaymentEvent.FailureKind.EXHAUSTED if exhausted else PaymentEvent.FailureKind.TRANSIENT
    event.error_code = "processing_lease_expired"
    event.processed_at = as_of
    event.next_retry_at = None if exhausted else as_of
    event.processing_lease_until = None
    event.save(update_fields=("processing_status", "failure_kind", "error_code", "processed_at",
                              "next_retry_at", "processing_lease_until"))
    return event
```

这里只保存 Event 元数据，没有调用付款、库存或发布器。原旧执行者的
异常留存也受 status/count 门槛保护，不能覆盖已经修复或后来提交的版本。

## 收尾边界

- 新增租约与人工过期修复，不添加 Beat、心跳续租或全自动接管。
- 修复保留预算，不直接付款或投递；处理仍复用原状态机。
- 终态清租约；历史空租约不靠猜时间处理，耗尽仍待调查。
- 默认同步、Mock 网关与现有前端保持；不扩展 Redis、依赖或第三方沙盒。
- 只在隔离测试迁移和故障实验，不操作开发业务库或真实资金。

## 面试可能怎么问

1. **为什么 late ACK 不够？** 它能让未确认任务重新投递，却不能自行
   修复数据库 PROCESSING 状态，重投可能再次被资格门槛挡住。
2. **租约到期是不是说明任务已死？** 不是，只能成为恢复候选；锁内
   复查与 skip_locked 保护仍在运行的 T2。
3. **60 秒会 kill 任务吗？** 不会，这里是数据库恢复资格时间，不是
   Celery 执行时限，也没有自动心跳或调度器。
4. **旧 Worker 醒来怎样避免重复？** 进入业务事务必须锁 Event，并核对
   PROCESSING 与原处理次数；修复后的状态或新版本会挡住它。
5. **硬中断能由 except 留错误吗？** 进程可能没有机会执行 except，
   已提交 T1 留下的租约才提供之后的恢复线索。
6. **修复为何不清零次数？** 预算统计资格已经领取过的轮次，进程消失
   也消耗一次；清零会把有限重试变成无限循环。
7. **提交后、ACK 前被 kill 会怎样？** 原消息可重投，但已提交终态
   直接返回，不新增核销、退款或处理次数；这个窗口必须真实验证。
8. **是不是已经自动恢复所有故障？** 人工单轮解决有明确租约的过期
   资格；空租约、持续持锁、耗尽与自动调度仍有边界。

## 闭卷小练习

画出 189 元订单在三个 kill 窗口的数据库状态，分别指出哪些修改已提交、
哪些会回滚。再解释：count=1 的事件修复后为什么仍是1、Worker 成功时
为什么变成2；租约到期但 Event 正被 T2 锁住时为什么应该跳过。
