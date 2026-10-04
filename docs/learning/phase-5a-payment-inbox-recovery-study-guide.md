# Phase 5A：支付通知已入箱之后，怎样恢复处理

本文件保留 5A 历史基线。当前恢复规则已由
[Phase 5B 指南](phase-5b-payment-event-retry-study-guide.md)更新：ERROR 必须
为到期可重试且预算未耗尽，未知异常不自动重跑，PROCESSING 暂不接管。
本页旧代码与测试结果不能当作当前版本的重试保证。

本段属于 Track C / Depth。按项目拥有者本轮要求，先交付最小实现和
操作指南，再学习核心代码。它补强支付、订单与库存的一致性故事；
RabbitMQ + Celery 仍是后续必做，本段没有接入消息队列。

## 解决什么业务问题

Inbox（事件收件箱：先把网关通知存进数据库，之后再处理）已经存在。
原来收到通知后会同步处理，失败时保留原事件；用户查单或网关重发
也可能触发处理。但没有独立入口主动找出已经保存、尚未处理的通知。

189 元鼠标订单的例子：订单已预占 1 件库存，Mock 网关发来付款成功
通知；后端先保存通知，随后中断。用户不再点击，网关也不再重发，
数据库就会留下 `RECEIVED` 通知，订单仍待付款，库存仍被预占。

本段增加人工运行一次的恢复命令：从已有收件箱中选出未完成事件，
重新走原来的处理器。一条失败继续下一条，不重新付款或重新下单。
这里的中断基线使用受控异常模拟，不等同于实际终止进程或 Worker。

## 两种状态分别在说什么

PaymentAttempt（支付尝试）记录这一次付款；PaymentEvent（支付事件）
记录某条通知及其处理情况。它们是不同记录，可以同时出现以下状态：

| 记录和状态 | 通俗含义 | 对应下一步 |
|---|---|---|
| Attempt `UNKNOWN` | 请求超时，本地尚不知道付款结果 | 查询原支付尝试 |
| Event `RECEIVED` | 通知已经保存，还没有完成处理 | 处理原通知 |
| Event `ERROR` | 处理通知时抛异常，业务事务回滚 | 调查原因，再恢复原通知 |
| Event `REJECTED` | 校验不通过，明确拒绝这条通知 | 不选入恢复批次 |
| Event `PROCESSED` | 通知已经完成处理 | 重复执行不再产生副作用 |

`ERROR` 不表示付款失败，也不保证付款成功。`PROCESSED` 同样不等于
付款成功：一条有效的失败或处理中通知，也可能已经正常处理完毕。
付款结果要看 Attempt，订单结果要看 Order，不能只看命令统计。

## 从失败基线到恢复的完整流程

```text
Vue 发起付款 → HTTP/API → 保存原 PaymentAttempt → 调用 Mock Gateway
→ 收到事件 → 短事务保存并提交 PaymentEvent 原文和签名
→ 原处理器开始业务事务 → 中断，或更新库存时抛异常
   ├─ 中断发生在业务处理前：Event 可停在 RECEIVED
   └─ 普通异常：业务修改回滚，另开短事务尽力留下 ERROR
→ 人工恢复命令 → 固定选出一批 RECEIVED/ERROR 的内部 ID
→ 每条独立调用原处理器 → 重新锁定事件并检查终态
→ 重新验签、金额、币种、订单和状态
→ 同事务提交 Attempt、Order、库存效果与 Event 处理状态
→ 下一条；异常条保留并报告，其他条仍继续
```

189 元成功通知恢复后，预期是 Attempt `SUCCEEDED`、Order `PAID`、
预占记录 `COMMITTED`，以及一条库存核销流水。再次运行不能再核销一遍。
如果金额被改成 199 元，恢复仍会校验并拒绝，不能因为“以前已入箱”
就直接信任。成功付款不能被迟到失败通知倒退，沿用现有状态机规则。

## 为什么每条独立事务

Transaction（事务：一组数据库修改要么一起成功，要么一起回滚）保护
的是一条通知产生的业务效果。不能把整个批次塞进同一个事务，否则
后面一条失败可能撤销前面已经处理的订单，也会长时间占用锁。

