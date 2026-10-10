# Phase 5C-1：让真实消息队列处理支付通知

本文件保留 2026-10-01 的 C1 基线和当时边界；2026-10-04 新增的后台
重试见 [C2 指南](phase-5c2-payment-mq-retry-study-guide.md)。下方“未自动重试”
指 C1 当时实现；当前默认 baseline 验证命令仍适用于正常与重复消费。

本段属于 Track C / Depth。按项目拥有者要求，先交付最小实现、验证与
学习资料，再阅读核心代码。它接在
[5B 持久重试预算](phase-5b-payment-event-retry-study-guide.md) 后面，
目标是建立真实 RabbitMQ 与 Celery Worker 的正常支付和重复任务基线。

## 本片前后变化

- **开始前：** 已有 Inbox、持久预算和人工恢复，通知处理仍主要占用当前 HTTP 请求，没有独立后台执行通道。
- **本片改变：** 原文先提交，RabbitMQ 只传内部 ID，由独立 Linux Celery Worker 复用原处理器；顾客读取 MySQL 业务状态确认结果。
- **完成后与边界：** 真实正常、重复及无效通知链路已验证，重复消息不重复核销；异步默认关闭，当时没有自动重试排程、发布补漏或 Worker 中断接管，202 仅表示入箱。

**后续衔接：** [C2 有界后台重试已完成](phase-5c2-payment-mq-retry-study-guide.md)。[学习导航](README.md)

## 解决什么业务问题

同步基线是“保存通知，再在当前 HTTP 请求中处理订单与库存”。规则正确，
但请求需要等待本地业务处理结束；人工恢复入口也需要有人主动运行。

Async Processing（异步处理：当前请求先受理，把后续工作交给后台）
让接收通知与处理业务分开。后端先将通知原文可靠保存，再发一个任务，
由独立后台进程处理。这一步不是重新付款，也不是把订单拆成微服务。

用 189 元鼠标订单理解：下单已预占 1 件库存，Mock 网关发来成功通知。
后端把通知保存并提交，向 RabbitMQ 发送“处理 Inbox 内部 ID 7”的任务。
HTTP 可以返回已受理；Worker 随后读取原通知，确认金额与签名，原子
更新支付、订单和库存。顾客需要根据后端最终状态判断是否付款成功。

## RabbitMQ、Celery 和数据库各做什么

| 部件 | 通俗作用 | 在本项目中的责任 |
|---|---|---|
| RabbitMQ / Broker | 消息中转服务 | 保存与投递支付处理任务 |
| Celery | Python 后台任务框架 | 定义任务、连接消息服务并启动执行进程 |
| Celery Worker | 独立后台执行进程 | 收到内部 ID，调用原支付处理服务 |
| MySQL Inbox | 已持久保存的通知原文 | 支付通知、处理状态和累计预算的事实记录 |
| MySQL 订单与库存 | 核心业务数据库 | 订单金额、状态、库存预占与流水的事实记录 |

消息只带内部整数 ID，不把价格、签名或完整支付原文当作任务参数。
Worker 根据 ID 读取数据库，再使用原规则验证；消息里没有“无需检查，
请直接把订单改成成功”的权力。队列只承担支付场景，不扩展通用任务平台。

Result Backend（任务结果存储）在本段不启用。任务返回成功不等于付款
成功，顾客状态应查询业务数据库，不能查询 Celery 的 SUCCESS 当作账单。

## 正常请求怎样完整运行

```text
Vue 发起 Mock 付款 → HTTP → 用户身份与订单归属检查
→ 短事务创建原 PaymentAttempt → 事务外调用 Mock Gateway
→ 网关结果交给统一 receive_payment_event
→ 短事务保存原始 PaymentEvent 和签名 → 提交
→ 事务外向支付专用队列发布 Event 的内部整数 ID
→ HTTP 返回“已受理”或当前 Attempt 状态
→ Linux Celery Worker 收到任务 → 读取原 Event
→ 5B 共用入口领取本次资格，提交次数与 PROCESSING
→ 业务事务重新验签、金额、币种、订单及状态
→ 支付确认、订单 PAID、库存核销、Event 终态一起提交
→ Worker 任务结束，按配置确认消息
→ 顾客读取后端状态，确认支付结果
```

