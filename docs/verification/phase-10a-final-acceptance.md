# Phase 10A：最终版本验收与范围统一

日期：2026-10-12（Asia/Shanghai）。Supporting / Delivery。[学习指南](../learning/phase-10a-final-acceptance-study-guide.md)。

验收源码基线：`0a94676f9f74b314fb5ea2fb1c93b982893cebcc`，开始时本地 `main` 与远端相同。backend/frontend最近变动为 `6a159ee`，部署候选配置为 `2722c98`，9C验收脚本为 `29b7944`；这些内容都已包含于基线。本片仅补证据与当前范围文档，未修改应用、测试、依赖、工作流、部署配置或真实环境文件。原 `phase-6e-flash-sale-browser-study-guide.md` 用户修改保留，不进入本片提交。

目标：将工程实测、环境启用和个人掌握分开，证明最终代码组合仍通过必要回归，并以实际 CI 结果校准本期收尾范围。最小充分证据是一次完整默认 MySQL 回归、前端构建、对应 SHA 的 CI、历史关键实验与当前代码的来源核对。达到后停止，不追加中间件部署或故障/性能矩阵。

## 本轮实际执行

| 检查 | 实际结果 | 原始/结构化材料 |
| --- | --- | --- |
| 基线 GitHub Actions，push，attempt 1 | `Backend CI` 成功；job `test` 全步骤成功；437 collected，313 passed / 124 skipped，162.93秒 | [run 38165837589](https://github.com/jasper507/E-commerce/actions/runs/38165837589)、[查询摘要](phase-10a-final-acceptance/ci-baseline.json)、[原日志摘录](phase-10a-final-acceptance/ci-baseline-log-excerpt.txt) |
| 本机完整默认 pytest，真实 MySQL | **313 passed / 124 skipped / 685 subtests passed，208.66秒**；退出0 | [raw](phase-10a-final-acceptance/backend-default.txt)、[JUnit](phase-10a-final-acceptance/backend-default-junit.xml)、[结果及跳过分类](phase-10a-final-acceptance/backend-result.json) |
| 前端 `npm run build` | Vue/TypeScript 检查及 Vite8.3.0构建通过，56模块 | [raw](phase-10a-final-acceptance/frontend-build.txt) |
| 本机 `manage.py check` / `pip check` | 0系统问题 / No broken requirements | [结果](phase-10a-final-acceptance/static-runtime.json) |
| CI 迁移检查 | `makemigrations --check --dry-run` 无漂移、迁移通过，包括 ordering.0005 | 上述 CI 原日志摘录；本机没有执行开发/部署库 migrate |
| 历史记录与 Git 来源 | 9组记录的首次提交均为基线祖先，保存各来源及之后的相关文件变化 | [证据清单](phase-10a-final-acceptance/evidence-manifest.json)、[验收源码 Git blob 清单](phase-10a-final-acceptance/source-tree.txt) |

CI 的 UTC 时间为2026-10-11 18:55–18:59，即北京时间2026-10-12 02:55–02:59。使用有权限的 GitHub `actions/runs?head_sha=...` 查询，核对 push 事件、完整 SHA、job步骤与日志；没有把空 legacy commit status 或仅 PR runs 的查询当作成功证据。匿名 REST 初查404属于私有仓库访问限制，随后连接器读取成功；未泄露凭据。

CI 不运行前端构建或真实 Redis/MQ/ES 服务。313 项和685子场景是两种统计口径，不相加当成998条独立测试；本机子场景数字不复制到 CI 结论。

## 测试隔离与实际清理

MySQL8.4.10，现有回环3307服务；Python3.11本机 `.venv`。运行前核对 `test_project_one` 不存在、活动测试连接0，默认占位库 `project_one_validation_parent` 也不存在。子进程明确指定 `DJANGO_ENV=test`、MySQL回环地址、`TEST.NAME=test_project_one`、四业务开关false；清空继承的实验变量，本轮再核对根 `.env` 没有任何 `PROJECT_ONE_` 项。没有使用 `--reuse-db`、直接改连接 NAME、开发 migrate 或部署库连接。

pytest-django 建立、迁移并销毁自己的测试库。运行后测试schema0、可见测试连接0；开发30表全行只读摘要与运行前一致（不是只比行数）。原始行和SHA摘要只在内存/系统临时目录保存，仓库只保存公开计数和相同结论。读摘要采用一致性快照只读事务，不清理、修改或倒退开发自增。

仓库没有全局 conftest 数据库保护钩子；settings仅约束测试库名的 `test_` 前缀。因此这里的安全结论依赖本次显式进程配置、实际预检、测试库独占生命周期和前后核对，不能扩大成任意配置都安全。复现时要显式给集成URL空字符串、实验开关false/0，避免删除变量后被本地 `.env` 重新启用。

命令主体为 `python -X utf8 -m pytest -q --junitxml=<本片证据路径>`；这会写入并销毁测试库。前端构建只生成忽略的 dist/类型构建缓存；没有重建或重启已运行网站。配置检查和依赖兼容检查均不迁移默认库。

## 124项跳过意味着什么

以本轮 raw/JUnit 为准，集成 URL 和诊断开关都未提供：

- 商品详情真实 Redis 与 Lua 准入85项依赖 `PROJECT_ONE_TEST_REDIS_URL`；没有启动开发6380或测试6381。
- MQ的12例依赖测试 Broker URL和C1/C2/C3/C4阶段、独立Linux Worker；没有启动、停机或中断Broker/Worker。
- ES在线/离线及别名17项依赖测试URL或离线开关；部分纯规则/注入测试仍在默认回归中执行。没有启动ES或操作索引。
- MySQL诊断/性能10例：旧订单查询1、正式索引大数据计划1、商品查询1、秒杀负载1、支付/到期锁观测6。它们需要显式开关，不因为本轮跳过就删除历史结论。

这次是 `skipped`，没有用marker过滤，所以不是 `deselected`；未执行项不写成通过。

## 历史证据怎样支持当前版本

| 方向 | 已有真实证据入口 | 本轮核对与不能扩大的结论 |
| --- | --- | --- |
| 普通交易 | [未付关闭及主链回归](phase-2b-unpaid-order-closure.md)、[退款模拟](phase-2b-refund-simulation.md) | 本次默认回归包含实际购物车建单→Mock支付→模拟发货→收货测试；身份/发货有测试辅助，不是一次完整普通浏览器交易 |
| 库存/秒杀 | [有限负载](phase-6c3-flash-sale-inventory-load.md)、[正式条件UPDATE/失败](phase-6c5-flash-sale-inventory-failures.md)、[浏览器原意图](phase-6e-flash-sale-browser.md) | 正式服务后主要增加HTTP/Lua适配；6C3原负载夹具在正式换策略后改为用测试专用NOWAIT对照。旧时延不是本轮测量，真实1205与注入1213/未知连接错误分开 |
| Redis | [4J](phase-4j-refill-admission.md)与其对照JSON | cache.py/cache_signals.py相比4J提交未变；新增搜索测试不算缓存重测。保护名额/SingleFlight按进程，非强一致或全站上限 |
| 支付/MQ | [C1](phase-5c1-payment-mq.md)、[C2](phase-5c2-payment-mq-retry.md)、[C3](phase-5c3-payment-publication-recovery.md)、[C4](phase-5c4-payment-worker-recovery.md)、[5D](phase-5d-payment-expiration-concurrency.md) | 租约/发布/恢复核心未变；付款与关闭服务后来加秒杀资格锁及终态转换，本次回归覆盖组合。没有重新杀Worker或观测六场景锁；仍人工补漏，不是自动全恢复 |
| Lua | [6F](phase-6f-flash-sale-admission.md)，36dc6d1 | 准入实现及测试未变；24/4事务是历史合成对照，不是库存预扣、在途并发上限或成交提速 |
| 订单索引 | [正式索引](phase-3-order-list-index.md)，59b54b9 | ordering源码未变；本轮正反迁移小数据回归通过，大数据计划未重跑；COUNT/深OFFSET仍有成本 |
| ES | [7B修正后证据](phase-7b-search-index-prototype.md)、[7C](phase-7c-search-read-api.md)、[7D](phase-7d-search-index-switch.md)，6a159ee | 搜索实现/目标测试未变；默认回归不能证明真实ES请求通过。test-only、单builder/操作者，无自动同步或前端切换 |
| 部署 | [9B](phase-9b-local-deployment-run.md)、[9C](phase-9c-session-csrf-operations.md)，29b7944 | 配置/验收脚本未变；本轮只读观察三部署容器healthy，没有运行HTTP/账号脚本、更新/备份/恢复 |

历史提交及JSON/指纹不刷新、不改日期。Git ancestry 只证明来源进入历史，不能代替实验；本轮保存相关文件变化并复核具体影响。本片没有新的性能、生产容量或全中间件运行结论。

## 最终收尾范围

当前工程结论：交易主链及三条旗舰主线已有证据，Lua准入/ES隔离扩展和基础本机部署收束，本轮普通回归、前端构建及基线CI通过，可以进入理解/简历阶段。

旧总纲“完整环境一条流程”和“三种负载基线＋优化对比”按拥有者随后明确的不需展示、准备收尾决定收窄为：基础部署入口＋各实验隔离复现入口，以及已有查询/缓存/下单/秒杀证据与限制。**不是把旧标准原样全部标完成。** 不要求追加一套全能力部署或三场新压测；ES完整顾客接入、自动同步、周期MQ接管、公网HTTPS和更新/备份恢复演练均保留为本期未做/可选，不隐去。

Learning Done、独立修改/排错、简历定稿与口述练习仍待完成；不把工程通过等同于本人掌握。下一步是围绕总览重建完整交易理解，不自动推进新代码。

本轮[文档静态核对](phase-10a-final-acceptance/document-checks.json)检查本地链接、JSON、raw/JUnit数字、CI SHA/job结论及应用/配置工作区未变；真实敏感值匹配0，Git diff检查通过。独立终审纠正提交来源措辞、关闭服务路径并明确源码清单的UTF-8/LF散列口径，实际重新核对关闭服务diff；不改变测试结果。

最终材料提交前再次核对scoped diff和Git状态，提交只含当前文档/证据，应用树相对上述基线必须不变。推送后核对该材料提交自己的CI，结果在最终回复链接到精确run；本记录中的基线CI不会冒充后续提交。停止重复验收。
