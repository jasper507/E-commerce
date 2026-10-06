# Phase 5D：支付成功与订单到期关闭的真实 MySQL 并发证据

- 日期：2026-10-05（Asia/Shanghai）。Track C / Depth，支付收尾的窄验证切片。
- 目标：证明活跃付款尝试下，成功通知与到期关闭竞争不能错误释放或重复核销。
- Git基点：main / `2dacc9a84c912c4d3be0c4f2efc2cba6a3b116e2`；保留前序C4本地改动。
- [学习/操作/面试指南](../learning/phase-5d-payment-expiration-concurrency-study-guide.md)
  与[六场景原始JSON](phase-5d-payment-expiration-concurrency-data.json)配套阅读。

## 基线、正确行为和最小方案

已有支付/关闭服务锁共同Order行，锁内检查状态，订单/支付/库存/事件在业务
事务中一致提交。旧测试已有UNKNOWN阻止关闭、付款与取消竞争，以及过期后
迟到成功退款，但不能把这些等同于“支付与到期两个顺序的真实锁等待已验证”。

本期只补测试，不预设故障、不重写状态机。沿用已确认规则：原Attempt为
CREATED/PENDING/UNKNOWN时不能因预占到期强行关闭；可信付款成功最终PAID，
库存只能核销一次。同一事件重放不能新建Attempt、重复核销或增加事件处理次数。

只用Barrier同时起跑的替代方案更短，但可能在数据库中完全没有竞争，不能确定
两种获锁顺序都被执行。本期使用线程自己的SQL执行包装器，在原锁SQL取得锁
后暂缓holder；竞争者继续执行原SQL，直接观察数据库报告的等待关系。
不额外包住原支付入口的事务，不替换原处理器、SQL返回值或业务判断。

## 真实服务与隔离条件

- Windows PowerShell，Python3.11.9，Django5.2.17，MySQL8.4.10。
- 宿主127.0.0.1:3307，仅`test_project_one`；MySQL已开启performance_schema。
- 通知为后端Mock合同的合成签名数据；不处理真钱，不接Sandbox。
- 支付服务同步处理原成功Inbox，PAYMENT_EVENTS_ASYNC=false；没有启动MQ Worker。
- 合成189.00元鼠标、初始库存20、买1件；每例新建隔离订单，预占明确设为已到期。
- CREATED来自原创建服务，PENDING由原签名事件入口推进，UNKNOWN由原状态服务推进。
  合法成功Inbox fixture已经提交，之后复用原process_stored_payment_event处理。

应用账号实际读取诊断表被拒绝（1142）；最初连接指向尚不存在的测试库也出现
1049。改用本机现有Docker db管理连接只读观察，不创建额外账号、不GRANT。
通过SQL核对观察器和两个业务连接的server_uuid一致，不输出server_uuid或凭据。
只过滤本轮两连接、测试库、ordering_order主键记录；不读取其他业务记录。

`connection.execute_wrapper`仅控制本线程的原SQL执行窗口。holder必须在
真实SELECT FOR UPDATE返回之后、原事务仍在时发出已获锁信号。contender
原SQL发起后，观察器关联data_lock_waits/threads/data_locks，必须看到：

- 不同的requesting/blocking连接ID；
- `test_project_one.ordering_order` / PRIMARY / RECORD；
- 请求状态WAITING，阻挡状态GRANTED，两个LOCK_DATA都是本场景订单主键。

此时独立连接仍见订单待支付、库存19/1。随后finally释放holder，让两条
业务流程结束，关闭各自连接；线程或观察超时都失败，不能改成基于sleep的推断。

