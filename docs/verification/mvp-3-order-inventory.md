# MVP-3 Order + Inventory Reservation — 自动验证记录

日期：2026-09-18（Asia/Shanghai）。本记录对应本地 `main` 的 MVP-3 实现；尚未 push。

## 1. 实现范围

- Order、OrderItem、AddressSnapshot；
- 不可猜测 UUID 公开订单号与用户所有权查询；
- CNY Decimal 金额、0 运费、0 优惠及服务端重算；
- Idempotency-Key + 规范化请求摘要；
- InventoryReservation、15 分钟预留期限与 InventoryMovement；
- MySQL 事务内行锁、库存预留、快照和 Cart 清理；
- 失败整体回滚并保留 Cart；
- 同 Key 并发重放只产生一次业务副作用；
- Vue `/checkout` 最小结账入口；
- Model、API、权限、事务、快照、幂等与双线程并发测试；
- MVP-3 学习指南与项目事实文档更新。

未包含 PaymentAttempt、Mock Payment、Webhook、库存核销/释放、订单取消/过期触发、Redis、MQ、秒杀或行锁性能结论。

## 2. 已确认 API

| Method | Path | 成功行为 |
|---|---|---|
| `GET` | `/api/orders/` | 只返回当前用户订单 |
| `POST` | `/api/orders/` | 幂等创建待支付订单、快照并预留库存 |
| `GET` | `/api/orders/{public_id}/` | 只返回当前用户对应公开订单 |

所有接口要求 Session；POST 要求 CSRF 和 `Idempotency-Key`。

## 3. 关键证据对应关系

| 规则 | 证据 |
|---|---|
| 服务端金额权威 | 请求伪造 `total=0.01`，响应仍按 SKU 当前价计算 |
| 原子预留 | 成功后 Order/Snapshot/Reservation/Movement 同时存在，available 减少、reserved 增加 |
| 失败回滚 | 第二 SKU 库存不足时无 Order/Reservation/Movement，两条 CartItem 与两份库存保持原值 |
| 地址与商品快照 | 下单后修改/删除 Address 并修改 Product/SKU，订单详情仍返回旧事实 |
| 同参幂等 | 同 Key 同 address_id 第二次返回原 public_id，副作用计数保持 1 |
| 异参冲突 | 同 Key 换 address_id 返回 409 |
| 并发幂等 | 两线程同时使用同 Key，仅一个 created=true，且只有一份业务记录 |
| 用户隔离 | 他人地址不可结账；他人订单列表为空、详情为 404 |
| 数据库防线 | 唯一 Key、金额公式、OrderItem 数量/行金额、库存非负约束由 MySQL 测试覆盖 |

## 4. 实际运行结果

| 命令 | 结果 |
|---|---|
| `C:\project-one-ecommerce\.venv\Scripts\python.exe backend\manage.py check` | 通过，0 issues |
| `...\python.exe backend\manage.py makemigrations --check --dry-run` | 通过，No changes detected |
| `...\python.exe -m pytest backend\apps\ordering\tests\test_order_models.py backend\apps\ordering\tests\test_order_api.py backend\apps\ordering\tests\test_order_concurrency.py -q` | **14 passed, 9 subtests passed in 13.40s** |
| `...\python.exe -m pytest -q` | **94 passed, 27 subtests passed in 44.52s** |
| `npm run type-check`（`frontend/`） | 通过 |
| `npm run build`（`frontend/`） | 通过，Vite 8.3.0，46 modules transformed |
| `git diff --check` | 通过；只有 Git 的 LF/CRLF 工作树提醒 |

目标测试和全量测试使用项目配置的隔离 MySQL 测试数据库。本轮没有运行 `manage.py migrate`，没有升级或写入开发数据库。前端没有新增依赖。

## 5. 尚未验证与限制

- 没有运行 `manage.py migrate`，未升级或写入开发数据库；
- 没有执行浏览器人工点击或 E2E；
- 没有接触真实支付、真实订单或真实手机号；
- 当前行锁实现是正确性 baseline，不是性能优胜结论；
- 并发测试覆盖同用户同 Key 重放，不等同于库存热点压力测试；
- Payment、核销、释放、取消和过期仍属于后续切片；
- 改动尚未 push，远端 CI 尚未观察。

因此当前结论是：MVP-3 后端正确性目标测试、完整回归、Django 检查、迁移漂移检查、Vue 类型检查和生产构建已实际通过；不是开发数据库升级、浏览器人工验收、支付闭环、热点库存压测或远端 CI 完成。
