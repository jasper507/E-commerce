# 07 实验入口与面试证据

2026-10-11，当前源码快照 `29b7944860080ccddedb2c0f0c060c0a27658376`，项目总纲v1.16。[支付总览](../payment-current-overview-study-guide.md) · [本轮归档核对](../../verification/payment-redis-interview-knowledge-base-restructure.md)。这里把“源码怎么设计”和“真实观察过什么”对应起来，帮助给出可信面试回答。本轮只做文档与源码核对，没有重跑以下实验；已有资料不代表项目拥有者已独立掌握。

## 面试回答怎样从结论走到证据

“支付做了幂等、MQ和重试”没有说明系统守住什么。更具体的讲法是：189元订单付款通知可能重复或处理失败，目标是继续处理原付款、付款/订单/库存同事务提交、每件预占最多一条核销效果；我用持久Inbox、锁后状态判断、有限预算和人工恢复，并在隔离MySQL/RabbitMQ/Worker里验证指定故障。

追问“你怎么知道没重复扣库存”，就指向Order/Attempt/Event、InventoryReservation、InventoryMovement和模拟退款终值，而不是只给pytest passed数或Rabbit ACK。测试数证明执行过哪些检查，不能独立说明正确性，更不能当QPS、生产容量或个人理解的证明。

## 按问题找最小充分证据

| 能支持的结论 | 实际证据及观察 | 明确不支持什么 |
| --- | --- | --- |
| 已入箱通知不必再次接收原文才能恢复 | [5A记录](../../verification/phase-5a-payment-inbox-recovery.md)：受控入箱后中断，人工扫描原Event，原Attempt成功、库存19/0、COMMIT1 | 用BaseException控制窗口，不是实际Worker进程被杀；当时未接MQ |
| 多入口共享持久资格与预算，业务回滚不恢复次数 | [5B记录](../../verification/phase-5b-payment-event-retry.md)：真实MySQL，未知异常排除，两个扫描累计count1；故意反转两行库存锁得到1213，失败原Event恢复count2 | 部分错误场景为异常注入；不代表全部错误都是实测产生，也不保证无死锁 |
| MQ收件与付款确认分离，同事件重复消息不重复效果 | [5C1记录](../../verification/phase-5c1-payment-mq.md)及[原始JSON](../../verification/phase-5c1-payment-mq-data.json)：停Worker时202/RECEIVED；启动后PAID；同Inbox三个task ID，ACK3而count1/COMMIT1 | 单Worker顺序消费；不是三个Worker并发、杀进程或Broker重启持久化测试 |
| 暂态故障等待后再处理，耗尽与不变量故障必须停 | [5C2记录](../../verification/phase-5c2-payment-mq-retry.md)及[原始JSON](../../verification/phase-5c2-payment-mq-retry-data.json)：真实库存行锁触发1205，count1/2到期5/10秒，count3 EXHAUSTED；错误预占BLOCKED | 消息数可能更多，不能说“只投递三条”；定时误差不是精确准点或恢复SLA |
| 首次和重试漏投由人工补投原ID恢复 | [5C3记录](../../verification/phase-5c3-payment-publication-recovery.md)及[原始JSON](../../verification/phase-5c3-payment-publication-recovery-data.json)：实际停Broker，两场景分别count1/2最终成功，COMMIT1 | 新CLI人工触发，没有周期补漏；实验为隔离补投曾清测试队列，不能照做业务恢复 |
| 硬中断前后有不同恢复责任，提交后重投不重复核销/退款 | [5C4记录](../../verification/phase-5c4-payment-worker-recovery.md)及[四场景JSON](../../verification/phase-5c4-payment-worker-recovery-data.json)：真实SIGKILL，四场景通过，未提交回滚，提交后终值不变 | 默认60秒租约，实验覆盖为8秒；没有心跳/自动接管/全部崩溃窗口承诺 |
| 付款与到期竞争仍保护未确认Attempt | [5D记录](../../verification/phase-5d-payment-expiration-concurrency.md)及[六场景JSON](../../verification/phase-5d-payment-expiration-concurrency-data.json)：两种Order锁顺序×三种活跃状态，实际锁等待，终值PAID/19/0/COMMIT1 | 服务层MySQL实验，非浏览器HTTP/MQ压测或全InnoDB锁证明 |
| 秒杀资格与支付/关闭同事务，付后取消不返资格 | [6B记录](../../verification/phase-6b-flash-sale-lifecycle.md)及[原始JSON](../../verification/phase-6b-flash-sale-lifecycle-data.json)：目标11项/13子场景、资格回滚与生命周期；双连接竞争观察终值 | 该双连接场景没有采集锁等待或控制两种顺序；5D普通订单证据不可自动替代它 |

早期[MVP-4记录](../../verification/mvp-4-mock-payment.md)当前保留2026-09-24的SQLite内存烟测与当时“MySQL待验证”的文字。它说明当时的API/流程覆盖，不是当前MySQL约束和锁证据。后续5A–5D等记录逐步提供真实MySQL和MQ证据；不能篡改早期记录让每一条看起来从开始就验证完整。

