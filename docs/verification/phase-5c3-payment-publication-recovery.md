# Phase 5C-3：人工支付发布补漏与真实 Broker 故障恢复

- 日期：2026-10-05（Asia/Shanghai）
- 主线：Track C / Depth；延续 C1 真实 MQ、C2 有界后台重试。
- 范围：初次/重试消息漏投的人工单轮补发布，原 Inbox 事件与预算保留。
- 本轮不增加依赖、模型、迁移、Beat、Outbox 或结果库；默认同步。

## 基线问题与最小设计

MySQL 提交原通知之后，RabbitMQ 发布仍可能失败。C1 的初次失败保留
RECEIVED/count0，C2 的重试发布失败保留 ERROR/TRANSIENT/count1或2/next。
这些状态有持久事实，但没有独立的下一次发送机会。

新增 `republish_payment_events`，管理命令通过显式 `--publish` 选择它；不带
该参数的人工同步恢复保持原行为。命令只允许 development/test 且异步开关
为 true，预览也遵守环境要求。底层服务拒绝外层事务。

补投选择 RECEIVED/NONE/count0 或已到期 ERROR/TRANSIENT/count1或2，
按 received_at/主键固定最旧批次；每条发布前再查资格。网络调用不持数据库
锁，只发原内部整数 ID，不写事件状态、次数、金额、订单或库存。
首次发布返回 False 就停止本轮，报告失败 ID 和未尝试条数；已发前缀不撤回。
未知编程错误不会被当作正常 Broker 返回值吞掉。

