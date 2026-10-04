# Phase 5A：支付 Inbox 人工恢复基线

此处保留 5A 当时源码和实际证据。当前行为已由
[Phase 5B](phase-5b-payment-event-retry.md)收紧：分类、到期门槛与持久预算
决定恢复资格；5A 的未分类 ERROR 重跑和并发返回方式不再代表当前版本。

日期：2026-10-01。Track C / Depth，按项目拥有者要求先交付最小实现、
操作指南与核心代码，再学习。MQ 仍是支付主线必做，尚未接入。

## 问题、基线和方案

原流程在短事务中保存 PaymentEvent，然后同步处理；异常后保存 ERROR。
已有测试能通过重新接收同一通知恢复，但没有独立入口处理已入箱的遗漏事件。
如果处理前中断且没有重投或查单，事件会留在 RECEIVED，订单仍待付款。

本轮先在修改业务入口前运行两个隔离 MySQL 基线测试：新增的入箱后中断
与已有的异常回滚/通知重投。实际结果 `2 passed in 8.74s`。
中断使用测试专用 BaseException，在入箱提交后、业务处理前注入并捕获；
这是受控窗口模拟，不是实际杀进程或 Worker 故障验证。

选择人工一次性扫描：固定选出至多 limit 个 RECEIVED/ERROR 的内部 ID，
逐条复用原处理器，每条本轮最多调用一次。与依赖用户查单/外部重投相比，
它不需要再次接收 payload，但需要人工触发及调查失败。后续 RabbitMQ/Celery
提供及时后台执行，仍需补漏和真实 Broker/Worker 故障证据。

## 实现与事务边界

- `services.py` 提取原有异常留存为 `process_stored_payment_event`；同步接收
  与恢复共用，原验签、金额、状态机和库存算法未改。
- `recovery.py` 在事务外取得固定 ID 批次；拒绝在外层 atomic 中调用。
  单条业务事务仍锁 Event → Order → Attempt → Reservation → Inventory，
  原子提交支付、订单、库存流水与事件结果。
- 普通异常退出业务事务后，另开事务锁内重读事件，非终态才记 ERROR。
  若别的处理者已提交 PROCESSED/REJECTED，不覆盖它的状态、原因和时间。
  如果连错误留存都失败，扫描报告本次异常 ID，不能保证该事件已变为 ERROR。
- `recover_payment_events` 管理命令支持 dry-run、limit 与内部 event-id。
  部分处理异常时先完成本批其他事件，再报告 ID 并以 CommandError 非零退出。
  拒绝事件有独立 rejected 计数；日志不输出 payload、签名或异常原文。

这是人工恢复工具，不是自动重试策略：ERROR 仍混合临时故障、代码异常和
库存不变量破坏，需先调查；没有累计重试计数、退避、耗尽状态或调度器。
limit 是批量限制。旧 ERROR 可挤占前面的候选，人工可用 event-id 定位。

## 本轮实际验证

环境：Windows、Python 3.11.9、Django 5.2.17、MySQL 8.4 容器
（镜像 mysql:8.4.10，127.0.0.1:3307）。pytest 创建/销毁专用
`test_project_one`；本轮不启用目录 Redis 缓存，不运行开发库恢复命令。

