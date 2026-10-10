# Phase 5C-2：支付暂时失败后，后台怎样有限重试

本文件保留2026-10-04的C2切片；后续人工发布补漏与Broker故障恢复见
[C3指南](phase-5c3-payment-publication-recovery-study-guide.md)。

本段属于 Track C / Depth，继续
[C1 真实 MQ 基础链路](phase-5c1-payment-mq-study-guide.md)。按项目拥有者
要求先实现、验证与交付文档，再阅读核心代码。MySQL 仍保存支付事实和
处理预算；RabbitMQ + Celery 只负责把下一次处理交给后台。

## 本片前后变化

- **开始前：** C1 已能后台处理通知，但数据库的下次可处理时间只是记录，锁故障后没有任务主动再次进入处理入口。
- **本片改变：** Worker 重读已提交的暂态失败，只对合格原事件安排短 ETA 消息；是否到期、还能尝试几次继续由 MySQL 锁内判断。
- **完成后与边界：** 真实 1205 后后台恢复成功、持续失败耗尽已验证，无需重新付款；消息条数可多于业务次数，发布失败仍可能遗漏，PROCESSING 硬中断仍不接管。

**后续衔接：** [C3 人工发布补漏已完成](phase-5c3-payment-publication-recovery-study-guide.md)。[学习导航](README.md)

## 解决什么业务问题

C1 已能让真实 Worker 处理已入箱通知，重复任务不会重复核销。但是
第一次遇到允许重试的数据库锁故障时，5B 只保存 next_retry_at；
时间到了并不会自动产生新的任务，需要人工恢复或再次触发。

189 元鼠标订单已经预占 1 件库存，Mock 成功通知已保存。Worker 核销
库存时恰好遇到 MySQL 锁等待超时，本轮订单与库存修改回滚，Event
为 ERROR / TRANSIENT，次数 1，下次时间为本次失败记录时间加 5 秒。
本段让 Worker 按数据库这条记录安排下一次消息，顾客不用再点击付款。

不是所有错误都值得再次尝试：错误金额与签名依旧拒绝；库存不变量或
未知程序异常 BLOCKED；三次暂态失败 EXHAUSTED。它们都不会自动重试。

## 哪些规则沿用，哪些是本段新增

| 规则 | 谁负责 |
|---|---|
| 总共最多 3 次，包含首次处理 | MySQL 的 processing_attempt_count |
| 只允许 MySQL OperationalError 1205/1213 | 5B 的失败分类 |
| 失败后等待 5 秒、10 秒 | MySQL 的 next_retry_at |
| 金额、签名、币种、状态、库存一致性 | 原业务事务 |
| 立即投递原 Event ID，指定下次最早执行时间 | 本段 Worker 与发布函数 |
| 是否真的可以再领取一次 | 5B 锁内资格复核 |

Scheduling（安排后续执行：给任务指定最早运行时间）使用消息的 ETA。
ETA（最早执行时刻）不早于数据库的 next_retry_at，也至少距离当前时间
1 秒；不是重新从消息次数推算的计数器。正常首次安排仍保持 5/10 秒，
接近时间边界的再次安排可能稍晚约 1 秒，数据库的资格时间不会被改早。
Celery 任务继续 `max_retries=0`，本段不用 self.retry，也不新增 Beat
定时调度服务或任务结果库，避免同时存在两套处理预算。

## 189 元事件怎样恢复

```text
原事件已入箱 → RabbitMQ 首条消息 → Worker 领取第 1 次处理资格
→ 核销遇到真实或受控注入的允许重试故障
→ 业务事务回滚 → 另事务保存 ERROR/TRANSIENT/count=1/下次+5秒
→ Worker 重新读取已提交的 Event
→ 发布原内部 ID，ETA=max(next_retry_at, 当前时间+1秒)
→ 到期后 Worker 执行已投递的消息 → 锁内复查 → 领取第 2 次资格
   ├─ 成功：订单 PAID、Attempt SUCCEEDED、库存核销、Event PROCESSED
   └─ 再次暂态失败：ERROR/TRANSIENT/count=2/下次+10秒
      → 同 ID 下一条 ETA 消息 → 第 3 次资格
         ├─ 成功：同一订单与原事件完成，只有一次库存 COMMIT
         └─ 暂态失败：ERROR/EXHAUSTED/count=3，不再发布下一次
```