## 最值得讲清的三个真实反例

**反例一：队列重投了，为什么仍卡PROCESSING。** T1已经持久保存领取，Worker随后消失，原消息再投也会因PROCESSING不能重新领取。5C4在T1提交后真实SIGKILL观察到这一状态，补持久租约后人工修复再补投，count从1到2，订单PAID、库存19/0、COMMIT1。这说明“Broker重投”与“应用可继续处理”不是同一保证。

**反例二：事件业务回滚了，为什么不应把次数回滚。** 5B把count放到独立T1，真实1213或处理故障使T2回滚，但预算已消耗；5C2持续1205到第三次停为EXHAUSTED，释放锁后再投仍count3、待付19/1、无COMMIT。设计保护了资源与可调查性，剩余代价是问题恢复后也不能无条件继续，需人工调查而非重置预算。

**反例三：提交后ACK前中断，不应该再扣一次。** 5C4记录PAID与Event终态已提交后杀Worker，原消息和主动重复任务都不再增加count或COMMIT；迟到成功退款对应退款1、RELEASE1、COMMIT0。它支持这些配置和窗口下的业务幂等，不支持真实资金已退回或所有消费系统天然exactly-once。

这三个例子分别回答恢复资格、事务边界、重复效果，足够构成一条深入支付故事。没有新问题或解释缺口时，先整理理解，不为堆实验数量扩大故障矩阵。

## 5A和5B到底差在哪里

5A的核心是“给已经存下来的事件一个人工恢复入口”。它能固定批次、逐条独立处理、失败留ERROR、继续下一条，但当时ERROR混合暂态、未知异常和不变量错误，没有累计预算/到期门槛。反复扫描可能反复尝试不该自动恢复的事件。

5B的核心是“谁有资格再处理，最多几次，什么时候必须停”。它增加持久count、failure_kind、next_retry_at与PROCESSING领取，用T1/T2/T3拆分保证回滚不退预算，并使所有入口锁内复查。区别不只是多几个状态显示，而是恢复行为从宽泛重跑变成受业务规则限制。5C再把符合资格的处理交给真实MQ/Worker，并覆盖发布与进程中断。

因此阅读历史5A中的“ERROR重跑”要同时看它页首的历史提示；当前策略是5B及之后版本，不能混合两个时期的规则回答。

## 如果要复现，先选对应环境与一个场景

当前源码可以通过[5C2实验脚本](../../verification/phase-5c2-payment-mq-retry.md)、[5C3补投实验](../../verification/phase-5c3-payment-publication-recovery.md)、[5C4硬中断实验](../../verification/phase-5c4-payment-worker-recovery.md)、[5D锁观察](../../verification/phase-5d-payment-expiration-concurrency.md)里的命令入口复现。这些命令会创建/销毁测试库、启动/停止专用Broker/Worker或故意杀专用进程，必须先检查专用资源、权限和并发占用，不能直接在普通开发或业务环境复制执行。

pytest负责切换它自己的测试连接；单独manage.py子进程不自动继承这个临时数据库语义。`DJANGO_ENV=test`只是环境标识，不能证明SQL实际写`test_project_one`。历史C3为补投子进程单独核验实际库、端口、队列等，正是避免“名字叫测试却写错库”。不同实验共享测试资源时应顺序运行，不并行建库/flush。

本轮无需重现：源码一致性与历史记录足以支持文档重构；如果以后要回答新增性能/自动恢复结论，再提出最小新实验及停止条件。

## 当前部署、当前源码与历史实验不能互相冒充

Charter v1.16的新独立部署已完成基础构建/迁移/静态资源/HTTP，以及[9C Session/CSRF十五项真实HTTP检查](../../verification/phase-9c-session-csrf-operations.md)，未在该部署验收完整付款交易，缓存/Lua/MQ/ES四开关关闭。历史本地MQ集成成功不能表述为“已上线启用MQ”。

当前源码存在支付所有权、签名/金额检查、终态重放及恢复实现；本轮静态读源码能确认代码怎么写，不能单独证明服务可连接或新回归通过。测试源码位置可以辅助解释，但必须指向实际执行记录才能说“跑过”。外部真实支付、真实退款、自动对账、生产容量与所有故障下无死锁均无当前证据。

## 如果重做，怎样让证据更容易维护

我会从第一片就给重要规则留一份“触发—预期终值—实际环境—剩余限制”的简短证据索引，更新当前入口而保留原始日期和失败修正记录。现有记录已经很丰富，问题主要是横跨多切片，读者容易把SQLite、静态核对、真实MySQL/MQ和部署验收混成一个“全部通过”。

重做优先改善这种关联与来源表达，再在新代码改变关键语义时选择相关回归。所有截图、日志和通过数都不能代替项目拥有者独立解释：设计为什么能守住189元付款和一件库存，什么时候不能继续，以及为了这个保证付出了什么成本。
