# Phase 5C-3：支付通知已保存，消息没有发出去时怎样补漏

本段属于 Track C / Depth，继续
[C2 有界后台重试](phase-5c2-payment-mq-retry-study-guide.md)。按项目拥有者
要求先实现、验证与交付指南，再学习核心代码。它补强支付通知从 MySQL
到 RabbitMQ 的恢复入口，不扩展商品秒杀或通用任务平台。

## 本片前后变化

- **开始前：** 入箱和重试状态能够持久保存，但数据库提交到 RabbitMQ 接收之间仍有漏投窗口，恢复 Broker 不会自行找出数据库候选。
- **本片改变：** 新增人工 `--publish` 单轮补投，重查资格、事务外发送原 ID，不改业务状态或预算；首个发布失败后报告未尝试项并停止。
- **完成后与边界：** 真实 Broker 停机后的首次/重试漏投可人工恢复，重复补投仍只有一次核销；没有周期补漏、自动恢复时限或 PROCESSING 中断接管。

**后续衔接：** [C4 Worker 中断恢复已完成](phase-5c4-payment-worker-recovery-study-guide.md)。[学习导航](README.md)

## 解决什么业务问题

前面已经做到：原始支付通知先可靠入箱并提交，再向消息服务发内部 ID；
Worker 复用原处理器，暂态故障按数据库预算安排下一条短 ETA 消息。
但数据库提交成功不代表 RabbitMQ 一定收到消息。

189 元鼠标订单已预占 1 件库存。Mock 网关成功通知保存为
RECEIVED/NONE/count=0，随后 Broker 不可用，发布返回失败。HTTP 202
仍只表示原通知已经保存；订单待付款，库存仍预占，没有后台任务去确认。
如果一直依赖这一次发送，原事件就会留在 Inbox。

另一种情况是 Worker 核销遇到真实 MySQL 1205，业务修改回滚后保存
ERROR/TRANSIENT/count=1/下次+5秒；此时下一条重试消息也可能发不出去。
到了资格时间，数据库知道可以重试，但队列中没有任务执行它。

Publication Recovery（发布补漏：重新把已有事件的处理任务交给队列）
增加一个人工单轮入口。从数据库找出符合条件的原事件，重新发布内部 ID。
它不重新付款、不重新下单，也不直接把订单改成支付成功。

## 补发布与直接处理有什么区别

| 入口 | 当前进程做什么 | 会不会执行支付业务 |
|---|---|---|
| 原 recover_payment_events 命令 | 逐条调用原处理入口 | 在当前进程同步处理 |
| 新增 `--publish` | 重新向 RabbitMQ 发送原内部 ID | 本命令不处理，由 Worker 后续执行 |
| `--publish --dry-run` | 只查看符合条件的候选 ID | 不发布，也不改数据 |

不带 `--publish` 继续保留原人工同步基线。新模式只用于
development/test 且 PAYMENT_EVENTS_ASYNC=true；预览也要符合这两个条件，
避免把同步基线的预览误读成异步发布配置已经正确。

## 哪些事件可以补投

候选规则比“所有 ERROR 都重发”更严格：

| 条件 | 为什么有处理机会 |
|---|---|
| RECEIVED / NONE，count=0 | 已保存，但还没有领取过首次处理资格 |
| ERROR / TRANSIENT，count=1或2，next_retry_at已到期 | 允许重试，仍有预算，而且已经可以领取 |

PROCESSED、REJECTED、BLOCKED、EXHAUSTED 和 PROCESSING 都不选。
未到期的 TRANSIENT 不提前补投，指定 `--event-id` 也不能绕过门槛。
PaymentAttempt 的 UNKNOWN 要查询原支付结果；没有 Inbox 原文的未知付款
结果，不能靠补发布命令猜出结果。

这不是“数据库已标记消息丢失”的证明：一条 RECEIVED 也可能正在队列
中等待 Worker。补投允许重复，最终是否处理由原资格入口和终态决定。
数据库不新增“已经发布”状态，因为发布成功和业务完成是两件事。

## 一轮补漏怎样运行

```text
人工选择异步补发布模式
→ 检查 development/test、异步开关与事务外运行要求
→ 按 received_at、内部主键取固定最旧批次，最多 limit 条
→ 每条重新读取数据库事实 → 再检查严格资格
   ├─ 已完成、阻断或不符合资格：跳过
   └─ 仍符合：数据库事务之外发布原内部 ID
      ├─ 成功：记录本轮发布统计，不改 Event 状态或次数
      └─ 返回失败：记录失败 ID，停止本轮后续发布
→ 剩余条列为 unattempted，保留下一轮人工选择
→ Worker 收消息 → 锁内复查资格 → 原支付/订单/库存事务
```

