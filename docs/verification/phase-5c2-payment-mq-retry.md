# Phase 5C-2：支付事件有界后台重试验证

阅读入口（2026-10-10补链）：[本片学习指南](../learning/phase-5c2-payment-mq-retry-study-guide.md)。仅增加导航，以下日期、结果与限制保留原实验记录。

本文件保留C2历史条件与结果；2026-10-05新增人工发布补漏及真实Broker
停机恢复，见[C3验证](phase-5c3-payment-publication-recovery.md)。历史JSON未覆盖。

日期：2026-10-04。属于 Track C / Depth，接在
[C1 真实消息链路](phase-5c1-payment-mq.md)之后，增强支付状态与幂等主线。
本段只覆盖已经持久留存为 TRANSIENT 的业务处理失败，不宣称完整消息恢复。

## 基线限制与最小选择

C1 已能保存原支付通知并交给真实 Worker，但5B的 next_retry_at 只是数据库
资格时间，不会主动产生下次任务。发生允许重试的锁故障后，原事件可能一直
ERROR，需要人工运行恢复。本段由 Worker 重新读取已提交事实，符合资格时
显式发布原内部 ID 的短 ETA 消息，稍后再进入共用处理入口。

MySQL 继续决定总共最多3次，包含首次；首次/第二次暂态失败后门槛为5/10秒。
消息参数仍仅整数 ID，ETA 是执行选项。没有新订单、付款尝试、重复扣款或
第二套库存规则。默认 PAYMENT_EVENTS_ASYNC=false，同步基线保留。

可选 self.retry 更贴近 Celery 框架重试接口，但其每条任务的计数不能替代
跨同步、恢复和重复消息入口的数据库预算。本段选择复用发布函数，任务仍
max_retries=0；这仅指不使用 Celery 自身 retry API，不指禁止本段显式排程。
另一方案是定时扫描/Beat，提供独立寻找事件的机会，但需要扫描与漏投策略，
留待C3从可靠发布窗口出发设计，不因为当前要重试就增加通用调度平台。

## 实现和事务边界

1. 原处理器先独立提交本次次数和PROCESSING，再执行业务事务；失败回滚后
   另事务分类并留存ERROR。5B的金额、签名、状态机、库存及累计规则未改。
2. Task 无论处理返回或抛异常，都重新GET数据库事件。仅 ERROR/TRANSIENT、
   次数1或2、有next_retry_at时安排；不能根据原异常猜资格或用旧对象调度。
3. 每次Task最多发布一条后续消息；发布不持数据库行锁，ETA为
   `max(next_retry_at, now + 1秒)`。正常5/10秒门槛不变，临界早醒时至少再等
   1秒，避免立即反复发布。实际业务资格仍在数据库锁内重新检查。
4. 提前消息不扣业务预算，但仍保留未来执行机会。多个重复任务可能各自排程；
   真正处理最多3次，传输消息不承诺最多3条。读取与发布间另一个任务已经
   成功时，可能多发一条终态无操作任务，仍不能再次核销。
5. 发布已知错误保留原ERROR/TRANSIENT、次数和时间；任务只抛稳定
   event_retry_publication_failed。没有自动补漏，也不把发布失败改成BLOCKED。
   未知处理错误、资格读取失败、失败留存失败不被盲目安排；PROCESSING不接管。
6. 安排成功时Task可以返回ERROR，表示本条任务完成安排，不是付款成功。
   Result Backend仍未启用，最终付款查询业务数据库。