`published` 是发布成功数，不是付款成功数。即使失败，也可能存在消息已收到
但确认丢失的情况；重复投递继续由原 MySQL 资格与业务事务保护。
[RabbitMQ 可靠性指南](https://www.rabbitmq.com/docs/reliability)说明确认丢失
可能导致重发，消费者需要幂等。本段允许多发，不引入已发布标记来推断业务完成。

同步恢复保留 5B 的历史候选规则；新增补投对不完整/矛盾计数更严格，不把
RECEIVED/count>0 或 TRANSIENT/count0 当作正常候选。PROCESSING 不接管。

替代方案是周期扫描或 Outbox；前者引入无人值守调度，后者引入发布意图维护。
当前人工入口能支持这一故障切片，代价是需要人工触发，没有自动恢复时限，
反复扫描可能重复发送、前部老候选可能占批次。未声称支付 MQ 全部完成。

## 实验与隔离

沿用隔离 MySQL `test_project_one`、主机端口3307；RabbitMQ test 端口5673、
管理API15673、vhost `project_one_test`、queue `payment-events-test`；Linux
prefork 单并发非root Worker，无 eager。凭据由脚本在内存读取，不写入证据。
测试服务源挂载只读，主 Compose、业务服务和 Celery Task 不变。

```powershell
.\scripts\verify_payment_mq.ps1 -Stage recovery -WriteEvidence
```

脚本校验测试 Worker 的数据库与 Broker/queue，建库前停止 Worker，在 finally
再次停 Worker 并恢复测试 Broker。pytest 建/迁移/清理专用测试库；没有执行
开发库迁移或管理命令。每场景重建专用队列的零统计，禁止共享该库并行跑测试。

初次漏投：停专用 Broker，实际 Webhook 202/RECEIVED/count0；真实补投失败
命令非零，原业务数据不变。恢复 Broker 后 Worker 仍停，连续两次补投原 ID；
此时数据库仍待处理，启动 Worker 后等待两条 ACK，处理次数1、唯一 COMMIT。

重试漏投：真实独立事务锁住 Inventory，测试 Worker 每连接仅设置 SESSION
锁等待1秒，业务真实1205后保存 ERROR/TRANSIENT/count1/+5秒。C3专用
launcher 仅在 ETA 发布前协调故障窗口：记录安全日志，最多等20秒，用 socket
观察 test Broker 关闭后调用原真实发布器。没有伪造数据库异常、发布结果或
资格时间；C1/C2没有启用此协调标志。

实际停 Broker，观察原 Task 的 retry publication failed，停 Worker、释放锁；
重启 Broker 后刻意清理原未确认任务，仅触及专用测试队列。这个实验步骤是
为了排除旧消息自动重投，不是业务恢复的必需操作，不能在业务队列照做。
同步 AMQP 被动声明确认 ready 条数与 consumer_count=0（管理API统计可能
滞后），清理后再次被动声明确认 ready=0，重新取得 ACK 基线。Worker 仍停，
最终由新的 Python/manage.py CLI 子进程唯一补投原 ID，继承并逐项硬检查
test库、host/port、test环境、async开关、队列与专用Broker，避免把 pytest
测试库切换误当成 manage.py 子进程也会自动切换；
最后 Worker count2成功，第二次资格时刻不早于原 next_retry_at。

## 本轮结果

- 规则/命令/5A/5B：36 passed、20 subtests passed，21.11秒。
- 首次真实恢复实验：1 passed、1 failed，63.58秒。首次漏投通过；重试
  漏投已观察原publisher实际失败和ERROR/count1不变，但Broker恢复后的
  同进程call_command首次发布再次失败。与检查到的ProducerPool复用
  Connection、retry=False不在发布中重连的路径一致，旧连接原因属于推断，
  未另记录原始异常或断言所有恢复首次必失败。
- 未改业务逻辑、未清连接池。按实际CLI使用新进程补投后，第二轮2 passed，
  72.54秒；随后补强子进程隔离校验和AMQP同步计数，最终复验见下面结果。
- 本轮C1真实基础链路：3 passed，41.00秒；C2真实有界重试：3 passed，68.02秒。
- 最终C3真实恢复实验：2 passed，71.69秒；已包含子进程隔离校验与AMQP
  同步计数补强。与本轮C1/C2合计8项真实MQ场景通过。
- 支付/订单回归：141 passed、8 skipped、62 subtests passed，97.96秒。
  8个skip仅因普通回归未开启显式MQ配置，已在本轮C1/C2/C3真实流程分别通过。
- 实际检查：pip check 无依赖冲突；compileall、Python AST、两段指南代码
  与源码AST一致、本地链接、组合Compose配置与git diff --check通过。
  本期全新文件另核对尾随空格与代码围栏，不把静态检查当作服务证据。

原始记录：[phase-5c3-payment-publication-recovery-data.json](phase-5c3-payment-publication-recovery-data.json)。
学习与操作：[C3 指南](../learning/phase-5c3-payment-publication-recovery-study-guide.md)。

| 真实场景 | 补投前 | 最终次数 | Event/Attempt 数 | 库存 available/reserved | COMMIT 数 | 最终 ACK 增量 |
|---|---|---:|---|---|---:|---:|
| 初次发布停机，恢复后补投两次 | RECEIVED/NONE/count0 | 1 | 1/1 | 19/0 | 1 | 2 |
| Worker1205后重试发布停机，清旧任务，再新CLI补投 | ERROR/TRANSIENT/count1 | 2 | 1/1 | 19/0 | 1 | 1 |

两场景最终 ready/unacknowledged=0；付款SUCCEEDED、订单PAID、预占COMMITTED。
初次失败仍HTTP202，不把已入箱误读为付款。未到期预览selected0，到期selected1；
补投命令返回前 Worker停止，因此SQL状态/次数及待付库存仍与补投前一致。
重试场景实测1205一次，AMQP同步确认恢复后旧ready任务1条并刻意清除。
第二次资格as_of `2026-10-04T17:05:43.951902+00:00`，原next
`2026-10-04T17:05:29.783981+00:00`，晚14.167921秒；这是实测恢复耗时，
不是调度SLA或生产性能。UTC记录在上海时区属于2026-10-05。

## 变更与后续边界

生产逻辑仅 recovery.py、recover_payment_events 命令，以及 dispatch.py 的
注释更新。其他是新目标测试、真实恢复实验、专用 Compose 覆盖、测试 launcher
协调标志、验证脚本 recovery 阶段和进度/学习/证据文档。

前端、Redis、金额/库存算法、支付状态机/共同处理器、Task、模型、迁移、
依赖和主 Compose 未修改。保留全部前序未提交修改，不自动 commit/push。

下一步处理 PROCESSING 的中断接管与真实 Worker 故障；后续须验证业务事务
提交与消息确认之间的中断不会重复核销/退款。本段不能证明该行为，也没有
自动周期补漏、数据库不可用恢复或多机容灾结论。

清理仅验证正常 Docker stop/恢复路径；Docker 不可达或 stop 失败时，外层
脚本虽报错，仍不能承诺 Django 清理前必已停止消费者。

## 最终源码指纹

下列为本轮文件的SHA256，用于将实验切片与当前文件核对；前序未提交修改
不属于本期新增内容，Git HEAD本身不能代表当前工作区。

```text
recovery.py                         9ea7bb51330abc6771d4b7fa5eee9b4c75e1f5587435037159b511cabcc4482c
commands/recover_payment_events.py   3d35b88aee3b400faea8a525bd4e96a2d61e191ec1c55ee54b2d5e454f0ee540
dispatch.py                         27e29c6a89c27399c42fa5be82caa76cc1dcfe35dc9f7d05f0772e6f6fcb29e8
test_event_publication_recovery.py   c3c47a849a1353a7318343aa28a5f1d05a935ee49c57d886430f8f0eb5ada708
test_event_mq_recovery.py            5c89836335e2dc4648f6dab44e280919047d1e7dfaa634387055e5bb53e2e8a6
test_event_mq.py                     e1b32cfe5a431cf9eadab1c5459db6d356b16c4677301d9f7360eee2635ca72a
mq_retry_worker.py                   4c495b91e40286703752ec843452be27b905e8da9a6d65c1cdf7895149ed6308
scripts/verify_payment_mq.ps1         2127c910c30629d768e02e49f7582413a37b82a7209f705e71152c29cb71ca9a
compose.payment-mq-recovery-test.yaml 1581c09f1a5dcd6e3a94e61287ceef7fe2361b217315159493e297b658da47a9
```

已实际核对主Compose、services.py、tasks.py和C1/C2历史JSON的指纹与前期
一致；本轮baseline/retry脚本未启用WriteEvidence，不覆盖历史原始记录。
最终C3原始JSON SHA256：`6227459ae49c95d6f54c958f2c4bf61ba0bceb11b9f29d76cadf85938c9ed758`。
结束状态：main；测试Worker已停止、测试Broker和原db保持healthy；未启动开发Worker。