扫描与发布之间，另一个 Worker 可能完成事件。因此每条要重读资格。
重读之后、发布之前也可能有状态变化；不能持数据库锁等待 Broker 来
消除这个窗口。多发的一条任务由共用幂等处理器安全收束。

Idempotency（幂等：重复处理不增加第二次业务效果）仍在业务入口，
不来自“补投命令只运行一次”。同 ID 发布两次后，订单、库存流水与
处理预算都需要验证，不能只数消息条数。

## 为什么一次发布失败就停

如果 Broker 不可用，继续对整批重复等待连接只会增加耗时。本段对
本轮第一个明确发布失败停止后续发送，把尚未尝试的候选报告出来。
这与原同步恢复“单条业务失败后继续其他事件”不同：两个模式处理的
故障位置不同，也保留不同的批次行为。

`published` 只表示本轮发布函数返回成功，不能当作付款成功数。
Publisher Confirm（发布确认）是 Broker 对消息接收负责，不是订单
PAID 的证明。失败也不证明 Broker 绝对没收过消息，所以再次发布必须
允许重复。这与 [RabbitMQ 可靠性说明](https://www.rabbitmq.com/docs/reliability)
中“确认不确定时可能重复发送，消费者需要幂等”的边界一致。业务终值
继续看原 Event、Attempt、Order、库存和唯一流水。

## 最小方案与取舍

本段选人工单轮 Inbox 补发布：没有新表和调度器，沿用已有原文、
分类、次数与时间，方便在隔离实验中展示故障与恢复。代价是需要人工
触发，老候选可能仍占据前面批次，而且本段没有自动补漏及时性保证。

只用 C1/C2 发送路径代码更少，但发布窗口出现后没有独立再次执行机会。
增加周期扫描可自动发现候选，却需要重复扫描与调度运维规则；Outbox
能明确追踪发布意图，但引入额外记录和发送维护。本段不因为有消息队列
就自动引入 Beat、Outbox 或完整后台调度平台。

## 操作指南

从项目根目录 PowerShell 开始，已有隔离 MySQL 测试环境与 C1/C2 镜像：

```powershell
docker compose --profile payment-mq-test build payment-worker-test
docker compose --profile payment-mq-test up -d --wait rabbitmq-test
```

这两条只准备专用测试 Worker 镜像、启动测试 Broker，不启动开发 Worker。
真实恢复实验会停止并恢复测试 Broker，不能在共享开发 Broker 上照做。
端口为 AMQP 5673、管理 API 15673；vhost project_one_test、
queue payment-events-test；数据库是 pytest 的 test_project_one。

先验证候选、批次、命令与无写入规则；以下会写入并清理专用测试库：

```powershell
$env:DJANGO_ENV = "test"
$env:CATALOG_CACHE_ENABLED = "false"
$env:MYSQL_TEST_DATABASE = "test_project_one"
$env:PAYMENT_EVENTS_ASYNC = "false"
.\.venv\Scripts\python.exe -X utf8 -m pytest backend\apps\payments\tests\test_event_publication_recovery.py -q --tb=short
```

测试内部显式启用所需异步规则并 Mock 发布，证明规则而不是 Broker 恢复。

再运行本期真实 Broker 停机、Worker 与人工补投实验：

```powershell
.\scripts\verify_payment_mq.ps1 -Stage recovery
.\scripts\verify_payment_mq.ps1 -Stage recovery -WriteEvidence
```

第一条只运行验证，第二条另写
[本期结构化证据](../verification/phase-5c3-payment-publication-recovery-data.json)。
需要留证据时选第二条即可，无须把同一实验连续跑两遍。
[脚本](../../scripts/verify_payment_mq.ps1) 自动加载
[恢复实验覆盖配置](../../compose.payment-mq-recovery-test.yaml)，默认 baseline
保留 C1，retry 保留 C2；recovery 才选择本期场景。

脚本在内存读取凭据和签名配置，不打印完整 Compose，不要求手输密码。
它临时把本次 Python 测试进程连接指向测试库和本机测试 Broker，再在 finally
中停止测试 Worker、恢复测试 Broker、还原调用者环境与目录。
pytest 会写入并清理专用测试库，实验会停测试 Broker 并清理测试队列；
不能并行运行共享该库、队列或 Broker 的测试。脚本退出非零时先看失败
与清理提示，不能把容器恢复健康当作业务实验通过。

下面是管理命令说明。普通 manage.py 使用当前配置的数据库，不会因为
DJANGO_ENV=test 自动切到 pytest 的库。必须先确认连接目标、已有迁移、
异步开关和 Broker，不能直接对开发库执行恢复或迁移操作。

配置已确认后，先只读查看补发布候选：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --publish --dry-run --limit 20
```

dry-run 不发消息、不写 MySQL，也不增加次数。selected=0 只表示这一轮
没有符合资格的候选，不能证明所有付款已成功或所有消息都已投递。
默认异步开关关闭时，此模式会被拒绝，即使带 dry-run 也一样。

确认目标与影响后，才使用发布操作；本轮不在开发库执行这些命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --publish --event-id 7
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --publish --limit 20
```

event-id 是 Inbox 内部正整数，不是网关字符串 ID。limit 只限制批量，
不会重置累计预算。发布只改变 Broker 的消息内容，不直接执行支付事务；
但 Worker 随后可能改变订单与库存，所以这仍是实际业务操作。
遇到发布失败时命令报告失败与未尝试项，并非已经发布的任务全部撤销。
输出格式为 `selected=... published=... errors=... skipped=... unattempted=...`。
例如选了 3 条，第一条成功、第二条失败，则 published=1、errors=1、
unattempted=1；第三条只是未尝试，不能归入“发送失败”。发布失败后命令
非零退出，但第一条任务仍可能由 Worker 正常完成。

## 真实实验怎样检查

第一次漏投场景：停专用 RabbitMQ，接收 189 元原通知；确认原文提交、
RECEIVED/count=0、订单待付与库存预占。恢复 Broker，人工双次补投原 ID，
再由真实 Worker 消费，检查仅一次预算、一次库存 COMMIT、原订单成功。

重试漏投场景按下面的顺序安排：

1. 原通知正常入箱并发给队列；测试独立连接锁住 Inventory。
2. 真实 Worker 核销库存时得到 MySQL 1205，原处理器留下
   ERROR/TRANSIENT/count=1 与下次时间；未到期预览应为 selected=0。
3. 测试专用启动器最多等 20 秒，用容器内 socket 观察测试 Broker 停机，
   再调用原发布器发送 ETA 任务；发布必须真实失败，不能伪造返回值。
4. 停止 Worker、释放库存锁并恢复 Broker，核对状态、次数和下次时间
   未被发布失败改写。清掉专用测试队列中原未 ACK 回队的消息，排除
   RabbitMQ 自动重新投递的贡献，单独验证人工补投是否有效。
5. 确认已到期，启动新的 Python / manage.py 命令进程补投同一内部 ID；
   Worker 目标是原事件成功、count=2、一次库存 COMMIT，同时领取第二次
   资格的 as_of 不早于原 next_retry_at。

第 4 步先用 AMQP Passive Declaration（被动声明：询问已有队列而不创建）
同步取得 ready 条数，并断言 consumer_count=0，再清理专用测试队列。
管理 API 的 ready 统计可能滞后，不能拿一个瞬时 0 误报实际移除数量；
本实验清理后也再次通过 AMQP 核对队列为空。

第 5 步先明确核对 test_project_one、127.0.0.1:3307、DJANGO_ENV=test、
异步开关、payment-events-test 与已校验的测试 Broker URL，再启动
`sys.executable -X utf8 backend/manage.py recover_payment_events --publish ...`。
它与 PowerShell 每次执行 Python 管理命令的进程形态一致，也验证另一
进程能从 MySQL 找回原 Inbox。早到 dry-run 仍在测试进程内调用命令；
不是把每一处预览都改成子进程。

第 3 步的停机协调和第 4 步的队列清理只属于受控实验，生产路径没有
等待 Broker 停机的包装，也不建议用清空队列恢复业务。原未 ACK 消息
可能自行回队，这是必须说明的另一条恢复来源。实验没有用 Mock 假装
Broker 故障或 MySQL 锁错误，但不能据此声称自然故障发生频率或生产表现。

实际流程见
[真实恢复测试](../../backend/apps/payments/tests/test_event_mq_recovery.py) 与
[测试专用 Worker](../../backend/apps/payments/tests/mq_retry_worker.py)。
脚本复用专用测试 Worker 的会话锁等待 1 秒，不修改 MySQL GLOBAL。
事件、支付、订单与库存必须核对；消息 ACK、ready/unacknowledged 等
观察帮助确认测试队列收尾，不等同于付款事实。

第一次真实运行为 1 passed / 1 failed：重试场景恢复 Broker 后，原测试
进程的第一次补投返回失败，原 ERROR/count=1 保留。现象与停机前 Broker
连接池复用旧连接相符，这是推断，不能断言每次重启后第一发必然失败。
随后只把该场景的最终补投换成上述新 CLI 进程，没有修改生产发布器、
清空生产连接池或给发布器增加隐藏重试。

规则与 5A/5B 目标回归已记录 36 passed / 20 subtests（21.11 秒）；
第二轮真实 C3 版本为 2 passed（72.54 秒）。此后补强 AMQP 同步计数与
子进程隔离校验，最终 C1/C2/C3 分别 3/3/2 项通过（41.00/68.02/71.69秒），
共8个真实MQ场景。支付与订单回归141 passed、62 subtests passed（97.96秒）；
普通回归未启用MQ而skip的8项已在上述真实流程独立验证。最终记录见
[C3 验证记录](../verification/phase-5c3-payment-publication-recovery.md)。
单轮通过与最终回归应分别记录，容器恢复健康也不能替代业务断言。

## 实际核心代码

先读 [recovery.py](../../backend/apps/payments/recovery.py) 的候选函数，
再读补发布循环，最后读
[管理命令](../../backend/apps/payments/management/commands/recover_payment_events.py)
的环境检查、模式分支与统计输出。

下面是实际候选查询。SQL 的到期过滤也排除 next_retry_at 为空的 ERROR：

```python
def _publication_candidates(*, cutoff):
    return PaymentEvent.objects.filter(
        Q(processing_status=PaymentEvent.ProcessingStatus.RECEIVED,
          failure_kind=PaymentEvent.FailureKind.NONE, processing_attempt_count=0)
        | Q(processing_status=PaymentEvent.ProcessingStatus.ERROR,
            failure_kind=PaymentEvent.FailureKind.TRANSIENT,
            processing_attempt_count__gt=0,
            processing_attempt_count__lt=MAX_EVENT_PROCESSING_ATTEMPTS,
            next_retry_at__lte=cutoff)
    )
```

固定候选元组取好后，实际循环如下；它重新查资格而不领取或修改状态：

```python
for position, pending_id in enumerate(event_ids):
    # 扫描后的状态可能已被其他执行者改变；网络等待仍不持锁。
    current_cutoff = as_of if as_of is not None else timezone.now()
    if not _publication_candidates(cutoff=current_cutoff).filter(pk=pending_id).exists():
        result.skipped_ids.append(pending_id)
        continue
    if not publish_payment_event(event_id=pending_id):
        result.failed_ids.append(pending_id)
        result.unattempted_ids.extend(event_ids[position + 1:])
        break
    result.published_ids.append(pending_id)
```

`exists()` 是一次新的数据库查询，发现事件已经不符合条件便跳过。
发布失败返回 False 时用 break 停止本轮；remaining ID 留在 unattempted。
as_of 是受控测试时钟，管理命令没有强制跳过资格或重置预算的参数。

## 收尾边界与下一步

- 人工单轮恢复发布，不是自动定时补漏；不保证无人值守的恢复时限。
- MySQL 状态、次数和到期时间不由发布命令改写；Worker 仍守原资格。
- 不补投 PROCESSING，不重置耗尽预算或解封 BLOCKED。
- 不承诺发布消息恰好一次，重复安全依赖共用处理器。
- 不新增 Beat、Outbox、依赖、前端或 Redis 改动；默认同步基线保留。
- 不操作开发库，不接第三方沙盒或真钱；Worker 中断和接管留给 C4。

## 面试可能怎么问

1. **为何有提交后发布还要补漏？** 提交成功与 Broker 接收不是一个事务，
   提交后崩溃或网络故障仍可能留下未执行事件。
2. **怎样知道某条消息丢了？** 这里不直接断言丢失，只筛选仍有处理资格的
   事件，再允许重复投递；终态和预算由数据库处理器保护。
3. **为什么补投不改为已发布？** 发布成功不能证明业务完成，Broker 确认
   与订单支付状态必须分开；本段不增加不可靠的完成标记。
4. **为何每条重新读？** 选批次后状态可能被 Worker 改变，必须再次检查，
   发布后的剩余竞态交给幂等处理器。
5. **为什么首失败停发？** Broker 故障可能影响整批，避免每条重复等待；
   尚未尝试的 ID 留给下一轮，不把它们假装成发送失败。
6. **能否直接重发所有 ERROR？** 不能，必须 TRANSIENT、次数1/2、已到期；
   BLOCKED、EXHAUSTED 和 PROCESSING 不会因命令绕过。
7. **两次补投是不是两次付款？** 两条任务使用同一原事件；Worker 不调用
   重新付款，最终次数与库存唯一副作用需要实测。
8. **这已经完整自动恢复了吗？** 只是人工单轮入口和本期故障实验，
   自动调度、Worker 接管及消息确认窗口还需要后续切片。

## 闭卷小练习

189 元原通知已保存，Broker 当时不可用。画出第一次发布失败到双次补投、
Worker 唯一核销的链路，指出哪一步不改次数、哪一步才领取处理预算。
再想一想：批次选了 3 条，第二条发不出去，第三条应该叫什么状态统计？
