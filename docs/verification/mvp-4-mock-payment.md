# MVP-4 Mock Payment + Completion — 自动验证记录

最后更新：2026-09-24（Asia/Shanghai）。

> 当前状态：MVP-4 源代码已落地。进程内 SQLite `:memory:` 支付专项烟测和全项目非并发回归已经通过，Django 静态检查、前端类型检查/构建和 `git diff --check` 也已通过；真实 MySQL 专属约束/锁/并发测试、真实 MySQL 全项目回归和浏览器验收尚未完成。SQLite 结果不能替代 MySQL 证据。

## 1. 已实现、等待完整验证的范围

- PaymentAttempt 最小状态机；
- Payment Event / Event Inbox，`(provider, event_id)` 唯一并保留原始 payload；
- Provider Adapter 与后端 Mock Payment Gateway；
- MockGatewayPayment 保存 Mock Provider 侧结果；
- PaymentAttempt 的 `Idempotency-Key`、规范化请求摘要、活跃 Attempt 与最多三次规则；
- Attempt 短事务提交后才调用 Gateway；
- 默认 Mock 成功事件的校验与处理；
- UNKNOWN 使用原 Attempt reconcile；
- 支付成功后 Order 进入 `PAID`；
- Reservation 最多一次 `RESERVED → COMMITTED`；
- `reserved_stock` 核销和 COMMIT InventoryMovement；
- Django Admin Order Action 模拟发货；
- Shipment 保存 Admin 与模拟发货时间；
- Customer 确认本人已发货订单；
- `/orders`、`/orders/:publicId` 和 `/checkout` 的最薄顾客入口；
- Model、API、权限、状态机、幂等、事件校验、库存核销和订单完成测试；
- MVP-4 学习指南与本验证记录。

明确不在本轮验证范围：取消/过期、库存释放、Refund Simulation、迟到支付完整策略、Celery/RabbitMQ、Transactional Outbox、第三方 Sandbox、真钱、真实物流和完整 Vue Admin。

## 2. 协定 API

| Method | Path | 预期行为 | 当前证据 |
|---|---|---|---|
| `POST` | `/api/payments/attempts/` | 当前 Customer 为自己的待支付 Order 幂等创建 Attempt；要求 CSRF 与 `Idempotency-Key` | SQLite API 烟测通过；MySQL 待验证 |
| `GET` | `/api/payments/attempts/?order_public_id=<uuid>` | 只返回当前 Customer 指定订单的支付尝试，支持刷新恢复 | SQLite API 烟测通过；MySQL 待验证 |
| `GET` | `/api/payments/attempts/<uuid>/` | 只返回当前 Customer 自己的 Attempt | SQLite API 烟测通过；MySQL 待验证 |
| `POST` | `/api/payments/attempts/<uuid>/reconcile/` | 对原活跃 Attempt 查单并收敛状态，不创建新 Attempt | SQLite API 烟测通过；MySQL 待验证 |
| `POST` | `/api/payments/mock/webhook/` | 仅本地 development/test + loopback 可用的签名 Mock Event 入口；body 最大 16 KiB | SQLite 烟测通过；MySQL 待验证 |
| `POST` | `/api/orders/<uuid>/confirm-receipt/` | 订单本人将 `SHIPPED` 合法转换为 `COMPLETED` | SQLite API 烟测通过；MySQL 待验证 |

Admin 模拟发货不新增普通 Customer API；入口位于 Django Admin 的 Order 列表 Action 下拉【模拟发货】。

## 3. 页面与按钮验收表

| 页面/位置 | 预期新增入口或按钮 | 显示条件 | 当前证据 |
|---|---|---|---|
| 主导航 | 【我的订单】 | 进入 `/orders`；Guest 由页面提示登录 | 待人工验证 |
| `/orders` | 【刷新订单】与每单【查看详情】 | 只显示当前用户 Order | 待人工验证 |
| `/checkout` 创建成功卡片 | 【查看订单】 | Order 创建成功 | 待人工验证 |
| `/orders/:publicId` | 【发起模拟支付】 | `PENDING_PAYMENT` 且允许创建新 Attempt | 待人工验证 |
| `/orders/:publicId` | 【查询支付结果】 | 当前 Attempt 为 `CREATED/PENDING/UNKNOWN` | 待人工验证 |
| `/orders/:publicId` | 【刷新状态】 | 重新读取 Order 与对应 Attempts | 待人工验证 |
| `/orders/:publicId` | 【确认收货】 | Order 为 `SHIPPED` | 待人工验证 |
| Django Admin Order 列表 | Action 下拉【模拟发货】 | Admin 选择合法 `PAID` Order | 待人工验证 |

