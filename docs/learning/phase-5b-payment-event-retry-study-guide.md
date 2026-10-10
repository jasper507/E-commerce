# Phase 5B：支付事件怎样有限重试，什么时候必须停下

本段属于 Track C / Depth。项目拥有者要求先交付实现、操作指南与核心代码，
再学习。它继续 [Phase 5A 人工恢复基线](phase-5a-payment-inbox-recovery-study-guide.md)，
补齐数据库中的失败分类、累计次数和下次处理时间，仍未接入 MQ。

## 本片前后变化

- **开始前：** 5A 能人工恢复原事件，但未知异常也可能被反复选中，批量限制不能限制一条事件的累计处理次数。
- **本片改变：** 在 MySQL 持久保存失败分类、总共三次的预算和 5/10 秒到期门槛；先提交领取次数，再原子处理支付、订单与库存，业务回滚不退回预算。
- **完成后与边界：** 多入口共用资格，暂态故障可有限重试，未知错误和耗尽事件停止；当时仍需人工触发，尚无 MQ、自动排程或 PROCESSING 中断接管。

**后续衔接：** [C1 真实 MQ 基础链路已完成](phase-5c1-payment-mq-study-guide.md)。[学习导航](README.md)

## 解决什么业务问题

5A 能找出已保存而未处理的支付通知，但是所有 ERROR 都可能再次选中。
这会混淆暂时锁竞争与代码错误；反复运行还可能无限尝试同一条坏记录。

189 元鼠标订单已预占 1 件库存，Mock 网关成功通知已经入箱。如果本地
核销恰好遇到数据库死锁，短暂等待后重试有机会成功；如果预占数量与
订单数量已经对不上，重复运行不会修复数据，应该停止并调查。

Retry Budget（重试预算：累计最多允许多少次处理）本段定为总共 3 次，
**包括首次处理**，不是“首次加三次重试”。预算存进 PaymentEvent，
所以新开命令、再次接收同一事件也不能把次数归零。没有重新付款。

## 新记录分别表示什么

PaymentAttempt 的 UNKNOWN 仍表示付款结果未确认，要查询原支付尝试；
以下字段都是 PaymentEvent 的处理信息，不表示顾客被扣了几次款：

| 字段或状态 | 含义 |
|---|---|
| `PROCESSING` | 已领取本次处理资格，业务结果尚未提交 |
| `processing_attempt_count` | 已领取的累计处理次数，最多 3 次 |
| `failure_kind=NONE` | 当前没有失败分类 |
| `failure_kind=TRANSIENT` | 本段允许稍后再处理的暂时故障 |
| `failure_kind=BLOCKED` | 停止重试，留存记录供调查 |
| `failure_kind=EXHAUSTED` | 暂时故障持续到预算耗尽，停止重试 |
| `next_retry_at` | 最早可以领取下一次处理的时间，不是定时任务 |

`processing_status=ERROR` 与失败分类要一起看。签名、金额或状态不合法，
沿用原来的 `REJECTED`；一条合法失败通知也可能是 `PROCESSED`。
因此，processed 计数不能当作成功付款次数，仍需看 Attempt 与 Order。

## 只允许哪两类错误重试

