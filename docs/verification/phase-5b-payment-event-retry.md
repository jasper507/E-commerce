# Phase 5B：支付事件失败分类与持久有界重试

日期：2026-10-01。Track C / Depth，延续
[Phase 5A 人工恢复基线](phase-5a-payment-inbox-recovery.md)。项目拥有者授权
先完成最小实现、操作指南和核心代码，再学习；本轮没有接入 MQ。

## 业务问题、对照与选择

189 元订单的成功通知已入箱，核销可能因暂时锁竞争失败，也可能因代码或
库存事实错误失败。5A 以 RECEIVED/ERROR 为候选，没有持久累计预算或到期
门槛；反复触发会再次选中未知异常，不能直接拿它当自动恢复策略。

目标测试在真实 MySQL 中注入未知程序异常，再执行 5A 的历史候选查询：
该 ERROR 确实被选中；当前候选查询排除它。这是查询规则的受控对照，
没有重新运行整套历史版本，也没有生产负载、吞吐或故障率结论。

选择在 MySQL PaymentEvent 中保存处理次数、失败分类和下次可处理时间。
每事件总共最多 3 次，含首次；暂态失败之后分别等待 5 秒、10 秒。
替代方案是只依赖调用方或未来 Celery 任务的重试次数，代码更少，但新命令、
重新发布的任务与同步重投可能有不同预算，无法统一全部入口。
本段数据库预算统一资格；它不是自动调度，也不是消息投递次数或付款次数。

## 当前实现与事务边界

1. Inbox 原文先独立提交；同步接收与恢复扫描共用 process_stored_payment_event。
2. 资格短事务锁 Event，复查状态、到期时间及累计预算；次数加一并提交
   PROCESSING。包装入口拒绝外层事务，防止这次提交被外层一起回滚。
3. 业务事务锁 Event 并核对取得资格时的次数版本，然后复用原业务处理器。
   验签、金额与币种、支付/订单状态、库存核销与事件终态仍原子提交；
   本段没有修改原支付规则、网关或库存算法。原处理器是内部业务原语，
   生产接收与扫描均经过包装资格入口。
4. 业务异常退出后另事务留存 ERROR；只有状态仍 PROCESSING 且版本相符时
   才能写错误，不能覆盖已提交终态或新处理者。次数不会随业务回滚消失。
5. MySQL OperationalError 仅 1205/1213 进入 TRANSIENT；库存不变量、未知
   程序异常、其他数据库错误进入 BLOCKED。第 3 次暂态失败 EXHAUSTED，
   仍保留最后稳定 error_code；原无效签名、金额等事件为 REJECTED。
6. 扫描固定选出最多 limit 条首次 RECEIVED 或到期 TRANSIENT ERROR，
   且累计次数小于 3；--event-id 不能绕过策略，dry-run 不领取或写数据。
   选中后状态可能变化，实际处理再锁内复查；skipped 统计未得到可计数终态
   的返回。单条异常继续其他条，命令部分异常非零退出，不是整批回滚。

