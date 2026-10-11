# 04 怎样安全复现：测试通过之前，先确认它会改哪一份数据

2026-10-12，源码快照`407066c61b6fed59a172320e997e66fa637fc9eb`。[专题总览](../testing-current-overview-study-guide.md) · [并发与故障验证](02-concurrency.md) · [本轮核验](../../verification/testing-evidence-knowledge-base-publication.md)。本轮只整理现有代码与历史证据，没有执行数据库测试或恢复。

**能够稳定重现支付中断、锁竞争与缓存故障，前提是实验不会影响顾客数据或另一轮实验。** 这页帮助你回答“测试怎样隔离、怎样确认实际连接、测试自身出错怎么办”，并把安全复现与业务正确性分别举证。最小可信证据是目标身份检查、受控副作用范围、失败记录和清理结果，不能仅拿一行passed作结论。

## 本专题前后变化：从配置看起来正确，到确认实际执行目标

项目已有隔离MySQL测试库、专用Redis/MQ/ES服务和显式集成开关；7B事故暴露的缺口是测试本身改变了真实连接目标。之后修正拒绝分支，搜索来源增加实际数据库检查，后续7C/7D保留串行运行、开发数据只读摘要比较和精确资源清理。

| 原有判断或做法 | 现在采用的判断或做法 | 能支持的结论 |
|---|---|---|
| `NAME`写着测试库，因此不会碰开发库 | 配置与实际`SELECT DATABASE()`共同核对 | 当前搜索操作使用正确来源；不是全项目所有连接永久安全 |
| 为测试拒绝开发库，临时修改真实连接配置 | 只替换被测模块引用的连接替身 | 拒绝分支不需要真的改变连接目标 |
| 被测代码没有商品SQL，测试就没有副作用 | 同时考虑建连、fixture、子进程与teardown | 业务调用之外也可能发生数据库操作 |
| 普通回归出现skip，当作集成已通过 | 看跳过原因，并另查独立集成记录 | 未执行与已通过能分开说明 |