[MySQL锁等待表说明](https://dev.mysql.com/doc/refman/8.4/en/performance-schema-data-lock-waits-table.html)
支持诊断方式；[Django数据库执行包装器](https://docs.djangoproject.com/en/5.2/topics/db/instrumentation/)
说明线程连接的观察接口。下面通过数来自实际运行，不由官方文档推断。

## 六个场景

| 先获Order锁的流程 | 原Attempt | 到期返回 | 最终业务事实 |
|---|---|---|---|
| payment | CREATED | changed=False / 无错误 | PAID、SUCCEEDED、PROCESSED/count1；库存19/0；COMMIT1 |
| payment | PENDING | 同上 | 同上 |
| payment | UNKNOWN | 同上 | 同上 |
| expiration | CREATED | changed=False / payment_unresolved | 同上 |
| expiration | PENDING | 同上 | 同上 |
| expiration | UNKNOWN | 同上 | 同上 |

六例都实际捕获`X,REC_NOT_GAP`记录锁等待，Reservation最终COMMITTED；
RESERVE1、COMMIT1、RELEASE0、RESTORE0、Refund0，原Attempt仍只有1条。
成功事件failure_kind=NONE、租约截止清空；PENDING准备通知与成功通知是
不同Inbox事件，不代表第二次付款或第二条付款尝试。

随后重放原成功事件和到期操作，均保持同样终值、原成功事件处理次数1。
原消息唯一事件记录仍为1，金额189.00与CNY不变。

## 实际运行结果

| 检查 | 实际结果 |
|---|---|
| 原UNKNOWN阻止关闭、过期后迟到成功退款两项基线 | 2 passed，9.00秒 |
| 新六场景目标实验 | **6 passed，11.47秒**；生成六场景JSON |
| 支付/订单相关回归（六例显式开启） | **157 passed、12 skipped、83 subtests，100.14秒** |

12项skip为普通回归未提供各自RabbitMQ/Worker实验配置的C1–C4组；已有前期
独立真实证据，本轮没有重跑它们。后续回归未开启证据写入，目标六场景JSON未覆盖。

目标实验没有失败；不虚构“移除锁前失败”的历史对照。故障风险通过原设计、
实际两顺序锁等待及数据库终值支持，不能声称本期测过一个删锁实现。
原C4事务/领取/租约代码不变，本期没有再运行真实RabbitMQ故障实验。

## 复现与CI边界

安全配置与完整命令见学习指南。入口：

```powershell
$env:PROJECT_ONE_TEST_EXPIRATION_CONCURRENCY = 'true'
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/apps/ordering/tests/test_payment_expiration_concurrency.py -q -s --tb=short
```

同时必须指定DJANGO_ENV=test、MYSQL_DATABASE/MYSQL_TEST_DATABASE=test_project_one、
MYSQL_HOST=127.0.0.1、MYSQL_PORT=3307。实验开关默认关闭，普通CI没有本机
Compose db，会明确skip六项；显式开启后Docker不可达、观察失败或目标不一致
均硬失败。没有修改CI规则或应用账号权限以掩盖环境差异。

可选PROJECT_ONE_WRITE_EXPIRATION_EVIDENCE=true仅在同一运行六场景均完成后
写JSON；本期后续回归不开证据写入，保留目标实验记录。测试可能创建/迁移/
销毁测试库；开发库、真实数据、Redis、RabbitMQ数据与外部账号不在操作范围。

## 限制、决定与下一步

- 本期人工修复/补投边界由项目拥有者确认；没有新增定时恢复或自动关单。
- UNKNOWN保护可能使库存超过15分钟预占，必须查清原Attempt，不据超时重付。
- 六例都是活跃Attempt竞争，不等同于FAILED后矛盾成功，或已EXPIRED后迟到付款。
  这些分支保留原状态机与[2B退款记录](phase-2b-refund-simulation.md)。
- 这是真实服务层MySQL交错，不是HTTP负载、真实MQ新证据、真实资金或生产容量测试。
- 不保证所有多订单死锁排列；现有暂态失败预算与恢复路径继续保留。
- 未来项目资料归档到有层级的飞书Wiki，复用已完成Redis总结，支付/MQ与秒杀分模块。
  本轮未访问外部账号、创建Wiki、移动普通云文档或发布内容。
- 下一步整理支付证据包，核对范围与学习材料，再按路线图进入商品秒杀。

## 本期文件与源码指纹

本期新增测试、指南、验证记录、原始JSON，更新Charter与CONTEXT进度；
共六个仓库文件。前序C4修改原样保留，没有提交、推送或执行开发库迁移。

原始JSON记录生成时三个SHA256：

```text
backend/apps/ordering/tests/test_payment_expiration_concurrency.py
bc96335089005ae30966a75dfe190f4d64a12d844b060fb94260fdc9f54b4a48
backend/apps/ordering/lifecycle_services.py
6dcc93d905278bee2196eb6c562a935f1544b3682866bcaa8f7683c3dacac3b5
backend/apps/payments/services.py
1a06542d957fc4114f604419542004279ebd18bf72f502b6a09b91a59879939a
```

收尾核对实际完成：测试 Python 语法正确，两段指南核心代码与业务源码 AST 一致，
两份新 Markdown 的本地链接、代码围栏与冲突/私钥标记检查通过；六场景 JSON
及其三个源码指纹一致。工作区没有 `.env`，没有读取或写入该文件。

本期开始前记录的167个保护文件指纹均未改变，包含既有后端代码、前端、依赖、
基础 Compose 和 MQ 验证脚本；前序 C1—C4 四份原始实验 JSON 指纹也未改变。
`git diff --check`通过。收尾时本机 MySQL 健康，专用测试 Worker 已停止。

本期原始 JSON 的 SHA256：

```text
31b757c8cd4f10d073b15676aab5a15bbe65601b64f017e06091c9a89db78885
```