扫描阶段只取 ID，不持行锁。实际处理时仍由原处理器按
Event → Order → Attempt → Reservation → Inventory 的顺序取锁。
另一个处理者可能已完成某条候选，所以必须在取锁后重新判断终态。

Idempotency（幂等：重复执行不会多产生一次业务效果）来自现有事件
终态检查、状态机和数据库约束，不来自“命令大概只运行一次”。
不论重发通知还是命令恢复，都复用相同规则，不维护第二套扣库存代码。

## 候选方案与取舍

| 方案 | 收益 | 代价或限制 |
|---|---|---|
| 人工重投通知、用户查单 | 不新增入口，沿用已有调用 | 依赖外部再次触发，遗漏事件不主动恢复 |
| 一次性 Inbox 扫描，本段选择 | 复用已存通知，少量代码可定位恢复 | 人工运行，存在扫描批次和失败调查工作 |
| RabbitMQ + Celery，下一阶段 | 提交后及时交给后台执行 | 增加 Broker、Worker、重复投递和发布失败窗口 |

Broker（消息中转服务）负责传递任务；Worker（后台执行进程）执行
业务代码。MQ 不能保证数据库提交后消息一定已发布，所以后续仍需
Inbox 恢复补漏。本段先建立可观察的数据库基线，不提前声称 MQ 可靠。

## 怎样操作和验证

以下命令都从项目根目录 `C:\project-one-ecommerce` 的 PowerShell 执行。
不在文档中填写数据库密码，连接配置沿用现有本地环境。
`-X utf8` 让 Python 的中文终端输出使用 UTF-8，避免本地默认编码报错。

先运行隔离测试，确认恢复规则。pytest 会创建或清理测试数据库数据；
必须指向专用 `test_` 库，不能拿真实业务库冒充测试库：

```powershell
$env:DJANGO_ENV = "test"
$env:CATALOG_CACHE_ENABLED = "false"
$env:MYSQL_TEST_DATABASE = "test_project_one"
.\.venv\Scripts\python.exe -X utf8 -m pytest backend\apps\payments\tests\test_event_recovery.py -q --tb=short
```

看到 `passed` 才能说本次测试通过；连接错误是环境未验证，测试断言
失败则需要查具体业务终值。本轮结果和相关回归见
[实际验证记录](../verification/phase-5a-payment-inbox-recovery.md)。
应关注入箱后中断、原事件恢复、重复运行、单条失败隔离与无效事件拒绝，
不是只确认函数被调用。Mock 的是网关或故障注入，事务由隔离 MySQL 验证。

下面是管理命令的操作示例，本轮不会执行开发库恢复操作。
管理命令直接使用当前配置的数据库，默认通常是开发库；设置
`DJANGO_ENV=test` 只改变环境模式，不会自动切到 pytest 的测试数据库。
先确认数据库目标，再做只读预览：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --dry-run --limit 20
```

示例输出 `dry_run=true selected=2 inbox_ids=7,9` 表示选中了两个候选，
没有处理它们。输出 `selected=0 inbox_ids=-` 表示本轮没有符合条件的事件，
不代表所有 UNKNOWN 支付都已恢复：查单是另一条路径。

确认目标和异常原因后，才选择下面的写操作。它会修改订单、支付和
库存等记录；`--event-id` 是 Inbox 内部整数主键，不是网关字符串事件号：

```powershell
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --event-id 7
# 或在审查候选后，人工处理一批：
.\.venv\Scripts\python.exe -X utf8 backend\manage.py recover_payment_events --limit 20
```

示例 `selected=2 processed=1 rejected=0 errors=1` 表示一条处理完成，
一条抛异常；命令报告失败 ID 并以非零状态退出，但其他条可能已经提交。
不能把非零退出理解成整批回滚。`processed=1` 也不能直接读成收到一笔钱。
参数必须为正整数，命令仅允许 development/test 环境运行。

## 核心代码：先固定批次，再逐条处理

以下是 [recovery.py](../../backend/apps/payments/recovery.py) 的恢复循环。
候选在前面按 `received_at, pk` 排序、限制数量并转成固定 ID 元组：

```python
for pending_id in event_ids:
    try:
        event = process_stored_payment_event(event_id=pending_id)
    except Exception:
        result.failed_ids.append(pending_id)
        # 异常原文可能含 SQL/交易输入；只输出内部 ID，原事件保留在 Inbox。
        logger.warning("Payment Inbox recovery failed: inbox_id=%s", pending_id)
        continue
    if event.processing_status == PaymentEvent.ProcessingStatus.PROCESSED:
        result.processed += 1
    elif event.processing_status == PaymentEvent.ProcessingStatus.REJECTED:
        result.rejected += 1