普通顾客页面不应出现失败、UNKNOWN、错误签名或错误金额的场景选择器；这些场景只从自动测试或受保护的开发注入路径触发。

## 4. 关键 Test Oracle 与证据矩阵

支付 API 的非并发路径已经在进程内 SQLite `:memory:` 烟测中执行；下表的“SQLite 已覆盖”只证明对应 Python/DRF/Service 流程在该替代数据库上通过，不证明 MySQL 的约束、行锁和并发语义。

| 规则 | 可执行预期 | 实际结果 |
|---|---|---|
| 默认成功闭环 | 创建 Attempt 后经过 Event Inbox，Attempt=`SUCCEEDED`、Order=`PAID` | SQLite 已覆盖；MySQL 待验证 |
| 库存核销 | 初始 available=8/reserved=2，成功后 available=8/reserved=0，Reservation=`COMMITTED` | SQLite 已覆盖；MySQL 待验证 |
| 核销流水 | 每条 Reservation 恰好一条 COMMIT Movement，delta 为 available=0/reserved=-quantity | SQLite 已覆盖；MySQL 待验证 |
| Attempt 同参幂等 | 同 Key + 同 Order 返回同一 Attempt，不重复副作用 | SQLite 已覆盖；MySQL 待验证 |
| Attempt 异参冲突 | 同 Key 换 Order 返回 409 | SQLite 已覆盖；MySQL 待验证 |
| 活跃 Attempt 唯一 | `CREATED/PENDING/UNKNOWN` 存在时拒绝新 Key 创建第二个活跃 Attempt | SQLite 已覆盖；MySQL 唯一约束待验证 |
| 最多三次 | 前次明确终态失败后可新建，但第 4 次主动尝试被拒绝 | SQLite 已覆盖；MySQL 待验证 |
| 成功后禁止新建 | 任一 Attempt 成功后不能再创建 | SQLite 已覆盖；MySQL 待验证 |
| Event 幂等 | 同 `(provider,event_id)` 重放不会重复改变 Order/库存/Movement | SQLite 已覆盖；MySQL 唯一约束待验证 |
| 语义重复防线 | 不同 event_id 的重复成功也不能二次核销同一 Reservation | SQLite 已覆盖；MySQL 待验证 |
| 事件先入箱 | 错误签名/金额/币种事件仍保留原始 payload 和失败原因 | SQLite 已覆盖；MySQL 待验证 |
| 无效事件去重 | 相同无效签名 payload 使用确定性 synthetic inbox ID，只保存一份且不占用合法 event_id | SQLite 已覆盖；MySQL 待验证 |
| 非 ASCII 签名 | 非 ASCII、长度不合法等签名安全拒绝，不让 `compare_digest` 抛异常 | SQLite 已覆盖；MySQL 待验证 |
| 错误事件无副作用 | 错误签名/金额/币种不改变确认状态、Order 和库存 | SQLite 已覆盖；MySQL 待验证 |
| Webhook 入口保护 | 远端来源 404、超 16 KiB 为 413、非本地环境 404 | SQLite API 烟测已覆盖 |
| UNKNOWN 不是失败 | UNKNOWN 保持活跃，不能新建 Attempt；reconcile 复用原 Attempt | SQLite 已覆盖；MySQL 待验证 |
| Provider 前崩溃恢复 | 缺 MockGatewayPayment 时，query 为同一 Attempt 补建 Provider 状态而非新 Attempt | SQLite 已覆盖；MySQL 待验证 |
| 刷新恢复 | 按 order_public_id 列出本人 Attempts，能够恢复查询按钮 | SQLite 已覆盖；MySQL 待验证 |
| 网络边界 | Attempt 已提交后才调用 Gateway，调用期间不持有 Order/Inventory 数据库锁 | SQLite 流程测试已覆盖；MySQL 锁证据待验证 |
| 用户隔离 | 他人 Order/Attempt 不可见且不可操作 | SQLite 已覆盖；MySQL 待验证 |
| Admin 发货 | 只有合法 Admin 操作可以 `PAID → SHIPPED` | SQLite 已覆盖；MySQL 待验证 |
| 发货事实 | 成功发货恰好创建一条 Shipment，记录 shipped_by/shipped_at | SQLite 已覆盖；MySQL 待验证 |
| 非法发货 | PENDING_PAYMENT/SHIPPED/COMPLETED 不能被 Admin Action 错误发货 | SQLite 已覆盖；MySQL 待验证 |
| 确认收货 | 只有订单本人可以 `SHIPPED → COMPLETED` | SQLite 已覆盖；MySQL 待验证 |
| 最小收货事实 | 确认收货只推进 Order 状态，不额外创建收货记录 | 静态实现已核对；MySQL 待验证 |
| 非法确认 | 他人、非 SHIPPED 或 Guest 请求被拒绝且无状态变化 | SQLite 已覆盖；MySQL 待验证 |
| 终态稳定 | 重复确认不产生新业务副作用，COMPLETED 不回退 | SQLite 已覆盖；MySQL 待验证 |
| 同 Key 双线程 | 两个并发请求只创建一个 Attempt、一次库存核销 | **未执行：SQLite 烟测主动 deselect；MySQL/Docker 不可用** |
| MySQL 模型约束 | user+key、活跃 Attempt、provider+event_id、金额/次数约束由 MySQL 拒绝非法数据 | **4 个测试未执行：MySQL/Docker 不可用** |