本段使用严格白名单：只有 Django `OperationalError` 中的 MySQL
错误码 1205（锁等待超时）和 1213（死锁）才允许重试。
这两个代码的含义见 [MySQL 官方错误参考](https://dev.mysql.com/doc/mysql-errors/8.4/en/server-error-reference.html)。

Deadlock（死锁：两个事务互相等待对方的锁）会让 MySQL 回滚一个事务；
锁等待超时默认可能只回滚失败语句。本项目让异常退出 Django 业务事务，
回滚这一轮业务修改，再重新执行完整处理，而不是只补写最后一条 SQL。
见 [InnoDB 官方错误处理说明](https://dev.mysql.com/doc/refman/8.4/en/innodb-error-handling.html)。

RuntimeError、库存不变量错误、其他 DatabaseError，以及不能明确识别
的异常都记为 BLOCKED。连接异常也不因为看起来“像网络问题”自动重试。
这是当前切片的保守选择，不能说覆盖了所有生产环境可恢复故障。

## 189 元通知怎样经过三次机会

假设第一次领取后遇到白名单暂时故障，失败记录提交时间为 20:00:00：

| 步骤 | 累计次数 | 结果与下一步 |
|---|---:|---|
| 首次处理失败 | 1 | ERROR / TRANSIENT，最早 20:00:05 再领取 |
| 20:00:03 再运行 | 1 | 尚未到期，不领取、不增加次数 |
| 到期后第二次也失败 | 2 | 再等待 10 秒；时间从第二次失败留存时计算 |
| 第三次暂时故障 | 3 | ERROR / EXHAUSTED，不再领取 |

Backoff（退避：失败后延迟一段时间再尝试）减少立即连续撞锁的机会。
5 秒和 10 秒是本段明确选择的简单策略，不是通过生产流量调优得到的值。
如果第二次成功，原 Attempt 成功、Order PAID、库存只核销一次。
如果任意一次遇到库存不变量错误，则立即 BLOCKED，不必等到第 3 次。

## 完整执行流程与事务边界

```text
原通知已经入箱并提交
→ 短事务锁 Event → 检查是否首次、到期、未阻断且预算未耗尽
→ 次数 +1 → 状态 PROCESSING → 提交本次处理资格
→ 另一业务事务锁 Event → 确认本次次数仍对应同一处理资格
→ 按原规则验签、查 Order/Attempt、复核 189 元与 CNY
→ 原子提交支付、订单、库存效果与 Event 终态
   或：业务事务整体回滚
→ 另开短事务重读 Event → 确认仍是本次资格且未到终态
→ 留存 TRANSIENT 与下次时间，或 BLOCKED/EXHAUSTED
```

Claim（领取资格：先在数据库保存本轮处理资格）在业务事务之前提交，
所以后续业务回滚不会把已经消耗的次数撤销。累计次数也用于核对本轮资格；
旧处理结果不能覆盖新一轮，错误留存也不能覆盖 PROCESSED/REJECTED。

两个人同时扫描可以看到相同 ID，但领取阶段重新锁 Event、重新检查资格。
只有符合条件的一方增加次数。不能仅凭扫描时看过 ERROR 就直接处理。
每个事件仍独立事务，一条失败不撤销其他条已经提交的结果。

## 最小方案与取舍

| 方案 | 好处 | 限制 |
|---|---|---|
| 维持 5A，靠人工调查所有 ERROR | 不增加字段和迁移 | 没有持久预算，重复运行难以约束 |
| 本段：MySQL 分类、次数和到期时间 | 多入口共用资格，不同命令共享预算 | 增加记录状态；处理中断需要另行恢复设计 |
| 只用未来 Celery 的任务重试计数 | 任务框架提供重试工具 | 独立任务/扫描可能各有计数，不能替代业务数据库预算 |

RabbitMQ + Celery 仍是后续必做：负责及时后台执行，并验证消息确认、
重复投递和中断恢复。MySQL 继续保存支付、订单、库存和事件处理事实。

## 怎样运行和怎样看结果

在项目根目录 `C:\project-one-ecommerce` 的 PowerShell 执行。
`-X utf8` 让 Python 中文输出使用 UTF-8，不改变项目运行配置。
以下测试会创建/清理专用测试库，必须有隔离 MySQL 环境与测试库权限：

```powershell
$env:DJANGO_ENV = "test"
$env:CATALOG_CACHE_ENABLED = "false"
$env:MYSQL_TEST_DATABASE = "test_project_one"
.\.venv\Scripts\python.exe -X utf8 -m pytest backend\apps\payments\tests\test_event_retry.py -q --tb=short
```

测试应检查分类、5/10 秒到期、累计 3 次、重复领取、业务回滚与终态保护。
实际运行结果见 [Phase 5B 验证记录](../verification/phase-5b-payment-event-retry.md)，
不能把这份指南里的预期当作已经通过。故障注入也不等于实测数据库死锁，
真实故障证据与受控规则测试须按验证文档区分。

本段新增了模型字段与迁移文件，**没有应用到开发库**。开发库未迁移时
直接调用恢复命令可能因缺字段失败；这里不提供让你直接执行的迁移命令。
先确认目标库、迁移影响和数据来源，另行安排开发库操作。

数据库已有对应结构，且确认连接目标后，可以先只读预览：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --dry-run --limit 20
```

dry-run 不领取资格、不增加次数、不改状态。候选仅包括 RECEIVED 首次
处理，以及到期、TRANSIENT、累计次数小于 3 的 ERROR。
`selected=0` 不表示所有支付成功；可能有未来到期、BLOCKED、EXHAUSTED
或 PROCESSING 的事件，它们都不在这轮候选中。

以下是会修改支付、订单、库存或处理记录的操作示例，本轮不执行开发库：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --event-id 7
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --limit 20
```

`--event-id` 是 Inbox 内部整数主键，不是网关字符串事件号；它只缩小
候选范围，不绕过到期时间、BLOCKED、EXHAUSTED 或累计预算。
`--limit` 仍只是本轮最多选取条数，不是每事件的最大处理次数。
有错误时命令可以非零退出，同时其他条已经提交，不能当成整批回滚。
输出格式为 `selected=... processed=... rejected=... errors=... skipped=...`；
skipped 表示选中后复查资格未得到可计数终态，不是又成功处理一笔付款。

`DJANGO_ENV=test` 不会把独立 `manage.py` 命令切到 pytest 的测试库。
普通管理命令使用当前配置的数据库，不要把设置环境模式当成库隔离保证。

## 实际核心代码摘录

先读 [services.py](../../backend/apps/payments/services.py) 的资格与分类函数，
再读 [recovery.py](../../backend/apps/payments/recovery.py) 的候选条件。
以下均摘自实际源码；次数在短事务领取时递增，不能因业务回滚归零。
共用入口拒绝外层事务，防止把领取、业务和错误记录一起回滚。

分类函数完整保留严格白名单，代码不根据异常文字猜测：

```python
def _classify_processing_failure(error):
    if isinstance(error, PaymentInvariantError):
        return PaymentEvent.FailureKind.BLOCKED, "payment_invariant_error"
    if (
        isinstance(error, OperationalError)
        and connection.vendor == "mysql"
        and error.args
        and error.args[0] in {1205, 1213}
    ):
        return PaymentEvent.FailureKind.TRANSIENT, "database_transient_error"
    if isinstance(error, DatabaseError):
        return PaymentEvent.FailureKind.BLOCKED, "database_nonretryable_error"
    return PaymentEvent.FailureKind.BLOCKED, "unexpected_processing_error"
```

业务执行前重新检查状态和次数。原处理器继续保护签名、金额与库存，
只有本轮资格匹配才执行；合法拒绝或迟到成功的原 error_code 仍保留：

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
    event.save(update_fields=("failure_kind", "next_retry_at"))
    return event
```

失败留存同样先锁内核对本轮资格。下列节选决定是否耗尽以及等多久；
后面的 save 在独立短事务中提交，其他失败类型会把 next_retry_at 清空：

```python
if kind == PaymentEvent.FailureKind.TRANSIENT:
    if event.processing_attempt_count >= MAX_EVENT_PROCESSING_ATTEMPTS:
        event.failure_kind = PaymentEvent.FailureKind.EXHAUSTED
        event.next_retry_at = None
    else:
        event.next_retry_at = as_of + timedelta(
            seconds=EVENT_RETRY_BASE_SECONDS * 2 ** (event.processing_attempt_count - 1)
        )
else:
    event.next_retry_at = None
```

`as_of` 是受控时钟测试接口，管理命令没有跳过等待或重置次数的参数。
测试入口是 [test_event_retry.py](../../backend/apps/payments/tests/test_event_retry.py)。

## 本段必须保留的限制

- 有持久预算与到期资格，没有自动调度；时间到了不会自动运行。
- PROCESSING 期间硬中断会留下处理中，本段不自动接管，没有租约或 Worker。
- 如果连失败留存的数据库事务也失败，事件可能仍为 PROCESSING，不能保证 ERROR。
- 历史 ERROR 迁移为 BLOCKED、次数 0；没有原异常类型，不能猜测可重试。
- 没有自动重置预算、修复库存或解封 BLOCKED 的管理入口。
- 未接入 RabbitMQ/Celery，未完成真实 Worker 中断恢复；沙盒与真钱仍不接入。
- 原通知验签、金额快照、状态机和库存效果仍由原处理器统一保护。

## 面试可能怎么问

1. **为什么次数要存 MySQL？** 同一个事件可能被同步入口、扫描或任务重复触发，
   数据库预算能统一约束，而进程内变量重新启动就丢失。
2. **为什么首次也占一次？** 总成本才能明确受限；这里最多 3 次完整处理资格，
   不需要猜不同入口对“重试次数”的定义。
3. **为什么不重试所有异常？** 数据错误和程序错误通常不会因等待自行修复；
   白名单减少盲目反复产生数据库压力的风险。
4. **为什么领取与业务处理分开提交？** 业务回滚后仍需记住已尝试一次；
   代价是硬中断可能留在 PROCESSING，需要后续接管协议。
5. **为什么重试整条事件？** 支付、订单和库存必须一起提交；不能仅补写失败 SQL，
   仍需复核签名、金额、当前状态与幂等。
6. **为什么旧 ERROR 不直接重试？** 旧字段没有可靠异常分类，迁移只保守留存，
   不推断它一定属于暂时锁故障。
7. **能否用 event-id 强制第四次？** 不能，定位不改变资格，耗尽后先调查。
8. **有预算是否代表 MQ 已可靠？** 没有；投递、确认、Worker 故障和处理中断接管
   都是后续真实隔离 RabbitMQ/Worker 的验证内容。

## 闭卷小练习

189 元事件连续两次死锁，第二次失败后 3 秒有人重复扫码恢复。
写出累计次数、failure_kind 和能否领取；再推理第三次失败与第二次成功的区别。
最后说明：领取后进程立刻消失，为什么不能直接把 PROCESSING 改回 RECEIVED？