```

固定批次让某条失败后不会立即再次选中自己；`continue` 让下一条继续。
异常输出只放内部 ID，避免把 SQL、交易原文或其他敏感值打印到终端。

以下是 [services.py](../../backend/apps/payments/services.py) 中的共用包装。
原处理器自带业务事务，退出并回滚后，包装另开事务记录错误：

```python
try:
    return process_payment_event(event_id=event_id)
except Exception:
    with transaction.atomic():
        failed_event = PaymentEvent.objects.select_for_update().get(pk=event_id)
        if failed_event.processing_status not in {
            PaymentEvent.ProcessingStatus.PROCESSED,
            PaymentEvent.ProcessingStatus.REJECTED,
        }:
            failed_event.processing_status = PaymentEvent.ProcessingStatus.ERROR
            failed_event.error_code = "processing_error"
            failed_event.processed_at = timezone.now()
            failed_event.save(
                update_fields=("processing_status", "error_code", "processed_at")
            )
    raise
```

重新取锁后才判断终态，避免把另一个处理者已提交的成功结果覆盖成 ERROR。
若数据库整体不可用，错误状态本身也可能写不进去，所以保留原 Inbox 更重要。

## 这一步的收尾边界

- 每条事件在本轮最多尝试一次；`limit` 只限制批量，不是累计重试次数。
- 只有人工单轮入口，没有自动调度、持久重试预算或故障分类。
- `ERROR` 可能是短暂故障，也可能是库存不变量损坏；先调查，不能挂定时盲目循环。
- 老 ERROR 可能反复挤占前面的批次；可以用内部 `--event-id` 定位其他事件。
- `REJECTED/PROCESSED` 不再被选中；终态重复处理沿用原来的幂等规则。
- 不增加依赖、迁移、配置、真实支付或第三方沙盒；不改变同步 API 返回约定。

阅读顺序：先看 `recovery.py` 的候选选择与循环，再看管理命令的参数和
输出，最后看 `process_stored_payment_event` 与原 `process_payment_event`。
测试文件是 [test_event_recovery.py](../../backend/apps/payments/tests/test_event_recovery.py)。

## 面试可能怎么问

1. **UNKNOWN 和 ERROR 的区别？** 前者是付款结果未确认，后者是通知处理异常；
   一个查原支付，一个恢复原事件，不重新付款。
2. **为什么先保存通知？** 业务处理失败后还有原文与签名可重新处理和审计；
   如果入箱本身没提交，本命令不能恢复尚不存在的记录。
3. **为什么整批不用一个事务？** 每条独立提交，失败隔离，减少锁持有时间。
4. **恢复为什么还验签和金额？** 入箱只保证留存，不代表事件可信；
   可信金额仍来自原订单快照和支付尝试。
5. **两个恢复者同时运行会怎样？** 候选可以重复，原处理器重新取事件锁，
   终态直接返回；恢复循环自己不假装实现了全局互斥。
6. **有扫描就不用 MQ 吗？** 扫描解决可定位的恢复入口，MQ 提供及时后台执行；
   后续要实测重复投递、中断、Broker 故障与提交后漏投。
7. **这已经是有界重试了吗？** 只是单轮和批量有限；尚无累计次数、退避或耗尽状态，
   不能声称完成自动重试策略。
8. **怎样证明恢复正确？** 看原 Event、Attempt、Order、Reservation 和库存流水；
   验证重复执行没有第二次核销，而不是只看命令返回码。

## 闭卷小练习

关掉核心代码后，画出“189 元成功通知已保存，核销库存时失败”的流程。
标出哪个事务回滚、ERROR 在哪里另行记录，以及恢复时为什么不重新付款。
再假设本批第一条一直失败、第二条合法：解释两条各自的状态和命令退出码。
最后自己改变预览批量，说明为什么它不会改变每条事件的累计重试预算。
