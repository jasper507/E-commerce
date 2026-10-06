# Phase 5C-4：支付 Worker 中断与人工过期领取恢复

- 日期：2026-10-05（Asia/Shanghai）；Track C / Depth。
- 交付：持久处理租约、人工单轮资格修复、真实进程中断与原消息重投证据。
- 前序Git快照：`2dacc9a84c912c4d3be0c4f2efc2cba6a3b116e2` 已推送main；本期C4增量仍在本地。
- [操作/核心代码/面试指南](../learning/phase-5c4-payment-worker-recovery-study-guide.md)
  与[四场景原始JSON](phase-5c4-payment-worker-recovery-data.json)配套阅读。

## 业务问题、基线与取舍

原事件处理已经分成T1领取、T2业务处理、T3异常留存。T1提交PROCESSING和
处理次数后，进程可能在T2之前或期间直接消失，没有机会运行except/T3。
RabbitMQ可以重投未确认消息，但原领取门槛会挡住PROCESSING，事件仍然卡住。
这影响189元原订单的付款确认与库存核销；应继续处理原通知，不创建新订单、
新付款尝试或按商品新价格重算金额。

| 方案 | 收益 | 代价与边界 |
|---|---|---|
| 只保留PROCESSING、依靠消息重投 | 实现简单，不抢正在执行的资格 | Worker退出后持久资格不能继续；C4实验在硬中断后观察到原状态 |
| 看到PROCESSING就改成ERROR | 恢复快 | 可能覆盖仍在运行的事务，无法识别原领取版本；不采用 |
| 持久租约+锁内版本复查+人工恢复 | 有确定的恢复候选，保留次数，沿用业务处理器 | 多两个字段/一条迁移；需要人工触发，不能承诺自动恢复时限 |

租约是“到这个时刻后可以检查处理资格”的持久时间。它不是进程死亡证明，
不是Celery time limit，也不会自动kill任务。默认60秒；四场景专用测试覆盖
设为8秒以缩短等待，真实等待到期，没有修改数据库截止时间来假装过期。

## 最小实现与不变量

1. PaymentEvent增加可空`processing_started_at`和`processing_lease_until`。
   T1拿到Event行锁、核对资格与预算后才读实际当前时间，避免把等待锁的时间
   算进新租约。每次合法领取count加1，重复PROCESSING不会续租或增加次数。
2. T2继续在同一事务内提交支付事实、Order状态、库存/退款效果与Event结果。
   原status/count门槛保留：旧执行者不能覆盖已经修复或重新领取的版本。
   处理成功、拒绝或异常留存会清租约截止时间，保留最后领取时间供调查。
3. `recover_stale_payment_events`固定最旧候选快照，每条独立短事务；只选
   有完整时间、开始不晚于截止、租约到期、count在1至3的PROCESSING。
   锁内使用`select_for_update(skip_locked=True)`，再核对原count和原截止。
   正在被T2持锁、状态/版本/截止变化、空租约或异常时间均跳过。
4. 合格count1/2改成ERROR/TRANSIENT，`next_retry_at=修复时刻`；过期等待已经
   经历，不额外伪造一次新的5/10秒处理异常。count仍是原值，下一次真正领取
   才增加。count3变ERROR/EXHAUSTED，无next；没有第四次领取。
5. 修复只写Event元数据，稳定错误码`processing_lease_expired`；不执行业务，
   不投递消息，不重置预算。之后人工选择原同步恢复或C3 `--publish`。
   `--recover-stale`与`--publish`互斥，CLI和`call_command`关键词均有检查。
6. 迁移0004没有猜测旧记录的开始时间。历史PROCESSING保留NULL租约供调查，
   不能用received_at冒充处理开始。只在隔离测试库检验迁移，没有操作开发库。

恢复服务禁止外层事务。每条修复自己的短事务已提交后，后续数据库异常可能
结束本轮，之前修复不会整体回滚。锁只能保护仍持有Event业务锁的事务；
一直持锁或没有完整租约的事件不被这条命令强行接管。