## 5. 实际运行的自动检查

因为本机 Docker/MySQL 不可用，后端测试使用**进程内临时 settings override + SQLite `:memory:`**；没有把 SQLite 结果包装成 MySQL 成功。真实 MySQL 测试仍需使用隔离测试数据库，不能写入开发数据库。

| 检查 | 实际结果 | 证据边界 |
|---|---|---|
| `backend\manage.py check` | **通过：0 issues** | Django 系统配置检查，不证明数据库并发语义 |
| `backend\manage.py makemigrations --check --dry-run` | **`No changes detected`**；同时出现 MySQL migration-history 连接警告 | 证明当前模型未发现迁移漂移；因 MySQL 不可用，不算 MySQL 迁移历史验证通过 |
| 支付专项 SQLite `:memory:` 烟测（排除并发用例） | **27 passed, 1 deselected, 6 subtests passed in 23.04s** | `1 deselected` 是同 Key 双线程 MySQL 并发用例 |
| 全项目 SQLite `:memory:` 非并发回归 | **123 passed, 3 deselected, 35 subtests passed in 69.82s** | `3 deselected` 是并发用例；不能替代 MySQL 锁/约束/并发验证 |
| MySQL 支付双线程并发 | **未执行** | Docker/MySQL 不可用；1 个支付并发目标测试没有真实运行 |
| MySQL Payment 模型约束 | **未执行** | Docker/MySQL 不可用；4 个 MySQL 专属约束目标测试没有真实运行 |
| 真实 MySQL 全项目回归 | **未执行** | 当前只有 SQLite 替代数据库回归证据 |
| `npm run type-check`（`frontend/`） | **通过** | Vue/TypeScript 静态类型检查 |
| `npm run build`（`frontend/`） | **通过：51 modules transformed** | 前端生产构建通过，不等于浏览器交互已验收 |
| `git diff --check` | **通过** | 补丁空白字符/格式检查 |

本轮没有声称以下内容已经验证：MySQL 的 `select_for_update` 行锁行为、MySQL 条件/唯一约束的真实拒绝效果、真实第三方网络、生产支付、浏览器 Session/CSRF 交互、Django Admin 人工操作和远端 CI。

## 6. 浏览器与 Admin 人工验收清单

自动测试不能代替页面和真实请求链验收。实际执行后逐项填写结果：