当前仓库没有`conftest.py`，pytest入口配置在[pyproject.toml](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/pyproject.toml#L1)。各模块分别定义fixture与隔离检查；没有统一全局守卫或自动数据恢复功能。Fixture是“为一项测试准备环境、并在结束时清理”的代码，它本身也需要审查。

## 7B第一错误怎样连锁造成开发数据误清

历史日期为2026-10-10。下面描述的是已记录事故的因果，不是建议重现它的步骤。

1. 测试要验证搜索原型拒绝开发库，却把真实Django连接对象的`settings_dict["NAME"]`临时改成`project_one`。第一错误已经发生：用改变真实执行目标来构造拒绝输入。
2. 前一项`transaction=True`测试已经关闭连接。此时“连接对象存在”不表示“物理连接已经打开”。物理连接是实际连到MySQL的那条会话。
3. 测试进入`CaptureQueriesContext`，这个用于捕获SQL的上下文会通过`ensure_connection()`确保可以查询。由于此刻配置指向开发库，新建的物理连接实际连到了开发库。
4. 测试把`NAME`字符串恢复成测试库。恢复配置不等于重新建立连接；已经打开的MySQL会话仍在开发库。
5. 拒绝分支观察到“0条商品SQL”，但这个数字没有覆盖连接初始化和测试收尾。它不能证明整个测试生命周期没有数据操作。
6. Teardown是“测试结束后的收尾阶段”。pytest随后执行`flush`，按实际`DATABASE()`枚举表并删除数据，于是误清开发表。测试断言先通过，错误副作用却在之后发生。

因此，这次事故不能解释为“用户跑错命令”，也不能用首轮绿色结果证明隔离安全。原始通过输出仍保留，说明**测试的业务断言与测试的副作用审查并非同一个判据**。[第一错误、binlog影响与修正](../../verification/phase-7b-test-isolation-incident.md) · [首轮与修正后结果](../../verification/phase-7b-search-index-prototype.md)。

### 恢复证据怎样准确复述

公开材料列出的四表删除/恢复计数为：`django_session` 1、`auth_permission` 104、`accounts_user` 1、`django_content_type` 26。按这四项相加是**132条**。[脱敏JSON](../../verification/phase-7b-test-isolation-incident.json)报告两次隔离恢复演练、明确授权后的四表恢复，以及新连接逐字段回读一致。

旧Markdown事故记录仍有两处“139条”文字，原型阶段记录也有该说法，与公开四表计数存在不同口径。**本页采用可由公开JSON计算的132，不把139写成已核实总数。** 本轮没有进一步读取私密原材料或操作数据，没有改写历史记录；这处文字差异仍应保留为待核对项。

“恢复完成”是历史记录报告的结果，不是本轮重新查库确认。历史记录没有浏览器登录验证，自增计数也没有倒退，不能说全部数据库元数据和所有使用场景都已还原。恢复候选先在可销毁库演练，之后才经明确授权操作四张开发表；这与项目具有自动恢复能力是不同的结论。

## 核心代码一：配置与活连接必须同时满足来源门槛

搜索构建和读取复用下面的守卫。`SOURCE_DATABASE`固定为`test_project_one`；参数传入的是本次操作实际使用的连接，不是随便读取一份环境变量。

```python
def require_test_source(db_connection):
    if (
        settings.ENVIRONMENT != "test"
        or db_connection.vendor != "mysql"
        or db_connection.settings_dict["NAME"] != SOURCE_DATABASE
    ):
        raise RuntimeError("Search prototype requires isolated MySQL test_project_one.")
    if not db_connection.get_autocommit() or db_connection.in_atomic_block:
        raise RuntimeError("Do not hold a MySQL transaction while building a search index.")
    with db_connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE()")
        if cursor.fetchone()[0] != SOURCE_DATABASE:
            raise RuntimeError("Search prototype requires the actual isolated MySQL database.")
```

[固定源码：来源守卫全文](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/catalog/search_prototype.py#L46-L58)。

第一层看test环境、MySQL类型和配置库名；配置错误直接拒绝。第二层查询当前会话的实际库名，防止“配置已经恢复，但物理连接仍留在另一库”的情况。中间还要求自动提交、没有外层事务，避免握着MySQL事务等待ES构建或查询。

例如配置库名是`test_project_one`，但活连接返回`project_one`，最后的检查仍会拒绝。单靠`DJANGO_ENV=test`或`test_`前缀，都不能代替这一步。项目通用设置检查测试库名以`test_`开头，但这只是名称约束；搜索这份双检查也不能被扩大为全项目所有fixture都有同样守卫。[测试库名配置](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/config/settings.py#L203-L205)。

## 核心代码二：测试拒绝输入，替换引用，不修改真实目标

事故修正后的拒绝测试使用下面的小块：

```python
with monkeypatch.context() as patch:
    # 拒绝分支使用替身；绝不能为测试而改变真实连接的数据库目标。
    patch.setattr(search_prototype, "connection", SimpleNamespace(
        vendor="mysql", settings_dict={"NAME": "project_one"},
    ))
```

[固定源码：替换模块connection引用](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/catalog/tests/test_search_index_prototype.py#L292-L296)。

Monkeypatch是“测试期间临时替换一个引用，并在退出作用域后还原”。`SimpleNamespace`在这里仅提供守卫要读的两个属性；不是一条真的开发库连接。`search_prototype.product_documents()`读到替身后在配置门槛直接拒绝；测试模块自己捕获SQL用的真实`connection`仍指向测试库。完整测试前后还检查实际测试来源、自动提交状态，并独立验证外层事务拒绝分支。[完整测试及前后检查](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/catalog/tests/test_search_index_prototype.py#L288-L308)。

这种替身适合验证“错误配置必须在写入前拒绝”。真正的并发、锁等待、消息重投语义仍需要对应真实依赖；两类证据怎样结合见[并发与故障验证](02-concurrency.md)。

## 复现前后怎样安排，避免实验互相污染

一次安全复现按以下顺序核对。本页链接已有阶段入口，不附带可直接误触开发目标的新运行命令。

1. **先说清影响。** 确定会建/迁移/销毁哪份测试库、写哪些Key或索引、是否停Broker或kill专用Worker；涉及真实数据或部署目标的操作先确认范围。目标不清楚时先读已有验证记录，不先跑一遍试试。
2. **独占同名测试库。** `test_project_one`应由一个pytest流程串行使用。一个实验内部的受控线程并发，是它自己的测试场景；两个pytest进程同时共享该库，可能互相清理数据，不构成有效的业务并发证据。
3. **核对所有执行者的身份。** 不只检查主测试进程，也检查Worker、管理命令子进程和诊断连接。建测试库前停止旧的专用Worker，防止它在建库/清理期间继续消费。
4. **明确资源所有权。** Redis使用随机测试前缀，MQ使用专用vhost/queue，ES使用随机候选名与指定测试集群。只清理自己确认创建的资源；发生连接错误时不能扩大删除范围。
5. **保留失败与清理证据。** 即使断言失败，`finally`也应尝试停止专用Worker、恢复本进程环境与清理本轮资源。清理失败要记录，不能把主断言通过伪装成环境已安全收尾。
6. **最后核对结果。** 区分业务终值、测试库/连接是否残留，以及被保护数据是否变化。仅“开发schema仍30表”证明表数相同，不证明每行相同；7C/7D另做只读整表摘要比较，公开结果只含计数和相等结论。

| 依赖 | 项目已有的隔离机制 | 复现时特别检查什么 |
|---|---|---|
| MySQL | pytest测试库；搜索fixture检查实际库名 | 目标会话、同名库独占、线程连接关闭与teardown |
| Redis | 显式测试URL、随机Key；仅精确删除本轮Key | 不使用共享库全量清空；传了URL却连不上应失败 |
| RabbitMQ/Worker | 专用Broker/vhost/queue、Linux测试Worker；拒绝eager替代 | Worker也必须指向测试库；主机与容器地址不同；失败后停止专用消费者 |
| Elasticsearch | 指定本地测试集群、随机候选/别名、来源与完成标记 | 同一测试库生命周期；只精确清理已确认创建的候选；未登记孤儿不能假装已清理 |

Redis依据[6F隔离与收尾](../../verification/phase-6f-flash-sale-admission.md)；MQ依据[5C4复现入口与影响](../../verification/phase-5c4-payment-worker-recovery.md)；ES依据[7C来源核对与收尾](../../verification/phase-7c-search-read-api.md)、[7D同生命周期约束](../../verification/phase-7d-search-index-switch.md)。这些入口各有特定前置条件，不能抽取最后一条pytest命令后忽略前面的准备和清理。

### 为什么子进程不会自动继承pytest的安全目标

Pytest-django在测试进程内切换连接去测试库；新启动的管理命令是另一个Python进程，会重新加载环境和Django设置，并不继承父进程已修改的连接对象或`override_settings`。容器Worker也一样需要自己核对目标。

现有MQ恢复实验在创建新CLI进程前，明确检查其环境中的数据库名、host/port、test模式、异步开关、专用队列和Broker，再启动命令；专用Worker入口另查自身配置。这解释了为什么父进程显示`test_project_one`仍不足以保证后台操作安全。[恢复子进程检查](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/payments/tests/test_event_mq_recovery.py#L96-L114) · [测试Worker入口守卫](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/payments/tests/mq_retry_worker.py#L34-L45)。这些是本项目已有的局部检查，没有据此声称所有进程都拥有统一实际库校验。

## 跳过、失败和通过怎样分别解释

普通回归不总启动所有中间件。项目通过显式开关防止无意启动破坏性实验，代价是你必须阅读结果中的skip原因。

| 状态 | 本项目的具体例子 | 可以怎样解释 |
|---|---|---|
| skipped | 没有`PROJECT_ONE_TEST_REDIS_URL`，Redis fixture跳过；ES在线未设置显式URL | 这次没有执行对应集成语义，不能当作服务通过 |
| deselected | 独立ES离线流程只选择离线场景，其他测试未选中 | 不在本次运行范围，不是失败或成功 |
| failed | 已给测试Redis URL，`ping()`连接失败 | 明确配置了依赖却不可用，源码没有静默跳过 |
| passed | 对应场景真的执行并满足断言 | 只支持该环境与场景；仍需单独看清理和数据保护证据 |

MQ基础集成需要显式`PROJECT_ONE_TEST_MQ_BROKER_URL`；重试、补漏和中断还分别选择`PROJECT_ONE_TEST_MQ_STAGE`。ES在线/离线通过URL与`PROJECT_ONE_TEST_ES_OFFLINE=1`区分；秒杀负载另有显式实验开关。开关表示“允许进入这一组测试”，没有替代环境身份和实际执行结果。[Redis失败/跳过的源码边界](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/catalog/tests/test_detail_cache.py#L43-L52) · [MQ目标与eager检查](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/payments/tests/test_event_mq.py#L30-L52) · [ES启用条件](https://github.com/jasper507/E-commerce/blob/407066c61b6fed59a172320e997e66fa637fc9eb/backend/apps/catalog/tests/test_search_index_prototype.py#L23-L36)。

5C4普通回归的12项MQ跳过，有各自独立真实服务验证记录支撑；你应引用那份独立记录，而不是把本次skip改说成passed。同样，7C在线和离线是不同流程，不把服务在线时跳过的离线用例当成已经测试。[5C4结果与跳过解释](../../verification/phase-5c4-payment-worker-recovery.md) · [7C在线/离线结果](../../verification/phase-7c-search-read-api.md)。

## 如果重新做，我会先收拢目标身份检查与资源所有权

保留实际`SELECT DATABASE()`、引用替身、专用服务和逐场景清理。改进候选是抽取小型公共隔离检查，让测试fixture、管理命令实验入口和Worker都声明并检查目标，再为每轮资源记录明确所有权。价值是减少复制检查时的遗漏，尤其避免主测试进程安全、子进程却使用另一份配置。

成本是公共工具可能掩盖各模块的特殊约束：Redis随机Key、MQ队列和ES同生命周期并不完全相同，不能用一个布尔`is_test`替代它们；工具自身也需要拒绝错误目标、子进程目标不符和清理失败的验证。因此先在两个有重复规则的入口复用，看到真实减少遗漏且不削弱特殊约束后再扩大，暂不重构整套测试框架。

另一层候选是独立可销毁数据库实例与只能访问测试目标的专用账号，减少误连接的损害范围。相比仅依靠程序守卫，它增加服务启动、迁移与多进程连接管理成本；采用前需证明账号无法读写开发库、真实锁/MQ实验仍能运行，并确认资源代价值得。当前项目并没有完成这项全项目隔离改造。

本页收束到“知道每个执行者在哪里、会改什么、怎样留下与清理证据”。已有故事足够支撑该解释，不为补充知识库重跑事故、读取私密数据、修改历史恢复记录或新增故障矩阵。