ETA任务可能先被Worker取到并暂存内存，到期后执行，也可能更晚。
[Celery ETA文档](https://docs.celeryq.dev/en/stable/userguide/calling.html#eta-and-countdown)
说明其执行与内存行为，本段只使用5/10秒短延迟，不当作长期定时器。

## 隔离环境与真实故障

沿用C1宿主Python3.11.9、Django5.2.17、Celery5.6.3、Kombu5.6.2、
MySQL8.4.10、RabbitMQ4.3.6及Linux Python3.11.16非root prefork Worker。
主机测试库 test_project_one、MySQL端口3307；Broker端口5673、管理API15673，
专用vhost project_one_test、队列payment-events-test。不开启eager。

本期新增Compose测试覆盖文件，只改变payment-worker-test的启动入口。
专用launcher验证测试环境与库名、Broker/queue后，每条测试Worker连接执行
`SET SESSION innodb_lock_wait_timeout = 1`，不改GLOBAL、开发连接或主Compose。
主测试进程另开真实数据库连接，事务中SELECT Inventory FOR UPDATE并持锁。
Worker业务事务实际等待该行锁并得到MySQL1205，不用Mock替换数据库异常。
[MySQL锁等待文档](https://dev.mysql.com/doc/refman/8.4/en/innodb-parameters.html#sysvar_innodb_lock_wait_timeout)
说明SESSION范围与1205语义；应用atomic异常退出负责整个业务事务回滚。

测试入口仅观察原资格函数的as_of与返回次数，不改变其规则、返回或异常；
连接执行观察器只记整数errno。日志按每个场景开始时间筛选、观察器不重复
安装，写入证据的只有ID、次数、时间、错误号与业务终值，不含SQL或凭据。
因此可直接比较第二/三次领取资格的as_of与前一次next_retry_at，而非仅看
处理完成时间。提前开始的事务也可能很晚才完成，完成时间不够证明资格门槛。

## 实际场景结果

最后一次成功原始记录：
[phase-5c2-payment-mq-retry-data.json](phase-5c2-payment-mq-retry-data.json)。

| 场景 | 实际观察 |
|---|---|
| 一次超时后自动成功，包含早到重复 | 同一事件两条初始立即消息，第一条真实1205后ERROR/TRANSIENT/count1、下次+5秒；第二条未领取资格，观察deferred后释放锁。无需人工恢复/查单/重新付款，后台count2成功，Order PAID、Attempt SUCCEEDED、库存19/0、唯一COMMIT；ACK增量4，只有1次本场景1205 |
| 持续锁故障直至耗尽 | 真实1205共3次；count1/2的next分别+5/+10秒，count3为EXHAUSTED且无next。释放锁后再次投递仍count3，订单待付、Attempt CREATED、库存19/1，无COMMIT；ACK增量4 |
| 库存不变量需调查 | 在测试库故意将Reservation改为RELEASED而不释放库存，原规则触发payment_invariant_error。Event BLOCKED/count1，无next、无后台排程，订单待付、库存仍19/1，无COMMIT，ACK增量1；不是正常取消流程 |

持续故障场景实际第2/3次资格分别比前一次到期时间晚0.002624秒、0.001711秒；
自动成功场景第2次资格晚0.001256秒。它们只是本次受控实验观测，不能当作
生产调度延迟、吞吐、锁竞争概率或性能保证。
所有场景最终ready=0、unacknowledged=0，核对数据库事实后先停Worker再清专用
队列，Django才flush/销毁测试库。消息数量大于业务次数并不等于预算失效。

## 实验修正与检查结果

- 初次后台实验3 passed in 71.06s。审查发现完成时间不能独立证明资格门槛，
  且容器累计日志/重复安装观察器会夸大错误观测次数；随后改为本场景观察。
- 补强观察后的实验1 failed, 2 passed in 58.40s。失败来自早到重复消息在
  首次失败之后入队，可能排在已预取ETA之后，短等待内没有deferred。改为
  先入队两条立即消息再启动Worker，并等待观察到拒绝领取后释放锁。
- 真实短ETA还观察到临界早醒重新排程，因此代码增加now+1秒最小再次等待；
  数据库5/10秒门槛与总共3次不变，未改为跳过门槛或降低测试判据。
- 修正后目标规则/事务测试：49 passed, 33 subtests passed in 23.04s。
  其中新增9项Task重试规则，Mock异常与直接task.run不充当MQ证据。
- 增加临界等待保护后，真实MQ后台重试3 passed in 65.30s；这次持续故障
  场景ACK5，含一次提前唤醒后的再次排程，实际第3次资格比到期时间晚约1秒。
- C1真实基线回归第一次1 failed, 2 passed in 74.75s：三条重复任务日志均
  succeeded，但共享长存队列的累计ACK增量判据未通过。测试改为每场景先停
  Worker、删除并重建硬编码专用测试队列、等待零统计；未降低ACK或业务判据。
  修正后C1真实基线3 passed in 42.16s，未重写C1历史原始JSON。
- 最终使用每场景独立队列的C2复查：3 passed in 70.58s，原始JSON为这次三场景，
  ACK分别1/4/4，1205观测分别0/3/1，实际业务次数分别1/3/2。
- 支付/订单回归：131 passed, 6 skipped, 54 subtests passed in 109.01s。
  6个skip为普通回归未开启的C1/C2真实MQ用例，它们已在上面两个独立流程
  分别3项实际通过，不将skip当作成功或用Mock代替集成。
- compileall、pip check与合并测试Compose config --quiet已实际通过。
- scoped git diff --check通过，仅既有LF/CRLF转换提示；本期Python语法、尾空白、
  Markdown围栏/链接、三场景JSON与源码指纹检查通过，指南2处摘录与源码AST一致。

## 复现命令

从仓库根目录PowerShell执行；既有MySQL测试服务和C1镜像可用，不与其他
test_project_one测试并行。没有新依赖，不必为本期重新构建镜像。

```powershell
docker compose --profile payment-mq-test up -d --wait rabbitmq-test
.\scripts\verify_payment_mq.ps1 -Stage retry
```

默认不重写原始证据，需要记录时加-WriteEvidence，仅写C2 JSON；未传Stage
仍运行C1 baseline，不重写C1历史JSON。脚本内存读取Compose凭据与共享Mock
签名密钥，显式校验测试库/主机/queue/vhost；pytest建库前先停专Worker，
结束后再次停止并检查结果、还原调用者环境。不要自行启动测试Worker。
每场景重新创建专用测试队列，避免混用累计统计。本轮停止操作均成功；
停止命令失败会使验证失败，Docker完全不可达时的消费者清理没有验证。

## 修改与保留范围

- 修改payments/tasks.py、dispatch.py、test_event_async.py、test_event_mq.py；
  MQ fixture提取共用隔离与清理，不改变原C1三场景的业务判据。
- 新增test_event_task_retry.py、test_event_mq_retry.py、mq_retry_worker.py及
  compose.payment-mq-retry-test.yaml；scripts/verify_payment_mq.ps1支持-Stage。
- 更新Charter、CONTEXT和中英文README；新增本期学习指南、证据和JSON，
  C1指南/证据增加历史边界与C2链接，原始C1结果及摘要保留。
- 没有修改services.py中的付款规则、网关、库存算法、模型/迁移、依赖、
  主Compose、前端或Redis；前序未提交修改保留。没有开发库迁移/数据操作、
  真实支付、Sandbox、commit或push。本期测试只创建/销毁隔离测试库。

## 剩余边界与下一步

当前后台重试依赖Worker读到已提交的合格TRANSIENT记录且成功发布后续消息。
首次入箱后未发布、重试发布失败、资格领取/数据库不可用，以及持久PROCESSING
中断都没有被这一步完整修复。队列确认/持久配置仍不能证明进程故障恢复。

下一步C3提供Inbox补漏，覆盖初次及重试任务漏投并验证Broker不可用后恢复；
再单独处理PROCESSING失效资格与真实Worker中断。Outbox仍为条件性方案，
不自动引入。测试Broker保留运行，专用Worker结束后停止，不启动开发Worker。

## 验证时源码指纹

验证针对保留前序修改的工作区，基准HEAD为
`4e1a1ae211bd9bfab37c14c85aed11d3cd580eef`，未提交本段修改。
services.py、主Compose及C1原始JSON的摘要与C1验证记录一致，本段没有修改它们。

| 文件 | SHA-256 |
|---|---|
| backend/apps/payments/tasks.py | 6f885683ff3c5acd1dc7f9177980409a9ccbc4a12c287e31976588cfb6402b58 |
| backend/apps/payments/dispatch.py | d46e345449b88578f108108e441836395b90c5d34b75a4f01bf44d6cd6c8d076 |
| backend/apps/payments/tests/test_event_mq.py | 514b5d6db7117265ee141cd61193ced239572cf379f44e6bb7075832ed330cca |
| backend/apps/payments/tests/test_event_mq_retry.py | ab99cb5bbdbcc2d97bbafe0ff2b4e8403554a4e235831a67d5af2ac48287efd7 |
| backend/apps/payments/tests/mq_retry_worker.py | e6dde968d74157a0efe8d410c83dee885f533e76180e53891a8a602c47c928e1 |
| scripts/verify_payment_mq.ps1 | 2c9b2455409be026fb9b73e5553486211356931cf4a76ea88cbbcc1b2e8446c9 |
| compose.payment-mq-retry-test.yaml | 1aa6168c256f4979281f2ddbb6063e240b6faaa6246674530220d101ef9cedd8 |
| docs/verification/phase-5c2-payment-mq-retry-data.json | c7deb90bb92a489e28389b9ef17ff2c936c8115bf0c89a23881cb2c04a55dea0 |