次数增加发生在资格短事务中，业务回滚不会把已经尝试的次数撤销。
等待从失败留存使用的时间计算，不从顾客点击时间或前一条消息发布时间计算。
等待期间库存继续预占，付款结果仍以原 Attempt 与订单为准，不创建新付款。

## 为什么一定重新读数据库

任务在执行时可能保存过一个旧 Python Event 对象；处理器异常退出后，
这个对象不一定反映另事务留下的失败分类、次数、到期时间和终态。
因此，成功返回或异常留存之后，都从数据库读取最新事件，再决定是否调度。

只有 ERROR / TRANSIENT、累计次数为 1 或 2、next_retry_at 非空才安排。
终态、BLOCKED、EXHAUSTED 和 PROCESSING 都不安排，不能靠捕获异常
就把任何问题自动送回队列。

如果某条重复消息在到期前到达，5B 入口不领取、不增加次数。本段仍
可以根据当前数据库的到期时间安排后续消息。这可能增加重复任务，但
真正执行时仍复查资格；消息数量与业务尝试次数不是同一个数字。
Celery 定时器实际可能提前几毫秒唤醒，所以近边界重新安排至少 1 秒，
避免把同一消息立即投回队列形成紧密循环；最终是否领取仍由数据库决定。

## ETA 的取舍

最小方案是复用发布函数，显式发一条带短 ETA 的新消息，参数仍只有
原内部 ID。替代方案是 Celery self.retry，框架使用更方便，但不能把
任务自身的 retries 当成跨入口的业务预算，仍必须服从数据库资格。

另一种方案是增加周期扫描器或 Beat。它能独立找出到期事件，但需要
调度部署、扫描去重与漏投恢复策略。本段只处理 Worker 已观察到的
暂态失败，不提前扩成完整补漏平台。

