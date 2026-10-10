# Phase 5C-1：真实支付消息队列基础链路验证

阅读入口（2026-10-10补链）：[本片学习指南](../learning/phase-5c1-payment-mq-study-guide.md)。仅增加导航，以下日期、结果与限制保留原实验记录。

日期：2026-10-01。属于 Track C / Depth，增强支付状态、Webhook 与幂等主线。
本段只完成真实 MQ 基础链路，不将后台重试、漏投补偿或崩溃恢复标为完成。

这是 C1 历史证据，源码摘要、原始 JSON 与当时测试结果保留。2026-10-04
的有界后台重试与当前代码摘要见 [C2 验证记录](phase-5c2-payment-mq-retry.md)。

## 业务问题与选择

同步基线在 HTTP 中保存事件后立即处理订单和库存，人工恢复依赖主动运行命令。
本段把接收与处理分开：MySQL Inbox 原文先独立提交，RabbitMQ 只传内部整数 ID，
独立 Linux Celery Worker 调用原有资格入口与支付处理器。默认
`PAYMENT_EVENTS_ASYNC=false` 保留同步基线；异步功能限本地开发/测试环境。

替代方案是仅定期扫描 MySQL Inbox：基础设施更少，但及时性与数据库轮询成本
需要权衡。MQ 提供独立任务执行通道，却增加 Broker、Worker 与确认窗口，仍需
Inbox 补漏。项目已明确 MQ 必做，本段不新增 Outbox、任务结果库、Beat 或通用平台。

## 已实现的边界

- 接收入口与发布入口拒绝外层数据库事务，保证发布时原文已独立提交。
  发布前独立数据库连接确实能读取 Inbox，订单/库存锁不陪同 Broker 网络等待。
- 消息只含正整数内部 ID。Mock 发起付款、原 Attempt 查单与 Webhook 都复用
  `receive_payment_event`，不另造付款状态机或付款尝试。
- Worker 复用 5B 持久资格、累计总共 3 次预算和到期门槛；支付、订单、库存
  副作用与 Event 终态仍在原业务事务中提交。重复任务看到终态不再次扣预算。
- 已知 Broker 发布错误只记录内部 ID，保留 `RECEIVED/count=0` 原文；
  不把消息网络失败伪装为支付业务 `ERROR`，本段也不自动补漏。
- 异步 Webhook 未完成时返回 202，处理完返回 200，已拒绝返回 400。
  202 表示原文已入箱，不表示验签通过、付款成功或消息已经可靠发布。
  创建 Attempt 的 201 仍表示创建记录；返回 CREATED 时订单尚未付款。
- JSON、持久任务消息、发布确认、延后消费确认与 prefetch=1 已配置。
  任务 `max_retries=0`，当前没有自动重试调度；数据库预算没有被 Celery
  的消息次数代替。延后确认配置不能单独证明所有 Worker 中断可恢复。

## 环境与实际安装

| 部件 | 实际环境 |
|---|---|
| 主机 | Windows PowerShell，Python 3.11.9，项目 `.venv` |
| 后端依赖 | Django 5.2.17、Celery 5.6.3、Kombu 5.6.2、mysqlclient 2.3.0 |
| MySQL | 既有 mysql:8.4.10，主机 127.0.0.1:3307 |
| 测试数据库 | `test_project_one`，pytest 创建/迁移/销毁，未应用开发库迁移 |
| RabbitMQ | 实际拉取并启动 rabbitmq:4.3.6-management-alpine |
| 隔离 Broker | AMQP 5673、管理 API 15673，均只绑定 127.0.0.1 |
| 消息空间 | vhost `project_one_test`、队列 `payment-events-test` |
| Worker | 实际构建 Linux Python 3.11.16 镜像，非 root，prefork 并发 1 |
| 源码 | 后端只读挂载；`.env` 不进入镜像；不执行开发库迁移 |

宿主 Celery 安装和 `pip check` 实际通过，RabbitMQ 镜像拉取、Worker 镜像构建
及测试 Broker healthy 均观察到。它们本身不构成付款或恢复正确性证据。
Windows 没有直接运行 prefork Worker；使用 Docker 中的 Linux Worker。

## 首次失败与最小修正