计数必须在业务事务外提交：InnoDB 死锁会回滚整个事务；锁等待超时默认
回滚当前语句，应用仍应处理完整业务事务的回滚边界。官方来源：
[MySQL 8.4 InnoDB 错误处理](https://dev.mysql.com/doc/refman/8.4/en/innodb-error-handling.html)、
[1205/1213 错误定义](https://dev.mysql.com/doc/mysql-errors/8.4/en/server-error-reference.html)。

迁移 0003 新增 PROCESSING 选择、processing_attempt_count、failure_kind 和
next_retry_at；旧未分类 ERROR 一律 BLOCKED、次数为 0，不猜旧异常或伪造
旧次数。原始通知与旧 error_code、其他事件终态保留。迁移只在测试库运行。

## 实际验证与证据边界

环境：Windows、Python 3.11.9、Django 5.2.17、既有 MySQL 8.4 容器
（mysql:8.4.10，127.0.0.1:3307）。本轮开始 Docker 未运行且启动报错，
项目拥有者打开后观察到容器 healthy；未修改 Docker 文件或服务配置。
pytest 创建/销毁专用 test_project_one，目录缓存关闭，没有操作开发库。

| 场景 | 实际观察 |
|---|---|
| 历史候选查询对照 | 未知 RuntimeError 留 ERROR；5A 查询选中，当前扫描排除且 BLOCKED/count=1 |
| 核销中途受控异常 | 写库存 COMMIT 流水时注入 1205；之前库存/预占修改回滚，订单待付、Attempt CREATED、reserved=1，无 COMMIT；count=1、TRANSIENT、下次时间+5秒 |
| 到期门槛 | 到期前1微秒，扫描、指定ID、共用入口和重复接收均不执行；次数仍1，到期时恢复为count=2、PROCESSED、PAID、仅1条COMMIT |
| 三次耗尽 | 三个独立调用从数据库读预算；失败间隔为5/10秒，第3次EXHAUSTED，无next_retry_at；以后扫描、重投和共用入口均不再执行业务，无新Attempt/Event |
| 严格分类 | 受控1205/1213可重试；2006断连接、IntegrityError、其他DatabaseError、库存不变量、普通TimeoutError/RuntimeError不能据此自动重试 |
| 库存不变量 | 构造错误预占状态被BLOCKED；即使测试修复状态，入口仍不会擅自重开资格；没有提供reset/force命令 |
| 无效事件 | 伪造签名终态REJECTED、count=1、failure_kind=NONE，重复运行不增加次数，保留拒绝error_code |
| 旧版本保护 | 受控先失败、再取得新版本；旧版本不能执行业务或改写新PROCESSING，新版本成功仅1条COMMIT |
| 终态留存保护 | 受控在失败留存前提交PROCESSED或REJECTED，状态/原因/时间不被旧错误覆盖；不是Worker并发证据 |
| 两线程重复扫描 | 真实MySQL，两扫描同选事件；第二个可见终态或skipped，累计count=1、库存19/0、唯一COMMIT |
| 真实MySQL死锁 | 两独立连接在业务事务里故意反转两行Inventory锁；恰好1个1213、1个PROCESSED。失败事件count=1/TRANSIENT，之后原文恢复count=2；两订单共2条COMMIT、库存18/0 |
| 处理中断 | BaseException受控中断留下PROCESSING/count=1；一天后扫描与重投仍不接管。不是实际杀进程或Worker实验 |
| 错误留存失败 | 受控让错误留存也不可用，已提交PROCESSING/count=1仍保留；不能宣称ERROR必然写成功 |
| 实际数据迁移 | 隔离库执行0002→0003，旧ERROR为BLOCKED/count=0，RECEIVED/PROCESSED/REJECTED与原文保留；finally恢复最新测试结构 |
| 预览/批量/命令 | dry-run无写入；部分异常不阻止其他条提交，命令非零；外层事务、正整数与本地环境限制仍有效 |

真实死锁来自测试故意构造的反向行锁，发生在原业务处理器 SQL 前，不是
生产锁顺序自然产生的死锁，也不是核销中途写入死锁。核销中途回滚由单独
的受控异常测试证明。5/10 秒用可控 as_of 时钟验证数据库时间与到期边界，
没有实际等待调度器或测试 Celery countdown。断连接分类也是规则注入，
没有停止数据库或证明数据库故障恢复。处理次数不代表顾客扣款次数。

实际结果：

- 事件重试与恢复目标：26 passed, 12 subtests passed in 18.08s。
- 支付与订单回归：108 passed, 33 subtests passed in 112.36s。
- compileall 与管理命令 --help 通过（Python -X utf8）。
- scoped git diff --check 通过，仅现有 LF/CRLF 提示；新增/相关文件的 Python
  语法、尾部空白、Markdown 围栏与本地链接检查通过。指南 Python 摘录按
  AST 与真实 services.py 比较一致（节选省略函数内层缩进，不改变逻辑）。
- 离线迁移/model 一致性通过：MigrationLoader(None)，English metadata 下
  MigrationAutodetector 无待生成变更，无数据库连接。首次以中文 metadata
  比较出现 Django 内置模型文字差异，英文上下文复查无差异；未生成其他迁移。

静态检查只证明源码和文档一致性；以上 MySQL 测试证明限定事务与规则行为。
没有运行 RabbitMQ、Celery Worker、生产支付、Sandbox、浏览器或性能实验。

## 复现命令

仓库根目录 PowerShell，既有隔离 MySQL 测试服务与 test_project_one 权限可用：

```powershell
$env:DJANGO_ENV = 'test'
$env:CATALOG_CACHE_ENABLED = 'false'
$env:MYSQL_TEST_DATABASE = 'test_project_one'
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/apps/payments/tests/test_event_retry.py backend/apps/payments/tests/test_event_recovery.py -q --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/apps/payments/tests backend/apps/ordering/tests -q --tb=short
```

两个 pytest 顺序运行，不能并行复用同一测试库。pytest 会创建、迁移、改动
并清理测试库；独立 manage.py 命令不会因 DJANGO_ENV=test 自动切到此库。
开发库尚未应用新迁移，直接运行事件处理会缺字段；开发库迁移或恢复操作
须另行确认。[操作指南](../learning/phase-5b-payment-event-retry-study-guide.md)
有命令含义、核心代码与面试问答。

## 源码快照与剩余限制

Git 基点 4e1a1ae211bd9bfab37c14c85aed11d3cd580eef，当前改动未提交且包含
前序 Redis/支付/文档修改，单靠该基点不能复现本轮实现。运行时 SHA256：

| backend/apps/payments 下的文件 | SHA256 |
|---|---|
| models.py | `18209907AB7D8D465823387ACB691E02003F9C5191EB2555451BD2A8834B4AEA` |
| services.py | `021781B9713B5132B596B1D2E8685B7AF3D87D81592B756081DE1DB2F4640729` |
| recovery.py | `CB047E4D7111EFCA216F26684E6CE5EDFC79EDE9A09B373ADE2DB6A131E45982` |
| management/commands/recover_payment_events.py | `380FD2BCFFC46A705AE657BADD51B1BF047E62AF0CBACFC3C94B8F07DCC86765` |
| migrations/0003_paymentevent_retry_policy.py | `DD21D1BF8439B985146F9222E70B889D78DFE088A8C7AE714E5C2D7DB84B83F1` |
| tests/test_event_retry.py | `B3ABC84DE4A73ECCF6463D5BEC8B049E2C513C71F80B7A5F167EEB63420E2E78` |
| tests/test_event_recovery.py | `71BD9A16483D1F161C0551A5971F1BB22E458D9CDAB1F33CED4A1920FAF49240` |
| tests/test_payment_api.py | `C447F2B20D9771001A34BBEAC1747B0602C542067A9E2A80D40308BAECEF0374` |

新增持久资格有一个明确窗口：取得资格后硬中断或无法记录失败，事件会留
PROCESSING，当前自动恢复不会接管。BLOCKED/EXHAUSTED 也没有开放盲目
重置入口，需要调查；本段不解决 UNKNOWN 原 Attempt 查单或重新付款规则。

下一段 Phase 5C 接入必做 RabbitMQ + Celery，复用数据库预算与业务事务，
明确 HTTP 受理/已支付、发布遗漏、PROCESSING 安全接管和真实 Worker/Broker
故障恢复证据。新依赖与配置留到该切片，没有 Outbox 或通用任务平台扩张。
本轮未改配置、依赖、HTTP接口定义、网关、库存算法、前端或 Redis，
未执行开发库迁移/恢复，既有改动保留，没有 commit/push。