- [ ] 登录 Customer 后，主导航显示【我的订单】；
- [ ] `/orders` 只显示本人订单，页面【刷新订单】和每单【查看详情】都工作；
- [ ] `/checkout` 创建成功后【查看订单】进入正确公开订单详情；
- [ ] 待支付订单显示【发起模拟支付】；
- [ ] 点击后通过真实 Session/CSRF 请求，默认 Mock 成功并刷新为 `PAID`；
- [ ] 页面显示 Attempt 与支付状态，不把前端提示当最终事实；
- [ ] `CREATED/PENDING/UNKNOWN` 能在刷新后恢复【查询支付结果】；
- [ ] 【刷新状态】重新请求 Order 与 Attempts，不沿用错误的本地猜测；
- [ ] Admin Order 列表仅把合法 `PAID` Order 模拟发货；
- [ ] Customer 详情刷新后看到 `SHIPPED` 和【确认收货】；
- [ ] 点击确认后 Order 为 `COMPLETED`，按钮消失；
- [ ] 另一个 Customer 不能查看或操作这张 Order/Attempt；
- [ ] 普通页面没有异常场景注入按钮；
- [ ] 浏览器 Network 中能解释每个请求的 Method、URL、CSRF、状态码和响应。

人工验收环境、合成数据、截图/日志位置和结果：**待填写**。

## 7. 失败注入与限制记录

下列场景应通过自动测试或受保护的开发注入验证，不能暴露给普通 Customer：

| 场景 | 预期 | 实际结果 |
|---|---|---|
| Mock 明确失败 | Attempt=`FAILED`，Order 仍 `PENDING_PAYMENT`，库存仍 reserved | SQLite 支付烟测已通过；MySQL 待验证 |
| Mock 超时/UNKNOWN | 保留原活跃 Attempt，禁止新建，允许 reconcile | SQLite 支付烟测已通过；MySQL 待验证 |
| 重复 Event | Inbox 幂等，业务副作用一次 | SQLite 支付烟测已通过；MySQL 唯一约束待验证 |
| 错误签名 | Event 保留并标记拒绝，不改变业务事实 | SQLite 支付烟测已通过 |
| 非 ASCII 签名 | 安全拒绝并保留审计结果，不因 `compare_digest` 输入抛处理异常 | SQLite 支付烟测已通过 |
| 错误金额 | Event 保留并标记拒绝，不改变业务事实 | SQLite 支付烟测已通过 |
| 错误币种 | Event 保留并标记拒绝，不改变业务事实 | SQLite 支付烟测已通过 |
| 非法状态转换 | 后端拒绝且数据库保持原状态 | SQLite 支付烟测已通过；MySQL 待验证 |
| 核销事务中断 | 不留下 Attempt 成功、Order 未支付或部分库存核销的半完成状态；Inbox 可重试 | SQLite 回滚/重试测试已通过；MySQL 待验证 |

本记录完成时仍需明确区分：

1. 已实际运行并通过；
2. 只做了代码/静态检查；
3. 因环境限制尚未验证。

## 8. 最终验证结论

当前证据支持：MVP-4 的最小同步 Mock Payment + 发货/确认收货流程已经通过 SQLite 非并发自动测试、Django 检查和前端静态构建验证；支付专项为 **27 passed, 1 deselected, 6 subtests passed**，全项目 SQLite 非并发回归为 **123 passed, 3 deselected, 35 subtests passed**。

当前证据**不支持**宣称“真实 MySQL 支付闭环已完整验证”：MySQL 双线程并发、4 个 MySQL Payment 模型约束、真实 MySQL 全项目回归和浏览器/Admin 人工验收仍未执行。`MockGatewayPayment` 也与业务表位于同一个 MySQL 服务中，不是独立第三方网络；即使代码把 Adapter 调用放在 Attempt 事务之外，也不能等同于生产支付验证。

即使后续 MySQL baseline 目标测试全部通过，也只能说明最小同步 Mock Payment/Completion 闭环获得了对应证据；它不等于取消/过期、迟到支付、Refund Simulation、完整乱序/崩溃窗口、性能实验、第三方 Sandbox 或 Payment Evidence Pack 已完成。