第一次真实联调：`3 failed in 94.43s`，均等待 Event 终态超时。Worker 启动
期间，RabbitMQ 拒绝内部控制队列，出现：

```text
Queue.declare: (541) INTERNAL_ERROR
Feature `transient_nonexcl_queues` is deprecated.
By default, this feature is not permitted anymore.
```

后续反复重连触发进程重启频率错误，任务没有正常消费。原文已保存，失败点
在 Worker/Broker 队列声明，不在支付金额或库存核销规则。
[RabbitMQ 队列文档](https://www.rabbitmq.com/docs/queues)说明 4.3 起默认
拒绝非持久且非独占经典队列。[Celery 配置文档](https://docs.celeryq.dev/en/stable/userguide/configuration.html)
提供内部控制与事件队列的 durable 设置。

最小修正是在 settings 中设置 `CELERY_CONTROL_QUEUE_DURABLE=True` 和
`CELERY_EVENT_QUEUE_DURABLE=True`，exclusive 保持默认 false。仍保留默认
未使用队列过期时间，control 10 秒、event 60 秒；这些内部队列有别于支付
任务队列。没有开启 RabbitMQ 已废弃特性的许可，也没有降低测试判据。

随后审查补齐测试脚本的隔离清理：pytest 建库前先停残留的专用 Worker；
结束时再次停 Worker，清理失败也报错，同时还原调用者环境变量。

## 真实服务验证

以下全部经过真实 RabbitMQ 与独立 Worker，不使用 eager 或 MQ 发布 Mock。
每项结束前等待专用队列对应 ACK 增量、ready=0、unacknowledged=0，之后
停止 Worker、清专用队列，才让 Django flush/销毁测试库。

| 场景 | 实际观察 |
|---|---|
| 先受理后确认 | 停 Worker 时 Webhook 返回202，Event RECEIVED/count=0，订单待付。启动真实 Worker 后 Event PROCESSED/count=1、Attempt SUCCEEDED、Order PAID、Reservation COMMITTED，库存19/0，唯一COMMIT，ACK增量1 |
| 同事件三条消息 | 三个不同 Celery task ID，参数为同一个 Inbox ID；等待三条全部确认。Event/Attempt各1，处理次数1，COMMIT流水1，ACK增量3，队列无待处理/未确认消息 |
| 伪造后合法通知 | 伪造签名事件 REJECTED/invalid_signature_or_provider，订单待付、库存仍预占，无COMMIT；它没有占用合法 provider event_id。随后合法原文 PROCESSED、订单PAID，仅一条COMMIT，两事件分别留存，ACK增量2 |

原始结果：[phase-5c1-payment-mq-data.json](phase-5c1-payment-mq-data.json)。
文件保存最后一次成功验证的三场景、任务 ID、数据库终值和队列确认，不含凭据。
多个任务是顺序消费的重复消息实验，不是多 Worker 并发、Broker 重启或杀进程实验。

## 实际检查结果

- 事务/异步合约与 5A/5B 目标：`40 passed, 24 subtests passed in 19.16s`。
  新增异步合约14项；发布 Mock 和直接 task.run 只证明规则，不是 MQ 证据。
- 修正内部队列配置后的真实 MQ：`3 passed in 54.40s`。
- 加强测试清理脚本后再次真实 MQ：`3 passed in 44.27s`，原始 JSON 为这次结果。
- 支付与订单回归：`122 passed, 3 skipped, 45 subtests passed in 98.87s`。
  这里3个skip是未设置显式 MQ 配置时的集成用例；它们已在上述独立流程实际运行。
- `pip check`、Python compileall、两个 MQ profile 的 Compose config --quiet、
  git diff --check 实际通过；Git 仅提示既有 LF/CRLF 转换。
- 本期文件语法/尾空白、Markdown 围栏/本地链接、三场景JSON及源码摘要检查通过；
  指南3处Python节选与源码AST一致。既有MySQL/Redis Compose服务文本与HEAD一致。

## 本期修改文件

- 支付入口与任务：`backend/apps/payments/services.py`、`views.py`，新增
  `dispatch.py`、`tasks.py`；新增 `tests/test_event_async.py`、`tests/test_event_mq.py`。
- Celery：`backend/config/settings.py`、`__init__.py`，新增 `celery.py`。
- 依赖与运行：`requirements/base.txt`、`pyproject.toml`、`compose.yaml`、
  `.env.example`，新增 `.dockerignore`、`docker/payments/Dockerfile`、
  `scripts/verify_payment_mq.ps1`。
- 当前进度：`PROJECT_CHARTER.md`、`CONTEXT.md`、`README.md`、`readme.md.cn`；
  新增本期学习指南、验证记录及原始JSON。前序支付模型/迁移与Redis修改保留。

## 复现与隔离要求

从仓库根目录 PowerShell 执行；既有 MySQL 测试服务及专用测试库权限可用。
不和其他使用 `test_project_one` 的 pytest 并行运行。

```powershell
docker compose --profile payment-mq-test build payment-worker-test
docker compose --profile payment-mq-test up -d --wait rabbitmq-test
.\scripts\verify_payment_mq.ps1
```

脚本从 Compose 在内存读取测试凭据和共享 Mock 签名密钥，不打印完整配置。
默认不重写原始证据，使用 `-WriteEvidence` 才写本段 JSON。
不要自行启动测试 Worker，让测试在数据库就绪后启停；不要无服务名地启动
整个 profile。Worker 的环境强制指定测试数据库，测试检查本地 Broker、
vhost/queue、实际测试库名并排除 eager。测试仅清专用测试队列。
Compose 当前 AMQP URL 要求自定义密码为 URL-safe 字符，见 `.env.example`。

## 尚未实现或验证

- 到期暂态失败的自动后台重试；当前记录 next_retry_at 不会自行产生新任务。
- 提交后尚未发布或发布失败的自动 Inbox 补漏；当前 RECEIVED 可能一直等待。
- PROCESSING 的租约/失效资格与接管；Worker 硬中断可能保留该状态。
- 业务提交与消息确认间的真实进程终止、Worker/Broker 故障与恢复实验。
- 第三方 Sandbox、真钱、生产高可用、性能容量和浏览器验收。

下一步 C2 复用现有预算与门槛补有界后台重试，再逐段补漏投和中断恢复。
当前测试 Broker 保留运行，专用 Worker 已停止；未启动开发 Worker、操作开发
数据、覆盖 Redis 前序修改或执行 commit/push。本段新增依赖、MQ配置、
异步接收/任务入口、目标测试及文档，没有改变金额、库存算法或前端。

## 工作区来源

基准 HEAD：`4e1a1ae211bd9bfab37c14c85aed11d3cd580eef`。
本次验证针对含前序 Redis/支付未提交修改的工作区，不能将 HEAD 本身当作
本段实现已提交的证据。Worker 本地镜像 ID：
`sha256:5c0cb3c045736ded4e76f7129010816529da70c0cdf4f9116685d4dfa4d70f71`。
RabbitMQ 拉取摘要：
`rabbitmq@sha256:95312f53d5b1115d410f1a56fba03a6325e3e03b4c60a845237bd345f341a097`。

验证时关键工作区文件 SHA-256：

| 文件 | SHA-256 |
|---|---|
| backend/apps/payments/services.py | 71df9a8a5b86aca26c56cab21efb1afddddc7f0c2e4198d1af4b76c9e6531065 |
| backend/apps/payments/dispatch.py | 4af1f55027e3cb2d486e12f472d13335cae4e947b88037a10a25344c59acb7d6 |
| backend/apps/payments/tasks.py | 5833a812eb9bcc0ce32082f9a6d07d3db1878c5f113a532634e7004df3f5ab24 |
| backend/apps/payments/tests/test_event_mq.py | 924276cc9470258fc4ba64856f1ea6d77e2acbc0fbe53354f227608bc6a2a782 |
| backend/config/settings.py | 9f7a7975af9781e7d3f31c517a96eeaaa8b2687976b2f3d9d8b94e6cf8a56e31 |
| compose.yaml | dcf23db158a7a1f7131966f9a55c278b632bbbe056571891f5f4bd6ac4d2e165 |
| scripts/verify_payment_mq.ps1 | 122f5393310d11bd5b42f9a29fda260ea23dda0c0bcfa9a9452d772b2471b8eb |
| docs/verification/phase-5c1-payment-mq-data.json | 4ba3c8bc74594a77b499166d29f9a4872976dbb086db367c9332794613d3fbd1 |