ETA 不是数据库事务，也不是到点必定执行的保证；排队和网络可能导致
更晚处理。Celery 官方说明 ETA 任务可被 Worker 提前取到并暂存内存，
因此本段只使用 5/10 秒短延迟，不把它当作长时间可靠定时器。
见 [Celery ETA 与 Countdown](https://docs.celeryq.dev/en/stable/userguide/calling.html#eta-and-countdown)。

## 发布下一条失败怎么办

如果本次业务暂态错误已提交，但发布 ETA 消息时 Broker 不可用，原
Event 仍是 ERROR / TRANSIENT，次数与 next_retry_at 都保留。调度失败
不改成支付失败，不重新付款，不清空预算，也不能冒充已进入下一次处理。

日志与任务只报告稳定错误，不直接输出 SQL、签名或交易原文。本段
不自动补齐发布遗漏；需要保留这条数据库事实，交给后续 C3 补漏路径。
即使 Broker 接受消息，也不能据此保证 Worker 最后一定完成业务。

## 怎样运行与看结果

本段不新增 Python 依赖，不改前端、Redis 或开发库迁移。已有 C1 环境
包括 `.venv`、真实 MySQL、RabbitMQ 测试 Broker 和 Linux Worker 镜像。
所有命令从项目根目录 `C:\project-one-ecommerce` 的 PowerShell 执行。

启动前核对的是专用 payment-mq-test 服务，不能启动开发 Worker：

```powershell
docker compose --profile payment-mq-test build payment-worker-test
docker compose --profile payment-mq-test up -d --wait rabbitmq-test
```

build 只准备 Worker 镜像，up 启动测试 Broker。真正的重试实验要由
隔离验证脚本启动 Worker、创建测试数据、保持并释放测试锁，再核对业务结果。
不用在开发库手工锁库存或运行恢复写操作，也不打印完整 Compose 配置。

使用 [verify_payment_mq.ps1](../../scripts/verify_payment_mq.ps1) 的 retry 阶段：

```powershell
.\scripts\verify_payment_mq.ps1 -Stage retry
# 需要写本期实验结果时，才增加证据选项：
.\scripts\verify_payment_mq.ps1 -Stage retry -WriteEvidence
```

脚本临时合并 [重试实验 Compose 文件](../../compose.payment-mq-retry-test.yaml)，
只改变测试 Worker 的启动入口；未指定 Stage 时仍运行 C1 baseline。
`-WriteEvidence` 会写 C2 原始 JSON，默认不重写证据。
脚本在内存读取测试凭据，不要求手输包含密码的 Broker URL；临时配置
测试库与 Broker，先停遗留 Worker，再启动测试，最后停止 Worker 并还原环境。
`DJANGO_ENV=test` 本身不会让独立管理命令自动切到 pytest 的数据库。

实际结果见 [C2 验证记录](../verification/phase-5c2-payment-mq-retry.md)。
本轮真实 C2 三项通过，C1 三项基线回归也通过，具体条件与实测见该记录。
支付/订单回归为131项与54个子用例通过；普通回归中6个MQ用例因未显式
配置被跳过，它们已经在两个真实服务流程分别运行，不能将skip算成通过。
没有明确 MQ 配置的 skipped 不算验证。共享 test_project_one 的不同 pytest
不能并行执行。每场景先停 Worker 并重新创建专用测试队列，让确认统计从零开始。

## 实验怎样证明后台确实重试

实验使用 [专用测试 Worker 入口](../../backend/apps/payments/tests/mq_retry_worker.py)，把该进程的 MySQL 会话
innodb_lock_wait_timeout 设为 1 秒。测试父进程使用独立连接持有
Inventory 行锁，让真实 Worker 的业务事务实际遇到 MySQL 1205。
Session（连接会话）设置只作用于测试 Worker 的每条连接，不修改数据库
全局参数，也不降低开发连接的锁等待时间。

先观察第一次 ERROR/TRANSIENT/count=1，再释放锁；无需人工重投，
等待 ETA 消息驱动原事件成功，预期 count=2、订单 PAID、唯一 COMMIT。
持续持锁则应走到 count=3/EXHAUSTED，无下一条重试消息、无核销。
BLOCKED 例子应保留人工调查状态，不自动重复处理。

早到重复场景先将两条同 ID 的立即消息入队，再启动 Worker。第一条
遭遇锁超时，第二条到期前被拒绝领取；测试观察到 deferred 日志后才
释放锁，随后等待已安排的后台消息完成。两条初始消息不是两次付款。

专用 launcher 包装原 `_claim_event_processing`，记录它收到的 as_of
以及返回的次数版本；不改变原规则、返回值或异常。每连接只安装一次
MySQL errno 观察器，按每场景开始时间筛选日志，避免累计历史或重复记录。
输出仅含内部 ID、次数、时间与错误号，不输出 SQL、交易原文或签名。

证明“没有提前领取”要比较第二/三次实际资格输入 as_of 与前一次
next_retry_at，断言 as_of >= previous_due。仅看完成时间晚于到期时间
不够：一个提前开始的事务，也可能在等待数据库锁后很晚才完成。

初次记录只看任务完成时间及容器累计日志，后来补强为上述场景隔离的
资格观察。把重复消息在首次错误后才入队时，它还可能排在 Worker 已
预取的 ETA 消息后，受 prefetch=1 影响而在短等待内看不到 deferred。
因此调整了实验消息顺序；这些实验修正和最终实测结果见验证记录。

这是受控构造的真实数据库锁竞争，不是生产环境自然发生的故障率或
性能结果；也不是仅用 Mock 替换 OperationalError。仍需要观察
原 Event/Attempt/Order、预占、库存与队列，而不只看任务执行日志。

## 实际核心代码摘录

[tasks.py](../../backend/apps/payments/tasks.py) 在调用共用处理器之后，
无论是否抛异常都重新读数据库。下面是实际的新消息资格与调度分支：

```python
try:
    # 以失败事务结束后的事实为准；并发处理者可能已经提交了终态。
    event = PaymentEvent.objects.get(pk=event_id)
except Exception:
    raise PaymentEventTaskError("event_processing_failed") from None

if (
    event.processing_status == PaymentEvent.ProcessingStatus.ERROR
    and event.failure_kind == PaymentEvent.FailureKind.TRANSIENT
    and 0 < event.processing_attempt_count < MAX_EVENT_PROCESSING_ATTEMPTS
    and event.next_retry_at is not None
):
    # 提前/重复消息也留下到期执行机会；不在这里领取资格或增加次数。
    # Celery 的定时器可能提前几毫秒唤醒，至少延后 1 秒避免立即循环投递。
    retry_eta = max(event.next_retry_at, timezone.now() + timedelta(seconds=1))
    try:
        published = publish_payment_event(event_id=event.pk, eta=retry_eta)
    except Exception:
        published = False
    if not published:
        logger.warning("Payment retry publication failed: inbox_id=%s", event_id)
        raise PaymentEventTaskError("event_retry_publication_failed") from None
    logger.info("Payment retry scheduled: inbox_id=%s attempt_count=%s", event_id,
                event.processing_attempt_count)
    return event.processing_status
```

安排成功时可以返回 ERROR，表示这一条任务已经完成“安排后续机会”，
不表示 189 元订单已支付。次数递增与库存处理仍在 5B 服务层。
没有取得合格事件而本次处理已抛异常时，后续分支报告稳定处理错误。

[dispatch.py](../../backend/apps/payments/dispatch.py) 的实际 ETA 参数透传：

```python
options = {} if eta is None else {"eta": eta}
try:
    # 不在 HTTP 入口反复等 Broker；原文已提交，遗漏发布留待补漏切片。
    process_payment_event_task.apply_async(
        args=(event_id,), retry=False, timeout=3, confirm_timeout=3, **options
    )
except (OperationalError, AMQPError, OSError):
    logger.warning("Payment event publication failed: inbox_id=%s", event_id)
    return False
return True
```

已知 Broker 错误捕获沿用 C1 并返回 False。
普通首次发布仍可省略 eta；每条重试消息只有原 Event 内部 ID，ETA 是
执行选项。`retry=False` 是发布端不反复重连发送，不是禁用本段显式安排。

## 本段的收尾边界

- 数据库控制累计 3 次与到期资格，消息多次到达也不能绕过它。
- 只对 Worker 观察到的合格暂态 ERROR 安排下一条消息，没有周期扫描器。
- BLOCKED/EXHAUSTED/终态不重试；未知错误不能因看似短暂就自动解封。
- 调度发布失败保留原状态，没有完整漏投补偿结论，留给 C3。
- PROCESSING 硬中断仍不自动接管，Worker 中断与确认窗口留给 C4。
- 无 Beat、结果库、Outbox 或新支付尝试，不操作开发库、第三方沙盒或真钱。

## 面试可能怎么问

1. **C1 有 next_retry_at，为何还需要 C2？** 字段只记录资格时间，C2 才安排
   下一条消息，让符合条件的事件在后台再次进入处理入口。
2. **最大三次在哪里控制？** MySQL Event 中统一控制，包含首次；
   Celery 消息次数和不同 task ID 都不另开预算。
3. **为什么处理异常后重读 Event？** 失败状态另事务提交，旧 Python 对象
   可能过时，必须按最新已提交事实判断。
4. **消息到早了怎么办？** 入口不领取、不扣次数，可按记录时间安排后续；
   重复消息可能增加，但不能增加库存副作用。
5. **为什么不直接 retry 所有异常？** 库存事实或程序错误通常不能靠等待修复，
   只有已确认白名单暂态故障进入后台重试。
6. **调度发不出去如何处理？** 原 ERROR/TRANSIENT 与时间保留，报告稳定错误；
   本段不宣称已经补漏，下一阶段提供独立恢复路径。
7. **ETA 能否用作长期订单过期定时器？** 本段只验证短延迟；Worker 内存、
   确认和部署故障都需要另行评估，不能自动扩大范围。
8. **如何证明不是 Mock 重试？** 用真实 Broker/Worker 和受控 MySQL 行锁
   产生1205，检查原事件成功或耗尽及唯一库存流水，区分单测与集成证据。

## 闭卷小练习

189 元通知第一次锁超时，下次资格是 20:00:05；20:00:03 又来一条消息。
说明这时次数、库存和订单各是什么，再解释它怎样安排下一次。
如果第三次仍锁超时，画出 EXHAUSTED 后为什么不再发第四次处理消息。