Mock 发起支付、查原支付结果和 Webhook 都走同一个接收入口，不能只让
Webhook 走队列，其他入口却直接改订单。否则同一套验证可能出现两条路线。

若 Worker 很快，HTTP 返回前事件可能已经完成；若 Worker 尚未处理，
返回仍可能是待处理状态。客户端不能靠“接口没有报错”推断已经支付。
它也不能因为仍待处理而换订单、换支付尝试再次付款。

## 已受理不等于已支付

HTTP 202 Accepted（已受理：服务端接受工作，但结果尚未完成）表示原
事件已经保存在 Inbox，不证明消息必定投递，也不证明签名或业务校验通过。
通知最终仍可能被 REJECTED，或者处理时出现 ERROR。

创建 PaymentAttempt 的 HTTP 201 只表示创建了支付尝试，也不等于订单
PAID；读取接口的 HTTP 200 同样只表示本次查询完成。业务结果看响应
中的 Attempt/Order 状态。本段没有前端自动轮询页面的完成声明。

发布失败时，原 Inbox 已提交，不能删掉它或改成付款失败；也不能重新
付款。发布失败在日志中只记录内部 ID，未发布事件的后台补漏还需下一切片。

## 为什么提交之后才发任务

如果事务还没提交就发任务，Worker 可能读不到 Event，或者业务事务最后
回滚，队列却留下一个不存在的事件任务。网络等待还会延长数据库锁占用。
当前接收入口先退出短事务，再发布，并要求入口位于外层事务之外。