| 场景 | 实际观察 |
|---|---|
| 入箱后中断基线 | 原通知和签名持久保留，Event RECEIVED，Attempt CREATED，Order待付；available/reserved=19/1，无 COMMIT |
| 无重投恢复 | 原 Event 变 PROCESSED，原 Attempt 成功、Order PAID；库存19/0，仅1条COMMIT，无新Attempt/Event，未再次调用付款 |
| ERROR 原文恢复 | 注入库存流水写异常，业务回滚；移除故障后直接扫描原事件成功，不重新接收通知 |
| 单条失败隔离 | 第一条持续异常仅本轮尝试1次，第二条独立提交；失败订单待付、成功订单PAID，库存18/1，仅1条COMMIT |
| 无效通知 | RECEIVED 的伪造签名转 REJECTED，订单/库存不变，重复扫描不再选中 |
| 不变量破坏 | 测试中构造错误预占状态，核销被拒绝并保留ERROR，订单未支付、无COMMIT；没有自动修复或自动循环 |
| 错误留存交错 | 受控地在留存ERROR前提交PROCESSED或REJECTED，原终态、原因、时间不被覆盖；非真实线程并发证据 |
| 两个并发扫描 | 真实MySQL、两个线程同选一个Event；两者返回PROCESSED，最终只有1条COMMIT、库存19/0 |
| 预览与批量 | dry-run不调用处理入口、不修改状态；limit与内部ID过滤正确，指定终态也不会重新选中 |
| 命令输出与限制 | 部分异常CommandError非零，输出批次统计及内部失败ID；正整数、事务外、development/test限制生效 |

两个扫描各返回 processed=1，不能相加当作两次付款或两次核销。计数只说明
选中事件处理入口返回的状态；本次出现异常的 failed_ids 也不保证最终仍为 ERROR。
业务证据看原订单、支付、预占、库存和唯一流水。

实际检查结果：

- 新恢复目标：`12 passed, 5 subtests passed in 10.75s`。
- 支付与订单相关回归：`94 passed, 26 subtests passed in 76.21s`。
- 新 Python 文件 compileall 通过。
- 命令 `--help` 通过。首次默认 Windows 编码无法输出中文，改用 Python
  `-X utf8` 后成功；没有修改项目编码配置。
- scoped `git diff --check` 通过；新增文件尾部空白、Markdown 代码围栏和本地
  链接检查通过。仅有现有 LF/CRLF 提示；未进行浏览器、真实支付或 MQ 验证。

## 怎样复现

仓库根目录 PowerShell，已有本地 MySQL 测试服务可用且具有测试库权限时：

```powershell
$env:DJANGO_ENV = 'test'
$env:CATALOG_CACHE_ENABLED = 'false'
$env:MYSQL_TEST_DATABASE = 'test_project_one'
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/apps/payments/tests/test_event_recovery.py -q --tb=short
.\.venv\Scripts\python.exe -X utf8 -m pytest backend/apps/payments/tests backend/apps/ordering/tests -q --tb=short
```

这是两个顺序执行的测试命令，不要并行运行共享测试库的 pytest。
pytest 会改动并清理专用测试库；连接失败或跳过不能算验证通过。
管理命令操作和核心代码见[学习与操作指南](../learning/phase-5a-payment-inbox-recovery-study-guide.md)。
`DJANGO_ENV=test` 不会把独立管理命令自动切到 pytest 测试数据库。

## 文件与证据边界

Git 基点 `4e1a1ae211bd9bfab37c14c85aed11d3cd580eef`，含既有未提交 Redis
和范围文档修改；本切片新增文件未提交，不能仅凭基点宣称已复现本轮实现。
本次运行时源码 SHA256：

| 文件 | SHA256 |
|---|---|
| payments/services.py | `830E4A9AC4762D524D39D28DC022860E0977AAE54F4F350D5A5DF57D98DAF6D0` |
| payments/recovery.py | `BC95CF655CA45C54CD05C4906BB6B3E90F76C96E95FCFB2ED61496461EED64FD` |
| payments/management/commands/recover_payment_events.py | `0F02A14C171661B0ED0781AB03BFA53B3D0D1AB9ABAE2BB629E0FCF00EDD0014` |
| payments/tests/test_event_recovery.py | `63552608935182DC69F52AD5A33F84983A322D09FE3BBC2D7C692CC68C813919` |

只修改支付服务中的共用异常入口，新增恢复服务、管理命令、目标测试及文档。
没有修改模型、迁移、配置、依赖、HTTP接口契约、网关、库存算法或前端；
既有 Redis 修改保留，没有恢复开发库数据，没有 commit/push。

下一切片再明确可重试故障、持久重试预算及异步受理边界，接入必做
RabbitMQ + Celery，并验证真实 Broker/Worker、发布遗漏与中断恢复。