[Django行锁说明](https://docs.djangoproject.com/en/5.2/ref/models/querysets/#select-for-update)
支持这里的skip_locked与事务边界；
[Celery late ACK说明](https://docs.celeryq.dev/en/stable/userguide/tasks.html#acks-late)和
[RabbitMQ连接关闭后回队说明](https://www.rabbitmq.com/docs/confirms#automatic-requeueing)
说明消息层的责任。下面的结果来自本项目实际实验，没有把文档当作实验结果。

## 隔离条件与故障注入

- Windows PowerShell、宿主Python3.11.9、Django5.2.17、Celery5.6.3、Kombu5.6.2。
- 沿用已构建的非root Linux Python3.11.16 prefork Worker，concurrency=1。
- MySQL镜像8.4.10，宿主127.0.0.1:3307，仅`test_project_one`；Worker连接db:3306。
- RabbitMQ镜像4.3.6-management-alpine；宿主AMQP5673/API15673，专用
  `project_one_test` vhost及`payment-events-test`队列，eager=false。
- 基础compose和业务Task未修改，无新增依赖/镜像安装。专用
  `compose.payment-mq-crash-test.yaml`启用测试launcher和8秒租约。
- launcher必须核对test环境、数据库、Broker、vhost、队列及专用标记。
  包装原函数观察执行窗口，不替换业务结果，不向生产任务加入sleep分支。
- 实际执行`docker compose ... kill -s SIGKILL payment-worker-test`，终止Linux
  Worker容器内进程。固定窗口依次断在T1提交后、T2写入但未提交、T2提交后。
  观察标记的`connection.in_atomic_block`分别为false、true、false。
- T2窗口在原处理器返回但外层业务事务仍在时暂停；此时实际SQL已经执行，
  独立连接看不到未提交业务事实。不是仅在处理之前sleep来冒充事务回滚。

每个场景在kill后通过AMQP被动声明确认至少1条ready任务、0消费者。恢复期间
保留原未ACK任务，不purge。重启后观察同一个Celery任务UUID从redelivered=false
变为true，另补发一条不同UUID，最终等待两条ACK和ready/unacknowledged=0，
再检查MySQL终值。C3为了隔离人工补投曾清队列；C4没有沿用那个做法。

## 四个真实场景的最终结果

| 场景 | 中断时持久事实 | 恢复与MySQL终值 | 消息收尾 |
|---|---|---|---|
| T1提交后、T2前kill | PROCESSING/count1，订单待付，库存19/1 | 租约到期后人工修复/补投原ID；PROCESSED/count2，PAID，库存19/0，COMMIT1 | 原UUID重投+补发；ACK增量2，ready/unack0 |
| T2写入未提交kill | 独立连接仍见PROCESSING/count1、待付、库存19/1 | 活进程租约到期仍持Event锁，扫描选中但跳过；kill后业务回滚，人工修复/补投；count2，PAID，库存19/0，COMMIT1 | 原UUID重投+补发；ACK增量2，ready/unack0 |
| PAID业务提交后、ACK前kill | PROCESSED/count1，PAID，库存19/0，COMMIT1，截止已清 | stale候选0；原消息与主动重复任务均不增加count或核销 | 原UUID重投+重复发布；ACK增量2，ready/unack0 |
| 迟到成功/模拟退款提交后、ACK前kill | PROCESSED/count1，Order保持CANCELED，Attempt成功，退款1，库存20/0 | stale候选0；重投后仍退款1、RELEASE1、COMMIT0，count1 | 原UUID重投+重复发布；ACK增量2，ready/unack0 |

第四场景先同步处理一条有效FAILED通知，确认原付款尝试失败，再通过现有
`cancel_order`取消并释放库存，最后接收迟到SUCCEEDED通知。最终是2条Inbox、
1条PaymentAttempt，不是2次付款；早先FAILED事件是合法准备步骤。迟到成功
不能把取消订单重新变成PAID，也不能再次核销或释放；原处理器产生一次退款模拟。

第二场景skip耗时原始记录为`0.0`秒，受Windows计时分辨率影响，只能作为
没有等到业务锁释放的观察。测试断言小于2秒；不是性能SLA或零成本证明。

## 实际运行、失败与修正

| 检查 | 实际结果 |
|---|---|
| 推送前普通后端回归（C4之前） | 231 passed、80 integration deselected、76 subtests，114.40秒 |
| C4租约/发布/恢复/重试/任务/异步六模块目标检查 | 69 passed、62 subtests，28.13秒 |
| 首次真实C4实验 | 3 passed、1 failed，71.39秒；退款准备的取消被现有payment_unresolved规则拒绝 |
| 修正后真实C4实验 | **4 passed，81.71秒** |
| C4支付与订单回归 | **151 passed、12 skipped、83 subtests，96.51秒** |
| C1真实MQ基线回归 | **3 passed，41.98秒** |
| C2真实后台重试回归 | **3 passed，65.37秒** |
| C3真实Broker停机/发布补漏回归 | **2 passed，73.87秒** |

首次失败发生在CREATED付款尝试尚未解决时取消订单，测试尚未进入退款中断
窗口。修正仅调整测试准备：先处理有效FAILED，再取消；没有放宽取消规则或
改支付/退款业务状态机。前三个真实窗口已通过，最终整轮四项通过。

目标测试包括真实MySQL行锁跳过、重复/竞争修复、旧T2/T3版本被挡、终态清租约、
候选与命令门槛、历史NULL迁移，以及第三次过期耗尽且不能第四次领取。后者是
规则/事务测试，四个真实实验没有对同一事件连续kill三次，不能混称同一证据。

支付/订单回归的12项skip是4组MQ用例没有普通回归配置；12项均已在独立真实
脚本中通过。C1/C2/C3在本期共享领取代码变化后顺序回归，不加WriteEvidence，
历史JSON未覆盖；本期C4原始JSON保留修正后四场景终值与原任务重投观察。

## 复现命令及影响

在项目根目录执行：

```powershell
.\scripts\verify_payment_mq.ps1 -Stage crash
```

脚本从Compose只在内存读取凭据，核对隔离目标，建测试库前停止专用Worker，
运行pytest并在finally停止它/恢复本进程环境。该命令会创建、迁移和销毁测试
数据库并kill测试Worker，不迁移开发库、不访问真实支付或真实资金。
默认不改历史证据；加`-WriteEvidence`才写本期四场景JSON。

人工恢复流程先`--recover-stale --dry-run --event-id 原ID`预览，再显式修复。
修复输出selected/retryable/exhausted/skipped，不能把retryable当成付款成功。
之后使用原同步处理，或异步环境下单独`--publish`，详细命令与输出解释见指南。
本轮没有对开发库执行这些修改数据的命令，也没有自动调度任务。

## 本期边界与下一步

- 默认同步开关保持，HTTP/API与顾客页面、金额、库存算法、Redis实现未修改。
- 没有Beat、Outbox、结果库、心跳续租或支付之外的异步任务；开发库没有迁移。
- 人工修复依赖触发时机，不承诺无人值守恢复、空租约接管、一直持锁的恢复或生产容量。
- late ACK仅提供重投机会，MySQL事务、资格版本与业务幂等共同保护副作用。
- 仅验证实际列出的窗口和服务配置；Docker不可达/stop失败仍可能影响清理，本轮正常清理路径成功。
- 下一步整理支付与MQ证据包、讲清原付款查单及支付/关闭竞态已有证据，再按路线图进入商品秒杀；不扩展第三方支付或普通优惠券。

## 实验实现指纹

以下为最终实验代码与原始数据的SHA256；不是仅用Git旧提交替代尚未提交的
C4源码版本。历史C1/C2/C3 JSON、基础compose.yaml和Task指纹已核对未变。

```text
backend/apps/payments/models.py
38e43ccc7629e8e1098b53898c953da57d0e6d27d4e9e1cb58e2eea29daf50ae
backend/apps/payments/migrations/0004_paymentevent_processing_lease.py
ab931e2302ed8bd7ad5107d8c5ff9d6423a8551a9b6edf34495272aac2c7bb80
backend/apps/payments/services.py
1a06542d957fc4114f604419542004279ebd18bf72f502b6a09b91a59879939a
backend/apps/payments/recovery.py
b2d11748fe1f3cb134e899978815344288a176e6bc4de5e2ca5b5ee91ef63f22
backend/apps/payments/management/commands/recover_payment_events.py
c65e92ddd78cdd18845cefc7c9e14b8d279b940a02b7a01728978a34e99a78f2
backend/config/settings.py
0cc26b870b6b32df6004b77946e5633c85b975deea884bb89369352b63350360
backend/apps/payments/tests/test_event_processing_lease.py
63b1c896b05e3d073d2d093b7ad7a98ccd434579f67a59bfb81b061813d7d300
backend/apps/payments/tests/mq_crash_worker.py
035da0009511895ea698f08a6b7cb3dbd6885f339d349ece01a3e08d4d92a867
backend/apps/payments/tests/test_event_mq_crash.py
0c350352c2a5a9314f80b3ecf4e56a6f9172afe90291b5c3273d51ed796e14c8
scripts/verify_payment_mq.ps1
b1ff6bcfa0340d657e9362f44b9730965954bd28e303855ce281fa462e7d1940
compose.payment-mq-crash-test.yaml
72b9483846c30c3b090d559739473c9fd55b16f96677ff179226772bbec437d4
docs/verification/phase-5c4-payment-worker-recovery-data.json
158a94d6113dba152bfefb08f3838d82dee00d200cf26d7970614973c511263f
```

AST、JSON解析、指南本地链接/代码块、PowerShell脚本语法与组合Compose配置
检查均实际通过；迁移检查没有未生成的变更。新增文件仅文本；本期差异审查
未发现私钥、冲突标记或本机`.env`私有值。源码/规则审查未发现阻断问题，
不等于生产验证。

最终专用Worker停止，测试Broker和原db健康；未启动开发Worker，没有修改
开发库。main仍是已推送的`2dacc9a`，本期C4代码/测试/文档20个文件留本地，
没有在“先推送再下一步”之后自动推送这组新改动。