Celery 的 Django 指南也将提交后发送任务作为避免未提交数据问题的做法。
见 [Celery 与 Django 的事务边界](https://docs.celeryq.dev/en/stable/django/first-steps-with-django.html)。

提交后发布仍有窗口：数据库已提交，进程在发送前消失，消息就可能没发。
即使使用 on_commit，也不能把数据库和消息服务变成同一个原子事务。
Inbox 仍是后续补漏的依据，Transactional Outbox 保持条件性评估。

## 为什么重复消息不能重复核销

At-least-once（至少一次投递：同一任务可能被再次送达）要求业务承受重复。
不同消息可以带同一个 Event ID，任务也可以调用多次；实际处理资格、
累计次数、终态与库存约束由 MySQL 原处理器统一保护。

第一次正常完成后，Event 为 PROCESSED，原 Attempt 成功，Order PAID，
库存预占 COMMITTED，只有一条 COMMIT 流水。第二条同 ID 消息读取终态
并返回，不能再扣库存，也不能消耗第二次处理预算。

未完成事件继续遵守 5B：每事件总共最多 3 次含首次；只有 MySQL
OperationalError 1205/1213 才是允许等待后重试的错误，间隔 5/10 秒。
本段 Worker 复用这个入口，不另外定义第二套付款状态机或无界重试。
本段没有自动安排下次重试任务，next_retry_at 本身不会启动 Worker。

## 队列配置能说明什么

支付专用队列、JSON 参数、durable 队列与 delivery_mode=2 持久消息，
让队列用途和序列化明确。Publisher Confirms（发布确认：Broker 对接收
消息给出确认）能帮助生产者判断 Broker 是否接收，不能证明业务处理成功。
见 [RabbitMQ 可靠性说明](https://www.rabbitmq.com/docs/reliability)。

acks_late（延后确认：任务执行后再确认消息）是本段选择的任务配置，
不是 Worker 所有中断都可恢复的结论。Celery 文档明确说明，某些子进程
被终止的情形仍可能确认消息；异常、确认和重投行为要按实际设置实验。
见 [Celery 任务与消息确认](https://docs.celeryq.dev/en/stable/userguide/tasks.html)。

如果执行进程在领取资格后硬中断，数据库可能保留 PROCESSING。即使
消息再次到达，5B 入口也不会擅自接管。租约、失效资格与 Worker 中断
实验都留在后续切片，不用一个配置项宣称已经解决。

## 首次真实测试遇到的兼容问题

首次 3 个 MQ 用例都因 Worker 启动失败而超时。第一原因是 RabbitMQ 4.3
默认拒绝 transient non-exclusive classic queue（非持久且非独占的经典队列）；
Celery 5.6 默认的内部控制/事件队列声明遇到这条限制，Worker 因而不能正常消费。
这不是 189 元通知校验失败，也不是库存事务失败。版本规则见
[RabbitMQ 队列文档](https://www.rabbitmq.com/docs/queues)。

settings.py 已设置 `CELERY_CONTROL_QUEUE_DURABLE=True` 和
`CELERY_EVENT_QUEUE_DURABLE=True`；独占设置保持默认 false，内部控制队列
保留默认的未使用队列过期时间：控制队列 10 秒、事件队列 60 秒。
参数含义见 [Celery 配置文档](https://docs.celeryq.dev/en/stable/userguide/configuration.html)。

这些队列用于 Celery 自身控制或监控，不是传递 Event ID 的支付任务队列。
支付任务仍使用 durable 队列与 persistent 消息。修复后真实 3 项测试通过，
加强测试 Worker 清理脚本后再次通过；具体结果见下方验证记录。
这证明本段正常与重复消费，不代表 Worker 崩溃恢复已完成。

## 为什么有 pip 安装，也有 Docker

pip 安装的是当前 Python 环境要导入的 Celery 等库；它不会启动一个
RabbitMQ 服务，也不会自动创建后台 Worker。Docker 启动的是独立运行
服务：本段使用 RabbitMQ 容器与 Linux Worker 容器。

Windows 的虚拟环境负责 Django 接收端与 pytest；Linux Worker 镜像要
安装它自己需要的 Python 依赖。两者共享项目业务规则，运行在不同进程，
所以不能只在 Windows 装了库就认为 Worker 已经拥有那些依赖。

Docker Profile（按用途选择的一组容器）用于隔离 payment-mq-test。
测试 Worker 只连 test_project_one，不能连到未迁移的开发库。开发库迁移、
真实账号、第三方沙盒、真钱与发布上线均不属于本轮操作。

## 操作指南与真实验证

以下从项目根目录 PowerShell 开始。Python 命令使用 `-X utf8`，避免
Windows 默认编码导致中文输出异常。安装和启动会改变本地环境并下载依赖
或镜像，不等于业务验证成功；本轮已由项目拥有者授权。

先安装当前 Windows 虚拟环境的项目依赖，不使用全局 Python 环境：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pip install -r requirements\dev.txt
```

这一步让 API 和 pytest 能导入 Celery；不是启动消息服务或迁移数据库。
本轮已完成 Celery 5.6.3 安装、RabbitMQ 4.3.6 管理版镜像下载及 Linux
Worker 镜像构建；上面的安装命令用于复现环境，不必每次测试重新安装。
本段不教你未经检查就运行开发库迁移或恢复写操作。

测试库隔离仍由明确数据库配置与 pytest 生命周期保证；设置
`DJANGO_ENV=test` 只改变模式，不会让独立 manage.py 或 Worker 自动选中
pytest 的库。本轮必须同时核对 API 测试端和容器 Worker 的数据库目标。

已有本地 MySQL 测试服务运行时，先构建 Worker 镜像，再启动测试 Broker：

```powershell
docker compose --profile payment-mq-test build payment-worker-test
docker compose --profile payment-mq-test up -d --wait rabbitmq-test
```

build 安装 Linux 镜像的依赖；up 启动 AMQP 5673、管理 API 15673 的专用
测试消息服务。不要改成无服务名的 `up`，以免启动连接开发库的 Worker。
不手动启动测试 Worker，集成测试在测试库就绪后自行启停它。

先执行隔离数据库的接收/发布边界测试；这部分不等于真实 MQ 验证：

```powershell
$env:DJANGO_ENV = "test"
$env:CATALOG_CACHE_ENABLED = "false"
$env:MYSQL_TEST_DATABASE = "test_project_one"
$env:PAYMENT_EVENTS_ASYNC = "false"
.\.venv\Scripts\python.exe -X utf8 -m pytest backend\apps\payments\tests\test_event_async.py -q --tb=short
```

真实链路使用 [verify_payment_mq.ps1](../../scripts/verify_payment_mq.ps1)，
无需手工拼接含密码的 Broker 地址。脚本在内存读取 Compose 测试配置，
校验 test_project_one、project_one_test vhost 和 payment-events-test 队列，
临时让当前 Python 测试进程连接 localhost 的测试 Broker 与数据库。
Vhost（虚拟主机：Broker 内相互隔离的消息空间）与专用队列一起限制测试范围。
测试端与 Worker 使用同一签名密钥来源，脚本不打印完整配置或凭据。

```powershell
.\scripts\verify_payment_mq.ps1
```

默认运行不重写项目证据。如要记录本次结果，使用 `-WriteEvidence`；
它会写入 `docs/verification/phase-5c1-payment-mq-data.json`，属于证据文件修改：

```powershell
.\scripts\verify_payment_mq.ps1 -WriteEvidence
```

集成测试会清专用测试队列、启停 payment-worker-test，并在测试库运行
真实业务；不要和其他共享 test_project_one 的 pytest 并行执行。
出现 skipped 表示没有执行 MQ 验证，环境失败也不能当作通过。
脚本在 pytest 创建/迁移测试库之前先停止残留的专用 Worker，并检查停止结果；
finally 再次停止与还原调用者环境变量，清理失败也报错，避免后续
pytest 清理测试库时仍有 Worker 写入。它不为开发库迁移或启动开发 Worker。

真实 MQ 测试应看到独立 Worker 消费同一条已持久事件，再核对订单、支付、
预占与唯一流水。eager（当前进程立刻执行任务）和直接调用 task.run 可补充
单元测试，但不能证明消息真的经过 RabbitMQ 和另一个进程。

实际结果见 [Phase 5C-1 验证记录](../verification/phase-5c1-payment-mq.md)。
不能把 Docker healthy、Worker ready、发布函数没抛异常当作付款已成功。
正常及重复场景要等专用队列 ready/unacknowledged 都为 0，观察相应 ACK，
再断言最终只有一次处理预算和一条库存 COMMIT，不能只等第一个任务完成。

## 实际核心代码摘录

阅读顺序为 [settings.py](../../backend/config/settings.py) 的异步开关、
[services.py](../../backend/apps/payments/services.py) 的提交后分支、
[dispatch.py](../../backend/apps/payments/dispatch.py) 的发布函数，最后读
[tasks.py](../../backend/apps/payments/tasks.py) 的 Worker 入口。
Celery 应用定义在 [config/celery.py](../../backend/config/celery.py)。

以下是 receive 中的实际分支，前面的 Inbox 短事务已提交：

```python
if settings.PAYMENT_EVENTS_ASYNC:
    from apps.payments.dispatch import publish_payment_event

    publish_payment_event(event_id=event.pk)
    # 返回持久收件事实。Worker 可能已经更快提交，不在这里推断已付款。
    return event
return process_stored_payment_event(event_id=event.pk)
```

发布函数首先检查正整数 ID，并拒绝在数据库事务中调用。
下面是实际发布与已知 Broker 错误处理：

```python
try:
    # 不在 HTTP 入口反复等 Broker；原文已提交，遗漏发布留待补漏切片。
    process_payment_event_task.apply_async(
        args=(event_id,), retry=False, timeout=3, confirm_timeout=3
    )
except (OperationalError, AMQPError, OSError):
    logger.warning("Payment event publication failed: inbox_id=%s", event_id)
    return False
return True
```

`retry=False` 避免在 HTTP 发布阶段反复等待，3 秒参数是相关调用的超时
配置，不是整个请求耗时的严格上限。发布错误只日志记录内部 ID，不把
原事件改成业务 ERROR，也不增加处理次数；这时它仍可能是 RECEIVED/count=0。

任务装饰器使用 `ignore_result=True, acks_late=True, reject_on_worker_lost=True,
max_retries=0`。最后这个 0 只禁止 Celery 本段自行重试，业务预算仍是 5B 的 3 次。
Worker 实际复用同一个处理入口，异常只报告稳定错误码：

```python
try:
    event = process_stored_payment_event(event_id=event_id)
except Exception:
    # 当前不自动调度重试或接管 PROCESSING；分类/次数留在业务数据库。
    logger.warning("Payment event task failed: inbox_id=%s", event_id)
    raise PaymentEventTaskError("event_processing_failed") from None
return event.processing_status
```

REJECTED 是已完成拒绝，不一定让任务抛异常；任务正常结束不能证明付款成功。
Task 的异常原文可能含 SQL 或交易输入，本段不把它直接输出为原始回溯。

## 本切片完成边界和下一步

- 开关默认关闭，保留 5B 同步 MySQL/Mock 基线；只在隔离测试环境启用 MQ。
- C1 验证正常事件、同 ID 重复任务、无效事件拒绝以及提交后发布边界。
- 队列持久与确认配置已选择，不等于已验证 Broker 重启、消息丢失或 Worker 中断。
- 发布失败事件保存在 Inbox；本切片没有完整的定时补漏或恢复投递工具结论。
- 没有 Celery Beat、自动重试调度、Outbox 或 PROCESSING 租约接管。
- 不对开发库应用迁移，不做第三方支付或浏览器验收。

后续 C2 补到期暂态任务的有界后台重试；C3 验证 Broker 不可用、提交后
漏投与 Inbox 补漏；C4 设计并验证 Worker 中断、业务提交与消息确认之间
的窗口及 PROCESSING 接管。具体顺序以当前路线图和证据为准。

## 面试可能怎么问

1. **为什么使用 MQ？** 接收通知先可靠入箱，把本地业务处理交给独立后台；
   它支持支付深度故事，不扩大到所有业务场景。
2. **202 能表示付款成功吗？** 只能表示通知已受理，Worker 还要复验并提交业务终值。
3. **为什么消息只带 ID？** 原文、金额、签名与预算统一存在 MySQL，避免多份输入分歧。
4. **为什么发任务必须在提交后？** Worker 要读取已提交事件，也避免网络等待占用业务锁；
   提交后崩溃窗口仍存在，后续需要 Inbox 补漏。
5. **同一任务来了两遍怎么办？** 原事件锁与终态、预算和库存约束保护副作用，不靠消息次数。
6. **confirms 与 ack 有什么区别？** 前者是 Broker 向发布者确认，后者是消费者向 Broker 确认；
   两者都不能单独代替 MySQL 的付款事实。
7. **为什么不用任务结果库表示支付？** 框架任务成功可能是处理失败通知或重复终态返回，
   顾客结果必须读取业务状态。
8. **这能宣称完整故障恢复了吗？** 不能，C1 只建立正常与重复真实链路；
   Worker/Broker 中断、自动重试和补漏还需要单独实验与实现。

## 闭卷小练习

画出 189 元通知从入箱、发布、消费到唯一核销的流程，标出三个独立进程。
如果 HTTP 已返回 202，Broker 发布失败，哪条记录还在？为什么不能重新付款？
如果第二条消息带相同 ID，说明它返回时为什么不一定消耗第二次业务预算。
