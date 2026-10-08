# 订单列表分页验证 — 2026-10-08

## 问题与本片结果

原GET /api/orders/返回本人全部订单，并包含地址与商品明细；用户历史越多，一次返回、序列化和传输的订单数量越大。本片改为固定每页20条，订单页同步增加上一页、下一页、URL页码恢复和错误重试。

本片属于Supporting/Pair，保护业务闭环与用户归属，建立后续数据库查询优化的可比较入口。采用项目商品列表已用的页码分页；游标分页可降低深翻页成本并减轻新增记录导致的位置漂移，但会增加当前接口与页面复杂度。本片达到分页、所有权、稳定数据排序和前后端验收后收束，不同时增加索引或负载矩阵。

## 接口与边界

- GET /api/orders/?page=2返回`{count,next,previous,results}`，固定20条；`page_size`不能扩大返回数量。
- 请求身份由Session决定，`user_id`参数不能改读他人订单；总数同样只计算本人。
- 顺序为`-created_at, -id`。相同时间的数据有确定顺序；不承诺跨多个请求的数据库快照。翻页期间新增或删除订单可能改变页码边界。
- 无订单的第一页返回200和空results；非法/越界页码返回404，本片不接受`last`别名。
- 单条订单字段、地址/商品快照、详情、普通下单与秒杀下单响应不变；库存/支付业务事务未修改。
- 采用COUNT统计总数，深页仍有OFFSET成本。本片成果是每次返回订单条数受限，不宣称查询次数减少、SQL延迟改善或生产容量。

## 实际验证

环境：Windows本地虚拟环境，Django 5.2.17、DRF 3.18.1、pytest 9.1.1；现有MySQL 8.4.10/127.0.0.1:3307。pytest仅创建和销毁`test_project_one`，未运行开发库迁移或写开发业务数据。前后测试库不存在、可见测试连接0；开发schema表数前后均30。未输出或记录.env敏感值，未变更服务配置或依赖。

执行测试时设置`DJANGO_ENV=test`、`MYSQL_TEST_DATABASE=test_project_one`、`CATALOG_CACHE_ENABLED=false`、`PAYMENT_EVENTS_ASYNC=false`，其余连接信息由既有本地配置加载。

```powershell
.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_order_list_pagination.py backend/apps/ordering/tests/test_order_api.py backend/apps/promotions/tests/test_flash_sale_api.py -q

.\.venv\Scripts\python.exe -m pytest backend/apps/ordering/tests/test_cart_api.py backend/apps/ordering/tests/test_order_lifecycle.py backend/apps/payments/tests/test_payment_api.py backend/apps/accounts/tests/test_session_auth_api.py -q

.\.venv\Scripts\python.exe backend/manage.py check
.\.venv\Scripts\python.exe backend/manage.py makemigrations --check --dry-run

# 在frontend目录执行；生成忽略的构建产物，不修改应用源码。
npm run build
```

结果：

| 检查 | 本轮观察 |
|---|---|
| 订单分页、订单API、秒杀API目标 | 28 passed，171 subtests passed，31.83秒 |
| 购物车API、订单生命周期、支付API、Session认证回归 | 63 passed，8 subtests passed，64.05秒 |
| Django系统检查 | 0 issues |
| 迁移漂移检查 | No changes detected |
| Vue/TypeScript与Vite构建 | 通过 |

测试耗时是验证记录，不是接口性能数据。原始输出见[target](phase-3-order-list-pagination-target-output.txt)和[regression](phase-3-order-list-pagination-regression-output.txt)。

新增目标覆盖21笔本人订单和2笔他人订单、相同时间跨页按ID排序、创建时间优先、20+1条无重复遗漏、完整快照及详情一致、归属与总数隔离、固定页长、空第一页、非法/越界页码。在测试认证被固定的情况下，非空20条页和1条页各有3条订单数据查询（统计总数、订单与地址JOIN、商品明细预加载）；这不是整个Session HTTP接口仅3条SQL的声明。

## 浏览器与视觉检查

使用Playwright CLI专用`order-pagination`会话，Vite端口5181。原有8000/5173服务保留，未停止其他聊天的进程。所有API请求由该浏览器的专用响应拦截提供，不连接开发业务数据。

| 场景 | 本轮观察 |
|---|---|
| 45笔模拟订单翻页 | 第一页20条，编号45–26；第二页20条，25–6；第三页5条，5–1 |
| 第三页边界 | 下一页禁用 |
| 浏览器刷新第三页 | URL保留page=3，仍显示5条 |
| 第二页注入一次503 | 显示失败与重试入口；重试恢复第二页20条 |
| URL含page=abc | 显示无效页码；返回第一页后URL为page=1、20条、无错误面板 |
| URL含page=99 | 404响应被显示为“这一页不存在”，不冒充空订单 |
| 手机390×844 | document.scrollWidth=390，无横向溢出；完整截图已查看 |
| JS运行时异常 | 修正后的检查会话中pageerror为0；503/404资源失败日志是预期错误路径 |

这些是使用模拟响应的页面行为检查，结合真实MySQL APIClient测试支撑本片行为；没有运行浏览器→真实HTTP→数据库端到端验收。页面检查首次因测试脚本的URL全局与路由匹配范围出错而超时，修正后才记录上述结果，不把第一次失败计作通过。

本地辅助脚本、截图和CLI快照位于忽略目录`output/playwright/`与`.playwright-cli/`，不提交为应用代码。截图为`output/playwright/order-pagination-mobile.png`。

## 未改与下一片

没有新增索引、模型迁移、依赖、部署配置、Redis Lua或Elasticsearch；下单、幂等、库存预占/核销/释放与支付规则保持原实现。开始前已核对main与origin/main同为18ffbe2；既有PROJECT_INSTRUCTIONS.md、Phase6E指南及知识库草稿修改保留，不混入本片提交。

下一片仅在隔离数据上采集真实完整列表查询、已有索引与执行计划，再比较同一数据下分页与索引的不同影响。届时才形成查询优化结论；本片不提前决定必须增加某个联合索引。
